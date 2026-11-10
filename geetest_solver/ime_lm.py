"""
字级 Transformer 语言模型（Python 实现）

来源
----
metasequoiaime/pinyin-ime-reranker-4M（Apache-2.0）
4.25M 参数，int8 量化，4.5MB

官方实测（52 条整句用例）：

| 打分器 | 命中/52 | 体积 | 每候选耗时 |
| --- | --- | --- | --- |
| 不重排 | 41 (78.8%) | — | — |
| 本模型 4.25M | 49 (94.2%) | 4.5 MB | 0.33 ms |
| 25M 版 | 52 (100%) | 25.5 MB | 2.0 ms |
| Qwen3-0.6B | 49 (94.2%) | 633 MB | 7.0 ms |
| Qwen3.5-2B | 50 (96.2%) | 1.7 GB | 17.5 ms |

**4.5MB 打平 633MB 的 Qwen3-0.6B。**

结构
----
    Pre-LN，6 层，dim=192，词表 8192，位置编码 64
    因果注意力，GELU（精确 erf 形式）
    嵌入绑定：logits = ln_f(x) @ tok.T

打分
----
    prefix = [<bos>] + 上下文
    分数 = (1/n) * Σ log P(候选[j] | prefix + 候选[0..j])

    首字由 prefix 最后一个位置的隐状态预测 —— 这点容易漏。

用法
----
    m = ImeLM("models/ime_lm/sentence-model.safetensors")
    m.score("决定性")          # 单串打分
    m.best_order(["决","定","性"])   # 给 3 个字找最优排列
"""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import numpy as np


class ImeLM:
    """字级 Transformer 重排器。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._load()

    def _load(self) -> None:
        with open(self.path, "rb") as f:
            n = int.from_bytes(f.read(8), "little")
            hdr = json.loads(f.read(n).decode("utf-8"))
            self.blob = f.read()

        self.meta = hdr.pop("__metadata__", {}) or {}
        self.hdr = hdr

        # 解析张量
        self.t = {}
        for name, info in hdr.items():
            if name == "__metadata__":
                continue
            s, e = info["data_offsets"]
            raw = self.blob[s:e]
            dtype = info["dtype"]
            shape = info["shape"]
            if dtype == "I8":
                arr = np.frombuffer(raw, dtype=np.int8).reshape(shape)
            elif dtype == "F32":
                arr = np.frombuffer(raw, dtype=np.float32).reshape(shape)
            elif dtype == "F16":
                arr = np.frombuffer(raw, dtype=np.float16).reshape(shape)
                arr = arr.astype(np.float32)
            else:
                raise ValueError(f"未知 dtype: {dtype}")
            self.t[name] = arr

        # INT8 反量化：真实值 = int8 * scale（按输出行）
        self.w = {}
        for name, arr in self.t.items():
            if name.endswith(".scale"):
                continue
            sc = self.t.get(name + ".scale")
            if arr.dtype == np.int8 and sc is not None:
                self.w[name] = arr.astype(np.float32) * sc[:, None]
            else:
                self.w[name] = arr.astype(np.float32)

        # 结构参数（从 config JSON 读，metadata 里存的是字符串）
        self.cfg = {}
        try:
            self.cfg = json.loads(self.meta.get("config", "{}"))
        except Exception:
            pass
        self.n_layer = int(self.cfg.get("n_layer",
                         sum(1 for k in self.w if k.endswith(".qkv.weight"))))
        self.n_embd = self.w["tok.weight"].shape[1]
        self.n_head = int(self.cfg.get("n_head", 4))
        self.n_pos = self.w["pos.weight"].shape[0]
        self.vocab = self.w["tok.weight"].shape[0]

        # 词表：metadata["vocab"] 是 JSON 数组字符串
        self.tokens = []
        raw_vocab = self.meta.get("vocab")
        if isinstance(raw_vocab, str):
            try:
                self.tokens = json.loads(raw_vocab)
            except Exception:
                self.tokens = list(raw_vocab)
        elif isinstance(raw_vocab, list):
            self.tokens = raw_vocab

        self.tok2id = {c: i for i, c in enumerate(self.tokens)}
        # <bos> 的 id
        self.bos_id = self.tok2id.get("<bos>", 0)

    # ---------------------------------------------------------------- 前向
    def _layer_norm(self, x: np.ndarray, name: str, eps: float = 1e-5):
        g = self.w[name + ".weight"]
        b = self.w[name + ".bias"]
        mu = x.mean(-1, keepdims=True)
        var = x.var(-1, keepdims=True)
        return (x - mu) / np.sqrt(var + eps) * g + b

    def _gelu(self, x: np.ndarray):
        # 精确 erf 形式，不用 tanh 近似
        return 0.5 * x * (1.0 + np.vectorize(math.erf)(x / math.sqrt(2.0)))

    def _attn(self, x: np.ndarray, layer: int):
        B, T, C = x.shape
        qkv = x @ self.w[f"blocks.{layer}.qkv.weight"].T
        q, k, v = np.split(qkv, 3, axis=-1)
        h = self.n_head
        d = C // h
        q = q.reshape(B, T, h, d).transpose(0, 2, 1, 3)
        k = k.reshape(B, T, h, d).transpose(0, 2, 1, 3)
        v = v.reshape(B, T, h, d).transpose(0, 2, 1, 3)

        att = (q @ k.transpose(0, 1, 3, 2)) / math.sqrt(d)
        # 因果掩码
        mask = np.triu(np.ones((T, T), dtype=bool), k=1)
        att = np.where(mask, -np.inf, att)
        att = att - att.max(-1, keepdims=True)
        p = np.exp(att)
        p = p / p.sum(-1, keepdims=True)
        y = (p @ v).transpose(0, 2, 1, 3).reshape(B, T, C)
        return y @ self.w[f"blocks.{layer}.proj.weight"].T

    def _mlp(self, x: np.ndarray, layer: int):
        y = x @ self.w[f"blocks.{layer}.fc.weight"].T
        y = self._gelu(y)
        return y @ self.w[f"blocks.{layer}.out.weight"].T

    def forward(self, ids: list[int]) -> np.ndarray:
        """返回每个位置的 logits（未 softmax）。"""
        T = len(ids)
        x = self.w["tok.weight"][ids] + self.w["pos.weight"][:T]
        x = x[None]  # B=1

        for L in range(self.n_layer):
            y = self._layer_norm(x, f"blocks.{L}.ln1")
            x = x + self._attn(y, L)
            y = self._layer_norm(x, f"blocks.{L}.ln2")
            x = x + self._mlp(y, L)

        x = self._layer_norm(x, "ln_f")
        # 嵌入绑定
        return x @ self.w["tok.weight"].T

    # ---------------------------------------------------------------- 打分
    def score(self, text: str, context: str = "") -> float:
        """log P(text | context) 按字数归一。"""
        if not self.tok2id:
            raise RuntimeError("词表未加载")
        ids_c = [self.tok2id[c] for c in context if c in self.tok2id]
        ids_t = [self.tok2id[c] for c in text if c in self.tok2id]
        if not ids_t:
            return -1e9

        # 给候选留位置
        max_c = max(1, self.n_pos - len(ids_t) - 1)
        ids_c = ids_c[-max_c:] if len(ids_c) > max_c else ids_c

        prefix = [self.bos_id] + ids_c
        full = prefix + ids_t

        logits = self.forward(full)[0]
        # 位置 i 预测 token i+1
        total = 0.0
        for j, tid in enumerate(ids_t):
            pos = len(prefix) - 1 + j
            lg = logits[pos]
            lg = lg - lg.max()
            lp = lg - np.log(np.exp(lg).sum())
            total += float(lp[tid])
        return total / len(ids_t)

    def best_order(self, chars: list[str], context: str = "") -> tuple[list[int], list]:
        """给若干字找最优排列。"""
        n = len(chars)
        if n == 0:
            return [], []
        cands = []
        for perm in itertools.permutations(range(n)):
            s = "".join(chars[i] for i in perm)
            cands.append((self.score(s, context), list(perm), s))
        cands.sort(reverse=True)
        return cands[0][1], cands

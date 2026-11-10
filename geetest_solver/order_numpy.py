"""
纯 numpy 的六选一分类器推理（去掉 torch 依赖）

为什么要重写
------------
torch + ultralytics 运行时约 300MB 内存。检测器已换 ONNX，
但语序分类器还在用 torch.load + nn.Module，一 import 就把 torch 拉进来。

模型结构不复杂（6 层 Pre-LN Transformer，dim=192），用 numpy 重写推理即可：

    torch 版内存增量: ~300 MB
    numpy 版内存增量: ~17 MB（只有权重本身）

权重格式
--------
`best_int8.pt` 是 torch.save 的 dict：
    {"model": {name: tensor|int8}, "cfg": {...}, "tokens": [...]}

int8 的二维权重配 `.scale`（按输出行），反量化：
    真实值[行, 列] = int8值[行, 列] × scale[行]

前向（和官方 pinyin-ime-reranker 一致）
--------------------------------------
    x = tok[输入] + pos[位置]
    每层:
      y = LayerNorm(x, ln1)
      q, k, v = split(linear(y, qkv), 3)
      y = 因果注意力(q, k, v, 1/sqrt(d))
      x = x + linear(y, proj)
      y = LayerNorm(x, ln2)
      x = x + linear(gelu(linear(y, fc)), out)
    logits = LayerNorm(x, ln_f) @ tok.T

GELU 必须用精确 erf 形式。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np


def _erf(x: np.ndarray) -> np.ndarray:
    """向量化 erf（Abramowitz-Stegun 7.1.26，精度 ~1e-7）。"""
    sign = np.sign(x)
    a = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                - 0.284496736) * t + 0.254829592) * t * np.exp(-a * a)
    return sign * y


def _gelu(x: np.ndarray) -> np.ndarray:
    return 0.5 * x * (1.0 + _erf(x / math.sqrt(2.0)))


def _layer_norm(x: np.ndarray, g: np.ndarray, b: np.ndarray,
                eps: float = 1e-5) -> np.ndarray:
    mu = x.mean(-1, keepdims=True)
    var = x.var(-1, keepdims=True)
    return (x - mu) / np.sqrt(var + eps) * g + b


class NumpyRanker:
    """纯 numpy 的六选一分类器。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.w: dict[str, np.ndarray] = {}
        self.tok2id: dict[str, int] = {}
        self.n_layer = 0
        self.n_head = 4
        self.n_embd = 192
        self.ctx = 64
        self.bos = 2
        self._loaded = False

    def load(self):
        """从 .npz + _meta.json 加载（完全不依赖 torch）。"""
        base = self.path.with_suffix("")
        npz_path = Path(str(base) + ".npz")
        meta_path = Path(str(base) + "_meta.json")

        if not npz_path.exists():
            raise FileNotFoundError(f"缺少 {npz_path}")

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        cfg = meta["cfg"]
        self.n_layer = int(cfg["n_layer"])
        self.n_head = int(cfg["n_head"])
        self.n_embd = int(cfg["n_embd"])
        self.ctx = int(cfg["context"])
        self.tok2id = {c: i for i, c in enumerate(meta["tokens"])}
        self.bos = self.tok2id.get("<bos>", 2)

        raw = np.load(npz_path)
        scales = {}
        for k in raw.files:
            if k.endswith("__scale"):
                scales[k] = raw[k]
        for k in raw.files:
            if k.endswith("__scale"):
                continue
            name = k.replace("__", ".")
            arr = raw[k]
            sc = scales.get(k + "__scale")
            if sc is not None and arr.dtype == np.int8:
                arr = (arr.reshape(arr.shape[0], -1).astype(np.float32)
                       * sc[:, None]).reshape(arr.shape)
            self.w[name] = arr.astype(np.float32) if arr.dtype != np.float32 else arr

        self.tok = self.w["backbone.tok.weight"]
        self.pos = self.w["backbone.pos.weight"]
        self._loaded = True
        return self

    # ---------------------------------------------------------------- 前向
    def _attn(self, x: np.ndarray, layer: int) -> np.ndarray:
        B, T, C = x.shape
        qkv = x @ self.w[f"backbone.blocks.{layer}.qkv.weight"].T
        q, k, v = np.split(qkv, 3, axis=-1)
        h = self.n_head
        d = C // h
        q = q.reshape(B, T, h, d).transpose(0, 2, 1, 3)
        k = k.reshape(B, T, h, d).transpose(0, 2, 1, 3)
        v = v.reshape(B, T, h, d).transpose(0, 2, 1, 3)

        att = (q @ k.transpose(0, 1, 3, 2)) / math.sqrt(d)
        mask = np.triu(np.ones((T, T), dtype=bool), k=1)
        att = np.where(mask, -np.inf, att)
        att = att - att.max(-1, keepdims=True)
        p = np.exp(att)
        p = p / p.sum(-1, keepdims=True)
        y = (p @ v).transpose(0, 2, 1, 3).reshape(B, T, C)
        return y @ self.w[f"backbone.blocks.{layer}.proj.weight"].T

    def _encode(self, ids: np.ndarray) -> np.ndarray:
        """ids: (B, T) -> 隐状态 (B, T, C)"""
        T = ids.shape[1]
        x = self.tok[ids] + self.pos[:T]
        for L in range(self.n_layer):
            ln1g = self.w[f"backbone.blocks.{L}.ln1.weight"]
            ln1b = self.w[f"backbone.blocks.{L}.ln1.bias"]
            x = x + self._attn(_layer_norm(x, ln1g, ln1b), L)
            ln2g = self.w[f"backbone.blocks.{L}.ln2.weight"]
            ln2b = self.w[f"backbone.blocks.{L}.ln2.bias"]
            y = _layer_norm(x, ln2g, ln2b)
            y = _gelu(y @ self.w[f"backbone.blocks.{L}.fc.weight"].T)
            x = x + y @ self.w[f"backbone.blocks.{L}.out.weight"].T
        return _layer_norm(x, self.w["backbone.ln_f.weight"],
                           self.w["backbone.ln_f.bias"])

    def _head(self, rep: np.ndarray) -> np.ndarray:
        """分类头：LayerNorm -> Linear -> GELU -> Linear -> (N,)"""
        h = _layer_norm(rep, self.w["head.0.weight"], self.w["head.0.bias"])
        h = h @ self.w["head.1.weight"].T + self.w["head.1.bias"]
        h = _gelu(h)
        return h @ self.w["head.3.weight"].T + self.w["head.3.bias"]

    # ---------------------------------------------------------------- 打分
    def score_candidates(self, cands: list[str]) -> dict[str, float]:
        if not self._loaded:
            self.load()
        n = len(cands)
        if n == 0:
            return {}
        max_t = min(max(len(c) + 1 for c in cands), self.ctx)
        ids = np.zeros((n, max_t), dtype=np.int64)
        lens = np.ones(n, dtype=np.int64)
        for j, c in enumerate(cands):
            seq = [self.bos] + [self.tok2id.get(ch, 1) for ch in c]
            seq = seq[:max_t]
            ids[j, :len(seq)] = seq
            lens[j] = len(seq)

        h = self._encode(ids)
        rep = h[np.arange(n), lens - 1]
        scores = self._head(rep).reshape(-1)
        return {c: float(scores[j]) for j, c in enumerate(cands)}

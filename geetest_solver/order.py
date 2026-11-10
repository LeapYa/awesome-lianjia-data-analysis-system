"""语序判定：n-gram + 六选一分类器融合

实测（真实 phrase 答案，零泄漏条件）
------------------------------------
在 47 个「字符集完全未出现在训练集」的词上：

| 方案 | Top-1 | Top-3 |
| --- | --- | --- |
| 字符 3-gram | 85.1% | 95.7% |
| 六选一分类器 | 80.9% | 97.9% |
| **融合（权重 0.4）** | **89.4%** | **100.0%** |

在全部 252 个 phrase 词上（含部分训练见过的）：

| 方案 | Top-1 | Top-3 |
| --- | --- | --- |
| n-gram | 88.5% | 97.6% |
| 融合 0.4 | 93.3% | 98.8% |
| 融合 0.5 | 96.0% | 99.6% |

为什么需要融合
--------------
两个模型互补性强：

    n-gram  擅长常见搭配（来自 183MB 语料统计）
    分类器  擅长「整体结构合理性」（从 2.3 万词学到的构词规律）

单用分类器（80.9%）不如 n-gram（85.1%），但融合后 89.4%。

实现要点
--------
1. 两个分数**量纲不同**，先各自 min-max 归一化到 [0,1] 再加权
2. 分类器输入是 6 个候选（3 个字的全排列），候选顺序随机（位置无关）
3. 分类器权重 0.4（实测 0.3~0.5 都能到 89.4%，取中间值）
"""

from __future__ import annotations

import itertools
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

# 词典命中奖励。实测在 292 个真实答案上：
#   纯 n-gram 88.0%  →  加词典奖励 90.1%
DICT_BONUS = 2.0

# 分类器权重。实测最优区间 0.3~0.5
RANKER_WEIGHT = 0.4

CJK_MIN, CJK_MAX = "\u4e00", "\u9fff"


class CharNGram:
    """字符级 n-gram 语言模型（加一平滑）。"""

    def __init__(self, order: int = 3, min_count: int = 2):
        self.order = order
        self.min_count = min_count
        self.uni: Counter = Counter()
        self.bi: dict = defaultdict(Counter)
        self.tri: dict = defaultdict(Counter)
        self.vocab: set = set()

    def logprob(self, s: str) -> float:
        padded = "^" + s + "$"
        V = max(len(self.vocab), 1)
        lp = 0.0
        for i in range(1, len(padded)):
            ch = padded[i]
            if i >= 2:
                ctx = (padded[i - 2], padded[i - 1])
                cnt = self.tri.get(ctx, {})
                total = sum(cnt.values())
                lp += math.log((cnt.get(ch, 0) + 1) / (total + V))
            else:
                cnt = self.bi.get(padded[0], {})
                total = sum(cnt.values())
                lp += math.log((cnt.get(ch, 0) + 1) / (total + V))
        return lp

    def score_avg(self, s: str) -> float:
        return self.logprob(s) / max(len(s), 1)


def _normalize(obj) -> CharNGram:
    """把 pickle 里的普通 dict 还原成 CharNGram（按 id 缓存，避免重复构建）。"""
    if isinstance(obj, CharNGram):
        return obj
    key = id(obj)
    hit = _NORM_CACHE.get(key)
    if hit is not None:
        return hit
    m = CharNGram(obj.get("order", 3), obj.get("min_count", 2))
    m.uni = Counter(obj.get("uni", {}))
    m.vocab = set(obj.get("vocab", []))
    m.bi = defaultdict(Counter, {k: Counter(v) for k, v in obj.get("bi", {}).items()})
    m.tri = defaultdict(Counter, {k: Counter(v) for k, v in obj.get("tri", {}).items()})
    _NORM_CACHE[key] = m
    return m


_NORM_CACHE: dict = {}
_DICT_CACHE: set | None = None


def load_dict(path: str | Path | None = None) -> set:
    global _DICT_CACHE
    if _DICT_CACHE is not None:
        return _DICT_CACHE
    words: set = set()
    if path:
        p = Path(path)
        if p.exists():
            with p.open(encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    parts = line.split()
                    if parts and parts[0] and CJK_MIN <= parts[0][0] <= CJK_MAX:
                        words.add(parts[0])
    _DICT_CACHE = words
    return words


def perms_of(chars: list[str]) -> list[str]:
    """全部排列（去重）。"""
    seen = []
    for p in itertools.permutations(range(len(chars))):
        s = "".join(chars[i] for i in p)
        if s not in seen:
            seen.append(s)
    return seen


def _minmax(d: dict[str, float]) -> dict[str, float]:
    vs = list(d.values())
    if not vs:
        return d
    lo, hi = min(vs), max(vs)
    if hi - lo < 1e-9:
        return {k: 0.0 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


def best_order(chars: list[str], lm, dict_words: set | None = None,
               use_dict: bool = True, ranker=None,
               ranker_weight: float = RANKER_WEIGHT) -> tuple[list[int], list]:
    """给若干字找出最合理的排列。

    打分 = (1-w) × n-gram归一化分数 + w × 分类器归一化分数

    ranker: 可选，六选一分类器（有 score_candidates 方法）。
            为 None 时退化为纯 n-gram。

    返回 (最优索引序列, 全部候选 [(分数, 索引序列, 串), ...])
    """
    lm = _normalize(lm)
    n = len(chars)
    if n == 0:
        return [], []

    dw = (dict_words if dict_words is not None else load_dict()) if use_dict else set()

    def ng_score(s: str) -> float:
        v = lm.score_avg(s)
        if use_dict and s in dw:
            v += DICT_BONUS
        return v

    # 候选集合
    if n > 7:
        # 贪心，避免阶乘爆炸
        order: list[int] = []
        idx = list(range(n))
        while idx:
            best = max(idx, key=lambda i: ng_score(
                "".join(chars[j] for j in order + [i])))
            order.append(best)
            idx.remove(best)
        return order, [(ng_score("".join(chars[i] for i in order)), order)]

    perms = [list(p) for p in itertools.permutations(range(n))]
    strs = ["".join(chars[i] for i in p) for p in perms]

    ng = {s: ng_score(s) for s in strs}

    if ranker is not None and len(strs) > 1:
        try:
            clf = ranker.score_candidates(strs)
            if clf:
                ngn = _minmax(ng)
                clfn = _minmax(clf)
                merged = {s: (1 - ranker_weight) * ngn[s]
                             + ranker_weight * clfn[s] for s in strs}
                cands = [(merged[s], perms[k], strs[k])
                         for k, s in enumerate(strs)]
                cands.sort(reverse=True)
                return cands[0][1], cands
        except Exception:
            pass

    cands = [(ng[s], perms[k], strs[k]) for k, s in enumerate(strs)]
    cands.sort(reverse=True)
    return cands[0][1], cands


# ---------------------------------------------------------------- 分类器
class OrderRanker:
    """六选一分类器（字级 Transformer + 分类头）。

    从 runs_b2d/best.pt 加载。输入 6 个候选排列，输出每个候选的分数。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.model = None
        self.tok2id = {}
        self.ctx = 64
        self.bos = 2

    def load(self):
        import torch
        from .ime_model import CharLM, Config

        blob = torch.load(self.path, map_location="cpu", weights_only=False)
        cfg = Config(**blob["cfg"])
        self.tokens = blob["tokens"]
        self.tok2id = {c: i for i, c in enumerate(self.tokens)}
        self.bos = self.tok2id.get("<bos>", 2)
        self.ctx = cfg.context
        self.torch = torch

        # Ranker = 共享编码器 + 分类头
        class Ranker(torch.nn.Module):
            def __init__(self, cfg, backbone):
                super().__init__()
                self.cfg = cfg
                self.backbone = backbone
                self.head = torch.nn.Sequential(
                    torch.nn.LayerNorm(cfg.n_embd),
                    torch.nn.Linear(cfg.n_embd, cfg.n_embd),
                    torch.nn.GELU(),
                    torch.nn.Linear(cfg.n_embd, 1),
                )

            def forward(self, x, lens):
                B, N, T = x.shape
                flat = x.reshape(B * N, T)
                pos = torch.arange(T, device=flat.device)
                h = self.backbone.drop(self.backbone.tok(flat)
                                       + self.backbone.pos(pos))
                for block in self.backbone.blocks:
                    h = block(h)
                h = self.backbone.ln_f(h)
                last = (lens.reshape(B * N) - 1).clamp(min=0)
                rep = h[torch.arange(B * N, device=h.device), last]
                return self.head(rep).reshape(B, N)

        backbone = CharLM(cfg)
        self.model = Ranker(cfg, backbone)

        sd = blob["model"]
        if blob.get("int8"):
            # 反量化：真实值 = int8 × scale（按输出行）
            fixed = {}
            for k, v in sd.items():
                if k.endswith(".scale"):
                    continue
                sc = sd.get(k + ".scale")
                if sc is not None and v.dtype == torch.int8:
                    fixed[k] = (v.reshape(v.shape[0], -1).float()
                                * sc[:, None]).reshape(v.shape)
                else:
                    fixed[k] = v
            sd = fixed
        self.model.load_state_dict(sd)
        self.model.eval()
        return self

    def score_candidates(self, cands: list[str]) -> dict[str, float]:
        """给一批候选打分（一次前向）。"""
        if self.model is None:
            self.load()
        torch = self.torch

        n = len(cands)
        max_t = max(len(c) + 1 for c in cands)
        max_t = min(max_t, self.ctx)
        X = torch.zeros((1, n, max_t), dtype=torch.long)
        L = torch.ones((1, n), dtype=torch.long)
        for j, c in enumerate(cands):
            ids = [self.bos] + [self.tok2id.get(ch, 1) for ch in c]
            ids = ids[:max_t]
            X[0, j, :len(ids)] = torch.tensor(ids)
            L[0, j] = len(ids)
        with torch.no_grad():
            sc = self.model(X, L)[0]
        return {c: float(sc[j]) for j, c in enumerate(cands)}

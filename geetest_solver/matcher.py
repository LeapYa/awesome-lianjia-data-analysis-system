"""模板检索求解器（word / icon 型）。

原理：
    ques 小图来自固定模板库，不是每张新生成：
        icon : 3483 张文件 -> 39 个唯一模板
        word : 6039 张文件 -> 905 个唯一模板
    所以「点选」= 闭集检索，不需要训练检测模型。

流程：
    1. 候选池   icon 用饱和度掩码+尺寸过滤，word 用 MSER
    2. 分类     ques vs 全库模板，向量化 IoU
    3. 定位     框内多尺度 × 多角度 TM_CCOEFF_NORMED
    4. 分配     匈牙利算法，一框一目标

实测：icon 三点全落不同位置 12/12，word 11/12，单张 0.6~1.2s
"""

from __future__ import annotations

import glob
import hashlib
import json
import math
import os
import pickle
import sys
import time
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

# ---------------------------------------------------------------- 参数
CANON_CLS = 32          # 分类用的归一化画布（小一点，分类快）
CANON_POS = 64          # 定位用的归一化画布
SCALES = (0.90, 1.05, 1.20, 1.35)     # 全图定位的尺度搜索
BOX_SCALES = (0.70, 0.85, 1.00, 1.15, 1.30)  # 框内定位的尺度搜索（范围更宽）
ANGLES = tuple(range(0, 360, 15))     # 角度搜索
CLASS_ANGLES = tuple(range(0, 360, 30))  # 分类时的角度采样
ANOMALY_K = 15
ANOMALY_THR = 42
TOPK = 3                # 取前 k 个候选模板去定位
MIN_SEP = 20            # 两个点击点最小间距（像素）


# ---------------------------------------------------------------- 基础工具
def canonical(mask: np.ndarray, size: int) -> np.ndarray:
    """裁外接框 -> 等比缩放到 size 画布中心。"""
    ys, xs = np.nonzero(mask > 0)
    if len(ys) == 0:
        return np.zeros((size, size), np.float32)
    mask = mask[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]
    h, w = mask.shape
    s = size / max(h, w)
    rw, rh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    r = cv2.resize(mask, (rw, rh), interpolation=cv2.INTER_AREA)
    out = np.zeros((size, size), np.float32)
    y0, x0 = (size - rh) // 2, (size - rw) // 2
    out[y0:y0 + rh, x0:x0 + rw] = r
    return out


def rotate_mask(mask: np.ndarray, angle: float) -> np.ndarray | None:
    """旋转并重新裁紧，越界返回 None。"""
    h, w = mask.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(h * sin + w * cos) + 2, int(h * cos + w * sin) + 2
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    r = cv2.warpAffine(mask, M, (nw, nh), flags=cv2.INTER_LINEAR, borderValue=0)
    ys, xs = np.nonzero(r > 40)
    if len(ys) == 0:
        return None
    return r[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]


def alpha_to_mask(path_or_bytes) -> np.ndarray:
    """读 PNG 的 alpha 通道当字形掩码。"""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        img = cv2.imdecode(np.frombuffer(path_or_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
    else:
        img = cv2.imread(str(path_or_bytes), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"图片读取失败: {path_or_bytes!r}")
    if img.ndim == 3 and img.shape[2] == 4:
        a = img[:, :, 3]
    elif img.ndim == 3:
        a = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        a = img
    m = (a > 90).astype(np.uint8) * 255
    ys, xs = np.nonzero(m > 0)
    if len(ys) == 0:
        raise ValueError("alpha 全空")
    return m[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]


def anomaly_mask(bg: np.ndarray, k: int = ANOMALY_K, thr: int = ANOMALY_THR) -> np.ndarray:
    """字形 = 与局部中值颜色差异大的像素（不依赖具体颜色，抗照片纹理）。"""
    d = cv2.absdiff(bg, cv2.medianBlur(bg, k)).max(axis=2).astype(np.float32)
    m = (d > thr).astype(np.uint8) * 255
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))


def sat_mask(bg: np.ndarray, st: int = 150, vt: int = 110) -> np.ndarray:
    """字形 = 高饱和且足够亮的像素。

    比中值差分更干净：链家/贝壳的图标和汉字都是高饱和描边色，
    而照片背景（建筑、树木、夜景）的饱和度分布明显更低。
    实测在 icon 上能精确切出 3 个连通域，且不引入建筑窗户的干扰。
    """
    hsv = cv2.cvtColor(bg, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    m = (((s > st) & (v > vt)).astype(np.uint8)) * 255
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))


def glyph_mask(bg: np.ndarray) -> np.ndarray:
    """两种掩码取并集：饱和度抓彩色字，中值差分抓低饱和的白字/黑字。"""
    a = sat_mask(bg)
    b = anomaly_mask(bg)
    return cv2.bitwise_or(a, b)


# ---------------------------------------------------------------- 模板库
class TemplateLibrary:
    """模板库：按 MD5 去重，缓存多角度归一化版本用于快速分类。"""

    def __init__(self):
        self.keys: list[str] = []
        self.masks: list[np.ndarray] = []        # 原始裁剪后掩码（定位用）
        self.rot_stack: np.ndarray | None = None  # (C, A, S, S) 分类用
        self._locate_cache: dict[str, tuple[float, tuple[int, int]]] = {}

    # ---- 建库
    @classmethod
    def build(cls, dataset_dirs: Sequence[str], out_path: str,
              types: Sequence[str] = ("icon", "word")) -> "TemplateLibrary":
        lib = cls()
        seen: set[str] = set()
        for root in dataset_dirs:
            for ctype in types:
                d = Path(root) / ctype
                if not d.exists():
                    continue
                files = sorted(d.glob("*_ques_*.png"))
                if ctype == "phrase":
                    files = sorted(d.glob("*.jpg"))
                for p in files:
                    h = hashlib.md5(p.read_bytes()).hexdigest()
                    if h in seen:
                        continue
                    seen.add(h)
                    try:
                        m = alpha_to_mask(p)
                    except ValueError:
                        continue
                    if m.shape[0] < 12 or m.shape[1] < 12:
                        continue
                    lib.keys.append(f"{ctype}_{h[:8]}")
                    lib.masks.append(m)
        lib._precompute()
        lib.save(out_path)
        print(f"建库完成：{len(lib.keys)} 个唯一模板 -> {out_path}")
        return lib

    def _precompute(self) -> None:
        t0 = time.time()
        stacks = []
        for m in self.masks:
            c = canonical(m, CANON_CLS)
            rots = []
            for ang in CLASS_ANGLES:
                M = cv2.getRotationMatrix2D((CANON_CLS / 2, CANON_CLS / 2), ang, 1.0)
                r = cv2.warpAffine(c, M, (CANON_CLS, CANON_CLS),
                                   flags=cv2.INTER_LINEAR, borderValue=0)
                rots.append(canonical(r, CANON_CLS))
            stacks.append(np.stack(rots))
        self.rot_stack = np.stack(stacks) if stacks else None
        print(f"  预计算分类缓存 {len(self.masks)} 类 / {time.time() - t0:.1f}s")

    # ---- 存取
    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump({"keys": self.keys, "masks": self.masks}, fh)

    @classmethod
    def load(cls, path: str) -> "TemplateLibrary":
        with open(path, "rb") as fh:
            blob = pickle.load(fh)
        lib = cls()
        lib.keys = blob["keys"]
        lib.masks = blob["masks"]
        lib._precompute()
        return lib

    # ---- 分类
    def classify(self, ques_mask: np.ndarray, topk: int = TOPK):
        """ques 掩码 -> [(iou, key)] top-k，以及 top1 分数。"""
        if self.rot_stack is None or not self.keys:
            return [], 0.0
        q = canonical(ques_mask, CANON_CLS) > 100
        R = self.rot_stack > 100
        inter = (R & q).sum(axis=(2, 3)).astype(np.float32)
        union = (R | q).sum(axis=(2, 3)).astype(np.float32) + 1e-6
        iou = (inter / union).max(axis=1)
        order = np.argsort(-iou)[:topk]
        return [(float(iou[i]), self.keys[i]) for i in order], float(iou.max())

    # ---- 定位
    def locate(self, bg_mask: np.ndarray, key: str) -> tuple[float, tuple[int, int]]:
        """模板在背景字形掩码上的最佳位置（多尺度 × 多角度，零均值相关）。"""
        if key in self._locate_cache:
            return self._locate_cache[key]
        tpl = self.masks[self.keys.index(key)]
        best_score, best_pos = -1.0, (0, 0)
        for sc in SCALES:
            t0 = cv2.resize(tpl, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
            for ang in ANGLES:
                t = rotate_mask(t0, ang)
                if t is None or t.shape[0] >= bg_mask.shape[0] or t.shape[1] >= bg_mask.shape[1]:
                    continue
                tf = t.astype(np.float32)
                if tf.std() < 1e-3:
                    continue
                r = cv2.matchTemplate(bg_mask, tf, cv2.TM_CCOEFF_NORMED)
                r = np.nan_to_num(r, nan=-1.0, posinf=-1.0, neginf=-1.0)
                _, mx, _, ml = cv2.minMaxLoc(r)
                if mx > best_score:
                    best_score = float(mx)
                    best_pos = (ml[0] + t.shape[1] // 2, ml[1] + t.shape[0] // 2)
        self._locate_cache[key] = (best_score, best_pos)
        return best_score, best_pos

    def clear_cache(self) -> None:
        self._locate_cache.clear()

    # ---- 连通域（候选池）
    @staticmethod
    def blobs(bg_mask: np.ndarray, min_side: int = 30, max_side: int = 80,
              min_area: int = 250, max_n: int = 8) -> list[dict]:
        """尺寸过滤的连通域。

        icon 的图标尺寸高度稳定（实测 w/h 中位数 45~50px，p5~p95 落在 30~80px），
        按边长过滤能剔除「整条建筑带」「鸟嘴色块」这类背景干扰。
        word 的汉字尺寸跨度极大（w 的 p5~p95 是 24~300），不能靠尺寸，改用 mser_blobs。
        """
        n, labels, stats, cents = cv2.connectedComponentsWithStats(bg_mask, 8)
        out = []
        for i in range(1, n):
            x, y, w, h, a = stats[i]
            if a < min_area:
                continue
            if not (min_side <= w <= max_side and min_side <= h <= max_side):
                continue
            cx, cy = cents[i]
            out.append({"bbox": (int(x), int(y), int(w), int(h)),
                        "center": (int(round(cx)), int(round(cy))),
                        "area": int(a)})
        out.sort(key=lambda b: -b["area"])
        return out[:max_n]

    @staticmethod
    def mser_blobs(bg_mask: np.ndarray, min_area: int = 300, max_area: int = 9000,
                   max_n: int = 8) -> list[dict]:
        """MSER 候选：对尺寸不做假设，适合 word 那种大小/倾斜都随机的汉字。"""
        mser = cv2.MSER_create()
        mser.setMinArea(min_area)
        mser.setMaxArea(max_area)
        mser.setDelta(5)
        regions, _ = mser.detectRegions(bg_mask)
        boxes = []
        for r in regions:
            x, y, w, h = cv2.boundingRect(r.reshape(-1, 1, 2))
            if w < 18 or h < 18:
                continue
            ar = w / h
            if ar < 0.35 or ar > 3.0:
                continue
            boxes.append((x, y, w, h))

        def iou(a, b):
            ax, ay, aw, ah = a
            bx, by, bw, bh = b
            x1, y1 = max(ax, bx), max(ay, by)
            x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
            if x2 <= x1 or y2 <= y1:
                return 0.0
            inter = (x2 - x1) * (y2 - y1)
            return inter / (aw * ah + bw * bh - inter)

        kept = []
        for b in sorted(boxes, key=lambda b: -b[2] * b[3]):
            if all(iou(b, k) < 0.35 for k in kept):
                kept.append(b)
        out = [{"bbox": b, "center": (b[0] + b[2] // 2, b[1] + b[3] // 2),
                "area": b[2] * b[3]} for b in kept]
        return out[:max_n]


# ---------------------------------------------------------------- 求解
def solve_click_points(bg: np.ndarray, ques_masks: Sequence[np.ndarray],
                       lib: TemplateLibrary, topk: int = TOPK,
                       ctype: str = "auto") -> list[dict]:
    """背景图 + 目标掩码 -> 按顺序的点击点。

    四级流水线：
        1. 候选池   : 按题型选策略
                      icon -> 饱和度掩码 + 尺寸过滤（图标边长稳定在 30~80px）
                      word -> MSER（汉字大小/倾斜随机，不能假设尺寸）
        2. 分类     : ques 掩码 vs 全库模板（向量化 IoU），拿到 top-k 候选类
        3. 局部定位 : 在每个候选框内（外扩 8px）做多尺度 × 多角度零均值相关，
                      限定区域能避免全图搜索时背景主导分数
        4. 全局分配 : 目标数 × 候选框数，匈牙利算法求最优，一个框只被一个目标占用

    ctype: "icon" / "word" / "auto"（auto 时两种候选池都试，取候选数更接近目标数的）
    """
    if not ques_masks:
        return []
    mask = glyph_mask(bg)
    bg_mask = mask.astype(np.float32)
    lib.clear_cache()

    boxes = _pick_boxes(mask, len(ques_masks), ctype)

    n_q, n_b = len(ques_masks), len(boxes)
    score = np.full((n_q, max(n_b, 1)), -1.0, np.float32)
    detail: list[list[dict]] = [[{} for _ in range(max(n_b, 1))] for _ in range(n_q)]

    for i, qm in enumerate(ques_masks):
        top, cls_score = lib.classify(qm, topk)
        if not top:
            continue
        for j, box in enumerate(boxes):
            best = None
            for cls_iou, key in top:
                pos_score, pos = _locate_in_box(bg, mask, box, lib, key)
                combined = pos_score * cls_iou
                if best is None or combined > best[0]:
                    best = (combined, pos, key, cls_iou, pos_score)
            score[i, j] = best[0]
            detail[i][j] = {"center": best[1], "class": best[2],
                            "cls_score": best[3], "pos_score": best[4]}

    out: list[dict] = [{} for _ in range(n_q)]
    if n_b:
        try:
            from scipy.optimize import linear_sum_assignment
            rows, cols = linear_sum_assignment(-score)
        except Exception:
            rows = np.arange(n_q)
            cols = np.array([int(np.argmax(score[i])) for i in rows])
        for r, c in zip(rows, cols):
            out[int(r)] = dict(detail[int(r)][int(c)])
            out[int(r)]["score"] = float(score[int(r), int(c)])
    else:
        for i, qm in enumerate(ques_masks):
            top, _ = lib.classify(qm, topk)
            best = None
            for cls_iou, key in (top or []):
                pos_score, pos = lib.locate(bg_mask, key)
                combined = pos_score * cls_iou
                if best is None or combined > best[0]:
                    best = (combined, pos, key, cls_iou, pos_score)
            out[i] = ({"center": best[1], "class": best[2], "cls_score": best[3],
                       "pos_score": best[4], "score": best[0]} if best else
                      {"center": None, "class": None, "cls_score": 0.0,
                       "pos_score": 0.0, "score": 0.0})

    taken = {r.get("center") for r in out if r.get("center")}
    spare = [b for b in boxes if b["center"] not in taken]
    for r in out:
        if r.get("center") is None and spare:
            b = spare.pop(0)
            r["center"] = b["center"]
            r["fallback"] = True
            r["score"] = max(r.get("score") or 0, 0.2)

    return out
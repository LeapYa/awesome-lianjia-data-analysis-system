"""阈值法分割（YOLO 检测器不可用时的兜底）。

实测字数正确率约 38.7%，明显不如 YOLO（98.7%），
仅在检测器缺失时使用。
"""

from __future__ import annotations

import cv2
import numpy as np


def _mask_white(bg: np.ndarray) -> np.ndarray:
    """白芯字：亮度很高、饱和度很低。"""
    hsv = cv2.cvtColor(bg, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    m = ((v > 195) & (s < 90)).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)


def _mask_color(bg: np.ndarray) -> np.ndarray:
    """彩色字：高饱和 + 与局部背景的差异。"""
    hsv = cv2.cvtColor(bg, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    base = ((s > 150) & (v > 110)).astype(np.uint8) * 255
    g = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    d = cv2.absdiff(g, cv2.medianBlur(g, 15))
    d = (d > 30).astype(np.uint8) * 255
    m = cv2.bitwise_and(base, d)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)


def _boxes(mask: np.ndarray, min_area: int = 400) -> list[tuple]:
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w * h < min_area:
            continue
        out.append((x, y, w, h))
    return out


def _iou(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    return inter / (aw * ah + bw * bh - inter)


def threshold_segment(bg: np.ndarray, want: int = 3) -> list[dict]:
    """切出背景中的字（阈值法）。

    返回 [{"bbox": (x,y,w,h), "center": (cx,cy), "crop": ndarray}, ...]
    """
    cands = _boxes(_mask_white(bg)) + _boxes(_mask_color(bg))

    # 大块拆分（彩色掩码常把相邻两字连成一块）
    split = []
    for x, y, w, h in cands:
        if w <= 85 and h <= 85:
            split.append((x, y, w, h))
            continue
        if w > 85 and w > h * 1.3:
            n = max(2, round(w / 55))
            step = w // n
            for i in range(n):
                split.append((x + i * step, y, min(step, w - i * step), h))
        elif h > 85 and h > w * 1.3:
            n = max(2, round(h / 55))
            step = h // n
            for i in range(n):
                split.append((x, y + i * step, w, min(step, h - i * step)))
        else:
            split.append((x, y, w, h))

    scored = []
    for x, y, w, h in split:
        if not (28 <= w <= 90 and 28 <= h <= 90):
            continue
        ar = w / max(h, 1)
        if not (0.5 <= ar <= 2.0):
            continue
        pad = 2
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(bg.shape[1], x + w + pad), min(bg.shape[0], y + h + pad)
        crop = bg[y0:y1, x0:x1]
        if crop.size == 0:
            continue
        g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        ink = float((cv2.absdiff(g, cv2.medianBlur(g, 5)) > 25).mean())
        if not (0.03 <= ink <= 0.80):
            continue
        # 尺寸越接近实测中位数（54×53）越可信
        sc = 1.0 - min(abs(w - 54) / 54, 1.0) * 0.4 \
                 - min(abs(h - 53) / 53, 1.0) * 0.4
        scored.append({"bbox": (x, y, w, h),
                       "center": (x + w // 2, y + h // 2),
                       "crop": crop, "score": sc})

    kept: list[dict] = []
    for s in sorted(scored, key=lambda d: -d["score"]):
        if all(_iou(s["bbox"], k["bbox"]) < 0.35 for k in kept):
            kept.append(s)
    return kept[:max(want, 6)]

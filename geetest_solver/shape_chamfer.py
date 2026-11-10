"""新版 word/icon 匹配：掩膜 + 边缘 + 对称 chamfer 距离（旋转搜索）。

为什么需要（2026-11 实测）
-------------------------
站点把 word/icon 的题面图换成了 24×24 的黑白小图，面板上的字形/图标是
「粗彩色描边 + 3D 效果」，两者颜色、线宽都不一样。旧方案（归一化二值 IoU）
在真实题面上相似度矩阵几乎是平的（0.35~0.47 → 随机点）。

新方案：
    1. 面板：先用「白描边 + 高饱和填充」掩膜把字形/图标抠出来，背景压 0
    2. 两边都用 Canny 取边缘，归一化到 64×64
    3. 对称 chamfer 距离（双向平均最近边缘距离），配 45° 步长旋转搜索
    4. 匈牙利分配得到「第 i 个 ques → 第 j 个框」

实测（2026-11-09 真实 word 题「苞谷酒」）：
    旧 IoU 方案 → 分配错误（0/3）
    本方案     → 分配正确（3/3），正确解在 3×3 矩阵每一行都是最小值
"""
from __future__ import annotations

import itertools

import cv2
import numpy as np

SIZE = 64
ROT_STEP = 8


def _mask_panel(crop: np.ndarray) -> np.ndarray:
    """抠出面板上的字形/图标（白描边或高饱和彩色描边）。"""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    m = (((v > 195) & (s < 60)) | ((s > 150) & (v > 140))).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    if n > 1:
        i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        m = (lab == i).astype(np.uint8)
    # 填洞
    ff = m.copy()
    mm = np.zeros((m.shape[0] + 2, m.shape[1] + 2), np.uint8)
    cv2.floodFill(ff, mm, (0, 0), 1)
    return (m | (1 - ff)).astype(np.uint8)


def _fit(mask: np.ndarray) -> np.ndarray:
    """裁到内容框并等比放进 SIZE×SIZE 居中。"""
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return np.zeros((SIZE, SIZE), np.uint8)
    sub = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = sub.shape
    s = min(SIZE / h, SIZE / w)
    r = cv2.resize(sub, (max(2, int(w * s)), max(2, int(h * s))),
                   interpolation=cv2.INTER_AREA if sub.dtype == np.float32
                   else cv2.INTER_NEAREST)
    out = np.zeros((SIZE, SIZE), np.uint8)
    y0 = (SIZE - r.shape[0]) // 2
    x0 = (SIZE - r.shape[1]) // 2
    out[y0:y0 + r.shape[0], x0:x0 + r.shape[1]] = r
    return out


def panel_edges(crop: np.ndarray) -> np.ndarray:
    """面板字块 → 64×64 边缘图（背景已压掉）。"""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
    m = _mask_panel(crop)
    return cv2.Canny(_fit((gray * m).astype(np.uint8)), 50, 150)


def ques_edges(img: np.ndarray) -> np.ndarray:
    """题面小图（黑白字/图标）→ 64×64 边缘图。"""
    if img.ndim == 3 and img.shape[2] == 4:
        g = img[:, :, 3]
        m = (g > 40).astype(np.uint8) * 255
    else:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        m = (g < 150).astype(np.uint8) * 255
    return cv2.Canny(_fit(m), 50, 150)


def chamfer(a: np.ndarray, b: np.ndarray, rot_step: int = ROT_STEP) -> float:
    """对称 chamfer 距离（越小越像），带旋转搜索。"""
    if a is None or b is None or (a > 0).sum() < 10 or (b > 0).sum() < 10:
        return 99.0
    db = cv2.distanceTransform(255 - b, cv2.DIST_L2, 3)
    best = 99.0
    for ang in range(0, 360, rot_step):
        M = cv2.getRotationMatrix2D((SIZE / 2, SIZE / 2), ang, 1.0)
        ar = cv2.warpAffine(a, M, (SIZE, SIZE), flags=cv2.INTER_NEAREST)
        ya, xa = np.where(ar > 0)
        if len(xa) == 0:
            continue
        da = cv2.distanceTransform(255 - ar, cv2.DIST_L2, 3)
        yb, xb = np.where(b > 0)
        if len(xb) == 0:
            continue
        best = min(best, 0.5 * (float(db[ya, xa].mean()) + float(da[yb, xb].mean())))
    return float(best)


def assign(ques_edges_list: list[np.ndarray],
           panel_edges_list: list[np.ndarray]) -> tuple[list[int], np.ndarray]:
    """返回 (每个 ques 对应的框索引, 代价矩阵)。"""
    nq, nb = len(ques_edges_list), len(panel_edges_list)
    cost = np.full((nq, nb), 99.0, np.float32)
    for i in range(nq):
        for j in range(nb):
            cost[i, j] = chamfer(ques_edges_list[i], panel_edges_list[j])
    if nb == 0:
        return [-1] * nq, cost
    if nq <= nb:
        try:
            from scipy.optimize import linear_sum_assignment
            rows, cols = linear_sum_assignment(cost)
            out = [-1] * nq
            for r, c in zip(rows, cols):
                if r < nq:
                    out[int(r)] = int(c)
            return out, cost
        except Exception:
            pass
        return [int(np.argmin(cost[i])) for i in range(nq)], cost
    # ques 比框多：贪心
    used, out = set(), []
    for i in range(nq):
        order = np.argsort(cost[i])
        pick = next((int(j) for j in order if j not in used), int(order[0]))
        used.add(pick)
        out.append(pick)
    return out, cost

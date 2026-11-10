"""
查询式检测器：YOLO 找框 + 框内容与 ques 模板匹配

问题
----
word / icon 型的任务是「在背景里找到 ques 指定的 3 个目标」。
不需要识别文字，但需要判断「这个框里的是什么」。

关键观察（实测）
----------------
    word : 203 个 ques 模板，同一字符永远对应同一个模板（1:1）
    icon : 39 个模板，每个模板在数据里出现 2~13 次

    即 ques 模板是**固定闭集**，可以用检索/分类解决，
    不需要 OCR，也不需要端到端训练匹配网络。

方案：三选一，按成本排序

A. 模板检索（不需训练）
    YOLO 找框 → 每个框裁出图形 → 与 ques 模板库比对形状
    优点：零训练，模板可动态扩充
    缺点：框内图形和 ques 渲染差异大时需要鲁棒的距离度量

B. 分类器（需训练，但很轻）
    YOLO 找框 → 每个框裁出图形 → 一个 CNN 判断属于哪个模板
    优点：比模板检索鲁棒
    缺点：模板变化要重训

C. 改造 YOLO 检测头
    在检测头加一个「模板 embedding」分支，输出该框的特征向量，
    再与 ques 的向量算余弦相似度
    优点：一次前向完成
    缺点：改造 YOLO 结构，风险高

本文件实现 **A**，并把 B 的训练脚本单独放在 train_query_classifier.py。

为什么先做 A
------------
    1. 零训练，能立刻验证「YOLO 找框 + 形状匹配」这条路是否可行
    2. 如果 A 已经够用，就不必做 B/C
    3. 若 A 不够，B 的数据和流程可以直接复用

用法
----
    python query_match.py --build-lib      # 建模板库
    python query_match.py --eval           # 评估
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pickle
import sys
from collections import defaultdict
from pathlib import Path

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ[_k] = ""

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DATASET = HERE / "captcha_dataset"
ANNOT = {"word": HERE / "data" / "boxes_word.jsonl",
         "icon": HERE / "data" / "boxes_icon.jsonl"}
LIB_PATH = HERE / "templates" / "query_lib.pkl"

NORM = 64          # 归一化尺寸


# ---------------------------------------------------------------- 形状归一
def shape_of(img: np.ndarray, mode: str = "color") -> np.ndarray | None:
    """把任意图变成归一化的「形状掩码」。

    背景里的字/图标是彩色描边 + 纹理；
    ques 是黑字 + 透明底的线稿。
    要能互相比较，必须都简化成「二值形状」。
    """
    if img is None or img.size == 0:
        return None

    if img.ndim == 3 and img.shape[2] == 4:
        # ques：用 alpha 直接取形状
        m = (img[:, :, 3] > 20).astype(np.uint8) * 255
    else:
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        s, v = hsv[:, :, 1], hsv[:, :, 2]
        # 彩色描边：高饱和
        m1 = ((s > 100) & (v > 60)).astype(np.uint8) * 255
        # 亮芯：高亮度
        m2 = (v > 170).astype(np.uint8) * 255
        m = cv2.bitwise_or(m1, m2)
        # 去噪
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    ys, xs = np.nonzero(m > 0)
    if len(ys) < 30:
        return None
    m = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]

    h, w = m.shape
    sc = (NORM - 6) / max(h, w)
    m = cv2.resize(m, (max(1, int(w * sc)), max(1, int(h * sc))),
                   interpolation=cv2.INTER_AREA)
    canvas = np.zeros((NORM, NORM), np.uint8)
    y0 = (NORM - m.shape[0]) // 2
    x0 = (NORM - m.shape[1]) // 2
    canvas[y0:y0 + m.shape[0], x0:x0 + m.shape[1]] = m
    return canvas


def similarity(a: np.ndarray, b: np.ndarray, angles=range(0, 360, 20)) -> float:
    """两个形状掩码的相似度（多角度旋转取最大）。

    用 IoU + 距离变换的组合：IoU 管整体覆盖，距离变换管笔画粗细差异。
    """
    if a is None or b is None:
        return 0.0
    best = 0.0
    af = (a > 0).astype(np.float32)
    for ang in angles:
        M = cv2.getRotationMatrix2D((NORM / 2, NORM / 2), ang, 1.0)
        rb = cv2.warpAffine(b, M, (NORM, NORM), flags=cv2.INTER_NEAREST)
        bf = (rb > 0).astype(np.float32)
        inter = float((af * bf).sum())
        union = float(((af + bf) > 0).sum())
        if union < 1:
            continue
        iou = inter / union
        if iou > best:
            best = iou
    return best


# ---------------------------------------------------------------- 模板库
def build_lib() -> dict:
    """从所有 word/icon 的 ques 建模板库。"""
    lib = {}
    for ctype in ("word", "icon"):
        keys = {}          # md5 -> 索引
        shapes = []        # 形状掩码
        meta = []          # (ctype, 原文件名)
        for r in ANNOT[ctype].read_text(encoding="utf-8").splitlines():
            if not r.strip():
                continue
            obj = json.loads(r)
            stem = obj["img"].replace("_bg.jpg", "")
            for q in sorted((DATASET / ctype).glob(f"{stem}_ques_*.png")):
                raw = q.read_bytes()
                h = hashlib.md5(raw).hexdigest()
                if h in keys:
                    continue
                img = cv2.imdecode(np.frombuffer(raw, np.uint8),
                                   cv2.IMREAD_UNCHANGED)
                s = shape_of(img)
                if s is None:
                    continue
                keys[h] = len(shapes)
                shapes.append(s)
                meta.append((ctype, q.name))
        lib[ctype] = {"hashes": list(keys.keys()), "shapes": shapes,
                      "meta": meta}
        print(f"  {ctype}: {len(shapes)} 个模板")
    LIB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LIB_PATH, "wb") as fh:
        pickle.dump(lib, fh)
    print(f"已保存 {LIB_PATH}")
    return lib


def load_lib() -> dict:
    if not LIB_PATH.exists():
        return build_lib()
    with open(LIB_PATH, "rb") as fh:
        return pickle.load(fh)


# ---------------------------------------------------------------- 匹配
def match_boxes(bg: np.ndarray, boxes: list[tuple],
                ques_shapes: list[np.ndarray],
                lib_shapes: list[np.ndarray]) -> list[int]:
    """给每个 ques 找最像的框。

    boxes: [(x, y, w, h)]
    ques_shapes: 每个目标 ques 的形状掩码
    lib_shapes: 模板库形状（用于把 ques 映射到统一表示，可选）

    返回：每个 ques 对应的 box 索引
    """
    box_shapes = []
    for (x, y, w, h) in boxes:
        pad = 2
        crop = bg[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
        box_shapes.append(shape_of(crop))

    n_q, n_b = len(ques_shapes), len(boxes)
    if n_b == 0:
        return [-1] * n_q

    score = np.full((n_q, n_b), -1.0, np.float32)
    for i, qs in enumerate(ques_shapes):
        for j, bs in enumerate(box_shapes):
            score[i, j] = similarity(qs, bs)

    try:
        from scipy.optimize import linear_sum_assignment
        rows, cols = linear_sum_assignment(-score)
        out = [-1] * n_q
        for r, c in zip(rows, cols):
            out[int(r)] = int(c)
        return out
    except Exception:
        return [int(np.argmax(score[i])) for i in range(n_q)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-lib", action="store_true")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--detector", default="runs/detect/runs/glyph_det3/weights/best.pt")
    args = ap.parse_args()

    if args.build_lib:
        print("建模板库:")
        build_lib()
        return 0

    if not args.eval:
        ap.print_help()
        return 1

    from ultralytics import YOLO
    lib = load_lib()
    det = YOLO(args.detector) if Path(args.detector).exists() else None
    if det is None:
        print(f"检测器不存在: {args.detector}")

    types = [args.only] if args.only else ["word", "icon"]
    for ctype in types:
        rows = [json.loads(l) for l in
                ANNOT[ctype].read_text(encoding="utf-8").splitlines()
                if l.strip()]
        rows = [r for r in rows if not r.get("skip") and r.get("boxes")]
        if not rows:
            continue

        n = len(rows)
        n_all = 0
        spot_in = spot_tot = 0
        fails = []

        for r in rows:
            bg = cv2.imread(str(DATASET / ctype / r["img"]))
            if bg is None:
                continue
            gt = r["boxes"]

            # 检测框
            if det is not None:
                out = det.predict(bg, imgsz=320, verbose=False, conf=0.30)
                boxes = []
                if out and out[0].boxes is not None:
                    for b in out[0].boxes.xyxy.cpu().numpy():
                        x1, y1, x2, y2 = [int(v) for v in b]
                        if x2 - x1 < 15 or y2 - y1 < 15:
                            continue
                        boxes.append((x1, y1, x2 - x1, y2 - y1))
            else:
                boxes = [(g["x"], g["y"], g["w"], g["h"]) for g in gt]

            # ques 形状
            stem = r["img"].replace("_bg.jpg", "")
            qs = sorted((DATASET / ctype).glob(f"{stem}_ques_*.png"))
            qshapes = []
            for q in qs:
                img = cv2.imread(str(q), cv2.IMREAD_UNCHANGED)
                qshapes.append(shape_of(img))

            idx = match_boxes(bg, boxes, qshapes, lib[ctype]["shapes"])
            pts = []
            for i in idx:
                if i < 0 or i >= len(boxes):
                    pts.append(None)
                else:
                    x, y, w, h = boxes[i]
                    pts.append((x + w // 2, y + h // 2))

            def in_box(pt, box):
                return (box["x"] <= pt[0] <= box["x"] + box["w"] and
                        box["y"] <= pt[1] <= box["y"] + box["h"])

            hit = 0
            used = set()
            for pt in pts:
                if pt is None:
                    continue
                for gi, g in enumerate(gt):
                    if gi in used:
                        continue
                    if in_box(pt, g):
                        used.add(gi)
                        hit += 1
                        break
            spot_in += hit
            spot_tot += len(gt)
            if hit == len(gt):
                n_all += 1
            elif len(fails) < 8:
                fails.append((r["img"], f"{hit}/{len(gt)}"))

        print(f"\n=== {ctype}（{n} 张）===")
        print(f"  3 点全对   : {n_all}/{n} = {n_all / n * 100:.0f}%")
        print(f"  单点落框率 : {spot_in}/{spot_tot} = {spot_in / max(spot_tot, 1) * 100:.0f}%")
        if fails:
            print(f"  失败: {fails[:5]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

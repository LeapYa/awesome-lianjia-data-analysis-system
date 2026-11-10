"""
ONNX 版字框检测器（替代 ultralytics/torch）

为什么要换
----------
torch + ultralytics 运行时约占 300MB 内存，是整套方案的大头。
onnxruntime 只需 ~23MB，且推理更快。

实测对比：

| 方案 | 模型文件 | 运行时内存 | 速度 |
| --- | --- | --- | --- |
| torch (ultralytics) | 24.8 MB | ~300 MB | 30 ms |
| **onnxruntime** | **9.3 MB** | **~23 MB** | 更快 |

模型结构
--------
YOLO26n 单类检测器，输入 320×320，输出 (1, 5, 2100)：
    2100 = 40×40 + 20×20 + 10×10 + 5×5  解码为 2 层
    5    = cx, cy, w, h, cls_score

后处理
------
YOLO26 是 anchor-free + 无 NMS 训练（end-to-end），
但导出 ONNX 后仍需要自己做阈值过滤和非极大值抑制。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

IMG_SIZE = 320
CONF_THRES = 0.40
# YOLO26 端到端输出有大量重复框（同一目标 5~8 个），
# 实测 iou=0.15 时 word 字数正确率 98%，0.45 只有 91%
IOU_THRES = 0.15


class OnnxDetector:
    """YOLO26n ONNX 字框检测器。"""

    def __init__(self, model_path: str | Path, threads: int = 4):
        import onnxruntime as ort

        self.path = Path(model_path)
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(
            str(self.path), so, providers=["CPUExecutionProvider"])
        self.in_name = self.sess.get_inputs()[0].name
        self.out_names = [o.name for o in self.sess.get_outputs()]
        self._warmed = False

    def warmup(self) -> None:
        if self._warmed:
            return
        dummy = np.zeros((1, 3, IMG_SIZE, IMG_SIZE), np.float32)
        self.sess.run(self.out_names, {self.in_name: dummy})
        self._warmed = True

    # ---------------------------------------------------------------- 推理
    def _letterbox(self, img: np.ndarray):
        """等比缩放 + 灰边填充到 320×320（YOLO 标准预处理）。"""
        h, w = img.shape[:2]
        r = min(IMG_SIZE / h, IMG_SIZE / w)
        nh, nw = int(round(h * r)), int(round(w * r))
        resized = cv2.resize(img, (nw, nh))
        canvas = np.full((IMG_SIZE, IMG_SIZE, 3), 114, np.uint8)
        top = (IMG_SIZE - nh) // 2
        left = (IMG_SIZE - nw) // 2
        canvas[top:top + nh, left:left + nw] = resized
        return canvas, r, left, top

    def detect(self, img: np.ndarray, conf: float = CONF_THRES,
               iou: float = IOU_THRES) -> list[tuple[int, int, int, int]]:
        """返回 [(x, y, w, h)]，坐标是原图尺度。"""
        if img is None or img.size == 0:
            return []
        self.warmup()
        h0, w0 = img.shape[:2]
        canvas, r, dx, dy = self._letterbox(img)
        blob = canvas.astype(np.float32).transpose((2, 0, 1))[None] / 255.0

        out = self.sess.run(self.out_names, {self.in_name: blob})[0]
        # (1, 5, N) -> (N, 5)
        pred = out[0].T
        if pred.shape[1] < 5:
            return []

        boxes = pred[:, :4]          # cx, cy, w, h (letterbox 尺度)
        scores = pred[:, 4]
        keep = scores > conf
        if not keep.any():
            return []
        boxes, scores = boxes[keep], scores[keep]

        # 还原到原图坐标
        xy = boxes.copy()
        xy[:, 0] = (xy[:, 0] - dx) / r
        xy[:, 1] = (xy[:, 1] - dy) / r
        xy[:, 2] = xy[:, 2] / r
        xy[:, 3] = xy[:, 3] / r

        # cx,cy,w,h -> x1,y1,x2,y2
        x1 = xy[:, 0] - xy[:, 2] / 2
        y1 = xy[:, 1] - xy[:, 3] / 2
        x2 = xy[:, 0] + xy[:, 2] / 2
        y2 = xy[:, 1] + xy[:, 3] / 2

        idxs = self._nms(x1, y1, x2, y2, scores, iou)
        out_boxes = []
        for i in idxs:
            bx1 = max(0, int(round(x1[i])))
            by1 = max(0, int(round(y1[i])))
            bx2 = min(w0, int(round(x2[i])))
            by2 = min(h0, int(round(y2[i])))
            bw, bh = bx2 - bx1, by2 - by1
            if bw < 15 or bh < 15:
                continue
            out_boxes.append((bx1, by1, bw, bh))
        return out_boxes

    @staticmethod
    def _nms(x1, y1, x2, y2, scores, thr) -> list[int]:
        order = scores.argsort()[::-1]
        areas = (x2 - x1) * (y2 - y1)
        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(int(i))
            if order.size == 1:
                break
            rest = order[1:]
            xx1 = np.maximum(x1[i], x1[rest])
            yy1 = np.maximum(y1[i], y1[rest])
            xx2 = np.minimum(x2[i], x2[rest])
            yy2 = np.minimum(y2[i], y2[rest])
            inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
            union = areas[i] + areas[rest] - inter
            ovr = np.where(union > 0, inter / union, 0.0)
            order = rest[ovr <= thr]
        return keep

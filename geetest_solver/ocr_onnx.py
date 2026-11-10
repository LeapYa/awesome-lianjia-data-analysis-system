"""
ONNX 版 PP-OCRv6-tiny 识别器

为什么自己写
------------
paddle 的 `ppocr` 包和静态图 predictor 加起来占 ~200MB。
ONNX 版只要 onnxruntime（检测器已经在用）+ 4.3MB 模型。

实测对比（真实人工框，876 个字）：

| 方案 | 单字准确率 | 速度 | 依赖 |
| --- | --- | --- | --- |
| paddle 静态图 | 97.8% | 26 ms/字 | paddle + ppocr |
| **ONNX** | **97.8%** | **3 ms/字** | onnxruntime |

CTC 解码
--------
PP-OCR 用 CTC 训练，输出 (1, T, C)，T=40（序列长度），C=6906（字符表）。

解码规则：
    1. 每帧取 argmax
    2. 合并连续相同索引
    3. 去掉 blank（索引 0）

字符表
------
`ppocrv6_tiny_dict.txt` 每行一个字，共 6905 行。
加上 blank（索引 0）和 space（末尾），总共 6906 项。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

REC_H = 48
REC_W = 320   # 默认值；实际以 ONNX 输入尺寸为准（窄宽模型导出为 96）


class OnnxOcr:
    """PP-OCRv6-tiny 的 ONNX 推理。"""

    def __init__(self, model_path: str | Path, dict_path: str | Path,
                 threads: int = 4, width: int | None = None):
        import onnxruntime as ort

        self.path = Path(model_path)
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(
            str(self.path), so, providers=["CPUExecutionProvider"])
        inp = self.sess.get_inputs()[0]
        self.in_name = inp.name
        # 从模型推断输入尺寸（导出时是固定的）：宽 320 的老模型 / 96 的新模型都能跑
        shape = inp.shape
        dims = [d if isinstance(d, int) else None for d in shape]
        self.rec_h = dims[2] or REC_H
        # 导出成动态宽度时（窄宽训练的新模型），按调用方给的建议宽度跑（默认 320 兼容旧模型）
        self.rec_w = dims[3] or width or REC_W

        # 字符表：blank + 每个字 + space
        self.chars = ["blank"]
        with open(dict_path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                self.chars.append(line.rstrip("\n"))
        if " " not in self.chars:
            self.chars.append(" ")
        self._warmed = False

    def warmup(self) -> None:
        if self._warmed:
            return
        dummy = np.zeros((1, 3, self.rec_h, self.rec_w), np.float32)
        self.sess.run(None, {self.in_name: dummy})
        self._warmed = True

    def _decode(self, pred: np.ndarray) -> tuple[str, float]:
        """CTC 贪心解码。返回 (文本, 置信度)。

        置信度 = 保留字符各帧最大 softmax 概率的均值。
        形近字混淆时这个值会明显偏低，可用来提示人工重点核对。
        """
        logits = pred[0]                      # (T, C)
        idx = logits.argmax(-1)
        # 逐帧 softmax 取最大概率
        z = logits - logits.max(-1, keepdims=True)
        e = np.exp(z)
        probs = (e / e.sum(-1, keepdims=True)).max(-1)

        out = []
        confs = []
        prev = -1
        for t, i in enumerate(idx):
            if i != prev and i != 0:
                if i < len(self.chars):
                    out.append(self.chars[i])
                    confs.append(float(probs[t]))
            prev = i
        if not confs:
            return "", 0.0
        return "".join(out), sum(confs) / len(confs)

    def predict(self, crop: np.ndarray) -> tuple[str, float]:
        """识别单字裁剪图。返回 (文本, 置信度)。"""
        if crop is None or crop.size == 0:
            return "", 0.0
        h, w = crop.shape[:2]
        if h < 8 or w < 8:
            return "", 0.0

        self.warmup()

        # 等比缩放到高 rec_h，右侧补 0 到宽 rec_w，归一化到 [-1, 1]
        rw = min(self.rec_w, int(np.ceil(self.rec_h * w / float(h))))
        r = cv2.resize(crop, (rw, self.rec_h))
        r = r.astype("float32").transpose((2, 0, 1)) / 255.0
        r -= 0.5
        r /= 0.5
        pad = np.zeros((3, self.rec_h, self.rec_w), np.float32)
        pad[:, :, :rw] = r

        try:
            out = self.sess.run(None, {self.in_name: pad[None]})[0]
            return self._decode(out)
        except Exception:
            return "", 0.0

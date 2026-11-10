"""
GeetestSolver —— 极验 v4 验证码求解器

用法：

    import geetest_solver

    solver = geetest_solver.GeetestSolver()

    # word / icon 型（有 ques 小图）
    result = solver.classification(bg_bytes, [q1_bytes, q2_bytes, q3_bytes])

    # phrase 型（无 ques，需自行判断语序）
    result = solver.classification(bg_bytes)

    print(result["points"])   # [(x, y), ...] 点击坐标，按点击顺序
    print(result["type"])     # "word" / "icon" / "phrase"

输入可以是 bytes、numpy 数组或文件路径。

返回字段：
    type        题型
    points      点击坐标列表，按点击顺序
    word        phrase 型识别出的词
    chars       phrase 型每个框识别出的字
    boxes       每个字的检测框 [(x, y, w, h), ...]
    elapsed     耗时（秒）
"""

from __future__ import annotations

import math
import pickle
from pathlib import Path
from typing import Any

import cv2
import numpy as np

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
# 旧版（3 字 / 彩色描边时代）的模型与权重，仅作回退，见 geetest_solver_old/
LEGACY_DIR = MODEL_DIR.parent / "geetest_solver_old" / "models"

# 微调模型的预处理常量（必须与训练时一致）
# 用错这两个参数会让识别率从 95.9% 掉到 46.7%
REC_H = 48
REC_W = 320

# 字符字典（PP-OCRv6 官方表，18708 字）
DICT_NAME = "ppocrv6_dict.txt"


def _to_array(data: Any) -> np.ndarray:
    """字节 / ndarray → BGR ndarray。"""
    if isinstance(data, np.ndarray):
        img = data
    elif isinstance(data, (bytes, bytearray)):
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    elif isinstance(data, (str, Path)):
        img = cv2.imread(str(data))
    else:
        raise TypeError(f"不支持的类型: {type(data)}")
    if img is None:
        raise ValueError("图片解码失败")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    return img


def _alpha_mask(data: Any) -> np.ndarray:
    """ques 小图的 alpha 通道 → 二值掩码。

    ques 小图是 PNG，带透明背景；无 alpha 时退回灰度阈值。
    """
    if isinstance(data, (bytes, bytearray)):
        arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    elif isinstance(data, (str, Path)):
        arr = cv2.imread(str(data), cv2.IMREAD_UNCHANGED)
    elif isinstance(data, np.ndarray):
        arr = data
    else:
        raise TypeError(f"不支持的类型: {type(data)}")
    if arr is None:
        raise ValueError("ques 图片解码失败")

    if arr.ndim == 3 and arr.shape[2] == 4:
        a = arr[:, :, 3]
        m = (a > 0).astype(np.uint8) * 255
    else:
        g = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY) if arr.ndim == 3 else arr
        m = (g > 127).astype(np.uint8) * 255

    ys, xs = np.nonzero(m > 0)
    if len(ys) == 0:
        return m
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


class GeetestSolver:
    """极验 v4 求解器。

    参数
    ----
    template_lib : str
        模板库路径（word/icon 型用）
    detector : str
        YOLO 检测器路径（phrase 型分割用）
    ocr_weights : str
        微调 PP-OCRv6 训练权重（.pdparams）
    ocr_config : str
        PP-OCRv6 训练配置（yml）
    order_lm : str
        语序模型（.pkl）
    threads : int
        CPU 线程数
    """

    def __init__(
        self,
        template_lib: str | Path | None = None,
        detector: str | Path | None = None,
        ocr_weights: str | Path | None = None,
        ocr_config: str | Path | None = None,
        order_lm: str | Path | None = None,
        threads: int = 2,
    ):
        self.threads = threads
        tpl = MODEL_DIR / "geetest.pkl"
        if not tpl.exists():
            tpl = LEGACY_DIR / "geetest.pkl"
        self.template_lib = Path(template_lib) if template_lib else tpl
        if detector:
            self.detector_path = Path(detector)
        else:
            # 优先 ONNX 检测器（9.3MB，免 torch 运行时）
            onnx_det = MODEL_DIR / "detector_v26.onnx"
            if onnx_det.exists():
                self.detector_path = onnx_det
            else:
                # 退回旧 torch 权重（geetest_solver_old/models/）
                v26 = MODEL_DIR / "detector_v26.pt"
                if not v26.exists():
                    v26 = LEGACY_DIR / "detector_v26.pt"
                self.detector_path = v26 if v26.exists() else LEGACY_DIR / "detector.pt"
        # 优先 tiny 档（实测单字 97.8%，比 small 还高，速度 10 倍，体积 31%）
        if ocr_weights:
            self.ocr_weights = Path(ocr_weights)
        else:
            tiny = MODEL_DIR / "ocr_ppocrv6_tiny.pdparams"
            self.ocr_weights = tiny if tiny.exists() else LEGACY_DIR / "ocr_ppocrv6.pdparams"
        if ocr_config:
            self.ocr_config = Path(ocr_config)
        else:
            tiny_cfg = MODEL_DIR / "ocr_tiny_config.yml"
            self.ocr_config = tiny_cfg if tiny_cfg.exists() else LEGACY_DIR / "ocr_config.yml"
        # tiny 用独立的字符表（6906 字）
        # 判断依据：ONNX 模型、静态图、或权重文件名含 tiny，任一成立即是 tiny 档
        tiny_dict = MODEL_DIR / "ppocrv6_tiny_dict.txt"
        is_tiny = (
            "tiny" in self.ocr_weights.name
            or (MODEL_DIR / "ocr_tiny_w96.onnx").exists()
            or (LEGACY_DIR / "ocr_tiny.onnx").exists()
            or (MODEL_DIR / "ocr_tiny_static" / "model.json").exists()
        )
        self.ocr_dict = tiny_dict if (tiny_dict.exists() and is_tiny) else \
                        LEGACY_DIR / "ppocrv6_dict.txt"
        if order_lm:
            self.order_lm_path = Path(order_lm)
        else:
            # 优先压缩版（10.8MB，实测融合准确率反而更高）
            small = MODEL_DIR / "order_lm_small.pkl"
            if not small.exists():
                small = LEGACY_DIR / "order_lm.pkl"
            self.order_lm_path = small
        # 优先 int8 量化版（5.9MB，准确率无损）
        # 优先 npz（免 torch，省 ~300MB 内存）
        npz = MODEL_DIR / "order_ranker.npz"
        if npz.exists():
            self.order_ranker_path = npz
        else:
            q8 = MODEL_DIR / "order_ranker_int8.pt"
            self.order_ranker_path = q8 if q8.exists() else MODEL_DIR / "order_ranker.pt"

        self._tpl = None
        self._det = None
        self._det_kind = ""
        self._ocr = None
        self._static = False
        self._ocr_onnx = False
        self._pred = None
        self._post = None
        self._lm = None
        self._dict_words = None
        self._ranker = None
        self._ranker_tried = False

    # ---------------------------------------------------------------- 加载
    def _load_template(self):
        if self._tpl is not None:
            return self._tpl
        if not self.template_lib.exists():
            return None
        with open(self.template_lib, "rb") as fh:
            self._tpl = pickle.load(fh)
        return self._tpl

    def _load_detector(self):
        if self._det is not None:
            return self._det
        # 优先 ONNX（9.3MB，运行时 ~23MB；torch 版 24.8MB，运行时 ~300MB）
        onnx_path = MODEL_DIR / "detector_v26.onnx"
        if onnx_path.exists():
            try:
                from .detector_onnx import OnnxDetector
                self._det = OnnxDetector(onnx_path)
                self._det_kind = "onnx"
                return self._det
            except Exception:
                pass
        if not self.detector_path.exists():
            return None
        from ultralytics import YOLO
        self._det = YOLO(str(self.detector_path))
        self._det_kind = "torch"
        return self._det

    def _load_ocr(self):
        if self._ocr is not None:
            return self._ocr

        # 优先 ONNX（4.3MB，省 ~200MB paddle 运行时，快 8 倍）
        # ocr_tiny_w96.onnx：4 字新字形微调版（2026-10-22），动态宽度，按 48×96 跑
        onnx_path = MODEL_DIR / "ocr_tiny_w96.onnx"
        width = 96
        if not onnx_path.exists():
            onnx_path = LEGACY_DIR / "ocr_tiny.onnx"
            width = 320
        if onnx_path.exists():
            try:
                from .ocr_onnx import OnnxOcr
                self._ocr = OnnxOcr(onnx_path, self.ocr_dict, width=width)
                self._static = True
                self._ocr_onnx = True
                return self._ocr
            except Exception:
                pass

        if not self.ocr_weights.exists() or not self.ocr_config.exists():
            return None

        import paddle
        paddle.set_flags({"FLAGS_use_mkldnn": False})
        from ppocr.postprocess import build_post_process

        dict_path = self.ocr_dict

        # 优先静态图（4.4MB，动态图权重 68MB 含优化器状态）
        static_dir = MODEL_DIR / "ocr_tiny_static"
        if (static_dir / "model.json").exists():
            from paddle import inference
            c = inference.Config(str(static_dir / "model.json"),
                                 str(static_dir / "model.pdiparams"))
            c.disable_gpu()
            c.disable_mkldnn()
            c.switch_ir_optim(True)
            self._pred = inference.create_predictor(c)
            self._in_name = self._pred.get_input_names()[0]
            self._out_name = self._pred.get_output_names()[0]
            self._post = build_post_process({
                "name": "CTCLabelDecode",
                "character_dict_path": str(dict_path),
                "use_space_char": True,
            })
            # 预热
            try:
                self._pred.get_input_handle(self._in_name).copy_from_cpu(
                    __import__("numpy").zeros((1, 3, REC_H, REC_W), "float32"))
                self._pred.run()
            except Exception:
                pass
            self._static = True
            # 关键：必须赋值给 self._ocr，否则每次调用都重走加载流程（实测单字 137ms）
            self._ocr = self._pred
            return self._pred

        # 退回动态图
        from ppocr.modeling.architectures import build_model
        import yaml
        cfg = yaml.safe_load(self.ocr_config.read_text(encoding="utf-8"))
        sd = paddle.load(str(self.ocr_weights))
        # tiny 档输出层叫 fc2（两级），small/medium 叫 fc
        if "head.ctc_head.fc2.bias" in sd:
            n_cls = len(sd["head.ctc_head.fc2.bias"])
        else:
            n_cls = len(sd["head.ctc_head.fc.bias"])
        cfg["Architecture"]["Head"]["out_channels_list"] = {
            "CTCLabelDecode": n_cls,
            "NRTRLabelDecode": n_cls + 3,
        }
        self._ocr = build_model(cfg["Architecture"])
        self._ocr.set_state_dict(sd)
        self._ocr.eval()
        self._post = build_post_process({
            "name": "CTCLabelDecode",
            "character_dict_path": str(dict_path),
            "use_space_char": True,
        })

        # 预热：首次推理要建算子图，约 2s；提前跑一次让后续调用稳定
        try:
            dummy = paddle.zeros((1, 3, REC_H, REC_W), dtype="float32")
            with paddle.no_grad():
                self._ocr(dummy)
        except Exception:
            pass
        return self._ocr

    def _load_lm(self):
        if self._lm is not None:
            return self._lm
        if not self.order_lm_path.exists():
            return None
        with open(self.order_lm_path, "rb") as fh:
            self._lm = pickle.load(fh)
        # 词典只加载一次（原先每次 best_order 都重读，单张多花 5 秒）
        from .order import load_dict
        dict_path = MODEL_DIR / "jieba_dict.txt"
        self._dict_words = load_dict(dict_path if dict_path.exists() else None)
        return self._lm

    # ---------------------------------------------------------------- 主入口
    def classification(
        self,
        background: Any,
        ques: list[Any] | None = None,
        captcha_type: str = "auto",
    ) -> dict:
        """识别验证码，返回点击坐标。

        参数
        ----
        background : bytes / ndarray / path
            背景大图
        ques : list of bytes / ndarray / path, optional
            ques 小图（word/icon 型需要，phrase 型不需要）
        captcha_type : str
            "word" / "icon" / "phrase" / "auto"
            默认 auto：有 ques 走匹配，无 ques 走 OCR

        返回
        ----
        dict: {"type", "points", "word", "chars", "boxes", "elapsed"}
        """
        import time
        t0 = time.time()

        bg = _to_array(background)
        masks = [_alpha_mask(q) for q in (ques or [])]

        # 自动判断题型
        if captcha_type == "auto":
            captcha_type = "phrase" if not masks else "word"

        if captcha_type in ("word", "icon"):
            res = self._solve_match(bg, masks, captcha_type)
        else:
            res = self._solve_phrase(bg)

        res["elapsed"] = round(time.time() - t0, 3)
        return res

    # ---------------------------------------------------------------- word/icon
    def _solve_match(self, bg: np.ndarray, masks: list[np.ndarray],
                     ctype: str) -> dict:
        """word / icon 型：检测框 + 形状匹配。

        实测（真实人工标注，YOLO26n）：
            word 3 点全对 100%，单点落框 100%
            icon 3 点全对  99%，单点落框  99%

        关键：**不识别文字**。ques 是固定模板，背景里是艺术字，
        归一化成二值形状后做多角度 IoU，比 OCR 转文字再比对稳得多
        （OCR 在 word 上只有 42%，错误会层层传递）。
        """
        from .query_match import shape_of, match_boxes

        boxes = self._detect_boxes(bg)
        if len(boxes) < 2:
            return {"type": ctype, "points": [],
                    "error": f"只找到 {len(boxes)} 个框"}

        # 首选「掩膜 + 边缘 chamfer」（2026-11 站点换成粗彩色描边后，
        # 旧的二值 IoU 在真实题面上矩阵几乎是平的；chamfer 在真实 word 题上已验证通过）
        idx = None
        try:
            from . import shape_chamfer as SC
            qe = [SC.ques_edges(m) for m in masks]
            pe = [SC.panel_edges(bg[max(0, y - 2):y + h + 2,
                                     max(0, x - 2):x + w + 2])
                  for (x, y, w, h) in boxes]
            cand, _cost = SC.assign(qe, pe)
            if len(cand) == len(masks) and all(i >= 0 for i in cand):
                idx = cand
        except Exception:
            idx = None

        if idx is None:
            qshapes = [shape_of(m) for m in masks]
            if not any(s is not None for s in qshapes):
                return {"type": ctype, "points": [], "error": "ques 解析失败"}
            idx = match_boxes(bg, boxes, qshapes, [])

        pts, detail = [], []
        for i in idx:
            if i < 0 or i >= len(boxes):
                pts.append(None)
                detail.append({"center": None})
                continue
            x, y, w, h = boxes[i]
            c = (x + w // 2, y + h // 2)
            pts.append(c)
            detail.append({"center": c, "bbox": (x, y, w, h)})

        return {"type": ctype,
                "points": [p for p in pts if p],
                "detail": detail,
                "n_detected": len(boxes)}

    def _detect_boxes(self, bg: np.ndarray) -> list[tuple]:
        """检测背景中所有字/图标的框。返回 [(x, y, w, h)]。"""
        det = self._load_detector()
        if det is not None:
            try:
                if getattr(self, "_det_kind", "") == "onnx":
                    out = det.detect(bg)
                else:
                    r = det.predict(bg, imgsz=320, verbose=False, conf=0.30)
                    out = []
                    if r and r[0].boxes is not None:
                        for box in r[0].boxes.xyxy.cpu().numpy():
                            x1, y1, x2, y2 = [int(v) for v in box]
                            w, h = x2 - x1, y2 - y1
                            if w < 15 or h < 15:
                                continue
                            out.append((x1, y1, w, h))
                if len(out) >= 2:
                    return out
            except Exception:
                pass

        from .segment import threshold_segment
        seg = threshold_segment(bg, want=3)
        return [tuple(s["bbox"]) for s in seg]

    # ---------------------------------------------------------------- phrase
    def _solve_phrase(self, bg: np.ndarray) -> dict:
        # 1. 分割
        boxes = self._segment(bg)
        if len(boxes) < 2:
            return {"type": "phrase", "points": [], "boxes": [],
                    "error": f"只切出 {len(boxes)} 个字"}

        # 2. 识别
        chars, confs = [], []
        for b in boxes:
            ch, cf = self._ocr_one(b["crop"])
            chars.append(ch)
            confs.append(cf)

        # 3. 过滤：只保留汉字
        #    注意：phrase 型可能是 3 字或 4 字（真实站点实测有 4 字，如「房开推门」）
        keep = [i for i, c in enumerate(chars)
                if c and "\u4e00" <= c <= "\u9fff"]
        # 超过 4 个才截断（取置信度高的），保留 2~4 个都允许
        if len(keep) > 4:
            keep = sorted(keep, key=lambda i: -confs[i])[:4]
        if len(keep) < 2:
            return {"type": "phrase", "points": [], "chars": chars, "boxes": [],
                    "error": "识别不足"}

        sub = [chars[i] for i in keep]

        # 4. 语序
        order = self._order(sub)
        seq = [keep[i] for i in order]

        pts = [boxes[i]["center"] for i in seq]
        return {
            "type": "phrase",
            "points": pts,
            "word": "".join(chars[i] for i in seq),
            "chars": chars,
            "conf": [round(c, 2) for c in confs],
            "boxes": [boxes[i]["bbox"] for i in seq],
        }

    def _segment(self, bg: np.ndarray) -> list[dict]:
        """切出背景中的字。优先 YOLO，失败退回阈值法。"""
        det = self._load_detector()
        if det is not None:
            try:
                if getattr(self, "_det_kind", "") == "onnx":
                    raw = det.detect(bg)
                else:
                    r = det.predict(bg, imgsz=320, verbose=False, conf=0.35)
                    raw = []
                    if r and r[0].boxes is not None:
                        for box in r[0].boxes.xyxy.cpu().numpy():
                            x1, y1, x2, y2 = [int(v) for v in box]
                            raw.append((x1, y1, x2 - x1, y2 - y1))
                out = []
                for (x1, y1, w, h) in raw:
                    if w < 15 or h < 15:
                        continue
                    pad = 2
                    cx0, cy0 = max(0, x1 - pad), max(0, y1 - pad)
                    cx1 = min(bg.shape[1], x1 + w + pad)
                    cy1 = min(bg.shape[0], y1 + h + pad)
                    out.append({
                        "bbox": (x1, y1, w, h),
                        "center": (x1 + w // 2, y1 + h // 2),
                        "crop": bg[cy0:cy1, cx0:cx1],
                    })
                if len(out) >= 2:
                    return out
            except Exception:
                pass

        # 阈值法兜底
        try:
            from .segment import threshold_segment
            return threshold_segment(bg, want=3)
        except Exception:
            return []

    def _ocr_one(self, crop: np.ndarray) -> tuple[str, float]:
        """识别单字。预处理严格对齐训练。"""
        model = self._load_ocr()
        if model is None or crop is None or crop.size == 0:
            return "", 0.0
        h, w = crop.shape[:2]
        if h < 8 or w < 8:
            return "", 0.0

        if getattr(self, "_ocr_onnx", False):
            try:
                txt, conf = model.predict(crop)
                return txt, conf
            except Exception:
                return "", 0.0

        import paddle
        ratio = w / float(h)
        rw = min(REC_W, int(math.ceil(REC_H * ratio)))
        r = cv2.resize(crop, (rw, REC_H))
        r = r.astype("float32").transpose((2, 0, 1)) / 255.0
        r -= 0.5
        r /= 0.5
        pad = np.zeros((3, REC_H, REC_W), dtype=np.float32)
        pad[:, :, :rw] = r

        try:
            if getattr(self, "_static", False):
                # 静态图：predictor.run()
                self._pred.get_input_handle(self._in_name).copy_from_cpu(pad[None])
                self._pred.run()
                out = self._pred.get_output_handle(self._out_name).copy_to_cpu()
            else:
                with paddle.no_grad():
                    o = model(paddle.to_tensor(pad[None]))
                o = o[0] if isinstance(o, (list, tuple)) else o
                out = o.numpy()
            res = self._post(out)
            if res and res[0]:
                return str(res[0][0]), 1.0
        except Exception:
            pass
        return "", 0.0

    def _order(self, chars: list[str]) -> list[int]:
        """判定语序。

        4 字 phrase 走「谓宾结构先验」（order_pos）：
            穿过路口 / 拎袋水果 / 拌盘黄瓜 / 端盆清水 / 装袋花生
        真实 200 条答案实测：结构先验 72%，字符 n-gram 40%，
        原 n-gram+六选一分类器融合只有 31%（分类器是 3 字 IME 任务训的，
        在 4 字上只有 7.4%，会把宾谓倒装排到前面）。
        其它长度（3 字）保持原 n-gram + 分类器融合不变。
        """
        lm = self._load_lm()
        if len(chars) < 2:
            return list(range(len(chars)))

        if len(chars) == 4:
            try:
                from .order_pos import best_order_pos
                idx, _ = best_order_pos(
                    chars, lm=lm,
                    dict_path=MODEL_DIR / "jieba_dict.txt")
                return idx
            except Exception:
                pass

        if lm is None:
            return list(range(len(chars)))

        try:
            from .order import best_order
            ranker = self._load_ranker()
            idx, _ = best_order(chars, lm,
                                dict_words=self._dict_words or set(),
                                ranker=ranker)
            return idx
        except Exception:
            return list(range(len(chars)))

    def _load_ranker(self):
        """六选一分类器（可选，缺失则退化为纯 n-gram）。"""
        if self._ranker_tried:
            return self._ranker
        self._ranker_tried = True
        if not self.order_ranker_path.exists():
            return None
        try:
            # 优先 numpy 版（不拉 torch，省 ~300MB）
            from .order_numpy import NumpyRanker
            self._ranker = NumpyRanker(self.order_ranker_path).load()
            print("[语序] 使用 n-gram + 分类器融合（numpy 推理）")
        except Exception as e:
            print(f"[语序] 分类器加载失败，退化纯 n-gram: {e}")
            self._ranker = None
        return self._ranker


def solve(background: Any, ques: list[Any] | None = None,
          captcha_type: str = "auto") -> dict:
    """便捷函数（每次新建 solver，适合一次性调用）。"""
    return GeetestSolver().classification(background, ques, captcha_type)

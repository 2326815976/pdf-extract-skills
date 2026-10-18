# -*- coding: utf-8 -*-
"""OCR 封装（RapidOCR / PP-OCR 中文，ONNX，CPU 可跑）。

三类引擎分开实例化，便于按需跑（方向检测只需要检测框 + 少量识别，比整页识别快很多）：
- OcrEngine：det + rec，整页识别；
- DetEngine：只做检测，拿文本框；
- RecEngine：只做识别，喂单行小图。
"""
from __future__ import annotations

import numpy as np


def _to_bgr(image):
    if hasattr(image, "convert"):
        arr = np.asarray(image.convert("RGB"))
    else:
        arr = np.asarray(image)
    return arr[:, :, ::-1]


class OcrEngine:
    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR

        self.engine = RapidOCR()

    def run(self, image):
        """返回 [{text, conf, cx, cy, box, x0,y0,x1,y1}]。

        image 可以是 PIL.Image / ndarray / 文件路径（路径最快，省一次编解码）。
        """
        payload = str(image) if isinstance(image, (str, bytes)) or hasattr(image, "__fspath__") else _to_bgr(image)
        result, _elapse = self.engine(payload)
        lines = []
        for item in result or []:
            box, text, score = item[0], item[1], item[2]
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            lines.append({
                "text": text,
                "conf": round(float(score), 4),
                "cx": round(sum(xs) / len(xs), 1),
                "cy": round(sum(ys) / len(ys), 1),
                "x0": round(min(xs), 1),
                "y0": round(min(ys), 1),
                "x1": round(max(xs), 1),
                "y1": round(max(ys), 1),
            })
        lines.sort(key=lambda t: (round(t["cy"] / 12.0), t["cx"]))
        return lines


class DetEngine:
    """只做文本检测，返回轴对齐框 [(x0,y0,x1,y1,area)]。"""

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR

        self.engine = RapidOCR(use_rec=False, use_cls=False)

    def run(self, image):
        result, _elapse = self.engine(_to_bgr(image))
        boxes = []
        for item in result or []:
            pts = item if isinstance(item, (list, tuple)) and len(item) == 4 and \
                isinstance(item[0], (list, tuple)) else item[0]
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            boxes.append((x0, y0, x1, y1, max(0.0, (x1 - x0)) * max(0.0, (y1 - y0))))
        return boxes


class RecEngine:
    """只做识别，返回 (text, score)。"""

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR

        self.engine = RapidOCR(use_det=False, use_cls=False)

    def run(self, crop):
        result, _elapse = self.engine(_to_bgr(crop))
        if not result:
            return "", 0.0
        text = "".join(str(it[0]) for it in result)
        score = float(np.mean([float(it[1]) for it in result]))
        return text, score

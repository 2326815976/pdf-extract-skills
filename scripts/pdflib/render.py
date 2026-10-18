# -*- coding: utf-8 -*-
"""页面渲染 + 自动摆正（快速方向判定）。

两步，避免昂贵的整页检测模型：
1) 长直线形态（毫秒级）判断文字行方向：候选 0/180 还是 90/270；
2) 在候选方向上用"墨迹投影找文本行 → 裁几小段 → 只做识别"判断是否 180° 颠倒。
"""
from __future__ import annotations

import cv2
import numpy as np
import pymupdf
from PIL import Image

RULE_MIN_FRAC = 30
PROBE_MAX_BANDS = 4
BAND_MIN_HEIGHT = 8
BAND_MAX_HEIGHT = 90
CROP_WIDTH = 320


def render_page(doc, index, dpi=200):
    page = doc[index]
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def rotate(image, angle):
    angle = angle % 360
    if angle == 0:
        return image
    return image.rotate(angle, expand=True)


def _line_ink(image, horizontal=True):
    gray = np.asarray(image.convert("L"))
    dark = (gray < 190).astype(np.uint8) * 255
    h, w = dark.shape
    if horizontal:
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, w // RULE_MIN_FRAC), 1))
    else:
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, h // RULE_MIN_FRAC)))
    lines = cv2.morphologyEx(dark, cv2.MORPH_OPEN, k)
    return float((lines > 0).mean())


def axis_hint(image):
    """0 = 文字行水平（候选 0/180）；90 = 文字行竖直（先转 90，再判 0/180）。"""
    h_ratio = _line_ink(image, horizontal=True)
    v_ratio = _line_ink(image, horizontal=False)
    return 90 if v_ratio > h_ratio * 1.2 else 0


def _text_bands(image, max_bands=PROBE_MAX_BANDS):
    """用行方向的墨迹投影找出若干文本行（滤掉 1-3px 的框线）。"""
    gray = np.asarray(image.convert("L"))
    dark = gray < 190
    if dark.size == 0:
        return []
    row = dark.mean(axis=1)
    threshold = max(0.02, float(np.percentile(row, 85)) * 0.5)
    bands, start = [], None
    for y, value in enumerate(row):
        if value > threshold and start is None:
            start = y
        elif value <= threshold and start is not None:
            if BAND_MIN_HEIGHT <= y - start <= BAND_MAX_HEIGHT:
                bands.append((start, y))
            start = None
    if start is not None and BAND_MIN_HEIGHT <= len(row) - start <= BAND_MAX_HEIGHT:
        bands.append((start, len(row)))
    if len(bands) > max_bands:
        idx = sorted({int(round(i)) for i in np.linspace(0, len(bands) - 1, max_bands)})
        bands = [bands[i] for i in idx]
    return bands


def _line_crops(image, bands):
    """每条文本行裁一小段（带墨迹的起点，宽度约 320px）用于识别打分。"""
    crops = []
    width = image.width
    for y0, y1 in bands:
        strip = np.asarray(image.crop((0, y0, width, y1)).convert("L")) < 190
        if strip.size == 0:
            continue
        cols = np.flatnonzero(strip.mean(axis=0) > 0.02)
        if cols.size == 0:
            continue
        x0 = int(cols[0])
        x1 = min(width, x0 + CROP_WIDTH)
        if x1 - x0 < 40:
            continue
        crops.append(image.crop((x0, max(0, y0 - 2), x1, min(image.height, y1 + 2))))
    return crops


def _rec_score(rec, crops, flip=False):
    total = 0.0
    for crop in crops:
        image = crop.rotate(180, expand=True) if flip else crop
        text, conf = rec.run(image)
        total += len(text.strip()) * (0.3 + conf)
    return total


def detect_orientation(image, rec=None, ocr=None):
    """返回 (angle, score, detail)；angle 为逆时针旋转角度，使页面正立。"""
    axis = axis_hint(image)
    detail = {"axis": axis}
    if rec is None and ocr is None:
        return axis, 0.0, detail

    axis_image = rotate(image, axis)
    if rec is not None:
        bands = _text_bands(axis_image)
        crops = _line_crops(axis_image, bands)
        detail["bands"] = len(bands)
        detail["crops"] = len(crops)
        if not crops:
            return axis, 0.0, detail
        upright = _rec_score(rec, crops)
        flipped = _rec_score(rec, crops, flip=True)
        detail.update({"score_upright": round(upright, 1), "score_flipped": round(flipped, 1)})
        if flipped > upright * 1.2 and flipped > 5:
            return (axis + 180) % 360, flipped, detail
        return axis, upright, detail

    # 兜底（只有整页 OCR 可用时）：两个候选各跑一次整页 OCR
    best_angle, best_score = axis, -1.0
    for angle in (axis, (axis + 180) % 360):
        candidate = rotate(image, angle)
        lines = ocr.run(candidate)
        score = sum(len(l["text"].strip()) * (0.3 + l["conf"]) for l in lines)
        detail["angle_%d" % angle] = round(score, 1)
        if score > best_score:
            best_angle, best_score = angle, score
    return best_angle, best_score, detail


def prepare_upright_images(pdf_path, out_dir, dpi=None, max_pages=None, rec=None, ocr=None,
                           cache_path=None, force_recheck=False, page_indices=None):
    """按页渲染并自动摆正，返回 [{page, path, angle, score, detail, width, height}]。"""
    import json
    from pathlib import Path

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = {}
    fingerprint = "%s|%s|%s" % (Path(pdf_path).resolve(), Path(pdf_path).stat().st_mtime, dpi)
    if cache_path and Path(cache_path).exists():
        try:
            payload = json.loads(Path(cache_path).read_text(encoding="utf-8"))
            if payload.get("fingerprint") == fingerprint:
                cache = payload.get("angles", {})
        except Exception:
            cache = {}

    doc = pymupdf.open(str(pdf_path))
    dpi = dpi or 200
    results = []
    indices = range(len(doc)) if page_indices is None else sorted(set(page_indices))
    for i in indices:
        if i < 0 or i >= len(doc):
            continue
        if max_pages and i >= max_pages:
            continue
        dst = out_dir / ("page-%02d.png" % (i + 1))
        key = str(i + 1)
        if key in cache and not force_recheck and dst.exists():
            with Image.open(dst) as existing:
                width, height = existing.size
            results.append({
                "page": i + 1,
                "path": str(dst),
                "angle": int(cache[key]),
                "score": None,
                "detail": {"cached": True},
                "width": width,
                "height": height,
            })
            continue
        image = render_page(doc, i, dpi=dpi)
        angle, score, detail = 0, None, {}
        if key in cache and not force_recheck:
            angle = int(cache[key])
            detail = {"cached": True}
        elif rec is not None or ocr is not None:
            angle, score, detail = detect_orientation(image, rec=rec, ocr=ocr)
        upright = rotate(image, angle)
        upright.save(dst)
        cache[key] = angle
        results.append({
            "page": i + 1,
            "path": str(dst),
            "angle": angle,
            "score": round(score, 2) if score is not None else None,
            "detail": detail,
            "width": upright.width,
            "height": upright.height,
        })
    doc.close()
    if cache_path:
        Path(cache_path).write_text(json.dumps({"fingerprint": fingerprint, "angles": cache},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    return results

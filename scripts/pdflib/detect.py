# -*- coding: utf-8 -*-
"""PDF 页面级检测与路由。

图片存在不等于扫描件。本模块只判断每一页实际适合走哪条提取路径：
- native: 文本层足够且可靠；
- visual: 文本较少、图片占比较高，通常包含截图或海报；
- scan: 基本没有文本层，需要整页 OCR；
- blank: 空白或近空白页。
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pymupdf


def _clip_area(rect, page_rect):
    clipped = pymupdf.Rect(rect) & page_rect
    return max(0.0, clipped.width) * max(0.0, clipped.height)


def _image_metrics(page):
    """返回图片数量、总面积占比和最大单图占比。"""
    page_area = max(1.0, page.rect.width * page.rect.height)
    rects = []
    for image in page.get_images(full=True):
        xref = image[0]
        try:
            found = page.get_image_rects(xref)
        except Exception:
            continue
        rects.extend(pymupdf.Rect(rect) for rect in found)
    areas = [_clip_area(rect, page.rect) for rect in rects]
    return {
        "count": len(rects),
        "coverage": round(min(1.0, sum(areas) / page_area), 4),
        "largest_coverage": round(min(1.0, max(areas, default=0.0) / page_area), 4),
    }


def _text_metrics(page):
    text = page.get_text("text") or ""
    chars = len(text.strip())
    if not chars:
        return {
            "chars": 0,
            "area_ratio": 0.0,
            "blocks": 0,
            "replacement_ratio": 0.0,
            "gibberish": False,
            "snippet": "",
        }

    page_area = max(1.0, page.rect.width * page.rect.height)
    blocks = []
    text_area = 0.0
    try:
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            block_text = "".join(
                span.get("text", "")
                for line in block.get("lines", [])
                for span in line.get("spans", [])
            ).strip()
            if not block_text:
                continue
            blocks.append(block_text)
            text_area += _clip_area(block["bbox"], page.rect)
    except Exception:
        blocks = [text]
        text_area = 0.0

    bad = 0
    visible = 0
    for char in text:
        if char.isspace():
            continue
        visible += 1
        if char == "\ufffd" or unicodedata.category(char) in ("Cc", "Co", "Cs"):
            bad += 1
    replacement_ratio = bad / max(visible, 1)
    return {
        "chars": chars,
        "area_ratio": round(min(1.0, text_area / page_area), 4),
        "blocks": len(blocks),
        "replacement_ratio": round(replacement_ratio, 4),
        "gibberish": replacement_ratio > 0.03,
        "snippet": text.strip()[:100],
    }


def _native_dpi(page):
    """扫描图的原生 dpi（按页面上的图像像素与放置尺寸估算）。"""
    best = None
    for image in page.get_images(full=True):
        xref = image[0]
        try:
            info = page.parent.extract_image(xref)
        except Exception:
            continue
        rects = page.get_image_rects(xref)
        if not rects or not info.get("width"):
            continue
        rect = rects[0]
        if rect.width <= 1 or rect.height <= 1:
            continue
        width_px, height_px = info["width"], info["height"]
        candidates = []
        for width_pt, height_pt in ((rect.width, rect.height),
                                    (rect.height, rect.width)):
            dpi_x = width_px / (width_pt / 72.0)
            dpi_y = height_px / (height_pt / 72.0)
            candidates.append((abs(dpi_x - dpi_y), (dpi_x + dpi_y) / 2.0))
        candidates.sort(key=lambda item: item[0])
        dpi = candidates[0][1]
        if best is None or dpi > best:
            best = dpi
    return round(best, 1) if best else None


def _route_page(text_metrics, image_metrics, drawings):
    chars = text_metrics["chars"]
    coverage = image_metrics["coverage"]
    reliable_text = chars >= 15 and not text_metrics["gibberish"]

    if chars == 0 and image_metrics["count"] == 0 and drawings == 0:
        return "blank", 0.99, "页面没有文本、图片或矢量内容"
    if chars < 15 and coverage >= 0.12:
        return "scan", 0.97, "几乎没有文本层，图片覆盖页面的 %.0f%%" % (coverage * 100)
    if coverage >= 0.12 and chars < 500:
        return "visual", 0.92, "图片覆盖 %.0f%%，文本层仅 %d 字符" % (coverage * 100, chars)
    if reliable_text:
        return "native", 0.99, "文本层可用，图片不影响原生文本提取"
    if image_metrics["count"]:
        return "visual", 0.78, "文本层可靠性不足，存在大图或截图"
    if drawings > 0:
        return "native", 0.65, "只有少量文本和矢量内容"
    if chars:
        return "native", 0.70, "存在少量原生文本"
    return "blank", 0.70, "页面内容不足"


def detect(pdf_path, max_pages=None):
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError("PDF 不存在：" + str(pdf_path))
    doc = pymupdf.open(str(pdf_path))
    if doc.needs_pass:
        doc.close()
        raise RuntimeError("PDF 已加密，请先提供无密码版本")

    total_pages = doc.page_count
    pages = []
    for index, page in enumerate(doc):
        if max_pages and index >= max_pages:
            break
        text = _text_metrics(page)
        images = _image_metrics(page)
        try:
            drawings = len(page.get_drawings())
        except Exception:
            drawings = 0
        route, confidence, reason = _route_page(text, images, drawings)
        pages.append({
            "page": index + 1,
            "width_pt": round(page.rect.width, 1),
            "height_pt": round(page.rect.height, 1),
            "rotation": page.rotation,
            "text_chars": text["chars"],
            "text_area_ratio": text["area_ratio"],
            "text_blocks": text["blocks"],
            "replacement_ratio": text["replacement_ratio"],
            "gibberish": text["gibberish"],
            "text_snippet": text["snippet"],
            "images": images["count"],
            "image_coverage": images["coverage"],
            "largest_image_coverage": images["largest_coverage"],
            "drawings": drawings,
            "native_dpi": _native_dpi(page),
            "route": route,
            "route_confidence": confidence,
            "route_reason": reason,
        })
    doc.close()

    parsed = pages or [{
        "text_chars": 0, "images": 0, "image_coverage": 0.0,
        "text_area_ratio": 0.0, "route": "blank", "native_dpi": None,
    }]
    count = max(len(parsed), 1)
    total_chars = sum(page["text_chars"] for page in parsed)
    avg_chars = total_chars / count
    has_text = avg_chars >= 50 or any(page["text_chars"] >= 15 for page in parsed)
    has_images = any(page["images"] for page in parsed)
    routes = [page["route"] for page in parsed]
    native_pages = routes.count("native")
    visual_pages = routes.count("visual")
    scan_pages = routes.count("scan")
    blank_pages = routes.count("blank")

    if has_text and not has_images:
        pdf_type = "text_based"
    elif has_text and has_images:
        pdf_type = "mixed"
    elif has_images:
        pdf_type = "scanned"
    else:
        pdf_type = "blank"

    if scan_pages > count / 2:
        document_kind = "scanned"
        recommended_route = "scan-ocr"
    elif visual_pages > count / 2:
        document_kind = "visual"
        recommended_route = "visual-layout"
    elif native_pages == count:
        document_kind = "document"
        recommended_route = "native-flow"
    elif blank_pages == count:
        document_kind = "blank"
        recommended_route = "skip"
    else:
        document_kind = "mixed"
        recommended_route = "page-routing"

    native_dpi = max([page["native_dpi"] or 0 for page in pages] or [0]) or None
    route_counts = {name: routes.count(name) for name in ("native", "visual", "scan", "blank")}
    return {
        "path": str(pdf_path),
        "file_size_mb": round(pdf_path.stat().st_size / 1048576, 2),
        "page_count": total_pages,
        "pages_parsed": len(pages),
        "pdf_type": pdf_type,
        "document_kind": document_kind,
        "recommended_route": recommended_route,
        "route_counts": route_counts,
        "avg_text_chars_per_page": round(avg_chars, 1),
        "native_dpi": native_dpi,
        "pages": pages,
        "suggest_dpi": int(min(300, max(150, round(native_dpi * 1.0)))) if native_dpi else 200,
    }


if __name__ == "__main__":
    import sys

    print(json.dumps(detect(sys.argv[1]), ensure_ascii=False, indent=2))

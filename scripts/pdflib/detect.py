# -*- coding: utf-8 -*-
"""PDF 类型检测：页数、文本层、扫描图分辨率、页面方向线索。"""
from __future__ import annotations

import json
from pathlib import Path

import pymupdf


def _page_native_dpi(page):
    """扫描图的原生 dpi（按页面上的图像像素与放置尺寸估算）。"""
    best = None
    for img in page.get_images(full=True):
        xref = img[0]
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
        iw, ih = info["width"], info["height"]
        # 直接放置 与 旋转 90 度放置，取两轴 dpi 更一致的那种
        cands = []
        for w_pt, h_pt, rotated in ((rect.width, rect.height, False),
                                    (rect.height, rect.width, True)):
            dpi_x = iw / (w_pt / 72.0)
            dpi_y = ih / (h_pt / 72.0)
            cands.append((abs(dpi_x - dpi_y), (dpi_x + dpi_y) / 2.0, rotated))
        cands.sort(key=lambda t: t[0])
        dpi = cands[0][1]
        if best is None or dpi > best:
            best = dpi
    return round(best, 1) if best else None


def detect(pdf_path, max_pages=None):
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError("PDF 不存在：" + str(pdf_path))
    doc = pymupdf.open(str(pdf_path))
    total_pages = doc.page_count
    pages = []
    for i, page in enumerate(doc):
        if max_pages and i >= max_pages:
            break
        text = page.get_text("text")
        pages.append({
            "page": i + 1,
            "width_pt": round(page.rect.width, 1),
            "height_pt": round(page.rect.height, 1),
            "rotation": page.rotation,
            "text_chars": len(text.strip()),
            "text_snippet": text.strip()[:80],
            "images": len(page.get_images(full=True)),
            "native_dpi": _page_native_dpi(page),
        })
    doc.close()

    total_chars = sum(p["text_chars"] for p in pages)
    n = max(len(pages), 1)
    avg_chars = total_chars / n
    has_text = avg_chars >= 50
    has_images = any(p["images"] for p in pages)
    if has_text and not has_images:
        pdf_type = "text_based"
    elif has_text and has_images:
        pdf_type = "mixed"
    else:
        pdf_type = "scanned"
    native_dpi = max([p["native_dpi"] or 0 for p in pages] or [0]) or None
    return {
        "path": str(pdf_path),
        "file_size_mb": round(pdf_path.stat().st_size / 1048576, 2),
        "page_count": total_pages,
        "pages_parsed": len(pages),
        "pdf_type": pdf_type,
        "avg_text_chars_per_page": round(avg_chars, 1),
        "native_dpi": native_dpi,
        "pages": pages,
        # 建议 OCR 渲染 dpi：原生分辨率（上限 300），至少 150
        "suggest_dpi": int(min(300, max(150, round(native_dpi * 1.0)))) if native_dpi else 200,
    }


if __name__ == "__main__":
    import sys
    print(json.dumps(detect(sys.argv[1]), ensure_ascii=False, indent=2))

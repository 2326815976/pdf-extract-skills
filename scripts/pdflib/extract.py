# -*- coding: utf-8 -*-
"""提取主流程：文本层直抽 或 渲染+OCR+表格重建，导出多格式。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pymupdf

from . import detect as detect_mod
from . import export, layout, tables
from .ocr import OcrEngine, RecEngine
from .render import prepare_upright_images


def _log(msg):
    print("[pdf] " + msg, flush=True)


def _text_layer_pages(pdf_path, max_pages=None):
    """有文本层：直抽文本 + 表格（不 OCR）。"""
    doc = pymupdf.open(str(pdf_path))
    pages, warnings = [], []
    for i, page in enumerate(doc):
        if max_pages and i >= max_pages:
            break
        texts, tables_found, table_rects = [], [], []
        try:
            found = page.find_tables().tables
            tables_found = [t.extract() for t in found]
            # 去掉表格区域后抽取正文，避免重复
            table_rects = [pymupdf.Rect(t.bbox) for t in found]
        except Exception:
            pass
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            rect = pymupdf.Rect(block["bbox"])
            if any(rect.intersects(tr) for tr in table_rects):
                continue
            para = []
            for line in block.get("lines", []):
                para.append("".join(span["text"] for span in line.get("spans", [])))
            text = "".join(para).strip()
            if text:
                texts.append(text)
        pages.append({
            "page": i + 1,
            "angle": 0,
            "text": "\n".join(texts),
            "tables": [rows for rows in tables_found if rows],
            "mode": "text",
        })
        if not texts and not tables_found:
            warnings.append("第 %d 页没有提取到文本" % (i + 1))
    doc.close()
    return pages, warnings


def _scanned_pages(pdf_path, render_dir, dpi, max_pages, progress=_log, recheck_orient=False):
    engine = OcrEngine()
    rec = RecEngine()
    progress("orientation detect + render (dpi=%s)" % dpi)
    t0 = time.time()
    images = prepare_upright_images(pdf_path, render_dir, dpi=dpi, max_pages=max_pages,
                                    rec=rec,
                                    cache_path=Path(render_dir) / "orientation.json",
                                    force_recheck=recheck_orient)
    progress("render done in %.1f s" % (time.time() - t0))

    pages, warnings = [], []
    for item in images:
        t1 = time.time()
        from PIL import Image
        with Image.open(item["path"]) as image:
            lines = engine.run(item["path"])
            recon = tables.reconstruct(lines, image)
        rows = recon["rows"] if recon["mode"] == "rules" or (
            recon["mode"] == "lines" and len(recon["rows"]) >= 2) else []
        text = ""
        if not rows:
            text = layout.lines_to_text(lines)
        if rows and recon["quality"].get("fill_ratio") is not None and recon["quality"]["fill_ratio"] < 0.15:
            warnings.append("第 %d 页表格填充率偏低（%.2f），建议人工抽查" % (item["page"], recon["quality"]["fill_ratio"]))
        detail = item.get("detail") or {}
        upright = detail.get("score_upright")
        flipped = detail.get("score_flipped")
        if upright is not None and flipped is not None:
            best, worst = max(upright, flipped), min(upright, flipped)
            # 识别到的文字太少，或两个方向得分接近，才提示人工抽查
            if best < 6 or (best < 20 and best < worst * 1.5):
                warnings.append("第 %d 页方向把握不大（正立 %.1f / 颠倒 %.1f），建议抽查"
                                % (item["page"], upright, flipped))
        pages.append({
            "page": item["page"],
            "angle": item["angle"],
            "image": item["path"],
            "text": text,
            "tables": [rows] if rows else [],
            "mode": "ocr-table" if rows else "ocr-text",
            "ocr_lines": len(lines),
            "grid_mode": recon["mode"],
            "quality": recon["quality"],
            "seconds": round(time.time() - t1, 2),
        })
        progress("page %d: %d lines, %s" % (item["page"], len(lines),
                                          ("table %dx%d" % (len(rows), len(rows[0]))) if rows else "text"))
    return pages, warnings


def _render_dir(out_dir):
    return Path(out_dir) / "pages"


def run(pdf_path, out_dir, formats=("md", "txt", "json", "xlsx", "docx", "csv"),
        dpi=None, max_pages=None, force_ocr=False, recheck_orient=False):
    t0 = time.time()
    pdf_path = Path(pdf_path).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = pdf_path.stem

    info = detect_mod.detect(pdf_path, max_pages=max_pages)
    _log("type=%s pages=%d native_dpi=%s" % (info["pdf_type"], info["page_count"], info["native_dpi"]))
    dpi = dpi or info["suggest_dpi"]

    warnings = []
    if info["pdf_type"] in ("text_based",) and not force_ocr:
        _log("text layer detected; extracting without OCR")
        pages, warns = _text_layer_pages(pdf_path, max_pages=max_pages)
        warnings.extend(warns)
        method = "text-layer"
        # 若几乎没抽到文本，自动回退 OCR
        if sum(len(p["text"]) + sum(len(r) for t in p["tables"] for r in t) for p in pages) < 50 * len(pages):
            _log("too little text; falling back to OCR")
            pages, warns = _scanned_pages(pdf_path, _render_dir(out_dir), dpi, max_pages, recheck_orient=recheck_orient)
            warnings.extend(warns)
            method = "ocr"
    else:
        pages, warns = _scanned_pages(pdf_path, _render_dir(out_dir), dpi, max_pages, recheck_orient=recheck_orient)
        warnings.extend(warns)
        method = "ocr"

    seconds = round(time.time() - t0, 2)
    meta = {
        "pdf": str(pdf_path),
        "page_count": info["page_count"],
        "pdf_type": info["pdf_type"],
        "method": method,
        "dpi": dpi,
        "seconds": seconds,
        "warnings": warnings,
        "native_dpi": info["native_dpi"],
    }

    produced = []
    if "md" in formats:
        p = out_dir / (stem + ".md")
        export.write_md(p, stem, meta, pages)
        produced.append(str(p))
    if "txt" in formats:
        p = out_dir / (stem + ".txt")
        export.write_txt(p, pages)
        produced.append(str(p))
    if "csv" in formats:
        rows = export.combined_rows(pages)
        if rows:
            p = out_dir / (stem + ".csv")
            export.write_csv(p, rows)
            produced.append(str(p))
    if "xlsx" in formats:
        try:
            p = out_dir / (stem + ".xlsx")
            export.write_xlsx(p, pages, stem)
            produced.append(str(p))
        except ImportError:
            warnings.append("缺少 openpyxl，跳过 xlsx 导出")
    if "docx" in formats:
        try:
            p = out_dir / (stem + ".docx")
            export.write_docx(p, stem, meta, pages)
            produced.append(str(p))
        except ImportError:
            warnings.append("缺少 python-docx，跳过 docx 导出")

    lines_json = out_dir / "lines.json"
    export.write_json(lines_json, {
        "pdf": str(pdf_path),
        "method": method,
        "pages": [{"page": p["page"], "angle": p.get("angle", 0),
                   "mode": p.get("mode"), "text": p.get("text", ""),
                   "tables": p.get("tables", [])} for p in pages],
    })
    produced.append(str(lines_json))
    tables_json = out_dir / "tables.json"
    export.write_json(tables_json, [{"page": p["page"], "tables": p.get("tables", [])} for p in pages])
    produced.append(str(tables_json))

    report = dict(meta)
    report.update({
        "outputs": produced,
        "pages": [{"page": p["page"], "mode": p.get("mode"), "angle": p.get("angle", 0),
                   "tables": len(p.get("tables") or []),
                   "rows": len((p.get("tables") or [[]])[0]) if p.get("tables") else 0,
                   "text_chars": len(p.get("text", ""))} for p in pages],
    })
    (out_dir / "run_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["outputs"].append(str(out_dir / "run_report.json"))
    return report
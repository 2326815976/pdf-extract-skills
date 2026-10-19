# -*- coding: utf-8 -*-
"""通用 PDF 提取主流程：页级路由、原生文本优先、局部或整页 OCR。"""
from __future__ import annotations

import math
import re
import time
from pathlib import Path

import pymupdf

from . import detect as detect_mod
from . import export, layout, tables
from .ocr import OcrEngine, RecEngine
from .render import prepare_upright_images, render_page

if hasattr(pymupdf, "no_recommend_layout"):
    pymupdf.no_recommend_layout()


def _log(message):
    print("[pdf] " + message, flush=True)


def _normalize_for_compare(text):
    return re.sub(r"[^\w\u3400-\u9fff]+", "", text or "", flags=re.UNICODE).lower()


def _color(value):
    value = int(value or 0)
    return "#%06X" % (value & 0xFFFFFF)


_SECTION_HEADINGS = {"教育背景", "专业技能", "实习经历", "项目经历", "主要荣誉"}


def _text_blocks(block):
    groups = []
    current = []

    def flush():
        if not current:
            return
        sizes = [item["size"] for item in current if item["size"]]
        fonts = [item["font"] for item in current if item["font"]]
        colors = [item["color"] for item in current if item["color"]]
        groups.append({
            "type": "text",
            "text": "\n".join(item["text"] for item in current).strip(),
            "bbox": [
                round(min(item["bbox"][0] for item in current), 2),
                round(min(item["bbox"][1] for item in current), 2),
                round(max(item["bbox"][2] for item in current), 2),
                round(max(item["bbox"][3] for item in current), 2),
            ],
            "style": {
                "size": round(max(sizes, default=0.0), 2),
                "font": next((font for font in fonts if "Bold" in font),
                             fonts[0] if fonts else ""),
                "bold": any(item["bold"] for item in current),
                "italic": any(item["italic"] for item in current),
                "color": next((color for color in colors
                               if color not in ("#000000", "#FFFFFF")), "#000000"),
            },
        })
        current.clear()

    for line in block.get("lines", []):
        spans = line.get("spans", [])
        line_text = "".join(span.get("text", "") for span in spans).strip()
        if not line_text:
            continue
        item = {
            "text": line_text,
            "bbox": line.get("bbox") or block["bbox"],
            "size": max((float(span.get("size") or 0) for span in spans), default=0.0),
            "font": next((str(span.get("font")) for span in spans if span.get("font")), ""),
            "bold": any(int(span.get("flags") or 0) & 16 for span in spans),
            "italic": any(int(span.get("flags") or 0) & 2 for span in spans),
            "color": next((_color(span.get("color")) for span in spans
                           if _color(span.get("color")) not in ("#000000", "#FFFFFF")),
                          "#000000"),
        }
        normalized = re.sub(r"\s+", "", line_text)
        if normalized in _SECTION_HEADINGS:
            flush()
            current.append(item)
            flush()
        else:
            current.append(item)
    flush()
    return [group for group in groups if group.get("text")]


def _native_table_signature(rows):
    values = [str(cell or "").strip() for row in rows for cell in row]
    nonempty = [value for value in values if value]
    if not nonempty:
        return {"largest_share": 1.0, "duplicate_ratio": 1.0,
                "header_hits": 0, "data_cells": 0, "populated_median": 0}
    lengths = [_normalize_for_compare(value) for value in nonempty]
    largest_share = max(map(len, lengths)) / max(sum(map(len, lengths)), 1)
    long_values = [value for value in lengths if len(value) >= 4]
    duplicate_ratio = (1.0 - (len(set(long_values)) / len(long_values))
                       if long_values else 0.0)
    header_text = "".join(nonempty[:max(1, len(rows[0]))])
    header_hits = sum(1 for word in tables.HEADER_WORDS if word in header_text)
    data_cells = sum(
        1 for value in nonempty
        if len(value) <= 60 and (re.search(r"\d", value) or re.search(
            r"[A-Za-z\u3400-\u9fff]{2,}", value))
    )
    populated_counts = [sum(1 for cell in row if str(cell or "").strip())
                        for row in rows]
    populated_counts.sort()
    populated_median = populated_counts[len(populated_counts) // 2]
    return {
        "largest_share": round(largest_share, 3),
        "duplicate_ratio": round(duplicate_ratio, 3),
        "header_hits": header_hits,
        "data_cells": data_cells,
        "populated_median": populated_median,
    }


def _native_page(doc, index, *, table_mode="auto"):
    page = doc[index]
    page_area = max(1.0, page.rect.width * page.rect.height)
    accepted_tables = []
    table_rects = []
    if table_mode != "off":
        try:
            found = page.find_tables().tables
        except Exception:
            found = []
        for table in found:
            try:
                rows = table.extract()
            except Exception:
                continue
            if not rows or len(rows) < 2:
                continue
            columns = max(len(row) for row in rows)
            filled = sum(1 for row in rows for cell in row if str(cell or "").strip())
            fill_ratio = filled / max(len(rows) * columns, 1)
            bbox = pymupdf.Rect(table.bbox)
            area_ratio = max(0.0, bbox.width * bbox.height) / page_area
            if columns < 2 or fill_ratio < 0.08:
                continue
            if table_mode != "force":
                if len(rows) < 3 or columns < 3:
                    continue
                if fill_ratio < 0.18 or area_ratio < 0.025:
                    continue
                signature = _native_table_signature(rows)
                if signature["largest_share"] > 0.50:
                    continue
                if signature["duplicate_ratio"] > 0.20:
                    continue
                if signature["populated_median"] < 2:
                    continue
                if signature["header_hits"] < 2 and signature["data_cells"] < max(3, columns):
                    continue
            else:
                signature = _native_table_signature(rows)
            accepted_tables.append({
                "rows": rows,
                "bbox": [round(value, 2) for value in table.bbox],
                "quality": {
                    "fill_ratio": round(fill_ratio, 3),
                    "area_ratio": round(area_ratio, 3),
                    "columns": columns,
                    "signature": signature,
                },
            })
            table_rects.append(bbox)

    blocks = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        rect = pymupdf.Rect(block["bbox"])
        if any(rect.intersects(table_rect) for table_rect in table_rects):
            continue
        blocks.extend(_text_blocks(block))

    for image in page.get_images(full=True):
        xref = image[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            continue
        for rect in rects:
            area_ratio = max(0.0, rect.width * rect.height) / page_area
            if area_ratio < 0.001:
                continue
            blocks.append({
                "type": "image",
                "xref": xref,
                "bbox": [round(value, 2) for value in rect],
                "area_ratio": round(area_ratio, 4),
            })

    return {
        "page": index + 1,
        "angle": 0,
        "text": layout.blocks_to_text(blocks),
        "blocks": blocks,
        "tables": [table["rows"] for table in accepted_tables],
        "table_details": accepted_tables,
        "mode": "native-table" if accepted_tables else "native",
        "native_text_chars": sum(len(block["text"]) for block in blocks
                                 if block["type"] == "text"),
    }


def _extra_ocr_blocks(lines, native_text):
    native_norm = _normalize_for_compare(native_text)
    kept_lines = []
    for line in lines:
        norm = _normalize_for_compare(line.get("text", ""))
        if len(norm) >= 6 and norm in native_norm:
            continue
        kept_lines.append(line)
    extra = layout.lines_to_blocks(kept_lines)
    for block in extra:
        block["style"] = {"size": 0, "font": "", "bold": False,
                          "italic": False, "color": "#000000"}
    return extra


def _ocr_page(record, image_path, engine, *, source_route, table_mode):
    from PIL import Image

    with Image.open(image_path) as image:
        lines = engine.run(image_path)
        recon = tables.reconstruct(lines, image)
    rows = recon["rows"]
    assessment = tables.assess_candidate(
        rows, recon, source_route=source_route, mode=table_mode)
    accepted = assessment["accepted"]

    native_text = record.get("text", "")
    native_blocks = record.get("blocks", [])
    if accepted:
        rules = recon.get("rules") or {}
        xs = rules.get("xs") or []
        ys = rules.get("ys") or []
        outside_lines = []
        if xs and ys:
            table_left, table_right = xs[0], xs[-1]
            table_top, table_bottom = ys[0], ys[-1]
            for line in lines:
                inside = (table_left <= line["cx"] <= table_right
                          and table_top <= line["cy"] <= table_bottom)
                if not inside:
                    outside_lines.append(line)
        extra_blocks = layout.lines_to_blocks(outside_lines)
        for block in extra_blocks:
            bbox = block.get("bbox") or (0, 0, 0, 0)
            if ys and bbox[1] < ys[0]:
                block["placement"] = "before"
            elif ys and bbox[3] > ys[-1]:
                block["placement"] = "after"
            else:
                block["placement"] = "side"
            block["style"] = {"size": 0, "font": "", "bold": False,
                              "italic": False, "color": "#000000"}
        outside_text = layout.blocks_to_text(extra_blocks)
        text = "\n\n".join(part for part in (native_text, outside_text) if part.strip())
        mode = "ocr-table"
    else:
        extra_blocks = _extra_ocr_blocks(lines, native_text)
        extra_text = layout.blocks_to_text(extra_blocks)
        text = "\n\n".join(part for part in (native_text, extra_text) if part.strip())
        mode = "ocr-text"

    confidence = (sum(float(line.get("conf") or 0.0) for line in lines) / len(lines)
                  if lines else 0.0)
    record.update({
        "text": text,
        "blocks": native_blocks + extra_blocks,
        "outside_blocks": extra_blocks if accepted else [],
        "tables": [rows] if accepted else [],
        "merges": [recon.get("quality", {}).get("merged_cells") or []] if accepted else [],
        "table_details": [{
            "rows": rows,
            "quality": recon.get("quality"),
            "rules": recon.get("rules"),
        }] if accepted else [],
        "mode": mode,
        "ocr_lines": len(lines),
        "ocr_confidence": round(confidence, 4),
        "grid_mode": recon.get("mode"),
        "table_assessment": assessment,
        "quality": recon.get("quality"),
    })


def _render_ocr_pages(pdf_path, page_records, render_dir, dpi, max_pages,
                      recheck_orient, warnings):
    scan_indices = [record["page"] - 1 for record in page_records
                    if record["route"] == "scan"]
    visual_indices = [record["page"] - 1 for record in page_records
                      if record["route"] == "visual"]
    rendered = {}
    if scan_indices:
        rec = RecEngine()
        _log("scan pages render + orientation: %s" % ",".join(str(i + 1) for i in scan_indices))
        items = prepare_upright_images(
            pdf_path, render_dir, dpi=dpi, max_pages=max_pages, rec=rec,
            cache_path=Path(render_dir) / "orientation.json",
            force_recheck=recheck_orient, page_indices=scan_indices)
        rendered.update({item["page"]: item for item in items})
        for item in items:
            detail = item.get("detail") or {}
            upright = detail.get("score_upright")
            flipped = detail.get("score_flipped")
            if upright is not None and flipped is not None:
                best = max(upright, flipped)
                if best < 6:
                    warnings.append("第 %d 页方向置信度偏低" % item["page"])

    if visual_indices:
        doc = pymupdf.open(str(pdf_path))
        try:
            for index in visual_indices:
                image = render_page(doc, index, dpi=dpi)
                path = Path(render_dir) / ("page-%02d.png" % (index + 1))
                path.parent.mkdir(parents=True, exist_ok=True)
                image.save(path)
                rendered[index + 1] = {
                    "page": index + 1,
                    "path": str(path),
                    "angle": 0,
                    "score": None,
                    "detail": {},
                    "width": image.width,
                    "height": image.height,
                }
        finally:
            doc.close()
    return rendered


def _render_dir(out_dir):
    return Path(out_dir) / "pages"


def _docx_mode(info, pages, requested):
    if requested != "auto":
        return requested
    if any((page.get("table_assessment") or {}).get("layout_preferred")
           for page in pages):
        return "layout"
    table_pages = sum(1 for page in pages if page.get("tables"))
    if pages and table_pages >= max(1, math.ceil(len(pages) / 2)):
        return "table"
    if info.get("document_kind") == "visual":
        return "layout"
    return "flow"


def run(pdf_path, out_dir, formats=("md", "txt", "json", "xlsx", "docx", "csv"),
        dpi=None, max_pages=None, force_ocr=False, recheck_orient=False,
        profile="auto", table_mode="auto", ocr_mode="auto", docx_mode="auto",
        conversion=False):
    t0 = time.time()
    timings = {}
    pdf_path = Path(pdf_path).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    render_dir = _render_dir(out_dir)
    render_dir.mkdir(parents=True, exist_ok=True)
    stem = pdf_path.stem
    formats = tuple(dict.fromkeys(str(fmt).strip().lower() for fmt in formats if str(fmt).strip()))

    info = detect_mod.detect(pdf_path, max_pages=max_pages)
    timings["detect_s"] = round(time.time() - t0, 2)
    _log("kind=%s route=%s pages=%s dpi=%s" % (
        info["document_kind"], info["recommended_route"], info["page_count"], info["suggest_dpi"]))
    dpi = int(dpi or info["suggest_dpi"])

    if profile == "text":
        table_mode = "off"
    elif profile == "table":
        table_mode = "force" if table_mode == "auto" else table_mode
    if force_ocr:
        ocr_mode = "force"

    page_infos = info["pages"][:max_pages] if max_pages else info["pages"]
    text_formats = bool(set(formats) & {"md", "txt", "json", "csv", "xlsx"})
    doc = pymupdf.open(str(pdf_path))
    records = []
    try:
        for page_info in page_infos:
            record = _native_page(doc, page_info["page"] - 1, table_mode=table_mode)
            record.update({
                "route": page_info["route"],
                "route_confidence": page_info["route_confidence"],
                "route_reason": page_info["route_reason"],
                "image_coverage": page_info["image_coverage"],
                "native_chars": page_info["text_chars"],
                "width_pt": page_info["width_pt"],
                "height_pt": page_info["height_pt"],
            })
            records.append(record)
    finally:
        doc.close()
    timings["native_s"] = round(time.time() - t0 - timings["detect_s"], 2)

    # 视觉文档的 layout DOCX 直接保留原 PDF 版面，不必为了 Word 转换先 OCR。
    layout_docx_only = (info.get("document_kind") == "visual"
                        and set(formats) <= {"docx"}
                        and docx_mode in ("auto", "layout")
                        and profile in ("auto", "visual")
                        and ocr_mode != "force")
    target_ocr = []
    if ocr_mode != "off":
        for record in records:
            if layout_docx_only and record["route"] == "visual":
                continue
            if ocr_mode == "force":
                target_ocr.append(record)
            elif record["route"] in ("scan", "visual"):
                target_ocr.append(record)
            elif record["route"] == "native" and record["native_text_chars"] < 15:
                target_ocr.append(record)
    elif text_formats:
        low_text = [record["page"] for record in records
                    if record["route"] in ("scan", "visual")
                    and record["native_text_chars"] < 15]
        if low_text:
            raise RuntimeError("OCR 已关闭，但以下页面没有足够文本层：" +
                               ", ".join(map(str, low_text)))

    warnings = []
    if target_ocr:
        rendered = _render_ocr_pages(
            pdf_path, target_ocr, render_dir, dpi, max_pages, recheck_orient, warnings)
        engine = OcrEngine()
        for index, record in enumerate(target_ocr, 1):
            item = rendered.get(record["page"])
            if item is None:
                warnings.append("第 %d 页渲染失败，未执行 OCR" % record["page"])
                continue
            page_t0 = time.time()
            try:
                _ocr_page(record, item["path"], engine,
                          source_route=record["route"], table_mode=table_mode)
                record["angle"] = item.get("angle", 0)
                record["seconds"] = round(time.time() - page_t0, 2)
                _log("page %d: %s, ocr_lines=%s, %.1fs" % (
                    record["page"], record["mode"], record.get("ocr_lines", 0),
                    record["seconds"]))
            except Exception as exc:
                warnings.append("第 %d 页 OCR 失败：%s" % (record["page"], exc))
                record["mode"] = "ocr-error"
    timings["ocr_s"] = round(max(
        0.0, time.time() - t0 - timings["detect_s"] - timings["native_s"]), 2)

    for record in records:
        if not record.get("text") and not record.get("tables"):
            warnings.append("第 %d 页没有提取到内容" % record["page"])

    selected_docx_mode = _docx_mode(info, records, docx_mode)
    if selected_docx_mode == "layout" and any(
            (record.get("table_assessment") or {}).get("layout_preferred")
            for record in records):
        warnings.append("检测到复杂扫描表单，Word 自动采用版面保真模式")
    table_pages = [record["page"] for record in records if record.get("tables")]
    ocr_pages = [record["page"] for record in records if record.get("ocr_lines")]
    route_plan = {
        "document_kind": info["document_kind"],
        "recommended_route": info["recommended_route"],
        "route_counts": info["route_counts"],
        "page_routes": [{
            "page": record["page"],
            "route": record["route"],
            "mode": record["mode"],
            "confidence": record.get("route_confidence"),
            "reason": record.get("route_reason"),
        } for record in records],
        "ocr_pages": ocr_pages,
        "table_pages": table_pages,
        "docx_mode": selected_docx_mode,
    }

    meta = {
        "pdf": str(pdf_path),
        "page_count": info["page_count"],
        "pages_processed": len(records),
        "pdf_type": info["pdf_type"],
        "document_kind": info["document_kind"],
        "method": "page-routing",
        "dpi": dpi,
        "seconds": round(time.time() - t0, 2),
        "warnings": warnings,
        "native_dpi": info["native_dpi"],
        "route": route_plan,
        "timings": timings,
    }

    produced = []
    if "md" in formats:
        p = out_dir / (stem + ".md")
        export.write_md(p, stem, meta, records)
        produced.append(str(p))
    if "txt" in formats:
        p = out_dir / (stem + ".txt")
        export.write_txt(p, records)
        produced.append(str(p))
    if "csv" in formats:
        rows = export.combined_rows(records)
        if rows:
            p = out_dir / (stem + ".csv")
            export.write_csv(p, rows)
            produced.append(str(p))
    if "xlsx" in formats:
        try:
            p = out_dir / (stem + ".xlsx")
            export.write_xlsx(p, records, stem)
            produced.append(str(p))
        except ImportError:
            warnings.append("缺少 openpyxl，跳过 xlsx 导出")
    if "docx" in formats:
        p = out_dir / (stem + ".docx")
        export.write_docx(p, stem, meta, records, pdf_path=pdf_path,
                          mode=selected_docx_mode, max_pages=max_pages)
        produced.append(str(p))

    lines_json = out_dir / "lines.json"
    export.write_json(lines_json, {
        "pdf": str(pdf_path),
        "method": meta["method"],
        "route": route_plan,
        "pages": [{
            "page": record["page"],
            "route": record["route"],
            "angle": record.get("angle", 0),
            "mode": record.get("mode"),
            "text": record.get("text", ""),
            "blocks": record.get("blocks", []),
            "tables": record.get("tables", []),
            "merges": record.get("merges", []),
            "quality": record.get("quality"),
            "table_assessment": record.get("table_assessment"),
        } for record in records],
    })
    produced.append(str(lines_json))
    tables_json = out_dir / "tables.json"
    export.write_json(tables_json, [{
        "page": record["page"],
        "tables": record.get("tables", []),
        "merges": record.get("merges", []),
    } for record in records])
    produced.append(str(tables_json))
    route_json = out_dir / "route.json"
    export.write_json(route_json, route_plan)
    produced.append(str(route_json))

    report = dict(meta)
    report["seconds"] = round(time.time() - t0, 2)
    report.update({
        "outputs": produced,
        "route": route_plan,
        "pages": [{
            "page": record["page"],
            "route": record["route"],
            "mode": record.get("mode"),
            "angle": record.get("angle", 0),
            "tables": len(record.get("tables") or []),
            "rows": len((record.get("tables") or [[]])[0]) if record.get("tables") else 0,
            "text_chars": len(record.get("text", "")),
            "ocr_lines": record.get("ocr_lines", 0),
            "ocr_confidence": record.get("ocr_confidence"),
            "table_assessment": record.get("table_assessment"),
            "quality": record.get("quality"),
            "seconds": record.get("seconds"),
        } for record in records],
    })
    if conversion:
        prompt = "转换已完成。是否需要我继续校验并给出转换准确度？"
        report["conversion"] = True
        report["verification_prompt"] = prompt
        report["next_action"] = {
            "type": "ask_verification",
            "prompt": prompt,
        }
    report_path = out_dir / "run_report.json"
    report_path.write_text(__import__("json").dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    report["outputs"].append(str(report_path))
    timings["total_s"] = report["seconds"]
    return report

# -*- coding: utf-8 -*-
"""导出：Markdown / TXT / CSV / XLSX / DOCX。

DOCX 使用三种策略：
- flow: 重建标题、段落、列表和图片，适合正文型 PDF；
- layout: 使用 pdf2docx 保留截图、分栏和复杂版面；
- table: 将 OCR/原生表格输出为可编辑 Word 表格。
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
import zipfile
from pathlib import Path

import pymupdf

from . import layout
from .render import render_page


def _md_cell(text):
    return str(text or "").replace("|", "\\|").replace("\n", "<br>").strip()


def md_table(rows):
    if not rows:
        return ""
    columns = max(len(row) for row in rows)
    rows = [list(row) + [""] * (columns - len(row)) for row in rows]
    lines = [
        "| " + " | ".join(_md_cell(cell) for cell in rows[0]) + " |",
        "|" + "|".join(["---"] * columns) + "|",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(_md_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


def page_tables(page):
    if page.get("tables"):
        return [table for table in page["tables"] if table]
    if page.get("table_rows"):
        return [page["table_rows"]]
    return []


def write_md(path, stem, info, pages):
    parts = ["# " + stem, ""]
    parts.append("- 来源：" + info["pdf"])
    parts.append("- 页数：%d ｜ 路由：%s" % (
        info["page_count"], info.get("route", {}).get("recommended_route", "")))
    if info.get("warnings"):
        parts.append("- 注意：" + "；".join(info["warnings"]))
    parts.append("")
    for page in pages:
        parts.append("## 第 %d 页（%s）" % (page["page"], page.get("mode", page.get("route", ""))))
        parts.append("")
        for table in page_tables(page):
            parts.append(md_table(table))
            parts.append("")
        if page.get("text"):
            parts.append(page["text"])
            parts.append("")
    Path(path).write_text("\n".join(parts), encoding="utf-8")


def write_txt(path, pages):
    parts = []
    for page in pages:
        parts.append("===== 第 %d 页 =====" % page["page"])
        for table in page_tables(page):
            for row in table:
                parts.append("\t".join(str(cell) for cell in row))
            parts.append("")
        if page.get("text"):
            parts.append(page["text"])
        parts.append("")
    Path(path).write_text("\n".join(parts), encoding="utf-8")


def write_json(path, payload):
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


_HEADER_HINTS = (
    "序号", "单位名称", "坐落位置", "收租时间节点", "使用情况", "使用人",
    "月租金", "应收租金", "减免租金", "合计", "备注", "名称", "金额",
)
_FLOW_HEADINGS = {"教育背景", "专业技能", "实习经历", "项目经历", "主要荣誉"}


def _is_header_row(row):
    text = "".join(str(cell) for cell in row)
    return sum(1 for word in _HEADER_HINTS if word in text) >= 2


def combined_rows(pages):
    all_rows, header = [], None
    for page_index, page in enumerate(pages):
        for table_index, rows in enumerate(page_tables(page)):
            if not rows:
                continue
            if page_index > 0 and table_index == 0 and _is_header_row(rows[0]):
                rows = rows[1:]
                if not rows:
                    continue
            if header is None:
                header = [str(cell).strip() for cell in rows[0]]
                all_rows.extend(rows)
            elif [str(cell).strip() for cell in rows[0]] == header:
                all_rows.extend(rows[1:])
            else:
                all_rows.extend(rows)
    return all_rows


def write_csv(path, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        for row in rows:
            writer.writerow([str(cell) for cell in row])


def write_xlsx(path, pages, stem):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    workbook.remove(workbook.active)
    for page in pages:
        tables = page_tables(page)
        rows = []
        for table in tables:
            rows.extend(table)
            rows.append([])
        if not rows and page.get("text"):
            rows = [[line] for line in page["text"].splitlines() if line.strip()]
        worksheet = workbook.create_sheet("P%02d" % page["page"])
        for row in rows:
            worksheet.append([str(cell) for cell in row])
        if rows and rows[0]:
            for cell in worksheet[1]:
                cell.font = Font(bold=True)
        widths = {}
        for row in rows:
            for index, cell in enumerate(row):
                widths[index] = min(60, max(widths.get(index, 8), len(str(cell)) + 2))
        for index, width in widths.items():
            worksheet.column_dimensions[
                worksheet.cell(row=1, column=index + 1).column_letter].width = width
        worksheet.freeze_panes = "A2"
    rows = combined_rows(pages)
    if rows:
        worksheet = workbook.create_sheet("汇总")
        for row in rows:
            worksheet.append([str(cell) for cell in row])
        if rows[0]:
            for cell in worksheet[1]:
                cell.font = Font(bold=True)
        worksheet.freeze_panes = "A2"
    workbook.save(str(path))


def _set_cell_shading(cell, fill):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    shading = tc_pr.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        tc_pr.append(shading)
    shading.set(qn("w:fill"), fill)


def _remove_table_borders(table):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "start", "bottom", "end", "insideH", "insideV"):
        node = borders.find(qn("w:" + edge))
        if node is None:
            node = OxmlElement("w:" + edge)
            borders.append(node)
        node.set(qn("w:val"), "nil")


def _configure_document(document, *, landscape=False, page_size_pt=None):
    from docx.enum.section import WD_ORIENT
    from docx.enum.text import WD_LINE_SPACING
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt

    section = document.sections[0]
    if page_size_pt and not landscape:
        width_pt, height_pt = page_size_pt
        section.page_width = Mm(width_pt / 72.0 * 25.4)
        section.page_height = Mm(height_pt / 72.0 * 25.4)
    elif landscape:
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width = Mm(297)
        section.page_height = Mm(210)
    else:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Mm(210)
        section.page_height = Mm(297)
    section.top_margin = Mm(6)
    section.bottom_margin = Mm(6)
    section.left_margin = Mm(10)
    section.right_margin = Mm(10)
    normal = document.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(9.5)
    normal.paragraph_format.space_after = Pt(2)
    normal.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE


def _set_run_style(run, *, size, bold=False, italic=False, color=None,
                   font="Microsoft YaHei"):
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    run.font.name = font
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    if size:
        run.font.size = Pt(size)
    run.font.bold = bool(bold)
    run.font.italic = bool(italic)
    if color:
        try:
            run.font.color.rgb = RGBColor.from_string(color.lstrip("#"))
        except Exception:
            pass


def _extract_image_bytes(pdf_doc, xref):
    try:
        return pdf_doc.extract_image(xref).get("image")
    except Exception:
        return None


def _add_flow_block(document, block, pdf_doc, median_size):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt

    block_type = block.get("type")
    if block_type == "image":
        image = _extract_image_bytes(pdf_doc, block.get("xref"))
        if not image:
            return False
        bbox = block.get("bbox") or (0, 0, 72, 72)
        width_inches = min(6.0, max(0.55, (bbox[2] - bbox[0]) / 72.0))
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_before = Pt(2)
        paragraph.paragraph_format.space_after = Pt(3)
        paragraph.add_run().add_picture(io.BytesIO(image), width=Inches(width_inches))
        return True
    if block_type == "table":
        rows = block.get("rows") or []
        if not rows:
            return False
        columns = max(len(row) for row in rows)
        table = document.add_table(rows=len(rows), cols=columns)
        table.style = "Table Grid"
        for row_index, row in enumerate(rows):
            for column_index in range(columns):
                value = str(row[column_index]) if column_index < len(row) else ""
                cell = table.cell(row_index, column_index)
                cell.text = value
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        _set_run_style(run, size=7.5)
                if row_index == 0:
                    _set_cell_shading(cell, "D9EEF3")
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            run.font.bold = True
        document.add_paragraph().paragraph_format.space_after = Pt(1)
        return True
    if block_type != "text":
        return False
    text = str(block.get("text") or "").strip()
    if not text:
        return False
    compact = re.sub(r"\s+", " ", text)
    style = block.get("style") or {}
    size = float(style.get("size") or 0.0)
    bold = bool(style.get("bold"))
    italic = bool(style.get("italic"))
    color = style.get("color") or "#000000"
    is_heading = (
        size >= max(11.0, median_size * 1.28)
        or (bold and len(compact) <= 40 and size >= median_size * 1.08)
        or compact in _FLOW_HEADINGS
    )
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(2 if is_heading else 0.8)
    paragraph.paragraph_format.keep_with_next = is_heading
    paragraph.paragraph_format.line_spacing = 1.0 if is_heading else 0.95
    if is_heading:
        paragraph.paragraph_format.line_spacing = 1.0
    bullet = re.match(r"^([•·▪◦●\-]|\d+[、.)])\s*", compact)
    if bullet:
        paragraph.paragraph_format.left_indent = Inches(0.18)
        paragraph.paragraph_format.first_line_indent = Inches(-0.16)
    line_text = compact if "\n" not in text else text
    run = paragraph.add_run(line_text)
    _set_run_style(
        run,
        size=min(24, max(8.0, size * 0.80)) if size else 9.2,
        bold=bold or is_heading,
        italic=italic,
        color=color,
    )
    return True


def _add_text_to_cell(cell, blocks, median_size):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    cell.text = ""
    first = True
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        style = block.get("style") or {}
        size = float(style.get("size") or 0.0)
        compact = re.sub(r"\s+", " ", text)
        size = min(18, max(7.0, size * 0.66)) if size else 8.3
        paragraph = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0.8)
        paragraph.paragraph_format.line_spacing = 0.92
        run = paragraph.add_run(compact)
        _set_run_style(
            run,
            size=size,
            bold=bool(style.get("bold")) or size >= 11,
            italic=bool(style.get("italic")),
            color=style.get("color") or "#000000",
        )


def _add_side_by_side(document, group, pdf_doc, median_size):
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt

    table = document.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    _remove_table_borders(table)
    widths = (Inches(5.05), Inches(1.75))
    for index, width in enumerate(widths):
        table.columns[index].width = width
        table.cell(0, index).width = width
    left, right = table.rows[0].cells
    left.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    right.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    _add_text_to_cell(left, group["texts"], median_size)
    right.text = ""
    paragraph = right.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_after = Pt(0)
    images = group["images"]
    image_width = min(1.25, 1.65 / max(len(images), 1))
    for image_index, image_block in enumerate(images):
        data = _extract_image_bytes(pdf_doc, image_block.get("xref"))
        if not data:
            continue
        if image_index:
            paragraph.add_run(" ")
        paragraph.add_run().add_picture(io.BytesIO(data), width=Inches(image_width))
    spacer = document.add_paragraph()
    spacer.paragraph_format.space_after = Pt(0)
    spacer.paragraph_format.line_spacing = Pt(2)


def _write_flow_docx(path, stem, info, pages, pdf_path, max_pages=None):
    from docx import Document
    from docx.shared import Pt

    document = Document()
    first_page = pages[0] if pages else {}
    page_size = None
    if first_page.get("width_pt") and first_page.get("height_pt"):
        page_size = (first_page["width_pt"], first_page["height_pt"])
    _configure_document(document, landscape=False, page_size_pt=page_size)
    all_sizes = [
        float(block.get("style", {}).get("size") or 0)
        for page in pages for block in page.get("blocks", [])
        if block.get("type") == "text" and block.get("style", {}).get("size")
    ]
    all_sizes.sort()
    median_size = all_sizes[len(all_sizes) // 2] if all_sizes else 10.0
    pdf_doc = pymupdf.open(str(pdf_path))
    wrote_any = False
    try:
        for page_index, page in enumerate(pages):
            elements = list(page.get("blocks") or [])
            for table_index, detail in enumerate(page.get("table_details") or []):
                tables_for_page = page.get("tables") or []
                elements.append({
                    "type": "table",
                    "rows": tables_for_page[table_index] if table_index < len(tables_for_page) else [],
                    "bbox": detail.get("bbox"),
                })
            ordered = layout.flow_groups(elements)
            if not ordered:
                continue
            if wrote_any:
                document.add_page_break()
            wrote_any = True
            for group in ordered:
                if group.get("type") == "side_by_side":
                    _add_side_by_side(document, group, pdf_doc, median_size)
                else:
                    _add_flow_block(document, group["block"], pdf_doc, median_size)
            if page_index + 1 == len(pages):
                break
    finally:
        pdf_doc.close()
    if not wrote_any:
        paragraph = document.add_paragraph(stem)
        for run in paragraph.runs:
            _set_run_style(run, size=14, bold=True)
    document.core_properties.title = stem
    document.core_properties.subject = "PDF flow conversion"
    document.save(str(path))


def _write_table_docx(path, stem, pages):
    from docx import Document
    from docx.enum.table import WD_ROW_HEIGHT_RULE
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    table_rows = [table for page in pages for table in page_tables(page)]
    max_columns = max((max((len(row) for row in table), default=0)
                       for table in table_rows), default=0)
    complex_form = any(
        (page.get("quality") or {}).get("merged_grid_used") for page in pages)
    document = Document()
    _configure_document(document, landscape=max_columns > 6 and not complex_form)
    section = document.sections[0]
    writable = []
    for page in pages:
        page_merges = page.get("merges") or []
        for table_index, table in enumerate(page_tables(page)):
            merges = page_merges[table_index] if table_index < len(page_merges) else []
            writable.append((page, table, merges))
    last_page_number = None
    for table_index, (page, rows, merges) in enumerate(writable):
        page_break_pending = bool(table_index and page["page"] != last_page_number)
        if page["page"] != last_page_number:
            outside_blocks = page.get("outside_blocks")
            if outside_blocks is None:
                outside_blocks = [
                    {"text": part.strip(), "placement": "before", "bbox": [0, 0, 0, 0]}
                    for part in str(page.get("text") or "").split("\n\n")
                    if part.strip()
                ]
            before = sorted(
                (block for block in outside_blocks if block.get("placement") == "before"),
                key=lambda block: (block.get("bbox", [0, 0])[1], block.get("bbox", [0, 0])[0]))
            for block_index, block in enumerate(before):
                paragraph = document.add_paragraph()
                if page_break_pending:
                    paragraph.paragraph_format.page_break_before = True
                    page_break_pending = False
                paragraph.alignment = (
                    WD_ALIGN_PARAGRAPH.CENTER
                    if page["page"] == 1 and block_index == 0 else WD_ALIGN_PARAGRAPH.LEFT)
                paragraph.paragraph_format.space_after = Pt(2)
                run = paragraph.add_run(str(block.get("text") or "").strip())
                _set_run_style(
                    run,
                    size=14 if page["page"] == 1 and block_index == 0 else 8.5,
                    bold=page["page"] == 1 and block_index == 0,
                )
            if len(pages) > 1:
                heading = document.add_paragraph()
                if page_break_pending:
                    heading.paragraph_format.page_break_before = True
                    page_break_pending = False
                run = heading.add_run("第 %d 页" % page["page"])
                _set_run_style(run, size=9, bold=True, color="#4E6B78")
        columns = max(len(row) for row in rows)
        table = document.add_table(rows=len(rows), cols=columns)
        table.style = "Table Grid"
        quality = page.get("quality") or {}
        column_ratios = quality.get("column_ratios") or []
        row_ratios = quality.get("row_ratios") or []
        fixed_form_layout = bool(
            quality.get("merged_grid_used")
            and len(column_ratios) == columns
            and len(row_ratios) == len(rows))
        if fixed_form_layout:
            table.autofit = False
            content_width_cm = (section.page_width.cm
                                - section.left_margin.cm
                                - section.right_margin.cm)
            max_height_cm = (section.page_height.cm
                             - section.top_margin.cm
                             - section.bottom_margin.cm - 2.2)
            source_height_cm = content_width_cm * float(
                quality.get("table_aspect") or 1.0)
            table_height_cm = min(max_height_cm, source_height_cm)
            for column_index, ratio in enumerate(column_ratios):
                width = Cm(content_width_cm * float(ratio))
                table.columns[column_index].width = width
                for row in table.rows:
                    row.cells[column_index].width = width
            for row_index, ratio in enumerate(row_ratios):
                table.rows[row_index].height = Cm(table_height_cm * float(ratio))
                table.rows[row_index].height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
        else:
            table.autofit = True
        table_font_size = 6.5 if columns >= 12 else 7.2
        for row_index, row in enumerate(rows):
            for column_index in range(columns):
                cell = table.cell(row_index, column_index)
                cell.text = str(row[column_index]) if column_index < len(row) else ""
                for paragraph in cell.paragraphs:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    paragraph.paragraph_format.space_before = Pt(0)
                    paragraph.paragraph_format.space_after = Pt(0)
                    paragraph.paragraph_format.line_spacing = 0.9
                    for cell_run in paragraph.runs:
                        _set_run_style(cell_run, size=table_font_size,
                                       bold=row_index == 0)
                if row_index == 0:
                    _set_cell_shading(cell, "D9EEF3")
        for region in merges:
            top = int(region.get("top", 0))
            bottom = int(region.get("bottom", top + 1))
            left = int(region.get("left", 0))
            right = int(region.get("right", left + 1))
            if top < 0 or left < 0 or bottom > len(rows) or right > columns:
                continue
            if bottom - top <= 1 and right - left <= 1:
                continue
            table.cell(top, left).merge(table.cell(bottom - 1, right - 1))
        after = sorted(
            (block for block in (page.get("outside_blocks") or [])
             if block.get("placement") in ("side", "after")),
            key=lambda block: (block.get("bbox", [0, 0])[1], block.get("bbox", [0, 0])[0]))
        for block in after:
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(2)
            paragraph.paragraph_format.space_after = Pt(1)
            run = paragraph.add_run(str(block.get("text") or "").strip())
            _set_run_style(run, size=8)
        last_page_number = page["page"]
    if not writable:
        paragraph = document.add_paragraph(stem)
        for run in paragraph.runs:
            _set_run_style(run, size=14, bold=True)
    document.core_properties.title = stem
    document.core_properties.subject = "PDF table conversion"
    document.save(str(path))


def _write_image_docx(path, stem, pdf_path, max_pages=None):
    from docx import Document
    from docx.shared import Mm

    document = Document()
    section = document.sections[0]
    document.core_properties.title = stem
    pdf_doc = pymupdf.open(str(pdf_path))
    try:
        limit = min(len(pdf_doc), max_pages or len(pdf_doc))
        for index in range(limit):
            page = pdf_doc[index]
            image = render_page(pdf_doc, index, dpi=150)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            buffer.seek(0)
            if index:
                document.add_page_break()
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_after = 0
            paragraph.add_run().add_picture(buffer, width=Mm(185))
    finally:
        pdf_doc.close()
    document.save(str(path))


def _validate_docx(path):
    from docx import Document

    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            return False
    document = Document(str(path))
    return bool(document.element.body is not None)


def write_docx(path, stem, info, pages, *, pdf_path, mode="flow", max_pages=None):
    path = Path(path)
    if mode == "layout":
        try:
            from pdf2docx import Converter

            converter = Converter(str(pdf_path))
            try:
                converter.convert(str(path), start=0,
                                  end=max_pages if max_pages else None)
            finally:
                converter.close()
            if _validate_docx(path):
                return
        except Exception:
            pass
    if mode == "image":
        _write_image_docx(path, stem, pdf_path, max_pages=max_pages)
        return
    if mode == "table":
        _write_table_docx(path, stem, pages)
    else:
        _write_flow_docx(path, stem, info, pages, pdf_path, max_pages=max_pages)
    if not _validate_docx(path):
        _write_image_docx(path, stem, pdf_path, max_pages=max_pages)

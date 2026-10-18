# -*- coding: utf-8 -*-
"""导出：Markdown / TXT / JSON / CSV / XLSX / DOCX。"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path


def _md_cell(text):
    return str(text or "").replace("|", "\\|").replace("\n", "<br>").strip()


def md_table(rows):
    if not rows:
        return ""
    cols = max(len(r) for r in rows)
    rows = [list(r) + [""] * (cols - len(r)) for r in rows]
    lines = ["| " + " | ".join(_md_cell(c) for c in rows[0]) + " |",
             "|" + "|".join(["---"] * cols) + "|"]
    for r in rows[1:]:
        lines.append("| " + " | ".join(_md_cell(c) for c in r) + " |")
    return "\n".join(lines)


def page_tables(page):
    """支持 page["tables"]（多张表）与旧字段 page["table_rows"]。"""
    if page.get("tables"):
        return [t for t in page["tables"] if t]
    if page.get("table_rows"):
        return [page["table_rows"]]
    return []


def write_md(path, stem, info, pages):
    parts = ["# " + stem, ""]
    parts.append("- 来源：" + info["pdf"])
    parts.append("- 页数：%d ｜ PDF 类型：%s ｜ 提取方式：%s" % (
        info["page_count"], info["pdf_type"], info["method"]))
    parts.append("- 生成时间：%s ｜ 耗时：%.1f s" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), info.get("seconds", 0)))
    if info.get("warnings"):
        parts.append("- 注意：" + "；".join(info["warnings"]))
    parts.append("")
    for page in pages:
        parts.append("## 第 %d 页（旋转 %s°）" % (page["page"], page.get("angle", 0)))
        parts.append("")
        tables = page_tables(page)
        if tables:
            for idx, table in enumerate(tables):
                if len(tables) > 1:
                    parts.append("### 表 %d" % (idx + 1))
                    parts.append("")
                parts.append(md_table(table))
                parts.append("")
        if page.get("text"):
            parts.append(page.get("text"))
        parts.append("")
    Path(path).write_text("\n".join(parts), encoding="utf-8")


def write_txt(path, pages):
    parts = []
    for page in pages:
        parts.append("===== 第 %d 页 =====" % page["page"])
        for table in page_tables(page):
            for row in table:
                parts.append("\t".join(str(c) for c in row))
            parts.append("")
        if page.get("text"):
            parts.append(page.get("text"))
        parts.append("")
    Path(path).write_text("\n".join(parts), encoding="utf-8")


def write_json(path, payload):
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


_HEADER_HINTS = ("序号", "单位名称", "坐落位置", "收租时间节点", "使用情况", "使用人",
                  "月租金", "应收租金", "减免租金", "合计", "备注", "名称", "金额")


def _is_header_row(row):
    text = "".join(str(c) for c in row)
    return sum(1 for w in _HEADER_HINTS if w in text) >= 2


def combined_rows(pages):
    """把各页表格拼成一张表；每页重复的表头只保留第一页的那一行。"""
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
                header = [str(c).strip() for c in rows[0]]
                all_rows.extend(rows)
            else:
                if [str(c).strip() for c in rows[0]] == header:
                    all_rows.extend(rows[1:])
                else:
                    all_rows.extend(rows)
    return all_rows


def write_csv(path, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        for row in rows:
            writer.writerow([str(c) for c in row])


def write_xlsx(path, pages, stem):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    wb.remove(wb.active)
    for page in pages:
        tables = page_tables(page)
        rows = []
        for table in tables:
            rows.extend(table)
            rows.append([])
        if not rows:
            rows = [[page.get("text", "")]]
        ws = wb.create_sheet("P%02d" % page["page"])
        for r in rows:
            ws.append([str(c) for c in r])
        if rows and rows[0]:
            for cell in ws[1]:
                cell.font = Font(bold=True)
        widths = {}
        for r in rows:
            for i, c in enumerate(r):
                widths[i] = min(50, max(widths.get(i, 8), len(str(c)) + 2))
        for i, w in widths.items():
            ws.column_dimensions[ws.cell(row=1, column=i + 1).column_letter].width = w
        ws.freeze_panes = "A2"
    rows = combined_rows(pages)
    if rows:
        ws = wb.create_sheet("汇总")
        for r in rows:
            ws.append([str(c) for c in r])
        if rows[0]:
            for cell in ws[1]:
                cell.font = Font(bold=True)
        widths = {}
        for r in rows:
            for i, c in enumerate(r):
                widths[i] = min(50, max(widths.get(i, 8), len(str(c)) + 2))
        for i, w in widths.items():
            ws.column_dimensions[ws.cell(row=1, column=i + 1).column_letter].width = w
        ws.freeze_panes = "A2"
    wb.save(str(path))


def write_docx(path, stem, info, pages):
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.add_heading(stem, level=0)
    meta = doc.add_paragraph()
    run = meta.add_run("来源：%s ｜ 页数：%d ｜ 类型：%s ｜ 方式：%s" % (
        info["pdf"], info["page_count"], info["pdf_type"], info["method"]))
    run.font.size = Pt(9)
    for page in pages:
        doc.add_heading("第 %d 页" % page["page"], level=1)
        tables = page_tables(page)
        wrote = False
        for rows in tables:
            if rows and len(rows) > 1 and len(rows[0]) > 1:
                table = doc.add_table(rows=len(rows), cols=len(rows[0]))
                table.style = "Table Grid"
                for i, row in enumerate(rows):
                    for j, cell in enumerate(row):
                        table.cell(i, j).text = str(cell)
                if len(rows[0]) > 6:
                    table.autofit = True
                wrote = True
        for para in (page.get("text") or "").split("\n"):
            if para.strip():
                doc.add_paragraph(para)
                wrote = True
        if not wrote:
            doc.add_paragraph("（本页没有可提取的内容）")
    doc.save(str(path))

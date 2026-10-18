# -*- coding: utf-8 -*-
"""表格比对：提取结果 vs 基准（Excel/CSV/JSON/Markdown 表格）。"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from pathlib import Path


def load_table(path):
    """读取二维表，返回 (rows, source_desc)。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = [[c for c in row] for row in csv.reader(fh)]
        return [r for r in rows if any(str(c).strip() for c in r)], "csv"
    if suffix in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(str(path), data_only=True, read_only=True)
        best = []
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                values = ["" if v is None else str(v) for v in row]
                if any(v.strip() for v in values):
                    rows.append(values)
            if len(rows) > len(best):
                best = rows
        return best, "xlsx"
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key in ("rows", "table", "data", "cells"):
                if key in data and isinstance(data[key], list):
                    data = data[key]
                    break
        if data and isinstance(data[0], dict):
            keys = list(data[0].keys())
            return [[str(r.get(k, "")) for k in keys] for r in data], "json"
        return [[str(c) for c in row] for row in data], "json"
    if suffix in (".md", ".txt"):
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("|") and line.endswith("|"):
                cells = [c.strip().replace("\\|", "|") for c in line[1:-1].split("|")]
                if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    continue
                rows.append(cells)
        return rows, "md"
    raise ValueError("不支持的表格式：" + suffix)


def norm(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.replace("\u3000", " ").strip()
    text = re.sub(r"\s+", "", text)
    text = text.replace("，", ",").replace("。", ".").replace("；", ";")
    text = text.replace("（", "(").replace("）", ")").replace("：", ":")
    return text


def to_num(text):
    t = norm(text).replace(",", "")
    t = re.sub(r"[^\d.\-]", "", t)
    if not t or t in {"-", ".", "-."}:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def compare_cells(base, got):
    b, g = norm(base), norm(got)
    if not b and not g:
        return "一致", ""
    if not g:
        return "缺失", "PDF 侧为空"
    if not b:
        return "多余", "基准为空"
    if b == g:
        return "一致", ""
    bn, gn = to_num(base), to_num(got)
    if bn is not None and gn is not None:
        if abs(bn - gn) < 0.005:
            return "一致", "数值相同"
        return "数值不同", "基准=%s ｜ PDF=%s" % (base, got)
    if b.startswith(g) or g.startswith(b):
        shorter = min(b, g, key=len)
        longer = max(b, g, key=len)
        return "截断", "较短=%s ｜ 完整=%s" % (shorter, longer)
    return "不一致", "基准=%s ｜ PDF=%s" % (base, got)


def _row_key(row):
    return norm("".join(str(c) for c in row[:6]))[:120]


# ---- 自动表头识别与列映射（qc 用，避免手工试参数/另行 profile 基准） ----

HEADER_ALIASES = ("序号", "单位名称", "坐落位置", "面积", "接收时间", "租赁期",
                  "收租时间节点", "使用情况", "使用人", "月租金", "押金",
                  "应收租金", "减免租金", "合计", "备注")


def norm_header(text):
    """表头归一化：去单位括号、空白、全半角差异。"""
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def _header_hits(row):
    hits = 0
    for cell in row:
        label = norm_header(cell)
        if len(label) < 2:
            continue
        if any(alias in label or label in alias for alias in HEADER_ALIASES):
            hits += 1
    return hits


def detect_header_block(rows, max_scan=8, min_hits=2):
    """在前 max_scan 行里找表头行，返回 (表头行下标, 数据起始下标)。

    兼容两行合并表头（第二行是子表头时数据再后移一行）。
    """
    best = None
    for index, row in enumerate(rows[:max_scan]):
        hits = _header_hits(row)
        if hits >= min_hits and (best is None or hits > best[1]):
            best = (index, hits)
    if not best:
        return None, None
    hi = best[0]
    data_start = hi + 1
    if hi + 1 < len(rows) and _header_hits(rows[hi + 1]) >= min_hits:
        data_start = hi + 2
    return hi, data_start


def header_labels(rows, hi):
    """取列标题：本行为空时用下一行的子表头补。"""
    width = 0
    for row in rows[hi:hi + 2]:
        width = max(width, len(row))
    labels = []
    for col in range(width):
        value = str(rows[hi][col]).strip() if col < len(rows[hi]) else ""
        if not value and hi + 1 < len(rows) and col < len(rows[hi + 1]):
            value = str(rows[hi + 1][col]).strip()
        labels.append(value)
    return labels


def map_columns(base_labels, got_labels, min_ratio=0.55):
    """按表头文字把 PDF 列映射到基准列，返回按基准列排序的 (基准列, PDF列, 分数)。"""
    import difflib

    pairs, used = [], set()
    for got_index, got_label in enumerate(got_labels):
        got_name = norm_header(got_label)
        if not got_name:
            continue
        best = None
        for base_index, base_label in enumerate(base_labels):
            if base_index in used:
                continue
            base_name = norm_header(base_label)
            if not base_name:
                continue
            score = 2.0 if base_name == got_name else difflib.SequenceMatcher(
                None, base_name, got_name).ratio()
            if best is None or score > best[0]:
                best = (score, base_index)
        if best and best[0] >= min_ratio:
            pairs.append((best[1], got_index, round(best[0], 3)))
            used.add(best[1])
    pairs.sort()
    return pairs, used



def align_rows(base_rows, got_rows):
    """行对齐：优先按顺序，行数不同时用相似度对齐。"""
    if len(base_rows) == len(got_rows):
        return [(i, i) for i in range(len(base_rows))], [], []
    import difflib
    base_keys = [_row_key(r) for r in base_rows]
    got_keys = [_row_key(r) for r in got_rows]
    sm = difflib.SequenceMatcher(None, base_keys, got_keys, autojunk=False)
    pairs, only_base, only_got = [], [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            pairs.extend((i1 + k, j1 + k) for k in range(i2 - i1))
        elif tag == "replace":
            overlap = min(i2 - i1, j2 - j1)
            for k in range(overlap):
                pairs.append((i1 + k, j1 + k))
            only_base.extend(range(i1 + overlap, i2))
            only_got.extend(range(j1 + overlap, j2))
        elif tag == "delete":
            only_base.extend(range(i1, i2))
        elif tag == "insert":
            only_got.extend(range(j1, j2))
    return pairs, only_base, only_got


def _sequence_compare(base_rows, got_rows, cols):
    """合并单元格列（如序号/单位名称）按"非空值序列"比对，忽略落在哪一行。"""
    import difflib

    out = []
    for col in cols:
        base_vals = [norm(r[col]) for r in base_rows if col < len(r) and str(r[col]).strip()]
        got_vals = [norm(r[col]) for r in got_rows if col < len(r) and str(r[col]).strip()]
        sm = difflib.SequenceMatcher(None, base_vals, got_vals, autojunk=False)
        matched = sum(block.size for block in sm.get_matching_blocks())
        diffs = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            diffs.append((i1 + 1, " / ".join(base_vals[i1:i2])[:60],
                          j1 + 1, " / ".join(got_vals[j1:j2])[:60]))
        out.append({
            "col": col + 1,
            "base_count": len(base_vals),
            "got_count": len(got_vals),
            "matched": matched,
            "rate": round(matched / max(len(base_vals), len(got_vals), 1), 4),
            "diffs": diffs[:10],
        })
    return out


def _diagnostics(base_rows, got_rows, stats, results):
    """给出可执行的下一步提示，防止用"自建脚本 + 全量目视"硬啃对齐问题。"""
    hints = []
    base_n, got_n = len(base_rows), len(got_rows)
    total = len(results) or 1
    rate = stats.get("一致", 0) / total
    if base_n and got_n >= 1.5 * base_n:
        hints.append(
            "PDF 行数 %d ≈ 基准 %d 的 %.1f 倍：疑似同一张表被重复打印/重复提取。"
            "优先用 qc <pdf> <基准> --max-pages <单份页数> 只取一份后重跑"
            "（或 extract --max-pages 后 compare）；不要写自定义对齐脚本。"
            % (got_n, base_n, got_n / base_n))
    if rate < 0.7:
        hints.append(
            "一致率仅 %.0f%%：大概率行列错位。先调 --base-skip/--got-skip 与 --base-cols/--got-cols，"
            "最多调 1 轮；仍不齐就隔离单份数据重跑，不要逐格人工对。" % (rate * 100))
    numeric = [r for r in results if r["verdict"] == "数值不同"]
    if numeric and len(numeric) <= 8:
        hints.append(
            "真实差异候选 %d 处（数值不同）：只对这 %d 处裁原图复核即可（≤5 张），其余不必逐格看。"
            % (len(numeric), len(numeric)))
    trunc = stats.get("截断", 0)
    if trunc:
        hints.append("截断 %d 处＝PDF 打印裁切（源问题），直接归类，不用裁图。" % trunc)
    row_bad = stats.get("整行多余", 0) + stats.get("整行缺失", 0)
    if row_bad:
        hints.append("整行错位 %d 处：先排除重复打印/表头未跳过，再判断是否真的缺行或多行。" % row_bad)
    hints.append(
        "时间纪律：本步应已足够给出结论。如需原图复核，只裁真实差异处（≤5 张、≤60 秒）；"
        "不要逐行/逐格全量目视，不要再起一套方法做全量复核。")
    return hints


def compare_tables(base_rows, got_rows, out_md, title="PDF 提取结果 vs 基准", seq_cols=None,
                   base_header_labels=None):
    seq_cols = [c for c in (seq_cols or []) if c >= 0]
    pairs, only_base, only_got = align_rows(base_rows, got_rows)
    max_cols = max([len(r) for r in base_rows] + [len(r) for r in got_rows] + [0])
    base_header = base_rows[0] if base_rows else []
    got_header = got_rows[0] if got_rows else []
    same_header = [norm(a) == norm(b) for a, b in zip(base_header, got_header)]

    results = []
    for bi, gi in pairs:
        base_row, got_row = base_rows[bi], got_rows[gi]
        for c in range(max_cols):
            if c in seq_cols:
                continue
            base_val = base_row[c] if c < len(base_row) else ""
            got_val = got_row[c] if c < len(got_row) else ""
            verdict, note = compare_cells(base_val, got_val)
            results.append({"base_row": bi + 1, "pdf_row": gi + 1, "col": c + 1,
                            "base": base_val, "pdf": got_val, "verdict": verdict, "note": note})
    for bi in only_base:
        results.append({"base_row": bi + 1, "pdf_row": None, "col": 0,
                        "base": " / ".join(str(c) for c in base_rows[bi] if str(c).strip()),
                        "pdf": "", "verdict": "整行缺失", "note": "PDF 无对应行"})
    for gi in only_got:
        results.append({"base_row": None, "pdf_row": gi + 1, "col": 0,
                        "base": "", "pdf": " / ".join(str(c) for c in got_rows[gi] if str(c).strip()),
                        "verdict": "整行多余", "note": "基准无对应行"})

    stats = {}
    for r in results:
        stats[r["verdict"]] = stats.get(r["verdict"], 0) + 1
    total = len(results) or 1

    seq_stats = _sequence_compare(base_rows, got_rows, seq_cols) if seq_cols else []

    col_stats = {}
    for r in results:
        if r["col"] == 0:
            continue
        stat = col_stats.setdefault(r["col"], {"total": 0, "一致": 0})
        stat["total"] += 1
        if r["verdict"] == "一致":
            stat["一致"] += 1

    lines = ["# " + title, ""]
    lines.append("- 基准行数 %d ｜ PDF 行数 %d ｜ 对齐后可比单元格 %d" % (len(base_rows), len(got_rows), len(results)))
    if same_header and any(same_header) and not all(same_header):
        lines.append("- 注意：两表列数或表头不完全一致，按列序号对齐")
    lines.append("")
    lines.append("## 结论分布")
    lines.append("")
    lines.append("| 结论 | 数量 | 占比 |")
    lines.append("|---|---|---|")
    for key, count in sorted(stats.items(), key=lambda kv: -kv[1]):
        lines.append("| %s | %d | %.2f%% |" % (key, count, count / total * 100))
    lines.append("")
    if seq_stats:
        lines.append("## 合并列（按值序列比对）")
        lines.append("")
        lines.append("| 列 | 基准值数 | PDF 值数 | 匹配 | 一致率 |")
        lines.append("|---|---|---|---|---|")
        for stat in seq_stats:
            lines.append("| %d | %d | %d | %d | %.1f%% |" % (
                stat["col"], stat["base_count"], stat["got_count"], stat["matched"], stat["rate"] * 100))
        lines.append("")
        for stat in seq_stats:
            if stat["diffs"]:
                lines.append("列 %d 差异：" % stat["col"])
                for i1, bv, j1, gv in stat["diffs"]:
                    lines.append("- 基准#%d %s → PDF#%d %s" % (i1, bv or "(空)", j1, gv or "(空)"))
                lines.append("")
        if seq_stats:
            bad_seq = sum(1 for s in seq_stats if s["rate"] < 0.99)
            lines.append("> 合并列共 %d 列，其中 %d 列值序列不完全一致。" % (len(seq_stats), bad_seq))
            lines.append("")

    lines.append("## 逐列一致率")
    lines.append("")
    lines.append("| 列 | 一致/可比 | 一致率 | 基准表头 |")
    lines.append("|---|---|---|---|")
    for col in sorted(col_stats):
        stat = col_stats[col]
        header = ""
        if base_header_labels and col - 1 < len(base_header_labels):
            header = base_header_labels[col - 1]
        elif col - 1 < len(base_header):
            header = base_header[col - 1]
        lines.append("| %d | %d/%d | %.1f%% | %s |" % (
            col, stat["一致"], stat["total"], stat["一致"] / max(stat["total"], 1) * 100,
            str(header).replace("|", "\\|")))
    lines.append("")
    bad = [r for r in results if r["verdict"] != "一致"]
    lines.append("## 差异明细（%d 处）" % len(bad))
    lines.append("")
    if bad:
        lines.append("| 基准行 | PDF 行 | 列 | 基准值 | PDF 值 | 结论 | 说明 |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in bad[:80]:
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                r["base_row"] or "-", r["pdf_row"] or "-", r["col"] or "-",
                str(r["base"])[:60].replace("|", "\\|"),
                str(r["pdf"])[:60].replace("|", "\\|"),
                r["verdict"], str(r["note"]).replace("|", "\\|")))
        if len(bad) > 80:
            lines.append("| ... | | | | | | 其余 %d 处见 JSON |" % (len(bad) - 80))
    else:
        lines.append("无差异。")
    hints = _diagnostics(base_rows, got_rows, stats, results)
    lines.append("")
    lines.append("## 诊断与下一步")
    lines.append("")
    for hint in hints:
        lines.append("- " + hint)
    Path(out_md).write_text("\n".join(lines), encoding="utf-8")

    ok = stats.get("一致", 0)
    summary = {
        "base_rows": len(base_rows),
        "pdf_rows": len(got_rows),
        "cells": len(results),
        "consistent": ok,
        "rate": round(ok / total, 4),
        "verdicts": stats,
        "hints": hints,
        "report": str(out_md),
    }
    if seq_stats:
        summary["sequence_cols"] = [
            {"col": s["col"], "rate": s["rate"], "matched": s["matched"],
             "base_count": s["base_count"], "got_count": s["got_count"]}
            for s in seq_stats
        ]
    return summary, results

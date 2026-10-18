# -*- coding: utf-8 -*-
"""表格重建：框线网格 + 窄列竖排文字归并；无框线时用文本行聚类兜底。"""
from __future__ import annotations

import re

import cv2
import numpy as np

HEADER_WORDS = ("序号", "单位名称", "坐落位置", "收租时间节点", "使用情况", "使用人",
                "月租金", "应收租金", "减免租金", "合计", "备注", "名称", "金额")


def _group(vals, gap=4):
    if not vals:
        return []
    runs, start, prev = [], vals[0], vals[0]
    for v in vals[1:]:
        if v - prev > gap:
            runs.append((start, prev))
            start = v
        prev = v
    runs.append((start, prev))
    return [int(round((a + b) / 2.0)) for a, b in runs]


def detect_rules(gray, dark_threshold=190, min_frac=40, cover=0.45):
    """检测长横线/竖线，返回 (xs, ys)。"""
    dark = (gray < dark_threshold).astype(np.uint8) * 255
    h, w = dark.shape
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // min_frac), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // min_frac)))
    hlines = cv2.morphologyEx(dark, cv2.MORPH_OPEN, hk)
    vlines = cv2.morphologyEx(dark, cv2.MORPH_OPEN, vk)
    row_ink = hlines.sum(axis=1) / 255.0
    col_ink = vlines.sum(axis=0) / 255.0
    ys = [y for y in range(h) if row_ink[y] > cover * max(row_ink.max(), 1)]
    xs = [x for x in range(w) if col_ink[x] > cover * max(col_ink.max(), 1)]
    ys = _group(ys, 5)
    xs = _group(xs, 5)
    if len(xs) < 3 or len(ys) < 3:
        return [], []
    return xs, ys


def _locate(value, bounds):
    for i in range(len(bounds) - 1):
        if bounds[i] <= value < bounds[i + 1]:
            return i
    return None


def _locate_col(line, xs):
    """按"与哪一列横向重叠最多"归列，避免贴边的字被算到隔壁列。"""
    best_col, best_overlap = None, -1.0
    for j in range(len(xs) - 1):
        overlap = min(line["x1"], xs[j + 1]) - max(line["x0"], xs[j])
        if overlap > best_overlap:
            best_col, best_overlap = j, overlap
    return best_col if best_overlap > 0 else None


def _cell_text(lines):
    if not lines:
        return ""
    ordered = sorted(lines, key=lambda t: (round(t["cy"] / 14.0), t["cx"]))
    return "".join(t["text"].strip() for t in ordered).strip()


def _group_vertical_lines(lines, split_y=None):
    """把同一合并单元格里竖排的字（逐字一行）按 y 间距合并成一段。

    split_y：表头下边线，跨过它的行必须断开（避免把表头字和第一条数据粘在一起）。
    """
    if not lines:
        return []
    lines = sorted(lines, key=lambda t: t["cy"])
    heights = [max(1.0, t["y1"] - t["y0"]) for t in lines]
    med = float(np.median(heights))
    groups = [[lines[0]]]
    for ln in lines[1:]:
        prev = groups[-1][-1]
        if split_y is not None and prev["y1"] <= split_y < ln["y0"]:
            groups.append([ln])
            continue
        gap = ln["y0"] - prev["y1"]
        # 同一单元格内换行：行间距接近 0；不同数据行之间会留出明显空白
        if gap < med * 0.45:
            groups[-1].append(ln)
        else:
            groups.append([ln])
    return groups


def build_grid(lines, xs, ys, page_width=None):
    """按框线网格归位文本；窄列里的竖排文字按字块归并到起始行。"""
    rows, cols = len(ys) - 1, len(xs) - 1
    if rows <= 0 or cols <= 0:
        return []
    width = page_width or xs[-1]
    narrow = [c for c in range(cols) if (xs[c + 1] - xs[c]) < 0.06 * width]

    buckets = {}
    narrow_lines = {c: [] for c in narrow}
    for ln in lines:
        cx, cy = ln["cx"], ln["cy"]
        if not (xs[0] <= cx <= xs[-1] and ys[0] <= cy <= ys[-1]):
            continue
        r, c = _locate(cy, ys), _locate_col(ln, xs)
        if r is None or c is None:
            continue
        if c in narrow_lines:
            narrow_lines[c].append(ln)
        else:
            buckets.setdefault((r, c), []).append(ln)

    for c in narrow:
        col_lines = narrow_lines[c]
        if not col_lines:
            continue
        split_y = ys[1] if len(ys) > 2 else None
        for group in _group_vertical_lines(col_lines, split_y=split_y):
            r = _locate(group[0]["cy"], ys)
            if r is None:
                continue
            buckets.setdefault((r, c), []).extend(group)

    return [[_cell_text(buckets.get((r, c), [])) for c in range(cols)] for r in range(rows)]


def _looks_header_row(row):
    text = "".join(str(c) for c in row)
    hits = sum(1 for w in HEADER_WORDS if w in text)
    has_number = any(re.fullmatch(r"[-+]?[\d,]*\.?\d+", str(c).strip())
                     for c in row if str(c).strip())
    return hits >= 2 and not has_number


def merge_leading_header_rows(rows):
    """表头常被拆成 2 行（二级表头），合并成 1 行，保证每页行数一致。"""
    if not rows:
        return rows
    n = 0
    while n < len(rows) and _looks_header_row(rows[n]):
        n += 1
    if n < 2:
        return rows
    cols = max(len(r) for r in rows[:n])
    merged = []
    for c in range(cols):
        parts = []
        for r in range(n):
            value = str(rows[r][c]).strip() if c < len(rows[r]) else ""
            if value and value not in parts:
                parts.append(value)
        merged.append(" ".join(parts))
    return [merged] + rows[n:]


def _cluster_rows(lines):
    if not lines:
        return []
    lines = sorted(lines, key=lambda t: t["cy"])
    heights = [max(1.0, t["y1"] - t["y0"]) for t in lines]
    med = float(np.median(heights)) if heights else 10.0
    groups = [[lines[0]]]
    for ln in lines[1:]:
        if ln["cy"] - groups[-1][-1]["cy"] > med * 1.4:
            groups.append([ln])
        else:
            groups[-1].append(ln)
    return groups


def _split_row(line_group):
    items = sorted(line_group, key=lambda t: t["x0"])
    cells, cur = [], [items[0]]
    for ln in items[1:]:
        gap = ln["x0"] - cur[-1]["x1"]
        if gap > max(18.0, (ln["y1"] - ln["y0"]) * 1.2):
            cells.append(cur)
            cur = [ln]
        else:
            cur.append(ln)
    cells.append(cur)
    return ["".join(t["text"].strip() for t in sorted(c, key=lambda t: t["cx"])) for c in cells]


def build_rows_without_rules(lines):
    groups = _cluster_rows(lines)
    return [_split_row(g) for g in groups]


def reconstruct(lines, image):
    """返回 {mode, rows, quality}。"""
    gray = np.asarray(image.convert("L")) if hasattr(image, "convert") else np.asarray(image)
    xs, ys = detect_rules(gray)
    if xs and ys:
        rows = merge_leading_header_rows(build_grid(lines, xs, ys, page_width=image.width
                                                    if hasattr(image, "width") else None))
        if len(rows) >= 2 and len(rows[0]) >= 2:
            filled = sum(1 for r in rows for c in r if c)
            total = len(rows) * len(rows[0])
            return {
                "mode": "rules",
                "rows": rows,
                "rules": {"xs": xs, "ys": ys},
                "quality": {"cells": total, "filled": filled,
                            "fill_ratio": round(filled / max(total, 1), 3)},
            }
    rows = build_rows_without_rules(lines)
    max_cols = max((len(r) for r in rows), default=0)
    rows = [r + [""] * (max_cols - len(r)) for r in rows]
    return {
        "mode": "lines",
        "rows": rows,
        "rules": None,
        "quality": {"cells": len(rows) * max_cols,
                    "filled": sum(1 for r in rows for c in r if c),
                    "fill_ratio": None},
    }

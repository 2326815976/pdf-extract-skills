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
    # 复杂表单的竖线经常被文字和合并单元格截断，不会贯穿整页。
    # 用连通线段补充这些分段竖线，避免整张表单退化成左右两个大列。
    count, _, stats, _ = cv2.connectedComponentsWithStats(vlines, 8)
    segment_centers = []
    min_segment_height = max(40, int(h * 0.025))
    for component in range(1, count):
        x, _, width, height, _ = stats[component]
        if height >= min_segment_height and width <= max(6, int(w * 0.005)):
            segment_centers.append(x + width / 2.0)
    if xs and len(xs) < 6 and segment_centers:
        left, right = xs[0], xs[-1]
        xs = _group(
            sorted(xs + [center for center in segment_centers if left < center < right]),
            gap=8,
        )
    if len(xs) < 3 or len(ys) < 3:
        return [], []
    x0, x1 = max(0, xs[0] + 2), min(gray.shape[1], xs[-1] - 2)
    filtered_ys = []
    for y in ys:
        strip = gray[max(0, y - 2):min(gray.shape[0], y + 3), x0:x1]
        if strip.size and float((strip < 190).any(axis=0).mean()) >= 0.50:
            filtered_ys.append(y)
    if len(filtered_ys) >= 3:
        ys = filtered_ys
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


def _column_boundaries(gray, xs, ys, column, support_boundaries=None):
    """返回某一列真实存在的横向分隔线索引。

    扫描表的纵向合并单元格只在部分列缺少横线。全局 `ys` 是整页共用的候选
    行线，必须逐列确认是否真的穿过该列，才能识别合并区域。
    """
    if column < 0 or column >= len(xs) - 1 or gray is None:
        return list(range(len(ys)))
    x0 = max(0, int(xs[column]) + 4)
    x1 = min(gray.shape[1], int(xs[column + 1]) - 4)
    if x1 - x0 < 8:
        return list(range(len(ys)))
    fractions = []
    for y in ys:
        best = 0.0
        for offset in range(-5, 6):
            row = min(max(0, int(y) + offset), gray.shape[0] - 1)
            strip = gray[row:row + 1, x0:x1]
            if strip.size:
                best = max(best, float((strip < 190).any(axis=0).mean()))
        fractions.append(best)
    width_ratio = (xs[column + 1] - xs[column]) / max(xs[-1] - xs[0], 1)
    threshold = 0.60 if width_ratio < 0.08 else 0.35
    support_boundaries = set(support_boundaries or ())
    boundaries = [
        index for index, fraction in enumerate(fractions)
        if fraction >= threshold or (width_ratio < 0.08 and fraction >= 0.30
                                     and index in support_boundaries)
    ]
    if not boundaries or boundaries[0] != 0:
        boundaries.insert(0, 0)
    if len(ys) > 2 and 1 not in boundaries:
        boundaries.append(1)
    if boundaries[-1] != len(ys) - 1:
        boundaries.append(len(ys) - 1)
    return sorted(set(boundaries))


def _locate_cell_span(cy, ys, boundaries):
    if not boundaries or len(boundaries) < 2:
        row = _locate(cy, ys)
        return (row, row + 1) if row is not None else (None, None)
    for position in range(len(boundaries) - 1):
        top_index, bottom_index = boundaries[position], boundaries[position + 1]
        top, bottom = ys[top_index], ys[bottom_index]
        if top <= cy < bottom:
            return top_index, bottom_index
    if cy < ys[boundaries[0]]:
        return boundaries[0], boundaries[1]
    return boundaries[-2], boundaries[-1]


def build_grid_with_meta(lines, xs, ys, page_width=None, gray=None):
    """按框线网格归位文本，并返回合并单元格的实际行跨度。"""
    rows, cols = len(ys) - 1, len(xs) - 1
    if rows <= 0 or cols <= 0:
        return [], {}
    boundaries_by_column = [
        _column_boundaries(gray, xs, ys, column) for column in range(cols)
    ]
    narrow_columns = {
        column for column in range(cols)
        if (xs[column + 1] - xs[column]) / max(xs[-1] - xs[0], 1) < 0.08
    }
    preliminary = list(boundaries_by_column)
    for column in narrow_columns:
        support = set()
        for neighbor in (column - 1, column + 1):
            if neighbor in narrow_columns:
                support.update(preliminary[neighbor])
        boundaries_by_column[column] = _column_boundaries(
            gray, xs, ys, column, support_boundaries=support)
    buckets = {}
    cell_spans = {
        (top_index, column): (top_index, bottom_index)
        for column, boundaries in enumerate(boundaries_by_column)
        for top_index, bottom_index in zip(boundaries, boundaries[1:])
    }
    for line in lines:
        cx, cy = line["cx"], line["cy"]
        if not (xs[0] <= cx <= xs[-1] and ys[0] <= cy <= ys[-1]):
            continue
        column = _locate_col(line, xs)
        if column is None:
            continue
        start_row, end_row = _locate_cell_span(cy, ys, boundaries_by_column[column])
        if start_row is None or end_row is None:
            continue
        buckets.setdefault((start_row, column), []).append(line)

    grid = [
        [_cell_text(buckets.get((row, column), [])) for column in range(cols)]
        for row in range(rows)
    ]
    return grid, {
        "column_boundaries": boundaries_by_column,
        "cell_spans": cell_spans,
    }


def build_grid(lines, xs, ys, page_width=None):
    grid, _ = build_grid_with_meta(lines, xs, ys, page_width=page_width)
    return grid


def _vertical_border_exists(gray, xs, ys, row, boundary_column):
    if gray is None or row < 0 or row >= len(ys) - 1:
        return True
    x = int(xs[boundary_column])
    y0 = max(0, int(ys[row]) + 2)
    y1 = min(gray.shape[0], int(ys[row + 1]) - 2)
    if y1 <= y0:
        return True
    x0 = max(0, x - 2)
    x1 = min(gray.shape[1], x + 3)
    strip = gray[y0:y1, x0:x1]
    if strip.size == 0:
        return True
    fraction = float((strip < 190).any(axis=1).mean())
    return fraction >= 0.45


def build_merged_grid(lines, xs, ys, gray, boundaries_by_column):
    """构建合并单元格模型，返回矩形文本网格和 Word 合并区。"""
    rows_count, cols_count = len(ys) - 1, len(xs) - 1
    if rows_count <= 0 or cols_count <= 0:
        return [], {"regions": []}
    parent = list(range(rows_count * cols_count))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    def index(row, column):
        return row * cols_count + column

    for row in range(rows_count):
        for column in range(cols_count):
            if column + 1 < cols_count and not _vertical_border_exists(
                    gray, xs, ys, row, column + 1):
                union(index(row, column), index(row, column + 1))
            if row + 1 < rows_count and (row + 1) not in boundaries_by_column[column]:
                union(index(row, column), index(row + 1, column))

    components = {}
    for row in range(rows_count):
        for column in range(cols_count):
            components.setdefault(find(index(row, column)), []).append((row, column))

    cell_region = {}
    regions = []
    for cells in components.values():
        component_rows = [row for row, _ in cells]
        component_columns = [column for _, column in cells]
        top, bottom = min(component_rows), max(component_rows) + 1
        left, right = min(component_columns), max(component_columns) + 1
        rectangular = len(cells) == (bottom - top) * (right - left)
        if rectangular:
            region_index = len(regions)
            regions.append({
                "top": top,
                "bottom": bottom,
                "left": left,
                "right": right,
            })
            for row, column in cells:
                cell_region[(row, column)] = region_index
        else:
            for row, column in cells:
                region_index = len(regions)
                regions.append({
                    "top": row,
                    "bottom": row + 1,
                    "left": column,
                    "right": column + 1,
                })
                cell_region[(row, column)] = region_index

    region_lines = {}
    for line in lines:
        row = _locate(line["cy"], ys)
        column = _locate_col(line, xs)
        if row is None or column is None:
            continue
        region_index = cell_region.get((row, column))
        if region_index is not None:
            region_lines.setdefault(region_index, []).append(line)

    grid = [["" for _ in range(cols_count)] for _ in range(rows_count)]
    for region_index, region in enumerate(regions):
        text = _cell_text(region_lines.get(region_index, []))
        if text:
            grid[region["top"]][region["left"]] = text
    return grid, {"regions": regions}


def _looks_header_row(row):
    text = "".join(str(c) for c in row)
    hits = sum(1 for w in HEADER_WORDS if w in text)
    has_number = any(re.fullmatch(r"[-+]?[\d,]*\.?\d+", str(c).strip())
                     for c in row if str(c).strip())
    return hits >= 2 and not has_number


def merge_leading_header_rows(rows, return_count=False):
    """表头常被拆成 2 行（二级表头），合并成 1 行，保证每页行数一致。"""
    if not rows:
        return rows
    n = 0
    while n < len(rows) and _looks_header_row(rows[n]):
        n += 1
    if n < 2:
        return (rows, n) if return_count else rows
    cols = max(len(r) for r in rows[:n])
    merged = []
    for c in range(cols):
        parts = []
        for r in range(n):
            value = str(rows[r][c]).strip() if c < len(rows[r]) else ""
            if value and value not in parts:
                parts.append(value)
        merged.append(" ".join(parts))
    result = [merged] + rows[n:]
    return (result, n) if return_count else result


def normalize_header_row(rows):
    """修复 OCR 常见的相邻表头粘连，不改变数据行。"""
    if not rows:
        return rows
    header = [str(cell or "").strip() for cell in rows[0]]
    if len(header) >= 2:
        first = header[0].replace(" ", "")
        second = header[1].replace(" ", "")
        if first in ("", "号", "序", "序号") and "单位名称" in second:
            header[0] = "序号"
            header[1] = re.sub(r"^序?", "", second) or "单位名称"
    for index, value in enumerate(header):
        compact = value.replace(" ", "")
        if "应收租金" in compact and "减免租金" in compact:
            header[index] = "减免租金"
        elif compact in ("月租金元", "月租金（元）", "月租金(元)"):
            header[index] = "月租金（元）"
    return [header] + rows[1:]


def _sequence_column(rows):
    if not rows:
        return None
    header = [re.sub(r"\s+", "", str(cell or "")) for cell in rows[0]]
    for index, value in enumerate(header):
        if value in ("序号", "序", "编号", "No", "NO", "No."):
            return index
    for index, value in enumerate(header):
        if "序号" in value:
            return index
    return None


def _sequence_value(value):
    text = re.sub(r"\s+", "", str(value or ""))
    match = re.fullmatch(r"(\d{1,4})[.、]?", text)
    return int(match.group(1)) if match else None


def normalize_sequence_column(rows, grid_meta):
    """把纵向合并的序号归到合并区域首行，并补齐 OCR 漏掉的单个序号。"""
    if len(rows) < 2:
        return rows, {"applied": False, "reason": "行数不足"}
    column = _sequence_column(rows)
    if column is None:
        return rows, {"applied": False, "reason": "未识别到序号列"}

    spans = sorted({
        span for (row, col), span in (grid_meta.get("cell_spans") or {}).items()
        if col == column and row >= 1
    })
    if not spans:
        return rows, {"applied": False, "reason": "序号列没有可用单元格跨度"}

    repaired_rows = [list(row) for row in rows]
    known = []
    for span_index, (start, _) in enumerate(spans):
        if start >= len(repaired_rows):
            continue
        value = _sequence_value(repaired_rows[start][column])
        if value is not None:
            known.append((span_index, value))

    inferred = False
    starts = [start for start, _ in spans]
    if known:
        offsets = [value - span_index for span_index, value in known]
        if len(offsets) == 1 or max(offsets) - min(offsets) <= 1:
            start_value = int(round(sum(offsets) / len(offsets)))
            for span_index, start in enumerate(starts):
                candidate = start_value + span_index
                if candidate < 0 or candidate > 9999:
                    continue
                existing = _sequence_value(repaired_rows[start][column])
                next_value = ""
                if column + 1 < len(repaired_rows[start]):
                    next_value = str(repaired_rows[start][column + 1] or "").strip()
                if existing is None and next_value:
                    repaired_rows[start][column] = str(candidate)
                    inferred = True

    next_column = column + 1
    if next_column < len(repaired_rows[0]):
        next_header = re.sub(r"\s+", "", str(repaired_rows[0][next_column] or ""))
        if "单位" in next_header or "名称" in next_header:
            for start in starts:
                if start >= len(repaired_rows):
                    continue
                sequence = _sequence_value(repaired_rows[start][column])
                unit = str(repaired_rows[start][next_column] or "").strip()
                if sequence is None or not unit:
                    continue
                match = re.match(r"^(\d{1,4})[.、]?(.+)$", unit)
                if match and int(match.group(1)) == sequence:
                    repaired_rows[start][next_column] = match.group(2).strip()

    filled = sum(
        1 for start in starts
        if start < len(repaired_rows) and _sequence_value(repaired_rows[start][column]) is not None
    )
    return repaired_rows, {
        "applied": True,
        "column": column,
        "groups": len(spans),
        "known": len(known),
        "filled": filled,
        "inferred": inferred,
        "spans": spans,
    }


def _shift_meta_after_header_merge(grid_meta, header_rows):
    if header_rows <= 1:
        return grid_meta
    shift = header_rows - 1
    shifted = {}
    for key, (start, end) in (grid_meta.get("cell_spans") or {}).items():
        row, column = key
        if row < header_rows:
            continue
        shifted[(row - shift, column)] = (start - shift, end - shift)
    shifted_regions = []
    for region in grid_meta.get("regions") or []:
        if region["top"] < header_rows:
            continue
        shifted_regions.append({
            **region,
            "top": region["top"] - shift,
            "bottom": region["bottom"] - shift,
        })
    return {
        "column_boundaries": grid_meta.get("column_boundaries"),
        "cell_spans": shifted,
        "regions": shifted_regions,
    }


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
        grid, grid_meta = build_grid_with_meta(
            lines, xs, ys,
            page_width=image.width if hasattr(image, "width") else None,
            gray=gray,
        )
        initial_columns = max((len(row) for row in grid), default=0)
        initial_cells = max(len(grid), 1) * max(initial_columns, 1)
        initial_fill = (sum(1 for row in grid for cell in row if cell) /
                        max(initial_cells, 1))
        vertical_merge_candidates = sum(
            1 for (_, _), (top, bottom) in grid_meta["cell_spans"].items()
            if bottom - top > 1)
        merged_grid_used = (
            (initial_columns >= 10 and initial_fill < 0.28)
            or (initial_columns >= 6 and vertical_merge_candidates >= 2)
        )
        merged_regions = []
        if merged_grid_used:
            merged_grid, merged_meta = build_merged_grid(
                lines, xs, ys, gray, grid_meta["column_boundaries"])
            merged_regions = merged_meta["regions"]
            if merged_regions:
                grid = merged_grid
                grid_meta["regions"] = merged_regions
        merged_rows, header_rows = merge_leading_header_rows(grid, return_count=True)
        grid_meta = _shift_meta_after_header_merge(grid_meta, header_rows)
        rows = normalize_header_row(merged_rows)
        rows, sequence_repair = normalize_sequence_column(rows, grid_meta)
        if len(rows) >= 2 and len(rows[0]) >= 2:
            filled = sum(1 for r in rows for c in r if c)
            total = len(rows) * len(rows[0])
            width = image.width if hasattr(image, "width") else max(xs)
            height = image.height if hasattr(image, "height") else max(ys)
            table_width = max(xs[-1] - xs[0], 1)
            table_height = max(ys[-1] - ys[0], 1)
            return {
                "mode": "rules",
                "rows": rows,
                "rules": {"xs": xs, "ys": ys},
                "quality": {"cells": total, "filled": filled,
                            "fill_ratio": round(filled / max(total, 1), 3),
                            "grid_width_ratio": round((xs[-1] - xs[0]) / max(width, 1), 3),
                            "grid_height_ratio": round((ys[-1] - ys[0]) / max(height, 1), 3),
                            "table_aspect": round(table_height / table_width, 5),
                            "column_ratios": [
                                round((xs[index + 1] - xs[index]) / table_width, 5)
                                for index in range(len(xs) - 1)
                            ],
                            "row_ratios": [
                                round((ys[index + 1] - ys[index]) / table_height, 5)
                                for index in range(len(ys) - 1)
                            ],
                            "header_rows_merged": header_rows,
                            "merged_cells": merged_regions,
                            "merged_grid_used": merged_grid_used,
                            "sequence_repair": sequence_repair},
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
                    "fill_ratio": round(sum(1 for r in rows for c in r if c) /
                                        max(len(rows) * max_cols, 1), 3),
                    "grid_width_ratio": None,
                    "grid_height_ratio": None},
    }


def assess_candidate(rows, reconstruction, *, source_route="scan", mode="auto"):
    """判断重建结果是否值得作为表格输出。

    默认宁可输出正文，也不把多栏、截图或纵向卡片误判成表格。
    """
    if not rows or len(rows) < 2 or not rows[0]:
        return {"accepted": False, "score": 0.0, "reason": "行数或列数不足"}
    if mode == "off":
        return {"accepted": False, "score": 0.0, "reason": "表格识别已关闭"}

    columns = max(len(row) for row in rows)
    quality = reconstruction.get("quality") or {}
    fill = float(quality.get("fill_ratio") or 0.0)
    mode_name = reconstruction.get("mode")
    rectangular = sum(1 for row in rows if len(row) == columns) / max(len(rows), 1)
    header_hits = [sum(1 for word in HEADER_WORDS if word in "".join(map(str, row)))
                   for row in rows[:3]]
    header_signal = max(header_hits, default=0) >= 2
    numeric_cells = sum(
        1 for row in rows for cell in row
        if re.fullmatch(r"[-+]?[\d,]*\.?\d+\s*", str(cell).strip())
    )
    numeric_ratio = numeric_cells / max(sum(len(row) for row in rows), 1)

    if mode_name == "rules":
        width_ratio = float(quality.get("grid_width_ratio") or 0.0)
        height_ratio = float(quality.get("grid_height_ratio") or 0.0)
        score = (0.35 + min(fill, 0.9) * 0.25 +
                 min(width_ratio, 1.0) * 0.15 +
                 min(height_ratio, 1.0) * 0.15 +
                 min(rectangular, 1.0) * 0.10)
        accepted = (len(rows) >= 2 and columns >= 2 and fill >= 0.12
                    and width_ratio >= 0.25 and height_ratio >= 0.12
                    and rectangular >= 0.75)
        reason = "框线表格" if accepted else "框线覆盖或填充率不足"
        layout_preferred = bool(
            accepted and columns >= 10 and fill < 0.28
            and not quality.get("merged_grid_used"))
    else:
        score = (0.15 + min(rectangular, 1.0) * 0.20 +
                 min(fill, 0.9) * 0.25 +
                 (0.20 if header_signal else 0.0) +
                 min(numeric_ratio, 0.3) * 0.60)
        accepted = (len(rows) >= 3 and columns >= 3 and rectangular >= 0.80
                    and fill >= 0.22 and (header_signal or numeric_ratio >= 0.20))
        reason = "无框线强表格特征" if accepted else "无框线结构证据不足"
        layout_preferred = False

    if source_route == "visual" and mode != "force":
        accepted = False
        score = min(score, 0.45)
        reason = "截图/视觉页默认保留版面，不重建表格"
    if mode == "force":
        accepted = len(rows) >= 2 and columns >= 2
        reason = "强制表格模式"
    return {
        "accepted": bool(accepted),
        "score": round(float(score), 3),
        "reason": reason,
        "header_signal": bool(header_signal),
        "numeric_ratio": round(numeric_ratio, 3),
        "rectangularity": round(rectangular, 3),
        "layout_preferred": bool(layout_preferred),
    }

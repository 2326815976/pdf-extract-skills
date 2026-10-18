# -*- coding: utf-8 -*-
"""无表格页的阅读顺序（XY-cut）。"""
from __future__ import annotations


def _xycut(lines, depth=0):
    if len(lines) <= 1 or depth > 12:
        return [lines]
    ordered = sorted(lines, key=lambda t: t["cy"])
    # 先找最大的横向空白带
    best_gap, best_i = 0.0, None
    for i in range(len(ordered) - 1):
        gap = ordered[i + 1]["y0"] - ordered[i]["y1"]
        if gap > best_gap:
            best_gap, best_i = gap, i
    heights = [max(1.0, t["y1"] - t["y0"]) for t in ordered]
    med_h = sorted(heights)[len(heights) // 2] if heights else 10.0
    if best_gap > med_h * 0.8 and best_i is not None:
        return _xycut(ordered[: best_i + 1], depth + 1) + _xycut(ordered[best_i + 1:], depth + 1)
    # 再找最大的纵向空白带（分栏）
    by_x = sorted(ordered, key=lambda t: t["x0"])
    best_gap, best_i = 0.0, None
    for i in range(len(by_x) - 1):
        gap = by_x[i + 1]["x0"] - by_x[i]["x1"]
        if gap > best_gap:
            best_gap, best_i = gap, i
    if best_gap > med_h * 2.0 and best_i is not None:
        return _xycut(by_x[: best_i + 1], depth + 1) + _xycut(by_x[best_i + 1:], depth + 1)
    return [ordered]


def lines_to_text(lines):
    blocks = _xycut(lines)
    out = []
    for block in blocks:
        text = "".join(t["text"].strip() for t in sorted(block, key=lambda t: (round(t["cy"] / 12.0), t["cx"])))
        if text:
            out.append(text)
    return "\n\n".join(out)

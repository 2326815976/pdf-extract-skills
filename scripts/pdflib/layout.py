# -*- coding: utf-8 -*-
"""版面阅读顺序：递归 XY-cut，同时支持原生文本块和 OCR 行。"""
from __future__ import annotations

from statistics import median


def _rect(block):
    return block.get("bbox") or (block["x0"], block["y0"], block["x1"], block["y1"])


def _merged_gaps(intervals):
    if not intervals:
        return []
    ordered = sorted((float(a), float(b)) for a, b in intervals if b > a)
    merged = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(merged[i][1], merged[i + 1][0]) for i in range(len(merged) - 1)]


def _split_horizontal(blocks):
    rects = [_rect(block) for block in blocks]
    min_y = min(rect[1] for rect in rects)
    max_y = max(rect[3] for rect in rects)
    heights = [max(1.0, rect[3] - rect[1]) for rect in rects]
    gaps = [(gap, start, end) for start, end in _merged_gaps((rect[1], rect[3]) for rect in rects)
            for gap in [end - start]]
    if not gaps:
        return None
    gap, start, end = max(gaps, key=lambda item: item[0])
    if gap < max(8.0, median(heights) * 0.75):
        return None
    split_y = (start + end) / 2.0
    top = [block for block, rect in zip(blocks, rects) if rect[1] < split_y]
    bottom = [block for block, rect in zip(blocks, rects) if rect[1] >= split_y]
    if not top or not bottom:
        return None
    if min_y == start and max_y == end:
        return None
    return top, bottom


def _split_vertical(blocks):
    rects = [_rect(block) for block in blocks]
    widths = [max(1.0, rect[2] - rect[0]) for rect in rects]
    min_x = min(rect[0] for rect in rects)
    max_x = max(rect[2] for rect in rects)
    best = None
    candidates = sorted({round(rect[0], 2) for rect in rects} |
                        {round(rect[2], 2) for rect in rects})
    for candidate in candidates:
        if candidate <= min_x or candidate >= max_x:
            continue
        left = [block for block, rect in zip(blocks, rects) if rect[2] <= candidate]
        right = [block for block, rect in zip(blocks, rects) if rect[0] >= candidate]
        if len(left) < 2 or len(right) < 2:
            continue
        if len(left) + len(right) != len(blocks):
            continue
        left_bottom = max(rect[3] for rect in rects if rect[2] <= candidate)
        right_bottom = max(rect[3] for rect in rects if rect[0] >= candidate)
        left_top = min(rect[1] for rect in rects if rect[2] <= candidate)
        right_top = min(rect[1] for rect in rects if rect[0] >= candidate)
        overlap_height = max(0.0, min(left_bottom, right_bottom) - max(left_top, right_top))
        if overlap_height <= 0:
            continue
        gap = min(rect[0] for rect in rects if rect[0] >= candidate) - \
            max(rect[2] for rect in rects if rect[2] <= candidate)
        if gap <= 0:
            continue
        score = (gap, len(left) + len(right), overlap_height)
        if best is None or score > best[0]:
            best = (score, left, right)
    if best is None:
        return None
    gap = best[0][0]
    if gap < max(16.0, median(widths) * 0.16):
        return None
    return best[1], best[2]


def reading_order(blocks, depth=0):
    """返回按阅读顺序排列的文本/图片块。"""
    blocks = [block for block in blocks if str(block.get("text", "")).strip()
              or block.get("type") in ("image", "table")]
    if len(blocks) <= 1 or depth >= 16:
        return sorted(blocks, key=lambda b: (_rect(b)[1], _rect(b)[0]))
    vertical = _split_vertical(blocks)
    if vertical:
        return reading_order(vertical[0], depth + 1) + reading_order(vertical[1], depth + 1)
    horizontal = _split_horizontal(blocks)
    if horizontal:
        return reading_order(horizontal[0], depth + 1) + reading_order(horizontal[1], depth + 1)
    return sorted(blocks, key=lambda b: (_rect(b)[1], _rect(b)[0]))


def lines_to_blocks(lines):
    if not lines:
        return []
    heights = [max(1.0, float(line["y1"]) - float(line["y0"])) for line in lines]
    threshold = max(2.0, median(heights) * 0.45)
    ordered = sorted(lines, key=lambda item: (float(item["cy"]), float(item["cx"])))
    groups = [[ordered[0]]]
    for line in ordered[1:]:
        prev = groups[-1][-1]
        gap = float(line["y0"]) - float(prev["y1"])
        if gap > threshold:
            groups.append([line])
        else:
            groups[-1].append(line)
    output = []
    for group in groups:
        group = sorted(group, key=lambda item: (round(float(item["cy"]) / 12.0), float(item["cx"])))
        text = "".join(item["text"].strip() for item in group).strip()
        if not text:
            continue
        bbox = (
            min(float(item["x0"]) for item in group),
            min(float(item["y0"]) for item in group),
            max(float(item["x1"]) for item in group),
            max(float(item["y1"]) for item in group),
        )
        output.append({"type": "text", "text": text, "bbox": bbox})
    return output


def lines_to_text(lines):
    blocks = reading_order(lines_to_blocks(lines))
    return "\n\n".join(block["text"].strip() for block in blocks if block["text"].strip())


def blocks_to_text(blocks):
    ordered = reading_order(blocks)
    return "\n\n".join(block["text"].strip() for block in ordered
                       if block.get("type") == "text" and block.get("text", "").strip())


def flow_groups(blocks):
    """把正文和旁侧图片组合成可顺序渲染的组。

    同一纵向区域中，右侧的小图/照片会和左侧文本组成 side_by_side 组；
    没有匹配文本的图片仍按普通图片块输出。
    """
    blocks = [block for block in blocks if block.get("type") in ("text", "image", "table")]
    images = [block for block in blocks if block.get("type") == "image"]
    texts = [block for block in blocks if block.get("type") == "text"]
    used_images, used_text, groups = set(), set(), []

    image_clusters = []
    for image in sorted(images, key=lambda item: _rect(item)[1]):
        rect = _rect(image)
        cluster = None
        for group in image_clusters:
            group_rect = group["bbox"]
            overlap = max(0.0, min(rect[3], group_rect[3]) - max(rect[1], group_rect[1]))
            if overlap >= min(rect[3] - rect[1], group_rect[3] - group_rect[1]) * 0.55:
                cluster = group
                break
        if cluster is None:
            image_clusters.append({"images": [image], "bbox": list(rect)})
        else:
            cluster["images"].append(image)
            cluster["bbox"] = [
                min(cluster["bbox"][0], rect[0]),
                min(cluster["bbox"][1], rect[1]),
                max(cluster["bbox"][2], rect[2]),
                max(cluster["bbox"][3], rect[3]),
            ]

    for cluster in image_clusters:
        cluster_rect = cluster["bbox"]
        image_ids = {id(image) for image in cluster["images"]}
        left_text = []
        for text in texts:
            if id(text) in used_text:
                continue
            rect = _rect(text)
            overlap = max(0.0, min(rect[3], cluster_rect[3]) - max(rect[1], cluster_rect[1]))
            cluster_height = max(1.0, cluster_rect[3] - cluster_rect[1])
            if overlap <= 0:
                continue
            if rect[1] >= cluster_rect[3] - cluster_height * 0.40:
                continue
            if rect[0] < cluster_rect[0] - 20:
                left_text.append(text)
        text_chars = sum(len(item.get("text", "")) for item in left_text)
        if left_text and (len(left_text) >= 2 or text_chars >= 50):
            for text in left_text:
                used_text.add(id(text))
            for image in cluster["images"]:
                used_images.add(id(image))
            groups.append({
                "type": "side_by_side",
                "bbox": [min([cluster_rect[0]] + [_rect(item)[0] for item in left_text]),
                         min([cluster_rect[1]] + [_rect(item)[1] for item in left_text]),
                         max([cluster_rect[2]] + [_rect(item)[2] for item in left_text]),
                         max([cluster_rect[3]] + [_rect(item)[3] for item in left_text])],
                "texts": left_text,
                "images": cluster["images"],
            })

    for block in blocks:
        if block.get("type") == "image":
            if id(block) not in used_images:
                groups.append({"type": "block", "bbox": list(_rect(block)), "block": block})
        elif block.get("type") == "text" and id(block) not in used_text:
            groups.append({"type": "block", "bbox": list(_rect(block)), "block": block})
        elif block.get("type") == "table":
            groups.append({"type": "block", "bbox": list(_rect(block)), "block": block})
    groups.sort(key=lambda group: (group["bbox"][1], group["bbox"][0]))
    return groups

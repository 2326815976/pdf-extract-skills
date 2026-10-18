# -*- coding: utf-8 -*-
"""qc：一条命令完成一次核对。

流程：info → extract（可复用上次结果）→ 自动识别表头/映射列 → compare。
目标：把"核对"从 30+ 个工具步骤压到 1-2 次调用，且不产出无关中间文件。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from pdflib import compare as cmp


def _compact(diff):
    return {
        "base_row": diff.get("base_row"),
        "pdf_row": diff.get("pdf_row"),
        "col": diff.get("col"),
        "base": str(diff.get("base", ""))[:80],
        "pdf": str(diff.get("pdf", ""))[:80],
        "verdict": diff.get("verdict"),
    }


def run(pdf, baseline, out_dir, *, dpi=None, max_pages=None, base_skip=None, got_skip=None,
        auto_map=True, stop_total=True, force_extract=False):
    t0 = time.time()
    timings = {}
    pdf_path, base_path, out = Path(pdf), Path(baseline), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    from pdflib import detect as detect_mod
    from pdflib import extract as extract_mod

    info = detect_mod.detect(str(pdf_path), max_pages=None)
    timings["info_s"] = round(time.time() - t0, 2)
    pages = info.get("page_count")

    ext_dir = out / "extract"
    meta_path = out / "qc_meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    want = {"pdf": str(pdf_path.resolve()), "mtime_ns": pdf_path.stat().st_mtime_ns,
            "max_pages": max_pages, "dpi": dpi}
    csv_path = None
    reused = False
    if not force_extract and meta == want:
        csvs = sorted(ext_dir.glob("*.csv"))
        if csvs:
            csv_path = csvs[0]
            reused = True

    if csv_path is None:
        if ext_dir.exists():
            shutil.rmtree(ext_dir, ignore_errors=True)
        extract_mod.run(str(pdf_path), ext_dir, formats=("csv",), dpi=dpi, max_pages=max_pages)
        timings["extract_s"] = round(time.time() - t0 - timings["info_s"], 2)
        csvs = sorted(ext_dir.glob("*.csv"))
        if not csvs:
            raise RuntimeError("提取没有生成 CSV，检查 PDF 是否有表格")
        csv_path = csvs[0]
        meta_path.write_text(json.dumps(want, ensure_ascii=False), encoding="utf-8")
    else:
        timings["extract_s"] = 0.0

    base_raw, _ = cmp.load_table(str(base_path))
    got_raw, _ = cmp.load_table(str(csv_path))

    b_hi, b_start = cmp.detect_header_block(base_raw)
    g_hi, g_start = cmp.detect_header_block(got_raw)
    if base_skip is not None:
        b_start = base_skip
    if got_skip is not None:
        g_start = got_skip

    base_labels = cmp.header_labels(base_raw, b_hi) if b_hi is not None else []
    got_labels = cmp.header_labels(got_raw, g_hi) if g_hi is not None else []
    pairs, used = ([], set())
    if auto_map and base_labels and got_labels:
        pairs, used = cmp.map_columns(base_labels, got_labels)

    base_rows = base_raw[b_start:] if b_start else list(base_raw)
    got_rows = got_raw[g_start:] if g_start else list(got_raw)
    mapped_labels = None
    seq_cols = []
    if len(pairs) >= 4:
        base_rows = [[row[bi] if bi < len(row) else "" for bi, _, _ in pairs] for row in base_rows]
        got_rows = [[row[gi] if gi < len(row) else "" for _, gi, _ in pairs] for row in got_rows]
        mapped_labels = [base_labels[bi] for bi, _, _ in pairs]
        seq_cols = [i for i, label in enumerate(mapped_labels)
                    if cmp.norm_header(label) in ("序号", "单位名称")]

    if stop_total:
        cut = None
        for index, row in enumerate(base_rows):
            if any(str(cell).strip() in ("合计", "总计", "小计") for cell in row):
                cut = index + 1
                break
        if cut:
            base_rows = base_rows[:cut]

    out_md = out / "compare.md"
    summary, details = cmp.compare_tables(base_rows, got_rows, str(out_md),
                                          title="PDF 提取结果 vs 基准",
                                          seq_cols=seq_cols,
                                          base_header_labels=mapped_labels)
    (out / "compare.json").write_text(json.dumps(
        {"summary": summary, "diffs": [d for d in details if d["verdict"] != "一致"]},
        ensure_ascii=False, indent=1), encoding="utf-8")

    hints = list(summary.get("hints", []))
    if base_raw and len(pairs) < 4:
        hints.append("自动列映射失败（匹配列 <4）：请改用 compare 显式指定 --base-cols/--got-cols。")
    unmapped = []
    if mapped_labels is not None and base_labels:
        for index, label in enumerate(base_labels):
            if index not in used and cmp.norm_header(label):
                unmapped.append(cmp.norm_header(label))
    if unmapped:
        hints.append("基准中未被 PDF 匹配的列（无法比对）：" + "、".join(dict.fromkeys(unmapped)))
    base_n, got_n = len(base_rows), len(got_rows)
    if base_n and got_n >= 1.5 * base_n:
        k = max(2, round(got_n / base_n))
        suggestion = None
        if pages:
            suggestion = max(1, round(pages / k))
        if suggestion:
            hints.append("疑似重复打印：本次已提取 %d 页，建议加 --max-pages %d 只取一份后重跑 qc"
                         "（提取结果会重算，约 4-5 秒/页）。" % (pages, suggestion))

    order = {"数值不同": 0, "不一致": 1, "整行缺失": 2, "整行多余": 3}
    real = sorted(
        (d for d in details if d["verdict"] in order),
        key=lambda d: (order[d["verdict"]], d.get("base_row") or 0))[:20]
    noise = {key: value for key, value in summary["verdicts"].items()
             if key not in ("一致", "数值不同", "不一致", "整行缺失", "整行多余")}
    timings["compare_s"] = round(time.time() - t0 - timings["info_s"] - timings["extract_s"], 2)
    timings["total_s"] = round(time.time() - t0, 2)

    return {
        "pdf": str(pdf_path),
        "baseline": str(base_path),
        "pages": pages,
        "extract_reused": reused,
        "timing_s": timings,
        "rows": {"base": base_n, "pdf": got_n},
        "rate": summary["rate"],
        "verdicts": summary["verdicts"],
        "mapping": {"base_header_row": b_hi, "pdf_header_row": g_hi,
                    "columns": mapped_labels, "seq_cols": seq_cols},
        "real_diffs": [_compact(d) for d in real],
        "noise_counts": noise,
        "hints": hints,
        "report": str(out_md),
        "json": str(out / "compare.json"),
    }

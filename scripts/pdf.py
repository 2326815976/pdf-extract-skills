# -*- coding: utf-8 -*-
"""PDF 提取 Skill 的命令行入口。

所有子命令都会先做环境自检：缺依赖时自动安装到固定目录
（%USERPROFILE%/.dsh/pdfskill，可用 PDFSKILL_HOME 覆盖），换工作目录不用重装。
用法：
  python pdf.py doctor
  python pdf.py info <pdf>
  python pdf.py extract <pdf> [-o OUTDIR] [--formats md,txt,json,xlsx,docx,csv] [--dpi 200] [--max-pages N]
  python pdf.py convert <pdf> --to xlsx|docx|md|csv|txt [ -o OUTDIR]
  python pdf.py compare <提取结果.csv/xlsx/md/json> <基准.xlsx/csv> [-o REPORT.md]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

READY_ENV = "PDFSKILL_READY"


def _workdir(args):
    return Path(args.workdir or os.getcwd()).resolve()


def _ensure_env(args):
    from pdflib import env as env_mod
    info = env_mod.ensure(_workdir(args), explicit_python=args.python,
                          install=not args.no_bootstrap)
    return info


def _reexec_with_env(argv, args):
    """用装着依赖的解释器重跑自己。"""
    from pdflib import env as env_mod
    info = _ensure_env(args)
    child_env = dict(os.environ)
    child_env[READY_ENV] = "1"
    state_path = env_mod.skill_home() / "env.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("site_packages"):
                child_env["PYTHONPATH"] = state["site_packages"] + os.pathsep + child_env.get("PYTHONPATH", "")
        except Exception:
            pass
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    cmd = [info.python, str(Path(__file__).resolve())] + argv
    proc = subprocess.run(cmd, env=child_env)
    sys.exit(proc.returncode)


def _ready():
    return os.environ.get(READY_ENV) == "1"


def cmd_doctor(args):
    from pdflib import env as env_mod
    info = env_mod.ensure(_workdir(args), explicit_python=args.python,
                          install=not args.no_bootstrap)
    ok, packages = env_mod.probe(info.python)
    print(json.dumps({"ready": bool(ok),
                      "python": info.python,
                      "env_source": info.source,
                      "install_home": str(env_mod.skill_home()),
                      "packages": packages,
                      "work_dir": str(_workdir(args))}, ensure_ascii=True, indent=2))


def cmd_info(args):
    from pdflib import detect as detect_mod
    print(json.dumps(detect_mod.detect(args.pdf, max_pages=args.max_pages), ensure_ascii=True, indent=2))


def cmd_extract(args):
    from pdflib import extract as extract_mod
    formats = tuple(f.strip().lower() for f in args.formats.split(",") if f.strip())
    out_dir = Path(args.out or (_workdir(args) / (Path(args.pdf).stem + "_extract")))
    report = extract_mod.run(args.pdf, out_dir, formats=formats, dpi=args.dpi,
                             max_pages=args.max_pages, force_ocr=args.force_ocr,
                             recheck_orient=args.recheck_orient)
    print(json.dumps(report, ensure_ascii=True, indent=2))


def cmd_convert(args):
    fmt = args.to.lower()
    args.formats = {"docx": "docx", "xlsx": "xlsx", "md": "md", "markdown": "md",
                    "csv": "csv", "txt": "txt", "text": "txt"}.get(fmt, fmt)
    cmd_extract(args)


def _parse_cols(spec):
    import re as _re
    out = []
    for token in str(spec or "").split(","):
        token = token.strip()
        if not token:
            continue
        if _re.fullmatch(r"[A-Za-z]{1,2}", token):
            idx = 0
            for ch in token.upper():
                idx = idx * 26 + (ord(ch) - 64)
            out.append(idx - 1)
        elif _re.fullmatch(r"-?\d+", token):
            value = int(token)
            out.append(value - 1 if value > 0 else value)
    return out


def _select_cols(rows, cols):
    if not cols:
        return rows
    out = []
    for row in rows:
        out.append([row[c] if 0 <= c < len(row) else "" for c in cols])
    return out


HEADER_WORDS = ("序号", "单位名称", "坐落位置", "收租时间节点", "使用情况", "使用人",
                "月租金", "应收租金", "减免租金", "合计", "备注", "名称", "金额")


def _fill_down(rows, cols):
    """合并单元格：把每块首行的值向下填充（仅指定列）。"""
    if not cols or not rows:
        return rows
    grid = [list(map(str, r)) for r in rows]
    for c in cols:
        last = ""
        for row in grid:
            while len(row) <= c:
                row.append("")
            if row[c].strip():
                last = row[c]
            else:
                row[c] = last
    return grid


def _drop_leading_header_rows(rows, limit=2):
    import re as _re
    dropped = 0
    while rows and dropped < limit:
        row = rows[0]
        text = "".join(str(c) for c in row)
        hits = sum(1 for w in HEADER_WORDS if w in text)
        has_number = any(_re.fullmatch(r"[-+]?[\d,]*\.?\d+", str(c).strip())
                         for c in row if str(c).strip())
        if hits >= 2 and not has_number:
            rows = rows[1:]
            dropped += 1
        else:
            break
    return rows


def cmd_compare(args):
    from pdflib import compare as compare_mod
    base_path, got_path = Path(args.baseline), Path(args.extracted)
    base_is_pdf = base_path.suffix.lower() == ".pdf"
    got_is_pdf = got_path.suffix.lower() == ".pdf"
    if base_is_pdf or got_is_pdf:
        # 对比两个 PDF：分别提取（含表格重建），再用导出的 CSV 表比对
        from pdflib import extract as extract_mod
        tmp = _workdir(args) / "Temp" / "pdfskill" / "compare"
        tmp.mkdir(parents=True, exist_ok=True)
        results = {}
        for tag, pdf in (("a", base_path), ("b", got_path)):
            if pdf.suffix.lower() == ".pdf":
                extract_mod.run(pdf, tmp / tag, formats=("csv",), dpi=args.dpi,
                                max_pages=getattr(args, "max_pages", None))
                csvs = sorted((tmp / tag).glob("*.csv"))
                if csvs:
                    results[tag], _ = compare_mod.load_table(csvs[0])
                else:  # 没有表格时退回纯文本行
                    tables_json = json.loads((tmp / tag / "tables.json").read_text(encoding="utf-8"))
                    rows = []
                    for item in tables_json:
                        for table in item.get("tables", []):
                            rows.extend(table)
                    results[tag] = rows
            else:
                results[tag], _ = compare_mod.load_table(pdf)
        base_rows, got_rows = results["a"], results["b"]
    else:
        base_rows, _ = compare_mod.load_table(base_path)
        got_rows, _ = compare_mod.load_table(got_path)
    if args.base_skip:
        base_rows = base_rows[args.base_skip:]
    elif base_is_pdf and args.auto_header:
        # 两个 PDF 对比时，基准侧也是提取表，同样跳过表头
        base_rows = _drop_leading_header_rows(base_rows)
    if args.base_stop_total:
        cut = None
        for index, row in enumerate(base_rows):
            if any(str(cell).strip() in ("合计", "总计", "小计") for cell in row):
                cut = index + 1
                break
        if cut:
            base_rows = base_rows[:cut]
    if args.base_limit:
        base_rows = base_rows[:args.base_limit]
    if args.got_skip:
        got_rows = got_rows[args.got_skip:]
    elif args.auto_header:
        got_rows = _drop_leading_header_rows(got_rows)
    base_cols = _parse_cols(args.base_cols)
    got_cols = _parse_cols(args.got_cols)
    if base_cols:
        base_rows = _select_cols(base_rows, base_cols)
    if got_cols:
        got_rows = _select_cols(got_rows, got_cols)
    if args.base_fill_cols:
        base_rows = _fill_down(base_rows, _parse_cols(args.base_fill_cols))
    if args.got_fill_cols:
        got_rows = _fill_down(got_rows, _parse_cols(args.got_fill_cols))

    out = Path(args.out or (_workdir(args) / "compare_report.md"))
    seq_cols = _parse_cols(args.seq_cols) if args.seq_cols else []
    summary, details = compare_mod.compare_tables(base_rows, got_rows, out,
                                                  title="PDF 提取结果 vs 基准", seq_cols=seq_cols)
    (out.with_suffix(".json")).write_text(json.dumps(
        {"summary": summary, "diffs": [d for d in details if d["verdict"] != "一致"]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True, indent=2))


def cmd_qc(args):
    from pdflib import quick as quick_mod
    out = Path(args.out or (_workdir(args) / (Path(args.pdf).stem + "_qc")))
    result = quick_mod.run(
        args.pdf, args.baseline, out, dpi=args.dpi, max_pages=args.max_pages,
        base_skip=args.base_skip, got_skip=args.got_skip, auto_map=args.auto_map,
        stop_total=args.stop_total, force_extract=args.force_extract)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser():
    p = argparse.ArgumentParser(description="PDF 提取 Skill")
    p.add_argument("--workdir", help="工作目录（默认当前目录；Temp 建在这里）")
    p.add_argument("--python", help="指定基础 Python 解释器路径")
    p.add_argument("--no-bootstrap", action="store_true", help="不自动安装依赖")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("doctor", help="环境自检（缺依赖自动装到固定目录）")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("info", help="查看 PDF 类型/页数/原生 dpi")
    sp.add_argument("pdf")
    sp.add_argument("--max-pages", type=int)
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("extract", help="提取全部内容并导出多格式")
    sp.add_argument("pdf")
    sp.add_argument("-o", "--out", help="输出目录")
    sp.add_argument("--formats", default="md,txt,json,xlsx,docx,csv")
    sp.add_argument("--dpi", type=int)
    sp.add_argument("--max-pages", type=int)
    sp.add_argument("--force-ocr", action="store_true")
    sp.add_argument("--recheck-orient", action="store_true", help="忽略方向缓存，逐页重新判定")
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("convert", help="转换：--to docx|xlsx|md|csv|txt")
    sp.add_argument("pdf")
    sp.add_argument("--to", required=True)
    sp.add_argument("-o", "--out")
    sp.add_argument("--dpi", type=int)
    sp.add_argument("--max-pages", type=int)
    sp.add_argument("--force-ocr", action="store_true")
    sp.add_argument("--recheck-orient", action="store_true", help="忽略方向缓存，逐页重新判定")
    sp.set_defaults(func=cmd_convert)

    sp = sub.add_parser("compare", help="提取结果与基准比对")
    sp.add_argument("extracted", help="提取结果：csv/xlsx/md/json 或另一个 PDF")
    sp.add_argument("baseline", help="基准：xlsx/csv/md/json 或另一个 PDF")
    sp.add_argument("-o", "--out", help="报告输出路径（.md）")
    sp.add_argument("--dpi", type=int)
    sp.add_argument("--base-skip", type=int, default=0, help="基准表跳过开头行数（表头/标题）")
    sp.add_argument("--got-skip", type=int, default=0, help="提取表跳过开头行数")
    sp.add_argument("--no-auto-header", dest="auto_header", action="store_false",
                    help="不自动跳过提取表开头的表头行")
    sp.add_argument("--base-cols", help="基准取哪些列，如 A,B,C,G（Excel 列字母或 1 起的序号）")
    sp.add_argument("--base-stop-total", action="store_true",
                    help="基准只取到第一处 合计/总计 行为止（去掉页脚说明行）")
    sp.add_argument("--base-limit", type=int, help="基准最多取前 N 行（跳过表头之后）")
    sp.add_argument("--max-pages", type=int, help="PDF vs PDF 比对时只提取前 N 页")
    sp.add_argument("--got-cols", help="提取表取哪些列，如 1,2,3,4")
    sp.add_argument("--base-fill-cols", help="基准里需要向下填充的合并单元格列，如 A,B")
    sp.add_argument("--got-fill-cols", help="提取表里需要向下填充的列，如 1,2")
    sp.add_argument("--seq-cols", help="合并单元格列（如 1,2），按值序列比对，忽略行位置")
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("qc", help="一条命令核对：提取(可复用) + 自动列映射 + compare")
    sp.add_argument("pdf")
    sp.add_argument("baseline")
    sp.add_argument("-o", "--out", help="输出目录（默认 <PDF名>_qc，内含 extract/ 与 compare.md）")
    sp.add_argument("--max-pages", type=int, help="只提取前 N 页（同表重复打印时用来只取一份）")
    sp.add_argument("--dpi", type=int)
    sp.add_argument("--base-skip", type=int, help="强制跳过基准开头 N 行（默认自动识别表头）")
    sp.add_argument("--got-skip", type=int, help="强制跳过提取表开头 N 行")
    sp.add_argument("--no-auto-map", dest="auto_map", action="store_false",
                    help="关闭按表头文字自动映射列")
    sp.add_argument("--no-stop-total", dest="stop_total", action="store_false",
                    help="基准不截到第一处合计行")
    sp.add_argument("--force-extract", action="store_true", help="忽略复用，强制重新提取")
    sp.set_defaults(func=cmd_qc)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "doctor":
        cmd_doctor(args)
        return

    if not _ready():
        _reexec_with_env(argv, args)
        return
    args.func(args)


if __name__ == "__main__":
    main()

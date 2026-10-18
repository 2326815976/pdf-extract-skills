# pdf-extract-skills

> 一个自包含的 **Agent Skill**：从 PDF 提取文本与表格（含扫描件 OCR、自动摆正、表格重建），导出 Markdown / CSV / Excel / Word，并可与 Excel/CSV 基准逐单元格比对。
>
> A self-contained Agent Skill that extracts text and tables from PDFs (text layer or scanned + OCR), exports Markdown / CSV / Excel / Word, and diffs PDF data against an Excel/CSV baseline.

- 遵循 [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills) 的目录约定：`SKILL.md` + `scripts/` + `references/`
- `SKILL.md` 内的 `whenToUse` 是 DSH 运行时扩展字段，其他平台加载器可忽略（信息已包含在 `description` 中）

## 特性

- **自动识别 PDF 类型**：`text_based` 走文本层直抽（<1 秒、零识别误差）；`scanned` 走逐页 OCR；`mixed` 逐页判断
- **扫描件逐页摆正**：0/90/180/270 自动判定，双面扫描奇偶页反向也能处理
- **表格重建**：框线网格 + 无框线文本行聚类兜底；合并表头、窄列竖排文字自动归并
- **多格式导出**：Markdown / TXT / CSV / Excel / Word / JSON（带坐标原文与表结构）
- **数据比对**：`qc` 一条命令完成「提取 + 自动列映射 + 逐单元格比对」；`compare` 支持行列映射、合并列按值序列比对
- **零配置环境**：首次运行自动创建 venv 并把依赖装到固定用户目录，换项目不用重装
- **本地运行**：不联网、不上传任何文件（仅首次装依赖需要网络）

## 工作方式

```
PDF ──info──> text_based ──> PyMuPDF 直抽文本/表格 ──┐
        └───> scanned ────> 逐页方向判定 + RapidOCR ──┴──> 表格重建 ──> md/csv/xlsx/docx
                                                              └──> compare/qc ──> 差异报告
```

## 环境要求

- Python **3.10+**（已在 Python 3.12.7 + Windows 验证）
- 首次运行需联网安装依赖（约数十 MB，装到 `~/.dsh/pdfskill`）
- 可选：`pip install -r requirements.txt` 手动安装（版本为已验证组合）

## 安装

```bash
git clone https://github.com/2326815976/pdf-extract-skills.git
cd pdf-extract-skills
python scripts/pdf.py doctor
```

`doctor` 输出 `"ready": true` 即可用。首次会自动完成依赖安装；也可以先执行 `pip install -r requirements.txt`，再加 `--no-bootstrap` 跳过自动安装。

> Windows 示例使用 `python scripts\pdf.py`，Linux/macOS 使用 `python scripts/pdf.py`。

## 快速开始

```powershell
# 1) 看 PDF 类型/页数/原生 dpi（1 秒）
& $PY "scripts\pdf.py" info "D:\path\to\file.pdf"

# 2) 提取全部内容
& $PY "scripts\pdf.py" extract "D:\path\to\file.pdf" -o "D:\out\file_extract"

# 3) 转 Word / Excel / Markdown / CSV / TXT
& $PY "scripts\pdf.py" convert "D:\path\to\file.pdf" --to docx

# 4) 与 Excel 基准核对（推荐：一条命令完成提取+自动映射+比对）
& $PY "scripts\pdf.py" qc "D:\path\to\file.pdf" "D:\baseline.xlsx" -o "D:\out\qc"
```

`$PY` 指 Python 解释器（如 `python` 或完整路径）。核对类任务请先读 [SKILL.md](SKILL.md) 的「时间纪律」与 `qc` 说明，避免不必要的全量人工复核。

## 命令一览

| 命令 | 作用 |
|---|---|
| `doctor` | 环境自检；缺依赖时自动安装到固定目录 |
| `info` | 输出 PDF 类型、页数、原生 dpi、每页文本量/图片数 |
| `extract` | 提取并导出 `md/txt/csv/xlsx/docx/json`；支持 `--max-pages`、`--dpi`、`--force-ocr`、`--recheck-orient` |
| `convert` | 只导出指定格式：`--to docx|xlsx|md|csv|txt` |
| `qc` | 一条命令核对：提取（可复用）+ 自动识别表头/映射列 + compare + 诊断提示 |
| `compare` | 提取结果 vs 基准逐单元格比对；支持 `--base-skip`、`--base-cols`、`--got-cols`、`--seq-cols` 等 |

## 输出说明

- `<stem>.md`：全文 Markdown + 每页表格
- `<stem>.csv`：各页表格合并为一张表
- `<stem>.xlsx`：每页一个 sheet + 「汇总」sheet
- `<stem>.docx`：标题 + 每页表格
- `lines.json` / `tables.json`：带坐标的识别原文与表结构
- `run_report.json`：耗时、每页行数、告警、处理方式

`qc` 输出 JSON 关键字段：`rows`（基准/PDF 行数）、`rate`、`verdicts`、`real_diffs`、`hints`、`mapping`、`timing_s`。

## 目录结构

```
pdf-extract-skills/
├── SKILL.md              # Skill 主文档（时间纪律、任务路径、参数、坑）
├── references/
│   └── workflow.md       # 决策树、比对策略、报告模板、排错
├── scripts/
│   ├── pdf.py            # CLI 入口：doctor/info/extract/convert/compare/qc
│   └── pdflib/           # 检测、渲染、OCR、表格重建、导出、比对
├── requirements.txt
├── LICENSE
└── .gitignore
```

## 平台与网络

- **已验证**：Windows 10/11 + Python 3.12（PowerShell）
- **理论兼容**：macOS / Linux（代码按 `os.name` 处理 `Scripts` 与 `bin`，但未实测）
- 首次运行会联网 `pip install`；离线环境请用 `--python` 指向已有依赖的解释器，或 `--no-bootstrap` + 手动安装
- 安装目录 `~/.dsh/pdfskill` 可用环境变量 `PDFSKILL_HOME` 覆盖

## 已知限制

- **加密 PDF**：需先解密（当前不处理密码）
- **手写体 / 印章遮挡**：OCR 主要针对印刷体，手写与红章覆盖识别率低
- **无框线表格**：退化为文本行聚类，列边界可能不准
- **复杂合并单元格 / 跨页续表**：合并格只保留值、跨页表头去重逻辑较简单，复杂版式可能需人工核对
- **打印件本身缺内容**（裁切、漏印整列）：属于源文件问题，任何工具都无法还原，可用 `compare` 核出差异
- **同一张表重复打印**：`qc` 会提示「行数 ≈ 基准×2」，按提示加 `--max-pages` 只取一份
- **超大字**：上千页 PDF 建议分批处理

## 许可

本项目使用 [MIT License](LICENSE)。

运行期依赖注意：**PyMuPDF 采用 AGPL-3.0**（Artifex Software），如需商用/闭源分发请自行评估其许可要求；其余依赖（RapidOCR、OpenCV、numpy、Pillow、openpyxl、python-docx）均为宽松许可（Apache-2.0 / BSD / MIT / HPND）。

## English (short)

A self-contained Agent Skill for PDF content extraction and data comparison:

- detects text-layer vs scanned PDFs, auto-rotates pages, OCRs with RapidOCR, rebuilds tables, exports MD/CSV/XLSX/DOCX
- `qc` compares a PDF against an Excel/CSV baseline with automatic header/column mapping
- self-bootstraps its Python environment into `~/.dsh/pdfskill` (override with `PDFSKILL_HOME`)
- tested on Windows + Python 3.12; macOS/Linux are theoretically supported but untested
- MIT licensed; note the runtime dependency PyMuPDF is AGPL-3.0

# pdf-extract-skills

> 一个自包含的 **Agent Skill**：从 PDF 提取文本与表格（含扫描件 OCR、自动摆正、表格重建），导出 Markdown / CSV / Excel / Word，并可与 Excel/CSV 基准逐单元格比对。
>
> A self-contained Agent Skill that extracts text and tables from PDFs (text layer or scanned + OCR), exports Markdown / CSV / Excel / Word, and diffs PDF data against an Excel/CSV baseline.

- 遵循 [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills) 的目录约定：`SKILL.md` + `scripts/` + `references/`
- `SKILL.md` 内的 `whenToUse` 是 DSH 运行时扩展字段，其他平台加载器可忽略（信息已包含在 `description` 中）

## 特性

- **页级自动路由**：`native / visual / scan / blank` 四类页面分别处理；图片不会触发整页 OCR
- **原生文本优先**：文本型简历、报告、文章直接抽取，保留标题、段落、列表与旁侧图片
- **扫描件逐页摆正**：0/90/180/270 自动判定，双面扫描奇偶页反向也能处理
- **正文/表格分流**：框线表格经过结构、填充率、表头和数值证据校验；低置信度结果退回正文
- **多策略 Word**：正文流式重建、截图型版面转换、扫描表格输出、图片保真兜底
- **复杂扫描表单**：分段竖线补全网格；结构仍过稀时 Word 自动保留整页版面，避免标题和表单框线丢失
- **多格式导出**：Markdown / TXT / CSV / Excel / Word / JSON（带坐标原文与表结构）
- **数据比对**：`qc` 一条命令完成「提取 + 自动列映射 + 逐单元格比对」；`compare` 支持行列映射、合并列按值序列比对
- **零配置环境**：首次运行自动创建 venv 并把依赖装到固定用户目录，换项目不用重装
- **本地运行**：不联网、不上传任何文件（仅首次装依赖需要网络）

## 工作方式

```
PDF -> 页级路由 -> native / visual / scan / blank
                 -> 原生文本 / 局部 OCR / 整页摆正 OCR
                 -> flow / layout / table / image DOCX
                 -> md / txt / csv / xlsx / json
                 -> compare / qc
```

## 环境要求

- 推荐 Python **3.12**；`rapidocr-onnxruntime 1.4.4` 要求 `<3.13`，因此 3.13 暂不可用
- Python 3.10/3.11 可尝试自动安装，由依赖解析选择可用版本
- 首次运行需联网安装依赖，安装到平台用户缓存目录 `pdf-extract-skill`
- 可选：`pip install -r requirements.txt` 手动安装固定依赖组合

## 安装

```bash
git clone https://github.com/2326815976/pdf-extract-skills.git
cd pdf-extract-skills
python scripts/pdf.py doctor
```

`doctor` 输出 `"ready": true` 即可用。首次会自动完成依赖安装；也可以先执行 `pip install -r requirements.txt`，再加 `--no-bootstrap` 跳过自动安装。

> 示例统一使用 `python "scripts/pdf.py"`；Windows 下如脚本由 PowerShell 执行，请显式选择 PowerShell 7。

## 快速开始

调用默认规则：

- 未指定输出格式且未要求“转换”：默认读取 PDF 内容，使用 `extract --formats md,txt`。
- 明确指定 Word/Excel/Markdown/CSV 等格式或要求“转换”：使用 `convert --to <格式>`，首次速度优先，交付后再询问是否校验。
- Windows 下使用 PowerShell 7 完整路径或跨 Shell 的 `python "scripts/pdf.py"` 命令，不要依赖默认 Bash。

`convert` 完成后会在 `run_report.json` 写入 `verification_prompt`，并输出：

```text
转换已完成。是否需要我继续校验并给出转换准确度？
```

```powershell
# 1) 可选：查看页级路由
python "scripts/pdf.py" info "D:\path\to\file.pdf"

# 2) 默认读取内容
python "scripts/pdf.py" extract "D:\path\to\file.pdf" --formats md,txt -o "D:\out\read"

# 3) 自动选择 flow / layout / table 转 Word
python "scripts/pdf.py" convert "D:\path\to\file.pdf" --to docx

# 4) 与 Excel 基准核对（推荐：一条命令完成提取+自动映射+比对）
python "scripts/pdf.py" qc "D:\path\to\file.pdf" "D:\baseline.xlsx" -o "D:\out\qc"
```

`python` 可替换为解释器完整路径。核对类任务请先读 [SKILL.md](SKILL.md) 的「执行规则」与 `qc` 说明，避免不必要的全量人工复核。

## 自检

```powershell
python -m unittest discover -s "tests" -v
```

核心单元测试覆盖原生文本与扫描路由、双栏阅读顺序、原生表格识别、纵向合并序号修复、正文 Word 导出和空白页处理。

## 命令一览

| 命令 | 作用 |
|---|---|
| `doctor` | 环境自检；缺依赖时自动安装到固定目录 |
| `info` | 输出文档类型、推荐路由、每页 `native/visual/scan/blank`、文本和图片指标 |
| `extract` | 自动路由并导出 `md/txt/csv/xlsx/docx/json`；支持 `--profile`、`--table-mode`、`--ocr-mode`、`--docx-mode` |
| `convert` | 自动路由后导出指定格式：`--to docx|xlsx|md|csv|txt` |
| `qc` | 一条命令核对：提取（可复用）+ 自动识别表头/映射列 + compare + 诊断提示 |
| `compare` | 提取结果 vs 基准逐单元格比对；支持 `--base-skip`、`--base-cols`、`--got-cols`、`--seq-cols` 等 |

## 输出说明

- `<stem>.md`：全文 Markdown + 每页表格
- `<stem>.csv`：各页表格合并为一张表
- `<stem>.xlsx`：每页一个 sheet + 「汇总」sheet
- `<stem>.docx`：按路由选择正文流式、版面保真或表格模式
- `lines.json` / `tables.json`：带坐标的识别原文与表结构
- `route.json`：页级路由、OCR 页、表格页和 Word 模式
- `run_report.json`：耗时、每页模式、置信度、告警和处理方式

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

- 推荐使用 Python 3.12；OCR 依赖暂不支持 Python 3.13。
- Windows 示例使用 PowerShell；代码同时兼容虚拟环境的 `Scripts` 与 `bin` 路径。
- 首次运行会联网 `pip install`；离线环境请用 `--python` 指向已有依赖的解释器，或 `--no-bootstrap` + 手动安装
- Windows 默认安装目录为 `%LOCALAPPDATA%\pdf-extract-skill`，macOS/Linux 使用对应用户缓存目录；可用 `PDFSKILL_HOME` 覆盖

## 已知限制

- **加密 PDF**：需先解密（当前不处理密码）
- **手写体 / 印章遮挡**：OCR 主要针对印刷体，手写与红章覆盖识别率低
- **无框线表格**：默认仅接受结构证据强的结果，否则退回正文；需要时用 `--table-mode force`
- **复杂合并单元格 / 跨页续表**：合并格只保留值、跨页表头去重逻辑较简单，复杂版式可能需人工核对
- **截图型 Word**：布局模式视觉保真较好，截图内文字是否可编辑取决于原 PDF 是否提供文本层
- **打印件本身缺内容**（裁切、漏印整列）：属于源文件问题，任何工具都无法还原，可用 `compare` 核出差异
- **同一张表重复打印**：`qc` 会提示「行数 ≈ 基准×2」，按提示加 `--max-pages` 只取一份
- **超大字**：上千页 PDF 建议分批处理

## 许可

本项目使用 [MIT License](LICENSE)。

运行期依赖注意：**PyMuPDF 采用 AGPL-3.0**（Artifex Software），如需商用/闭源分发请自行评估其许可要求；其余依赖（RapidOCR、OpenCV、numpy、Pillow、openpyxl、python-docx、pdf2docx）均为宽松许可（Apache-2.0 / BSD / MIT / HPND）。

## English (short)

A self-contained Agent Skill for PDF content extraction and data comparison:

- detects text-layer vs scanned PDFs, auto-rotates pages, OCRs with RapidOCR, rebuilds tables, exports MD/CSV/XLSX/DOCX
- `qc` compares a PDF against an Excel/CSV baseline with automatic header/column mapping
- self-bootstraps into a platform user cache directory (override with `PDFSKILL_HOME`)
- designed for Python 3.12 with platform-aware virtual environment paths
- MIT licensed; note the runtime dependency PyMuPDF is AGPL-3.0

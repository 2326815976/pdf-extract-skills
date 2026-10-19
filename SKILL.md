---
name: pdf-extract
description: Extract complete content from PDF files (text, tables, layout metadata) and convert to Markdown/Excel/Word/CSV, or compare PDF data against an Excel/CSV baseline. Handles scanned PDFs with automatic per-page rotation and OCR, bootstrapping its own Python environment into a fixed user-level home when dependencies are missing.
whenToUse: Use when the user uploads or points to a PDF and wants its full content extracted, converted (PDF to Markdown/Excel/Word/CSV), tabulated, or compared field-by-field with a baseline spreadsheet.
license: MIT
---

# PDF 内容提取 / 转换 / 比对

一条自包含流水线：**页级路由 → 原生文本优先 / 截图 OCR / 扫描件摆正 + OCR → 正文或表格分流 → 导出 Markdown/CSV/Excel/Word**，并可与 Excel/CSV 基准逐单元格比对。默认 `auto`，不需要先运行 `info` 再猜参数。

## 0. 环境与执行规则

### 0.1 执行器与 Shell（Windows 硬约束）

- Windows 下必须显式使用 PowerShell 7，不得依赖默认 Bash 执行 PowerShell 语法。
- 若检测到 `E:/Env/PowerShell/7.5.3/pwsh.exe`，优先使用该完整路径；其他环境用 `pwsh`。
- 命令示例优先采用跨 Shell 形式：`python "scripts/pdf.py" ...`，避免 `& $PY` 在 Bash 中失效。
- 如果因 Shell 选错导致 `&`、`Get-ChildItem` 等语法失败，只允许用完整 `pwsh.exe` 路径重试一次，不要改写业务流程。

### 0.2 环境：装一次，长期复用

```powershell
cd <本仓库根目录>
python "scripts/pdf.py" doctor      # 需要时替换为完整解释器路径
```

- 依赖安装在平台用户缓存目录 `pdf-extract-skill`：Windows 为 `%LOCALAPPDATA%\pdf-extract-skill`，macOS 为 `~/Library/Caches/pdf-extract-skill`，Linux 为 `${XDG_CACHE_HOME:-~/.cache}/pdf-extract-skill`；可用 `PDFSKILL_HOME` 覆盖。
- 每次运行先复用显式 `--python` / `PDFSKILL_PYTHON`、已记录解释器、已有 venv 和 PATH 环境，只有找不到可用环境时才安装。
- 首次运行自动建 venv 并安装 pymupdf / rapidocr-onnxruntime / numpy / pillow / openpyxl / python-docx / pdf2docx（需联网；`--no-bootstrap` 可禁用）。
- 已有可用环境会直接复用：`--python <path>`、环境变量 `PDFSKILL_PYTHON`、`PDFSKILL_VENVS`。
- 下文示例使用 `python "scripts/pdf.py"`；如果 Python 不在 PATH，直接替换为解释器完整路径。

### 0.3 执行纪律

- 提取和转换优先使用一次 `auto` 路由，不要先用 `info` 再手工猜 OCR 或表格参数。
- 核对优先使用一条 `qc`；只有自动列映射失败时才退回 `compare`。
- 同一动作最多重试一次。出现明确告警时针对受影响页面处理，不重复全量提取。
- 只抽查 `real_diffs` 或告警指向的页面，避免无关的全量目视复核。
- 最终只保留用户需要的产物；渲染图、临时脚本和诊断文件用完删除。

### 0.4 反模式

1. **禁止自建"逐格对齐/比对"脚本。** 内置 `compare` 已覆盖行/列映射（`--base-skip`、`--base-cols`、`--got-cols`、`--seq-cols`…）。参数调 1 轮仍不齐，几乎都是"重复打印 / 表头未跳过"，用 `--max-pages` 隔离一份重跑，不要造轮子。
2. **禁止全量原图目视。** 只对 `compare` 报出的“数值不同/不一致”做少量裁图，避免逐格检查整份文档。
3. **禁止"独立复核"二次全量重算。** 同一结论不需要两套方法互证，除非用户明确要求复核。
4. **禁止在交付目录堆中间脚本/裁图。** 临时脚本用完删；产物只留报告（用户要才加差异清单）。
5. **禁止 spawn 子 agent 去 profile 基准表。** `qc` 会自动识别表头、按表头文字映射列；需要看清结构时，`qc` 输出里已带 `mapping` 和"未匹配到的基准列"。
6. **禁止"先全量提取、再发现重复打印、再重提一遍"。** 按 `qc` 的提示用 `--max-pages <单份页数>` 隔离样本，并在同一输出目录复用已有结果。

## 1. 任务类型与默认路径

| 类型 | 判定方式 | 默认动作 |
|---|---|---|
| **读取内容** | 未指定输出格式，也没有明确要求“转换” | 只做内容提取，使用 `extract --formats md,txt`，读取结果并回答内容 |
| **格式转换** | 明确要求转成 Word/Excel/Markdown/CSV 等，或明确说“转换” | 使用 `convert --to <格式>`，速度优先，首次交付前不做逐页准确性校验 |
| **读取 / 核对** | 明确问“对不对/准不准”、提供基准文件，或要求准确度 | 使用 `qc` 或 `compare` |

读取内容示例：

```powershell
python "scripts/pdf.py" extract "D:\path\file.pdf" --formats md,txt -o "D:\out\read"
```

格式转换首次只交付结果。交付文本末尾必须包含：

> 转换已完成。是否需要我继续校验并给出转换准确度？

`convert` 命令会同时在 `run_report.json` 写入 `verification_prompt` 和 `next_action=ask_verification`，并在 stderr 输出 `[next]` 提示。不要省略该提示。

用户确认校验后，再执行以下任一方式并给出准确度：

- 有 Excel/CSV 基准：使用 `qc` 输出一致率和真实差异。
- 无基准的表格转换：重新提取源 PDF 表格，与转换后的 xlsx/csv/docx 表格做逐单元格归一化比对。
- 无基准的正文/Word 转换：比较源 PDF 提取文本与转换结果的归一化字符覆盖率、表格单元格一致率和页数；报告 OCR 告警与无法校验项。

**核对默认只用 1 条命令（`qc`）：**

```powershell
python "scripts/pdf.py" qc "D:\path\file.pdf" "D:\baseline.xlsx" -o "D:\out\qc"
```

`qc` 内部一次完成：info → 提取（同一 `-o` 目录再次调用可复用）→ 自动识别表头并按表头文字映射列 → compare → 写 `compare.md`，stdout 输出精简 JSON（行数、一致率、`real_diffs`、`hints`、未匹配的基准列、耗时）。

- 出现"行数 ≈ 基准×2，疑似重复打印"提示时，加 `--max-pages <单份页数>` 再跑一次；其余情况不要重跑。
- 只有提示"匹配列 <4"（自动映射失败）时，才退到第 4 节的 `compare` 手工指定列。
- `qc` 已默认：自动跳过表头、按顺序取到"合计"行、序号/单位名称按值序列比（`--no-auto-map` / `--no-stop-total` 可关）。

**收尾 2 步：**

- 只对 `real_diffs` 里"数值不同/不一致"的**真实差异**裁原图，**≤5 张**。
- 写 **1 份报告**（三栏：真实差异 / 已知噪声 / 无法比对），交付即可。

> 不要拆成 info、extract、compare、读 CSV、检查表结构、自建对齐脚本的一长串步骤。优先使用内置路由和 `qc`。

## 2. 页面路由（通常不需要单独运行 info）

```powershell
python "scripts/pdf.py" info "D:\path\to\file.pdf"
```

关键字段：`document_kind`、`recommended_route`、`route_counts`、每页的 `route` / `route_reason` / `image_coverage`。

- `native`：文本层可靠。即使含照片、Logo、二维码也直接走原生文本，不因“有图片”触发 OCR。
- `visual`：文本较少、截图或图片占比高。Word 默认使用布局转换；提取正文时才补 OCR。
- `scan`：几乎没有文本层，逐页判断方向后 OCR。
- `blank`：空白或近空白页，不触发 OCR。
- `recommended_route` 是整份文档的摘要；真正的提取按每页 `route` 执行。

`info` 仍可用于排查，但正常提取/转换直接运行 `extract` 或 `convert` 即可。

## 3. 提取 / 转换

```powershell
python "scripts/pdf.py" extract "D:\path\file.pdf" -o "D:\out\file_extract"
python "scripts/pdf.py" convert "D:\path\file.pdf" --to docx   # docx/xlsx/md/csv/txt
```

默认自动路由：

- 文本型正文走 `native-flow`，Word 保留标题、段落、列表和旁侧图片。
- 截图/海报型文档走 `visual-layout`，由 `pdf2docx` 保留版面；若依赖不可用，自动退回逐页图片 Word。
- 复杂扫描表单如果表格列塌缩、填充率低，先补全分段竖线并构建可编辑的合并单元格网格；只有合并区域无法稳定形成矩形时才退回版面保真模式。
- 扫描表格在 OCR 后输出真实 Word 表格；检测到纵向或横向合并区域时使用原生 Word 合并单元格，低置信度假表格自动退回正文。
- 纵向合并的序号/单位名称按逐列真实横线归到区块首行；OCR 漏掉的单个序号只在序号列且区块存在单位名称时，按连续规律补齐。
- 与序号粘连的单位名称会自动剥离前导数字，例如 `6检察` 变为序号 `6`、单位 `检察`。

产物：`<stem>.md`、`.txt`、按需生成的 `.csv` / `.xlsx`、`.docx`、`lines.json`、`tables.json`、`route.json`、`run_report.json`。`route.json` 和 `run_report.json` 会记录页级路由、OCR 页、表格页、耗时与告警；扫描表格页还包含 `quality.sequence_repair`，可审计合并跨度、已知序号、补齐数量和推断结果。

常用参数：

- `--formats md,xlsx`：只出需要的格式。
- `--profile auto|text|table|visual`：目标偏好；默认 `auto`。
- `--table-mode auto|force|off`：表格接受策略；默认只接受结构证据足够的表格。
- `--ocr-mode auto|force|off`：OCR 策略；默认按页自动决定。
- `--docx-mode auto|flow|layout|table|image`：Word 输出策略；默认按文档和页面自动选择。
- `--max-pages N`：只处理前 N 页。
- `--dpi` / `--recheck-orient`：仅在低清、方向异常时干预。

## 4. 数据比对（读取/核对任务用）

**首选第 1 节的 `qc`（一条命令完成提取 + 自动映射 + 比对）。** 只有需要精确控制行列、或 `qc` 提示"匹配列 <4"时，才用下面的 `compare`：

```powershell
python "scripts/pdf.py" compare "D:\out\file_extract\file.csv" "D:\baseline.xlsx" -o "D:\out\compare.md"
```

列/行对不上时显式指定（长命令写成一行）：

```powershell
python "scripts/pdf.py" compare "extract.csv" "baseline.xlsx" -o "compare.md" --base-skip 4 --base-stop-total --base-cols A,B,C,G,H,I,J,L,M,N,O --got-skip 1 --got-cols 1,2,3,4,5,6,7,8,9,10,11 --base-fill-cols A,B --seq-cols 1,2
```

| 参数 | 作用 |
|---|---|
| `--base-skip N` / `--got-skip N` | 跳过开头标题/表头行（提取表默认自动跳过表头） |
| `--base-cols` / `--got-cols` | 选列：`A,B,C`（Excel 字母）或 `1,2,3`（序号） |
| `--base-stop-total` | 基准只取到第一处"合计"行，去掉页脚 |
| `--base-fill-cols` / `--got-fill-cols` | 合并单元格向下填充 |
| `--seq-cols` | 合并列（序号/单位名称）按**值序列**比，不看落在第几行 |
| `--max-pages N` | PDF vs PDF 比对时只提取前 N 页 |

- **先跑一次，最多再调 1 轮参数**；仍对不齐 → 用 `--max-pages` 隔离一份，不要写自定义脚本。
- 输出末尾有 **「诊断与下一步」**（行数倍数、低一致率、真实差异候选数、时间提醒），照做即可。结果 JSON 里同样有 `hints`。
- 比对两个 PDF：第二个参数也写成 `.pdf`（两侧表头自动跳过）。输出 `compare.md` + 同名 `.json`。

**差异分级（判读一步到位，别逐格看）：**

| 结论 | 怎么处理 |
|---|---|
| 数值不同 | 真实差异候选 → 只对这几处裁图确认 |
| 不一致（非截断） | 先判断是否合并单元格串列/OCR 串行；确认后再裁图 |
| 截断 | PDF 打印裁切，属源问题，直接归类，**不裁图** |
| 单格缺失/多余 | 多为合并格、备注跨行居中，归类为噪声 |
| 成批整行多余/缺失 | 重复打印或表头未跳过 → 调 `--max-pages` / `--got-skip` |
| 合计行差异 | 单独用一句话判断"合计与明细是否自洽"，不展开推演 |

## 5. 性能原则

- 原生文本页直接提取，不对整页 OCR。
- 截图型 PDF 仅转 Word 时优先保存版面，不为排版先做全页 OCR。
- OCR 只处理路由判定需要的页面。
- 同一输出目录再次运行时可复用方向判定结果。
- 不对同一文档重复做无目标的全量提取或全量复核。

## 6. 必须知道的坑

1. **同一张表可能被重复打印。** 扫描件里常见同一份表装订两次或夹杂空白页。提取后若 CSV 行数呈整数倍关系，先用 `--max-pages <单份页数>` 只抽一份再比；若两份内容不同，要点明“PDF 内部版本不一致”。
2. **扫描件先摆正**：逐页判 0/90/180/270，双面扫描奇偶页反向也能处理；不要整本统一旋转。
3. **原生分辨率**：渲染 dpi 超过扫描图原生 dpi 无收益，脚本已按 `native_dpi` 自动定。
4. **合并单元格**：序号/单位名称常一格多行跨多行，重建会把格内换行拼成一条并尽量落块首行；比对用 `--seq-cols` 更稳。
5. **打印版可能本来缺内容**：PDF 会裁长文本尾、漏印整列。出现"截断/缺失"先归类，只对真实数值差异裁原图确认，别当成提取错误。
6. **不要自动跑第二轮全页/单元格复核**：重复高清识别可能把竖排合并格叠字。只对确认有问题的个别格子做裁切复核。
7. **图片不等于扫描件**：简历照片、Logo、二维码属于页面元素，只要文本层可靠就走 `native`。
8. **不要先 `info` 再手工推断完整流程**：`extract/convert` 已内置一次性的页级路由，`info` 只用于排查。
9. **加密 PDF**：先解密；**超大 PDF**：先 `--max-pages 3` 试跑。

## 7. 排错

- `ready:false`：看缺哪个包；确认缓存目录后删除 `pdf-extract-skill` 并重跑 doctor 重装。
- 找不到 Python：`--python "C:\...\python.exe"`（推荐 3.12；3.13 暂不兼容 rapidocr-onnxruntime）。
- OCR 全是乱码：方向判错，删输出目录里的 `pages/orientation.json` 或加 `--recheck-orient` 重跑；仍不对就看 `references/workflow.md` 手动处理。
- `compare` 一致率 <70%：先按输出提示调行/列参数，最多 1 轮；仍不行就是数据分份问题（见第 6 节第 1 条）。
- 更细的决策树、参数组合、报告模板：`references/workflow.md`。

## 8. 交付格式（默认，不额外发挥）

- 只交 **1 份 Markdown 报告**（结论 + 差异表 + 无法核对项）；用户要"清单/表格"时才生成 xlsx。
- 不默认生成证据图、裁图、中间 JSON；需要时再裁 ≤5 张，并在报告里引用路径。
- 中间脚本不落在交付目录；用完删除。

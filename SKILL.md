---
name: pdf-extract
description: Extract complete content from PDF files (text, tables, layout metadata) and convert to Markdown/Excel/Word/CSV, or compare PDF data against an Excel/CSV baseline. Handles scanned PDFs with automatic per-page rotation and OCR, bootstrapping its own Python environment into a fixed user-level home when dependencies are missing.
whenToUse: Use when the user uploads or points to a PDF and wants its full content extracted, converted (PDF to Markdown/Excel/Word/CSV), tabulated, or compared field-by-field with a baseline spreadsheet.
license: MIT
---

# PDF 内容提取 / 转换 / 比对

一条自包含流水线：**检测类型 → 有文本层直抽；扫描件逐页自动摆正 + OCR + 表格重建 → 导出 Markdown/CSV/Excel/Word**，并可与 Excel/CSV 基准逐单元格比对。

## 0. 环境与时间纪律

### 0.1 环境：装一次，长期复用

```powershell
cd <本仓库根目录>
$PY = "python"                     # 或解释器完整路径；DSH 环境用 load_workspace_dependencies 返回的 python.exe
& $PY "scripts\pdf.py" doctor      # 输出 ready: true 才继续
```

- 依赖固定安装在 `~/.dsh/pdfskill`（Windows 为 `%USERPROFILE%\.dsh\pdfskill`；可用 `PDFSKILL_HOME` 覆盖），换工作目录不用重装。
- 首次运行自动建 venv 并安装 pymupdf / rapidocr-onnxruntime / numpy / pillow / openpyxl / python-docx（需联网；`--no-bootstrap` 可禁用）。
- 已有可用环境会直接复用：`--python <path>`、环境变量 `PDFSKILL_PYTHON`、`PDFSKILL_VENVS`。
- 下文示例中的 `$PY` 与 `scripts\pdf.py` 分别指解释器和本仓库脚本；Linux/macOS 把 `\` 换成 `/`。

### 0.2 时间纪律（硬性，先读这一节）

单次任务总时长上限 **10 分钟**；24 页扫描件核对目标 **≤3 分钟**。到上限必须收尾：交付已确认结论 + 未确认项，不允许继续加验证。

| 环节 | 预算 | 超出时 |
|---|---|---|
| doctor（已装好） | <2 s | 缺包才装，装完直接用 |
| info | <2 s | — |
| 扫描件提取 | 4–5 s/页（24 页 ≈100 s） | 检查类型/页数；不要反复重跑 |
| compare | **≤5 s** | 参数问题，看输出里的「诊断与下一步」 |
| 差异原图复核 | **≤5 张裁图、≤60 s** | 只裁真实差异，不裁噪声 |
| 写报告 | 1 份 md，一次写完 | 不默认出证据图/差异 xlsx |
| **工具步骤总数** | **≤8 次** | 用 `qc` 一条命令替代 info+extract+compare |

- 同一动作**最多重试 1 次**；某一步超过 2× 预算就换方法，而不是加大验证力度。
- 每做完一步顺手记一下时间（`Get-Date` 或脚本自带 `seconds`），发现偏离立即收窄范围。
- **实测：慢的主因不是工具，而是模型每多一步就多 ~19 s（TTFT 占大头）。** 34 步的核对 = 10 分钟模型时间 + 1.5 分钟工具时间。所以优先砍步骤数：能一条 `qc` 解决的绝不拆成五次调用。

### 0.3 反模式：这样会拖成一小时（禁止）

1. **禁止自建"逐格对齐/比对"脚本。** 内置 `compare` 已覆盖行/列映射（`--base-skip`、`--base-cols`、`--got-cols`、`--seq-cols`…）。参数调 1 轮仍不齐，几乎都是"重复打印 / 表头未跳过"，用 `--max-pages` 隔离一份重跑，不要造轮子。
2. **禁止全量原图目视。** 122 行 × 9 列逐格裁图 ≈30 分钟。只对 `compare` 报出的"数值不同/不一致"且 ≤5 处裁图。
3. **禁止"独立复核"二次全量重算。** 同一结论不需要两套方法互证，除非用户明确要求复核。
4. **禁止在交付目录堆中间脚本/裁图。** 临时脚本用完删；产物只留报告（用户要才加差异清单）。
5. **禁止 spawn 子 agent 去 profile 基准表。** `qc` 会自动识别表头、按表头文字映射列；需要看清结构时，`qc` 输出里已带 `mapping` 和"未匹配到的基准列"。
6. **禁止"先全量提取、再发现重复打印、再重提一遍"。** `qc` 的比较结果会直接提示"行数 ≈ 基准×2 → 用 `--max-pages 12` 重跑"；第二次调用会复用已提取结果（约 2 s）。

> 事故记录：同一份 24 页扫描件 vs Excel，第一次 **55 分 45 秒**（自建 6 个对齐脚本 + 30 余张裁图 + 独立复核）；加了时间纪律后第二次仍 **12 分 06 秒**。复盘：第二次工具只花 89 s，**模型推理 636 s / 34 步（每步 ≈19 s）**，其中还重复提取了一轮、spawn 了一个子 agent 去 profile 基准。因此本版新增 `qc` 一条命令 + 工具步骤 ≤8 的硬预算，按新流程同类任务目标 **≈3 分钟**。

## 1. 任务类型与默认路径

| 类型 | 典型说法 | 默认动作 | 目标时长 |
|---|---|---|---|
| **提取 / 转换** | "转成 Word/Excel/Markdown""导出内容" | **只提取，不做数据核验**；交付时说明已知局限，然后**主动询问**是否需要数据核验 | ≤3 min |
| **读取 / 核对** | "读一下""对不对/准不准""和这个 Excel 比一下" | `qc` 一条命令（见下） | 24 页 ≤3 min，一般 ≤5 min |

用户明确要求核验，或任务本身就是"核对准确性"时，按读取/核对类处理。

**核对默认只用 1 条命令（`qc`）：**

```powershell
& $PY "scripts\pdf.py" qc "D:\path\file.pdf" "D:\baseline.xlsx" -o "D:\out\qc"
```

`qc` 内部一次完成：info → 提取（同一 `-o` 目录再次调用可复用，约 2 s）→ 自动识别表头并按表头文字映射列 → compare → 写 `compare.md`，stdout 输出精简 JSON（行数、一致率、`real_diffs`、`hints`、未匹配的基准列、耗时）。

- 出现"行数 ≈ 基准×2，疑似重复打印"提示时，加 `--max-pages <单份页数>` 再跑一次（提取重算，约 4-5 s/页）；其余情况**不要重跑**。
- 只有提示"匹配列 <4"（自动映射失败）时，才退到第 4 节的 `compare` 手工指定列。
- `qc` 已默认：自动跳过表头、按顺序取到"合计"行、序号/单位名称按值序列比（`--no-auto-map` / `--no-stop-total` 可关）。

**收尾 2 步：**

- 只对 `real_diffs` 里"数值不同/不一致"的**真实差异**裁原图，**≤5 张**。
- 写 **1 份报告**（三栏：真实差异 / 已知噪声 / 无法比对），交付即可。

> 不要拆成 先 info、再 extract、再 compare、再读 CSV、再读表结构、再写对齐脚本 的一串步骤——这正是 12 分钟的来源。

## 2. 看一眼 PDF 类型（1 秒）

```powershell
& $PY "scripts\pdf.py" info "D:\path\to\file.pdf"
```

关键字段：`pdf_type`（`text_based` / `scanned` / `mixed`）、`page_count`、`native_dpi`、`suggest_dpi`。
- `text_based` → 直抽，<1 秒、零识别误差。
- `scanned` → OCR；脚本按原生 dpi 自动选渲染 dpi，一般不用改。
- `page_count`：扫描件约 0.4 MB/页；页数明显是"份数 × 每份页数"时，按第 6 节第 1 条处理（只取一份）。

## 3. 提取 / 转换

```powershell
& $PY "scripts\pdf.py" extract "D:\path\file.pdf" -o "D:\out\file_extract"
& $PY "scripts\pdf.py" convert "D:\path\file.pdf" --to docx   # docx/xlsx/md/csv/txt
```

产物：`<stem>.md`（全文 Markdown + 每页表格）、`.txt`、`.csv`（各页表格合并）、`.xlsx`（每页一表 + 汇总）、`.docx`（标题 + 每页表格）、`lines.json` / `tables.json`（带坐标原文与表结构）、`run_report.json`（耗时/每页行数/告警）。

常用参数：`--formats md,xlsx` 只出需要的格式（核对时 `csv,md` 就够）；`--max-pages 3` 先试跑；`--dpi 300` 小字/低清扫描件；`--force-ocr` 文本层是乱码时强制 OCR；`--recheck-orient` 怀疑方向判错时才用。

## 4. 数据比对（读取/核对任务用）

**首选第 1 节的 `qc`（一条命令完成提取 + 自动映射 + 比对）。** 只有需要精确控制行列、或 `qc` 提示"匹配列 <4"时，才用下面的 `compare`：

```powershell
& $PY "scripts\pdf.py" compare "D:\out\file_extract\file.csv" "D:\baseline.xlsx" -o "D:\out\compare.md"
```

列/行对不上时显式指定（长命令写成一行）：

```powershell
& $PY "scripts\pdf.py" compare "extract.csv" "baseline.xlsx" -o "compare.md" --base-skip 4 --base-stop-total --base-cols A,B,C,G,H,I,J,L,M,N,O --got-skip 1 --got-cols 1,2,3,4,5,6,7,8,9,10,11 --base-fill-cols A,B --seq-cols 1,2
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

## 5. 速度参考（本机 8C16T，无 GPU）

| 场景 | 耗时 |
|---|---|
| 11 页扫描件 200dpi 首次 | **≈50 s**（方向判定 ≈7 s + OCR ≈39 s + 导出） |
| 24 页扫描件 200dpi 首次 | **≈100–125 s** |
| 同一 PDF 再次提取 | ≈45 s（复用方向缓存） |
| 文本层 PDF | <1 s |
| `compare`（120 行 × 10 列） | **≈1.5 s** |
| `qc` 单份（12 页，含提取+映射+比对） | **≈68 s**（实测） |
| `qc` 二次调用（复用已提取结果） | **≈2 s**（实测） |
| `qc` 全本 24 页（两份打印，用于发现重复） | **≈127 s**（实测） |
| 整条核对流水线（24 页，单份） | **≈70 s** |

提速手段已内置：方向判定用"墨迹投影找行 + 少量单行识别"（不再整页跑检测模型）；方向结果按 PDF 指纹缓存；多进程并行实测更慢（ONNX 已多线程），不要加。

## 6. 必须知道的坑

1. **同一张表可能被重复打印。** 扫描件里常见"同一份表装订了两次"（如 24 页 = 两份 11 页 + 2 张空白）。提取后若 CSV 行数 ≈ 基准行数的 2/3 倍，先用 `--max-pages <单份页数>` 只抽一份再比；比对时若两份内容不同，要点明"PDF 内部版本不一致"，这本身是重要发现。
2. **扫描件先摆正**：逐页判 0/90/180/270，双面扫描奇偶页反向也能处理；不要整本统一旋转。
3. **原生分辨率**：渲染 dpi 超过扫描图原生 dpi 无收益，脚本已按 `native_dpi` 自动定。
4. **合并单元格**：序号/单位名称常一格多行跨多行，重建会把格内换行拼成一条并尽量落块首行；比对用 `--seq-cols` 更稳。
5. **打印版可能本来缺内容**：PDF 会裁长文本尾、漏印整列。出现"截断/缺失"先归类，只对真实数值差异裁原图确认，别当成提取错误。
6. **不要自动跑第二轮全页/单元格复核**：实测降分（高清重识别会把竖排合并格叠字），且极慢。只对确认有问题的个别格子做高清裁切。
7. **加密 PDF**：先解密；**超大 PDF**：先 `--max-pages 3` 试跑。

## 7. 排错

- `ready:false`：看缺哪个包；删 `%USERPROFILE%\.dsh\pdfskill` 后重跑 doctor 重装。
- 找不到 Python：`--python "C:\...\python.exe"`（已验证 3.12；3.13 暂不兼容 rapidocr-onnxruntime）。
- OCR 全是乱码：方向判错，删输出目录里的 `pages/orientation.json` 或加 `--recheck-orient` 重跑；仍不对就看 `references/workflow.md` 手动处理。
- `compare` 一致率 <70%：先按输出提示调行/列参数，最多 1 轮；仍不行就是数据分份问题（见第 6 节第 1 条）。
- 更细的决策树、参数组合、报告模板：`references/workflow.md`。

## 8. 交付格式（默认，不额外发挥）

- 只交 **1 份 Markdown 报告**（结论 + 差异表 + 无法核对项）；用户要"清单/表格"时才生成 xlsx。
- 不默认生成证据图、裁图、中间 JSON；需要时再裁 ≤5 张，并在报告里引用路径。
- 中间脚本不落在交付目录；用完删除。

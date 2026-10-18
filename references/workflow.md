# pdf-extract 工作流与排错参考

## 0. 停止规则

- 优先一次 `auto` 提取或 `qc` 完成主流程，不在提取前反复试探参数。
- Windows 执行前确认 Shell 为 PowerShell 7；命令本身优先采用跨 Shell 的 `python "scripts/pdf.py"` 形式。
- 同一动作最多重试一次；明确失败时换方法，不通过重复全量运行扩大验证。
- 只查看告警、真实差异或指定页面，不做无关的全量目视复核。
- 用户要求核对时只裁真实差异图，并输出一份结论明确的报告。

## 1. 决策树

```
任务开始
├─ 未指定格式、未要求转换
│  └─ extract --formats md,txt → 读取并回答内容
├─ 明确要求 Word/Excel/Markdown/CSV 或“转换”
│  └─ convert --to <格式>（速度优先）→ 交付结果 → 必须追加校验提示词
│     └─ 用户确认校验 → qc/内容与表格比对 → 报告准确度
└─ 读取/核对类（对不对 / 和 Excel 比）
   ├─ 1. qc <pdf> <基准> -o <out>        ← 一条命令：info+提取+自动映射+比较
   │     ├─ 看 stdout：行数、rate、real_diffs、hints
   │     ├─ 提示"行数 ≈ 基准×2" → qc --max-pages <单份页数>（复用提取，重新 OCR 该范围）
   │     └─ 提示"匹配列 <4" → 退回 compare 手工 --base-cols/--got-cols
   ├─ 2. 只对 real_diffs 的"数值不同/不一致"裁图（≤5 张）
   └─ 3. 写 1 份报告（真实差异 / 已知噪声 / 无法比对）
```

## 2. 不同文档的参数建议

| 文档情况 | 建议 |
|---|---|
| 原生文本 + 照片/Logo/二维码 | 默认即可；走 `native`，不要强制 OCR |
| 截图、长图、图文教程 | Word 默认 `layout`；要提取截图文字时 `extract` 会自动 OCR |
| 复杂扫描表单、低填充率多列表格 | 先补全分段网格；结构仍过稀时 Word 自动 `layout`，保留标题、框线和签章区 |
| 双面扫描、奇偶页方向相反 | 默认即可（逐页判断方向） |
| 页面 90° 侧躺表格 | 默认即可 |
| 原生只有 150dpi、字很小 | `--dpi 300` 放大再 OCR（会变慢） |
| 彩色底/水印重 | 先 `--max-pages 2` 试；必要时预处理图片 |
| 无框线表格 | 默认需强结构证据；明确是表格时用 `--table-mode force` |
| 序号/单位名称纵向合并 | 默认即可；系统逐列检测真实横线并把值归到合并区块首行 |
| 单个序号被 OCR 漏掉 | 仅当序号列存在连续规律且该区块有单位名称时自动补齐，并记录在 `sequence_repair` |
| 多栏文章/杂志 | 自动按纵向空白带切栏，md/txt 输出左栏后右栏 |
| Word 排版异常 | 用 `--docx-mode flow|layout|table|image` 显式指定，不必重跑 OCR |
| **同表重复打印/多份装订** | `info` 后按页数拆份，`--max-pages <单份页数>` 只抽一份 |
| 上千页 | 分批 `--max-pages` 或先拆 PDF |

## 3. 输出质量自检（读取/核对任务）

1. `run_report.json` 的 `warnings`：方向存疑的页才抽查。
2. `<stem>.md` 前 2 页：表头、每行列数是否对齐。
3. `tables.json`：每页 `tables[0]` 列数是否稳定（应等于真实列数）。
4. 只在 `compare` 报出真实差异后才打开 `pages/page-XX.png` 裁切目视，**总数 ≤5 张**。

## 4. 比对策略（PDF vs Excel/CSV）

**默认走 `qc`；`compare` 只在 qc 自动映射失败或需要精确列控制时用。**

### 4.1 qc（推荐，一条命令）

```powershell
python "scripts/pdf.py" qc "<pdf>" "<基准.xlsx>" -o "<out>"
python "scripts/pdf.py" qc "<pdf>" "<基准.xlsx>" -o "<out>" --max-pages <N>   # 只取一份打印
```

输出 JSON 关键字段：`rows`（基准/PDF 行数）、`rate`、`verdicts`、`real_diffs`、`noise_counts`、`mapping`（识别到的列与表头行）、`hints`、`timing_s`。

判读顺序：

1. `rows.pdf ≈ rows.base × 2` → 重复打印，加 `--max-pages <单份页数>` 重跑，并在同一 `-o` 目录复用已有提取结果。
2. `mapping.columns` 是否与预期列一致；`hints` 里"未匹配的基准列"就是无法比对的列。
3. `real_diffs` 里的"数值不同"才是金额类真实差异；"不一致"先判断是否 OCR 串列；"整行多余"在重复打印未排除前不算差异。
4. 其余走 `noise_counts` 归类，不逐格看。

### 4.2 compare（手工，qc 映射失败时）

**固定顺序，最多 1 轮参数调整：**

1. **先看行数倍数。** `提取行数 ≈ 基准行数 × k`（k=2/3）→ 同表重复打印，`--max-pages` 只取一份再比，别逐行对齐。
2. **再对齐行。** 行数不一致 → `--base-skip` / `--got-skip` / `--base-stop-total`。
3. **再对齐列。** 列数不一致 → `--base-cols` / `--got-cols`。
4. **合并列单独处理。** 序号/单位名称/部门 → `--seq-cols` 按值序列比；基准侧可 `--base-fill-cols`。
5. **判读，不再加验证。** 按 SKILL.md 的"差异分级"一次归类；数值差异才裁图（≤5 张）。

**常见噪声（直接归类，不裁图）：**

| 现象 | 结论 |
|---|---|
| 文本"截断" | PDF 打印裁切，源问题 |
| 单格"缺失/多余" | 合并单元格落行、备注垂直居中 |
| "在租"混进使用人 | OCR 串列，合并格导致 |
| `0` vs 空 vs `0.00` | 显示差异，数值相同 |
| 千分位逗号、全半角括号 | `norm()` 已归一，不应报差异；报了就是列错位 |

**报告模板（1 份 md，别写长）：**

```markdown
# <文档> 差异核对
- 来源：PDF（N 页，扫描件）｜基准：<xlsx>（M 行）
- 提取：<耗时>；比对：<一致率>

## 结论
<一句话：是否有真实差异，几处>

## 真实差异（逐条：位置 / PDF / Excel / 判断）
## 已知噪声（类别 + 计数，不逐条展开）
## 无法比对（PDF 未打印的列、裁切内容）
## 建议
```

## 5. 常见故障

| 现象 | 原因 / 处理 |
|---|---|
| doctor 报缺包且安装失败 | 离线：`--python` 指向已有依赖的解释器；或设 `PIP_INDEX_URL` 后重跑 |
| 安装到一半失败 | 删除用户缓存目录 `pdf-extract-skill` 重来（pip 缓存也在里面） |
| 所有页 OCR 都乱 | 方向判断失败：删输出目录 `pages/orientation.json` 或加 `--recheck-orient`；仍不对就先手动旋转另存再跑 |
| 表格列数比真实少/多 | 框线检测受扫描质量影响；加 `--dpi 300`，或用 md/txt 后处理 |
| 序号落到合并单元格中间行 | 不应再出现；检查 `run_report.json` 的 `quality.sequence_repair.spans` 与 `filled` |
| 单位名称前粘连数字 | 自动剥离；若仍出现，检查序号列是否识别为“序号”以及单位列是否紧邻 |
| 某几页表格串行 | 该页可能无框线或斜扫描；看原图，必要时单页人工核对 |
| compare 大量"整行多余/缺失" | 重复打印或表头/页脚未跳过；不要写脚本，用 `--max-pages`/`--got-skip` |
| compare 一致率 <70% | 行/列错位；按提示调参数 1 轮，仍不行隔离单份重跑 |
| 加密 PDF | 本工具不解密，先解密 |
| 输出 docx 打开异常 | 极少见；先用 xlsx/md，或去掉异常页重导 |
| 简历被做成表格 | 检查 `route.json` 的 `table_pages`；不要用旧版 `mixed -> OCR -> table` 流程 |
| 文本型 PDF 很慢 | 看 `run_report.json` 的 `ocr_pages`，正常情况下应为空 |
| 截图型 Word 转换很慢 | 只转 Word 时不应执行全页 OCR；若 `ocr_pages` 非空，检查 `--docx-mode` |

## 6. 目录约定

- 运行环境：平台用户缓存目录 `pdf-extract-skill`（venv、pip 缓存、env.json）——可用 `PDFSKILL_HOME` 改；换工作目录不用重装。
- 环境发现顺序：显式 `--python` / `PDFSKILL_PYTHON` → 已记录解释器 → 本地已有 venv → 当前解释器/PATH → 新建环境。
- 提取产物：`-o` 指定目录（默认 `<PDF名字>_extract/`），含 `pages/` 渲染图与 `run_report.json`。
- 交付目录只放：报告 md（必要时差异 xlsx）。中间脚本、裁图用完删。
- 本仓库：根目录含 `SKILL.md` + `scripts/` + `references/`。

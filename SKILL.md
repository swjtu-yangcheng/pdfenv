---
name: pdfenv
description: 对学术论文、学位论文、送审稿等 PDF 进行非破坏性水印分析、文本提取、选择性高清渲染、双引擎视觉比对和按页 OCR。用户要求读取带防伪水印 PDF、修复提取错乱、精读图表/公式、处理扫描件时调用。
metadata:
  version: "2.2.1"
  category: pdf
  changelog: "v1.0 行级启发式清洗（杨老师/WorkBuddy 实战版）→ v2.2 GPT 全面重构（texttrace 轨迹证据 + glyph 级过滤 + 审计回退 + 按需渲染/parity/OCR）→ v2.2.1 本机适配：import pymupdf as fitz 修复 PyMuPDF≥1.26 弃用警告污染 stdout 导致 --json 解析失败的问题；融合本机 venv 路径与实测记录"
---

# pdfenv v2.2 — 水印隔离与科研 PDF 渲染阅读

**本 Skill 只生成衍生文本/图像，不修改原始 PDF；任何清理结果都不是"被更改后的 PDF"。** 禁止未经确认就把水印、表格数值、公式或插图从原文件永久删除。

## 本机环境（杨老师电脑已就绪，直接用，无需重建）

```
VENV PYTHON: C:\Users\yangc\.workbuddy\binaries\python\envs\pdfenv\Scripts\python.exe
SKILL 目录:  C:\Users\yangc\.workbuddy\skills\pdfenv
已装依赖:    pymupdf 1.28.x, Pillow, pypdfium2, numpy, pdfplumber, pytest（阿里云镜像）
OCR 状态:    Tesseract 未装 → --ocr 不可用（其余命令全可用）；需要 OCR 时先装 Tesseract + chi_sim
```

本机直接这样调用（区别于通用的 .venv 安装指引）：

```bash
PY="C:/Users/yangc/.workbuddy/binaries/python/envs/pdfenv/Scripts/python.exe"
S="C:/Users/yangc/.workbuddy/skills/pdfenv"
"$PY" "$S/scripts/read_pdf.py" info   "论文.pdf" --json
"$PY" "$S/scripts/read_pdf.py" full   "论文.pdf" --clean --format md -o "论文_正文.md"
"$PY" "$S/scripts/read_pdf.py" render "论文.pdf" --pages 51 --dpi 300 -o renders
```

## 使用决策（必须按顺序）

1. **先体检：** `info --json` 检查总页数、各代表页原生字符数、图像覆盖率和方向。不能仅用 `Producer` 判断扫描件。
2. **再抽样：** `sample` 覆盖封面、摘要、主体中部和结尾。确认语言、双栏、公式、表格和文字层的质量。
3. **按需提取：** `full --clean --format md` 仅对透明度、颜色、方向、跨页重复坐标等证据一致的**高置信度矢量文字水印**做预拼接过滤。严禁按“单字/短行”删除。必须保留 `.watermark-audit.json`。
4. **视觉核验：** 对出现表格、图、公式、图片型水印或版面异常的页面使用 `render --pages`；大型页面用 `--clip`/`--clip-rel`，高清时配合 `--tile`，避免整本全部栅格化。
5. **异常排查：** 对渲染缺字/色块/图形位置异常，用 `parity` 比对 MuPDF 与 PDFium。两者结果不一致时报告而非猜测；像素差异并不能直接断言原 PDF 已损坏。
6. **逐页 OCR：** 仅当原生文本不足且高图像覆盖、或人工确认文字层不可用时使用 `--ocr auto/force`。OCR 不是公式、图表和数值的最终依据，关键数据回看原页面。
7. **报告不确定性：** 审计中的 `needs_review_pages` 和 `rollback_pages` 必须交代；高比例候选删除会直接回退到原生文字。文字层与渲染截图冲突时，图像用于视觉核对，必要时检查 PDF 原始对象。

## 安装（Python 3.10+，Windows/macOS/Linux）

在 Skill 目录中执行：

```bash
python -m venv .venv
# Windows PowerShell（在 Windows 执行）：
# .\.venv\Scripts\python.exe -m pip install -r requirements-core.txt
# .\.venv\Scripts\python.exe -m pip install -r requirements-optional.txt
# macOS/Linux（在 Unix 系统执行）：
.venv/bin/python -m pip install -r requirements-core.txt
.venv/bin/python -m pip install -r requirements-optional.txt
```

OCR 另需系统安装 **Tesseract** 和语言包 `chi_sim`、`eng`。可用 `tesseract --list-langs` 检查；未安装 OCR 引擎不影响其它命令。代码不绑定某个人的 Python 路径；在 Windows 上建议使用相同虚拟环境中的 `python.exe`。

## 常用命令（从 Skill 目录执行）

```bash
# PDF 结构、页面抽样
python scripts/read_pdf.py info "论文.pdf" --json
python scripts/read_pdf.py sample "论文.pdf"

# 全文 -> Markdown + 审计 JSON；与原版兼容 --clean/--pages/-o
python scripts/read_pdf.py full "论文.pdf" --clean --format md -o "论文_正文.md"
python scripts/read_pdf.py full "论文.pdf" --pages 50-80 --clean --format md -o "第三章.md"

# 先审计再决定是否自动过滤（严格文献核对优先）
python scripts/read_pdf.py audit "论文.pdf" --pages 10-15 -o watermark_audit.json
python scripts/read_pdf.py full "论文.pdf" --clean --max-remove-ratio 0.15 -o output.txt

# 高精度渲染：指定页；自动生成 render_manifest.json
python scripts/read_pdf.py render "论文.pdf" --pages 50-52 --dpi 200 -o renders
python scripts/read_pdf.py render "论文.pdf" --pages 51 --dpi 450 --tile 2 -o figure_51

# 选取页面中部四分之一：相对坐标 [0,1]，以左上为原点
python scripts/read_pdf.py render "论文.pdf" --pages 51 --dpi 400 --clip-rel 0.25 0.25 0.75 0.75 -o crop

# 以红色方框可视化检测到的水印位置：仅用于核验
python scripts/read_pdf.py render "论文.pdf" --pages 51 --dpi 200 --mark-watermarks -o verify
# 如果水印实际是 PDF 注释层，MuPDF 渲染可选择隐藏注释
python scripts/read_pdf.py render "论文.pdf" --pages 51 --hide-annots -o no_annotations

# 图片渲染引擎备选 / 像素级双引擎对照
python scripts/read_pdf.py render "论文.pdf" --pages 51 --engine pdfium -o pdfium_renders
python scripts/read_pdf.py parity "论文.pdf" --pages 51 --dpi 150 -o renderer_compare

# 扫描页：仅需 OCR 的页；谨慎使用 --ocr-preprocess suppress-light
python scripts/read_pdf.py full "论文.pdf" --pages 30-31 --ocr auto --ocr-lang chi_sim+eng --format md -o ocr_pages.md

# 结构化表格（需 pdfplumber，仍应对照原图验证）
python scripts/read_pdf.py tables "论文.pdf" --pages 51-55 -o tables.json
```

### 自动化代理执行限制

- 不传 `--pages` 时，`render` 仅渲染代表页，不可默认渲染整本。渲染页数默认最多 30；用户明确要求批量导出时可加 `--force-many`。
- 每个输出图像限制 `--max-pixels`（默认 2600 万像素）。超过预算**报错并建议裁切/切片**，不静默缩小 DPI。`--tile 2` 将页面分成 2×2 张，不会降低各张 DPI。
- 对于 PDF 文字层已存在且可靠的页面，禁止“先栅格化再 OCR”作为默认步骤：OCR 往往丢失数学符号、上/下标、希腊字母、表格层次。
- `--clean` 只过滤高置信度的原生文字水印：**扫描进图片的水印、XObject 图片、复杂剪裁图层、全文不重复的签章**不保证能自动识别。对此优先局部渲染并报告需要人工审核。
- `--hide-annots` 只适用于 `--engine mupdf`，可隐藏**注释对象**，但不能消除直接绘制在页面内容流中的文字、图片水印。
- `--ocr-preprocess suppress-light` 会抹掉浅色线条、图表网格和浅灰注释，必须显式授权/核验才使用，永不作为默认配置。
- 多栏论文、图注位置、复杂公式、特殊字体编码和旋转页面仍可能影响文本阅读顺序或坐标。需要依据原页渲染校核。
- 若权限/环境受限无法 OCR、无法安装语言包、无法渲染某页，明确记录页码、错误和仍可验证的提取结果；不得声称全文已完成核查。

## 输出及验证要求

- `full -o *.md`：逐页标记文本、保留小于 3 字符的合法行、行内字符不依高频字符集合删改。
- `full --clean -o`：自动写 `*.watermark-audit.json`，含原件 SHA256、样本页、候选框、实际移除字符数、回退页与待复核页。
- `render -o 目录`：页面 PNG/JPEG 和 `render_manifest.json`，含 PDF 坐标裁剪框、DPI 和尺寸；PNG 优先供公式/细线辨认。
- `parity -o 目录`：两种渲染器生成的图片、diff 图以及 `parity.json`；不同抗锯齿和色彩管理会产生正常差异。
- 任一关键数据的最终复核：**原 PDF 页面及可视化渲染图优先于经过清洗的文本**。

参考技术细节与局限：`docs/TECHNICAL_NOTES.md`；GPT 升级审查报告：`docs/REVIEW_REPORT.md`；示例与测试：`tests/test_pdfenv.py`。

## 实测记录（本机，2026-10-18 验收 v2.2.1）

| 验证项 | 结果 |
|---|---|
| 自带 10 项 pytest | 9 通过、1 跳过（OCR 无 Tesseract 属预期） |
| info --json（210 页评审论文） | 正常（v2.2 原版因 fitz 弃用警告污染 stdout 挂在 --json 上，v2.2.1 已修复） |
| full --clean --format md（第 5-6 页） | **行内穿插水印字被 glyph 级精准剔除、正文零误伤**，效果优于 v1 行级过滤（v1 只能保守保留行内水印字） |
| 水印审计 JSON | 识别出完整水印串"学位中心学位论文质量监测平台——993975485——20261017"（opacity 0.08、24pt、跨 14 采样页坐标复现），每页删 111 glyph、删除比 12%~22%、未触发回退 |
| render 第 51 页 @200dpi | PNG + render_manifest.json 正常输出 |

**准确性优先级：PDF 原件 > 原件渲染 > 经过水印过滤的文本 > OCR 推断。**

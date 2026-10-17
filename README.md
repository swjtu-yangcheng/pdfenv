# pdfenv v2.2 — 学术 PDF 非破坏性读取增强版

适用于 Workbuddy / Claude Code / Codex 等可执行 Python 命令、可调用本地文件的智能体。

核心定位：**从 PDF 对象/字符层判别水印 → 保留原文证据 → 按需渲染与 OCR → 复核有风险的页面**。不是修改原 PDF，也不是用 OCR 替代所有文本解析。

## 安装

复制整个 `pdfenv/` 文件夹到你的智能体 Skills 目录（例如 Windows Workbuddy 的 `%USERPROFILE%\.workbuddy\skills\pdfenv\`），保持 `SKILL.md`、`scripts/`、`tests/` 等子目录。

```powershell
cd "$env:USERPROFILE\.workbuddy\skills\pdfenv"
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-core.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-optional.txt
.\.venv\Scripts\python.exe scripts\read_pdf.py info "D:\论文.pdf"
```

在国内网络环境可以通过可信赖的镜像源安装 Python 库，但不能假设其可用；`requirements` 未锁定到用户机器的绝对路径。OCR 需另外安装 Tesseract 和正确的语言包。

## 最短工作流程

```bash
python scripts/read_pdf.py info "paper.pdf"
python scripts/read_pdf.py sample "paper.pdf"
python scripts/read_pdf.py full "paper.pdf" --clean --format md -o "paper_clean.md"
python scripts/read_pdf.py render "paper.pdf" --pages 5,20,33 --dpi 200 -o renders
```

内容较复杂时，请阅读 `SKILL.md`；改进原理与适用边界见 `docs/TECHNICAL_NOTES.md`。

## 与 v1.0 区别

| 维度 | 旧版 | v2.2 |
|---|---|---|
| 类型判断 | Producer 经验规则 | 按页抽样字符量、图像覆盖率 |
| 水印过滤 | 删除短行、高频水印字符 | 跨页位置+透明度+方向+颜色，逐字匹配后再重组 |
| 安全阀 | 无审计/撤销机制 | 最大删除比例、回退、审计 JSON |
| 渲染 | 无 | MuPDF / PDFium、裁切、分块、像素预算 |
| 视觉核验 | 无 | 水印候选框、双引擎差异图 |
| OCR | 原文提建议但代码未实现 | 按页 auto/force、语言包检查、超时 |
| 输出 | TXT | TXT / MD / JSON、表格 JSON、图像清单 |

### 已知局限

- 目前高置信度水印识别以**重复出现的矢量文字水印**为主要目标；对图像水印、隐藏图层、旋转异常或嵌入扫描图像的印记不保证移除。
- 一页文档且只有一次出现的不明确水印，保守模式可能保留；通过渲染与审计进一步核查。
- 现有工具不是专业 LaTeX / 表格版面重建器。大量公式、扫描件和数据图表必须用页面图像对照。

## 测试

```bash
python -m pip install pytest
python -m pytest -q tests/test_pdfenv.py
```

测试数据均为代码合成，不含原始送审论文或私人文件。软件没有从互联网传输 PDF 的功能。

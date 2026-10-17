# pdfenv —— 带水印 PDF 可靠读取工具

针对**送审论文/学位论文等带防伪水印 PDF**的可靠读取与文本提取工具。
当直接 Read PDF 报 "binary file" 失败时，按本工具的四步法降级处理，稳定可复现。

## 四步法

1. **体检**（`info`）：页数 + 元数据，`Producer` 字段判断文本型/扫描型
2. **抽样验证**（`sample`）：前 5 页 + 中部 2 页，确认正文语义连贯
3. **全文提取**（`full`）：pdfplumber 逐页提取
4. **水印过滤**（`--clean`）：保守规则剔除竖排水印噪声（单字行、水印字符行、拆散日期行），宁漏勿伤

## 快速上手

```bash
PY="C:/Users/yangc/.workbuddy/binaries/python/envs/pdfenv/Scripts/python.exe"
SCRIPT="C:/Users/yangc/.workbuddy/skills/pdfenv/scripts/read_pdf.py"

$PY "$SCRIPT" info   "D:/path/to/论文.pdf"        # ① 体检
$PY "$SCRIPT" sample "D:/path/to/论文.pdf"        # ② 抽样
$PY "$SCRIPT" full   "D:/path/to/论文.pdf" -o "out.txt" --clean   # ③④ 全文+过滤
$PY "$SCRIPT" tables "D:/path/to/论文.pdf" --pages 100-110        # 表格
```

## 依赖

pdfplumber、pypdf（隔离 venv，阿里云镜像安装，不污染系统 Python）

## 实战验证

2026-10-17：长安大学工程博士论文（210 页，竖排防伪水印，iText 5.5.13 生成）——
体检、抽样、全文提取、水印过滤全流程成功，产物直接用于评审分析。

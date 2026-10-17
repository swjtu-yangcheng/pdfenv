---
name: pdfenv
description: 带水印 PDF 文档的可靠读取与文本提取（评审论文/学位论文防伪水印场景）。当直接 Read PDF 报"binary file"失败、或 PDF 含竖排防伪水印/送审平台水印、需要先体检再抽样验证、或需要全文提取+水印噪声过滤时使用。内置隔离环境指引与四步法流程。
agent_created: true
metadata:
  version: "1.0.0"
  category: pdf
---

# pdfenv —— 带水印 PDF 可靠读取四步法

> 实战来源：2026-10-17 成功读取 210 页带竖排防伪水印的博士论文评审 PDF
> （《长安大学工程博士大论文.pdf》，送审平台水印层，pdfplumber 全文提取正常）。

## 核心结论（先记住这三条）

1. **内置 Read 对部分 PDF 会直接失败**（报 `Cannot display content of binary file`），
   尤其是含水印层/非常规编码的送审论文 PDF——失败后**立即降级到 Python 解析通道**，不要反复重试。
2. **pdfplumber + pypdf 组合拳**：pdfplumber 提文本/表格，pypdf 读页数/元数据。
   元数据 `Producer` 字段可直接判断文本型 vs 扫描型（扫描型才需要 OCR）。
3. **水印 ≠ 不可读**：竖排防伪水印只是噪声层，不破坏正文文字。先抽样确认正文
   语义连贯，后续分析时按规则过滤水印噪声即可。

## 隔离环境（本机已建好，直接复用）

```
VENV:   C:\Users\yangc\.workbuddy\binaries\python\envs\pdfenv
PYTHON: C:\Users\yangc\.workbuddy\binaries\python\envs\pdfenv\Scripts\python.exe
依赖:   pdfplumber, pypdf（阿里云镜像安装）
```

若环境丢失，重建一条命令：

```bash
"C:\Users\yangc\.workbuddy\binaries\python\versions\3.13.12\python.exe" -m venv "C:\Users\yangc\.workbuddy\binaries\python\envs\pdfenv"
"C:\Users\yangc\.workbuddy\binaries\python\envs\pdfenv\Scripts\python.exe" -m pip install pdfplumber pypdf -i https://mirrors.aliyun.com/pypi/simple/ -q
```

## 四步法流程

```text
直接 Read PDF
├─ 成功 → 正常阅读，结束
└─ 失败（binary file / 乱码）→ 降级 Python 通道
     ① 体检：读页数 + 元数据（Producer 判断文本型/扫描型）
     ② 抽样验证：前 5 页（封面/声明/摘要，最难解析区）+ 中部 2 页（正文真实质量）
        ├─ 正文语义连贯 → 放心全文提取
        └─ 正文乱码/为空 → 扫描型？转 OCR（pytesseract + pdf2image）
     ③ 全文提取：pdfplumber 逐页 extract_text()
     ④ 水印过滤：保守规则剔除噪声（见下），切忌过度过滤误伤正文
```

## 水印特征识别与过滤

**识别**（以送审平台竖排水印为例）：
- 提取文本中大量**单字符成行**（竖排水印被逐字拆行）；
- 同一组字反复高频出现（如"平台测监量质文论位学中心位学"式字串拆散穿插）；
- 日期数字水印（如"20261017—20261015"被拆成单个数字行）；
- 水印字会**穿插在正文行内**（行尾粘字、行中断字），打乱阅读顺序但不破坏内容。

**过滤规则（保守优先，宁漏勿伤）**：
1. 整行为单字符或长度 ≤ 2 的行 → 剔除（中文正文行几乎不可能这么短）；
2. 整行完全由已知水印字符集组成的行 → 剔除；
3. 行内夹杂的水印字 → **不自动删**（会误伤"质/量/平/台"等正文常用字），
   阅读时靠上下文忽略，或按坐标/对象属性做精细分离（进阶）。

## 用法（scripts/read_pdf.py）

```bash
PY="C:/Users/yangc/.workbuddy/binaries/python/envs/pdfenv/Scripts/python.exe"
SCRIPT="C:/Users/yangc/.workbuddy/skills/pdfenv/scripts/read_pdf.py"

# 体检：页数 + 元数据（第一步，判断文本型/扫描型）
$PY "$SCRIPT" info  "D:/path/to/论文.pdf"

# 抽样验证：前 5 页 + 中部 2 页（第二步）
$PY "$SCRIPT" sample "D:/path/to/论文.pdf"

# 全文提取 + 水印过滤 → 输出 txt（第三、四步）
$PY "$SCRIPT" full   "D:/path/to/论文.pdf" -o "D:/path/to/输出.txt" --clean

# 提取指定页范围（如只读第 3 章对应的 50-80 页）
$PY "$SCRIPT" full   "D:/path/to/论文.pdf" -o "第3章.txt" --pages 50-80 --clean

# 表格提取（评审核对数据时用）
$PY "$SCRIPT" tables "D:/path/to/论文.pdf" --pages 100-110
```

## Windows 环境小坑（必看）

1. **Git Bash 写文件前先 `mkdir -p` 确认目录存在**——heredoc 重定向到不存在目录会失败；
2. Python 脚本开头加 `sys.stdout.reconfigure(encoding="utf-8")`，防控制台中文乱码；
3. 路径一律带引号（评审论文路径常含中文和空格）。

## 实测记录

| 日期 | 对象 | 结果 |
|---|---|---|
| 2026-10-17 | 长安大学工程博士论文（210 页，竖排防伪水印，iText 5.5.13 生成） | 体检+抽样+全文提取全部成功，水印过滤后文本可直接用于评审分析 |

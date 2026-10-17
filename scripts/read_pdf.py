# -*- coding: utf-8 -*-
"""
pdfenv —— 带水印 PDF 可靠读取工具
用法:
    python read_pdf.py info   <pdf路径>                     # 体检: 页数+元数据
    python read_pdf.py sample <pdf路径> [--head N]          # 抽样: 前N页+中部2页
    python read_pdf.py full   <pdf路径> [-o out.txt] [--pages A-B] [--clean]   # 全文提取
    python read_pdf.py tables <pdf路径> [--pages A-B]       # 表格提取
"""
import sys
import argparse
import re
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

try:
    import pdfplumber
except ImportError:
    print("缺少依赖: 请先安装 pdfplumber (pip install pdfplumber)")
    sys.exit(1)


# ---------- 水印过滤（保守规则，宁漏勿伤） ----------

def build_watermark_charset(sample_text: str) -> set:
    """从样例文本推断竖排水印字符集: 高频出现且整行为单字/短行的字符。"""
    freq = {}
    for line in sample_text.splitlines():
        s = line.strip()
        if 0 < len(s) <= 2:
            for ch in s:
                freq[ch] = freq.get(ch, 0) + 1
    # 出现次数 >= 8 的单字大概率是水印构件
    return {ch for ch, n in freq.items() if n >= 8}


def clean_lines(text: str, wm_chars: set | None = None) -> str:
    """剔除竖排水印噪声行; 行内夹杂的水印字保留(避免误伤正文用字)。"""
    if wm_chars is None:
        wm_chars = set()
    out = []
    for raw in text.splitlines():
        s = raw.strip()
        # 规则1: 单字符/超短行 -> 水印
        if 0 < len(s) <= 2:
            continue
        # 规则2: 整行由水印字符集组成 -> 水印
        if wm_chars and s and all(ch in wm_chars or ch.isdigit() or ch in "—-" for ch in s):
            continue
        # 规则3: 纯日期数字行(水印日期拆散) 如 "7 1 0 1 6 2 0 2"
        if len(s.split()) >= 6 and all(p.isdigit() or p in "—-" for p in s.split()):
            continue
        out.append(raw)
    # 合并连续空行
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    return result


# ---------- 子命令 ----------

def cmd_info(path: str):
    with pdfplumber.open(path) as pdf:
        n = len(pdf.pages)
        print(f"总页数: {n}")
        meta = pdf.metadata or {}
        print("元数据:")
        for k, v in meta.items():
            print(f"  {k}: {v}")
        producer = str(meta.get("Producer", ""))
        if any(x in producer for x in ("iText", "LaTeX", "Word", "Microsoft", "WPS")):
            print("\n判断: 文本型 PDF, 可直接提取, 无需 OCR")
        else:
            print("\n判断: Producer 未知, 建议先跑 sample 验证文字流质量; 若乱码/为空则转 OCR")
        # 附加体检: 第 5 页字符量
        if n >= 5:
            t = pdf.pages[4].extract_text() or ""
            print(f"第 5 页字符数: {len(t)} ({'正常' if len(t) > 50 else '过少, 可能是扫描件'})")


def cmd_sample(path: str, head: int):
    with pdfplumber.open(path) as pdf:
        n = len(pdf.pages)
        k = min(head, n)
        idx = list(range(k)) + ([n // 2, n // 2 + 1] if n > 10 else [])
        for i in idx:
            if i >= n:
                continue
            t = pdf.pages[i].extract_text() or ""
            print(f"--- 第 {i+1} 页 (字符数 {len(t)}) ---")
            print(t[:600])
            print()


def cmd_full(path: str, out: str | None, pages: str | None, clean: bool):
    with pdfplumber.open(path) as pdf:
        n = len(pdf.pages)
        lo, hi = (0, n - 1)
        if pages:
            a, b = pages.split("-")
            lo, hi = int(a) - 1, min(int(b), n) - 1
        wm_chars = None
        if clean:
            # 用前 10 页样本推断水印字符集
            sample = "\n".join((pdf.pages[i].extract_text() or "") for i in range(min(10, n)))
            wm_chars = build_watermark_charset(sample)
            if wm_chars:
                print(f"[水印字符集推断成功] {sorted(wm_chars)}", file=sys.stderr)
        parts = []
        total = 0
        for i in range(lo, hi + 1):
            t = pdf.pages[i].extract_text() or ""
            if clean:
                t = clean_lines(t, wm_chars)
            total += len(t)
            parts.append(f"\n\n===== 第 {i+1} 页 =====\n{t}")
        text = "".join(parts)
        if out:
            Path(out).write_text(text, encoding="utf-8")
            print(f"已提取第 {lo+1}-{hi+1} 页, 共 {total} 字符 -> {out}")
        else:
            print(text)


def cmd_tables(path: str, pages: str | None):
    with pdfplumber.open(path) as pdf:
        n = len(pdf.pages)
        lo, hi = (0, n - 1)
        if pages:
            a, b = pages.split("-")
            lo, hi = int(a) - 1, min(int(b), n) - 1
        for i in range(lo, hi + 1):
            tables = pdf.pages[i].extract_tables()
            for j, tb in enumerate(tables):
                print(f"--- 第 {i+1} 页 表 {j+1} ---")
                for row in tb:
                    print(row)
                print()


def main():
    ap = argparse.ArgumentParser(description="带水印 PDF 可靠读取工具 (pdfenv)")
    ap.add_argument("cmd", choices=["info", "sample", "full", "tables"])
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out")
    ap.add_argument("--pages", help="页范围, 如 50-80")
    ap.add_argument("--head", type=int, default=5, help="抽样页数, 默认前 5 页")
    ap.add_argument("--clean", action="store_true", help="启用水印噪声过滤")
    a = ap.parse_args()
    if not Path(a.pdf).exists():
        print(f"文件不存在: {a.pdf}")
        sys.exit(1)
    if a.cmd == "info":
        cmd_info(a.pdf)
    elif a.cmd == "sample":
        cmd_sample(a.pdf, a.head)
    elif a.cmd == "full":
        cmd_full(a.pdf, a.out, a.pages, a.clean)
    elif a.cmd == "tables":
        cmd_tables(a.pdf, a.pages)


if __name__ == "__main__":
    main()

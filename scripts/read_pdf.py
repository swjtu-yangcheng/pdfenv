# -*- coding: utf-8 -*-
"""pdfenv v2.2: audit-first PDF reading, non-destructive watermark separation, rendering.

No destructive rewriting of input PDFs. Requires PyMuPDF. Optional: pdfplumber,
Pillow, pypdfium2, pytesseract (+ Tesseract executable and languages).
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF (official import, avoids fitz deprecation warning)
except ImportError:
    raise SystemExit("缺少 PyMuPDF: python -m pip install PyMuPDF")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ---------- Pages and PDF triage ----------

def parse_pages(expr: str | None, n: int) -> list[int]:
    if n == 0:
        return []
    if not expr:
        return list(range(n))
    found = set()
    for chunk in expr.split(","):
        chunk = chunk.strip()
        m = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", chunk)
        if not m:
            raise ValueError(f"无效页码范围: {chunk!r}; 使用 1,3-6,9")
        start, end = int(m[1]), int(m[2] or m[1])
        if start < 1 or end < start or end > n:
            raise ValueError(f"页码超出范围: {chunk!r}; PDF 共有 {n} 页")
        found.update(range(start - 1, end))
    return sorted(found)


def sample_ids(n: int, head: int = 5, cap: int = 12) -> list[int]:
    """Representative sample across document, avoiding front-matter-only bias."""
    if n == 0:
        return []
    if n <= cap:
        return list(range(n))
    # Select real body pages over the length of a thesis; include cover and final page.
    uniform = [round(i * (n-1) / max(1, cap-1)) for i in range(cap)]
    return sorted(set(uniform + list(range(min(head, 3, n)))))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def page_metrics(page) -> dict:
    # Metadata/Producer alone does not distinguish digital text from scanned PDF.
    txt = page.get_text("text")
    visible_chars = len(txt.strip())
    img_coverage = 0.0
    page_area = max(1, page.rect.get_area())
    try:
        for im in page.get_image_info():
            r = fitz.Rect(im.get("bbox", (0, 0, 0, 0))) & page.rect
            img_coverage += max(0, r.get_area()) / page_area
    except Exception:
        pass
    img_coverage = min(1.0, img_coverage)
    if visible_chars >= 60 and img_coverage >= .7:
        kind = "mixed_or_scanned_with_text_layer"
    elif visible_chars >= 60:
        kind = "digital_text_likely"
    elif img_coverage >= .65:
        kind = "scanned_image_likely"
    else:
        kind = "sparse_text_or_graphics"
    return {"page": page.number + 1, "chars": visible_chars, "image_coverage_approx": round(img_coverage, 3), "classification": kind}


def cmd_info(doc, args):
    inds = sample_ids(len(doc), args.head)
    obj = {
        "file": str(args.pdf), "pages": len(doc), "encrypted": bool(doc.is_encrypted),
        "metadata": doc.metadata, "sampled_page_metrics": [page_metrics(doc[i]) for i in inds],
        "note": "Producer 不是扫描件判据；分类依据页面文本量与图像覆盖率（启发式）。"
    }
    if args.json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(f"PDF 页数: {len(doc)} | 元数据 Producer: {doc.metadata.get('producer')}")
        for m in obj["sampled_page_metrics"]:
            print(f"第{m['page']:>3}页: 文字 {m['chars']:>5} 字符, 图像覆盖≈{m['image_coverage_approx']:.0%}, {m['classification']}")
        print("注意: PDF 可能混合扫描页与文本页，应逐页判断 OCR 需求。")


def cmd_sample(doc, args):
    ids = parse_pages(args.pages, len(doc)) if args.pages else sample_ids(len(doc), args.head)
    for idx in ids:
        p = doc[idx]
        met = page_metrics(p)
        print(f"\n--- 第 {idx + 1} 页 | {met['classification']} | {met['chars']} 字符 ---")
        print((p.get_text("text", sort=True) or "").strip()[:args.limit])


# ---------- Watermark evidence, never delete solely based on short line length ----------

def span_text(span: dict) -> str:
    return "".join(chr(c[0]) if 0 <= c[0] <= 0x10ffff else "�" for c in span.get("chars", ()))


def signature(span: dict, page) -> tuple:
    r = fitz.Rect(span["bbox"])
    center = (r.tl + r.br) / 2
    w, h = max(1, page.rect.width), max(1, page.rect.height)
    dx, dy = span.get("dir", (1, 0))
    orient = "vertical" if abs(dx) < .55 else ("slanted" if abs(dy) > .40 else "horizontal")
    return (round(center.x / w * 30), round(center.y / h * 30),
            int(round(span.get("size", 0) / 3)), orient)


def repeated_patterns(doc) -> tuple[dict, list[int]]:
    ids = sample_ids(len(doc))
    occurrences = defaultdict(set)
    for idx in ids:
        for sp in doc[idx].get_texttrace():
            if sp.get("chars"):
                occurrences[signature(sp, doc[idx])].add(idx)
    return {k: len(v) for k, v in occurrences.items()}, ids


def stack_members(spans: list[dict], page) -> set[int]:
    """Detect vertically distributed, pale, separately drawn one-glyph spans.
    This supplements rotated-span detection for watermarks drawn char by char.
    """
    bins = defaultdict(list)
    for idx, sp in enumerate(spans):
        txt = span_text(sp).strip()
        color = sp.get("color", (0, 0, 0))
        if isinstance(color, (int, float)):
            color = (color,)
        pale = bool(color) and min(color) > .64 and max(color) - min(color) < .18
        if len(txt) != 1 or (sp.get("opacity", 1.0) > .56 and not pale):
            continue
        r = fitz.Rect(sp["bbox"])
        x = int(((r.x0 + r.x1) / 2) / 18)
        bins[(x, round(sp.get("size", 0) / 3))].append((idx, (r.y0 + r.y1) / 2))
    members = set()
    for items in bins.values():
        if len(items) >= 6 and max(y for _, y in items) - min(y for _, y in items) > page.rect.height * .17:
            members.update(i for i, _ in items)
    return members


def inspect_spans(page, repeats: dict, sample_count: int, mode="conservative") -> list[dict]:
    spans = list(page.get_texttrace())
    body_sizes = [s.get("size", 0) for s in spans
                  if s.get("opacity", 1.0) >= .9 and abs(s.get("dir", (1, 0))[1]) < .2
                  and 7 <= s.get("size", 0) <= 18 and len(span_text(s).strip()) > 2]
    body_size = statistics.median(body_sizes) if body_sizes else 11.0
    stack = stack_members(spans, page)
    reports = []
    min_repeats = 3 if sample_count >= 3 else max(2, sample_count)
    for i, sp in enumerate(spans):
        tx = span_text(sp)
        if not tx.strip():
            continue
        alpha = float(sp.get("opacity", 1.0))
        size = float(sp.get("size", 0.0))
        color = sp.get("color", (0, 0, 0))
        if isinstance(color, (int, float)):
            color = (color,)
        pale = bool(color) and min(color) > .64 and max(color) - min(color) < .18
        lowalpha = alpha <= .55
        vrot = abs(sp.get("dir", (1, 0))[0]) < .70
        large = size >= max(24, body_size * 1.9)
        repeat_n = repeats.get(signature(sp, page), 0)
        repeated = repeat_n >= min_repeats
        has_stack = i in stack
        r = fitz.Rect(sp["bbox"])
        x, y = (r.x0 + r.x1) / 2 / max(1, page.rect.width), (r.y0 + r.y1) / 2 / max(1, page.rect.height)
        central = .12 < x < .88 and .12 < y < .88
        named = bool(re.search(r"评审|送审|仅供|保密|试阅|水印|FOR REVIEW|CONFIDENTIAL|DRAFT", tx, re.I))
        evidence = []
        if lowalpha: evidence.append("low_opacity")
        if pale: evidence.append("pale_color")
        if vrot: evidence.append("rotated")
        if large: evidence.append("large_font")
        if repeated: evidence.append(f"repeats_in_{repeat_n}_sample_pages")
        if has_stack: evidence.append("vertical_single_glyph_stack")
        if named: evidence.append("watermark_phrase")
        # Require combined geometric, rendering, and/or cross-page evidence.
        # Conservative removal MUST have cross-page support plus visual evidence.
        # On a one-page PDF, leave ambiguous marks in place and report them for review.
        select = repeated and (
            (lowalpha and large and (vrot or named or central)) or
            (lowalpha and vrot and (len(tx.strip()) >= 5 or has_stack)) or
            (lowalpha and has_stack and central) or
            (pale and has_stack and central) or
            (pale and vrot and (large or len(tx.strip()) >= 5 or has_stack)) or
            (vrot and large and central) or
            (named and large and (vrot or lowalpha or pale))
        )
        if mode == "balanced":
            select = select or (repeated and pale and large and central) or \
                     (repeated and lowalpha and vrot and len(tx.strip()) >= 5)
        reports.append({"span_index": i, "text_preview": tx[:100], "bbox": [round(float(n), 2) for n in r],
                        "opacity": round(alpha, 3), "size": round(size, 2),
                        "repeated_sample_pages": repeat_n, "reasons": evidence,
                        "remove": bool(select), "trace": sp})
    return reports


def build_glyph_grid(reports: list[dict]):
    """Spatial hash of watermark glyph boxes, used to filter text BEFORE joining it."""
    cell = 32
    grid = defaultdict(list)
    for rep in reports:
        if not rep["remove"]:
            continue
        for c in rep["trace"].get("chars", []):
            code, bbox = c[0], fitz.Rect(c[3])
            if bbox.is_empty or bbox.is_infinite:
                continue
            key = (int((bbox.x0 + bbox.x1) / 2 // cell), int((bbox.y0 + bbox.y1) / 2 // cell))
            grid[key].append((code, bbox))
    return grid, cell


def remove_glyph(ch: dict, grid, cell: int) -> bool:
    bbox = fitz.Rect(ch["bbox"])
    if bbox.is_empty or bbox.is_infinite:
        return False
    center = (bbox.tl + bbox.br) / 2
    code = ord(ch["c"]) if len(ch.get("c", "")) == 1 else -1
    x, y = int(center.x // cell), int(center.y // cell)
    for ix in (x - 1, x, x + 1):
        for iy in (y - 1, y, y + 1):
            for target_code, target in grid.get((ix, iy), ()):
                if code != target_code:
                    continue
                # Prevent accidentally deleting a same-letter body glyph under
                # the overlay: require closely matching glyph footprint, not just
                # overlap with a much larger watermark character.
                widths = bbox.width / max(target.width, 0.01)
                heights = bbox.height / max(target.height, 0.01)
                if not (0.72 <= widths <= 1.38 and 0.72 <= heights <= 1.38):
                    continue
                overlap = (bbox & target).get_area()
                if overlap / max(0.01, min(bbox.get_area(), target.get_area())) > .73:
                    return True
    return False


def extract_text_filtered(page, reports: list[dict]) -> tuple[str, int]:
    grid, cell = build_glyph_grid(reports)
    if not grid:
        return page.get_text("text", sort=True) or "", 0
    blocks = page.get_text("rawdict", sort=True).get("blocks", [])
    lines_out = []
    removed = 0
    for b in blocks:
        if b.get("type") != 0:
            continue
        block_lines = []
        for ln in b.get("lines", []):
            chunks = []
            for sp in ln.get("spans", []):
                chars = []
                for c in sp.get("chars", []):
                    if remove_glyph(c, grid, cell):
                        removed += 1
                    else:
                        chars.append(c["c"])
                chunks.append("".join(chars))
            text = "".join(chunks).strip()
            if text:
                block_lines.append(text)
        if block_lines:
            lines_out.append("\n".join(block_lines))
    return "\n".join(lines_out), removed


def audit_entry(page, repeats, sample_count, mode) -> tuple[list[dict], dict]:
    reps = inspect_spans(page, repeats, sample_count, mode)
    review = [{k: v for k, v in r.items() if k != "trace"}
              for r in reps if r["remove"] or len(r["reasons"]) >= 2]
    return reps, {"page": page.number + 1, "watermark_spans_selected": sum(r["remove"] for r in reps),
                  "review_candidates": review}


def write_file(path, contents):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")
    return str(target)


def cmd_audit(doc, args):
    repeats, ids = repeated_patterns(doc)
    pages = parse_pages(args.pages, len(doc))
    report = {"file": str(args.pdf), "sample_pages": [i + 1 for i in ids],
              "mode": args.clean_mode, "pages": []}
    for idx in pages:
        _, a = audit_entry(doc[idx], repeats, len(ids), args.clean_mode)
        report["pages"].append(a)
    result = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        write_file(args.out, result)
        print(f"已写入证据审计: {args.out}")
    else:
        print(result)


# ---------- OCR only when needed; pixel preprocessing is opt-in ----------

def ocr_image(image, args):
    try:
        import pytesseract
    except ImportError:
        raise RuntimeError("OCR 需要 pytesseract: pip install pytesseract，并安装 Tesseract 引擎")
    requested = {x for x in args.ocr_lang.split("+") if x}
    available = set(pytesseract.get_languages(config=""))
    if not requested.issubset(available):
        raise RuntimeError(f"OCR 缺少语言包: {sorted(requested-available)}; 已有: {sorted(available)}")
    if args.ocr_preprocess == "suppress-light":
        try:
            import numpy as np
        except ImportError:
            raise RuntimeError("OCR 浅色像素抑制需要 numpy")
        from PIL import Image
        arr = np.asarray(image.convert("L")).copy()
        arr[arr >= args.light_threshold] = 255
        image = Image.fromarray(arr)
    try:
        return pytesseract.image_to_string(image, lang=args.ocr_lang,
                                           config=f"--psm {args.psm}", timeout=args.ocr_timeout)
    except (pytesseract.TesseractError, RuntimeError) as exc:
        raise RuntimeError(f"OCR 引擎运行失败: {exc}") from exc


def ocr_page(page, args):
    from PIL import Image
    scale = args.ocr_dpi / 72.0
    if page.rect.width * page.rect.height * scale * scale > args.max_pixels:
        raise RuntimeError("OCR 渲染超出像素预算。请降低 --ocr-dpi 或提高 --max-pixels")
    pm = page.get_pixmap(dpi=args.ocr_dpi, colorspace=fitz.csRGB, alpha=False)
    im = Image.frombytes("RGB", (pm.width, pm.height), pm.samples)
    return ocr_image(im, args)


def cmd_full(doc, args):
    pages = parse_pages(args.pages, len(doc))
    repeats, ids = repeated_patterns(doc) if args.clean else ({}, [])
    output, audits = [], []
    for idx in pages:
        p = doc[idx]
        raw_text = p.get_text("text", sort=True) or ""
        txt = raw_text
        entry = {"page": idx + 1, "status": "untouched", "removed_glyphs": 0,
                 "predicted_glyphs": 0, "predicted_remove_fraction": 0.0,
                 "needs_review": False, "review_candidates": []}
        if args.clean:
            reps, entry = audit_entry(p, repeats, len(ids), args.clean_mode)
            predicted = sum(len(span_text(r["trace"]).strip()) for r in reps if r["remove"])
            denominator = max(1, sum(len(span_text(r["trace"]).strip()) for r in reps))
            ratio = predicted / denominator
            entry.update({"predicted_glyphs": predicted,
                          "predicted_remove_fraction": round(ratio, 4),
                          "needs_review": False, "status": "no_confident_watermark",
                          "removed_glyphs": 0})
            if predicted and ratio <= args.max_remove_ratio:
                txt, removed = extract_text_filtered(p, reps)
                entry.update({"removed_glyphs": removed, "status": "watermark_filtered"})
            elif predicted:
                # Fail closed; original text remains unchanged.
                entry.update({"needs_review": True,
                              "status": "rollback_excessive_removal"})
            if any(not r["remove"] and len(r["reasons"]) >= 2 for r in reps):
                entry["needs_review"] = True
            # Limit audit volume; preserve indices, reasons and bounding boxes.
            entry["review_candidates"] = entry["review_candidates"][:150]
        met = page_metrics(p)
        want_ocr = args.ocr == "force" or (args.ocr == "auto" and
                     met["chars"] < args.ocr_min_chars and
                     met["image_coverage_approx"] >= args.ocr_min_image)
        used_ocr = False
        if want_ocr:
            try:
                txt = ocr_page(p, args)
                used_ocr = True
                entry["status"] = "ocr"  # OCR supersedes native watermark filtering.
            except (RuntimeError, ValueError) as exc:
                entry["needs_review"] = True
                entry["ocr_error"] = str(exc)
                entry["status"] += "+ocr_failed"
                if args.ocr == "force":
                    raise
                print(f"[OCR] 第 {idx+1} 页失败，保留原始文本: {exc}", file=sys.stderr)
                txt = raw_text
        if args.clean or args.ocr != "off":
            audits.append(entry)
        output.append({"page": idx+1, "mode": "ocr" if used_ocr else "native_text",
                       "content": txt.strip(), "metrics": met, "status": entry["status"]})
        if not args.quiet:
            print(f"[{idx+1}/{len(doc)}] {len(txt)} chars | {entry['status']}", file=sys.stderr)
    if args.format == "json":
        value = json.dumps({"file": str(args.pdf), "pages": output}, ensure_ascii=False, indent=2)
    else:
        chunks = []
        for record in output:
            marker = f"## PDF 第 {record['page']} 页" if args.format == "md" else f"===== PDF 第 {record['page']} 页 ====="
            chunks.append(f"{marker}\n\n{record['content']}")
        value = "\n\n".join(chunks) + "\n"
    if args.out:
        write_file(args.out, value)
        print(f"提取完成: {len(pages)} 页 -> {args.out}")
    else:
        print(value)
    if args.clean or args.ocr != "off":
        default_suffix = ".watermark-audit.json" if args.clean else ".extraction-audit.json"
        auditpath = args.audit or (str(Path(args.out).with_suffix(default_suffix)) if args.out else None)
        audit = {"tool": "pdfenv", "version": "2.2.0", "file": str(args.pdf),
                 "sha256": sha256_file(args.pdf),
                 "sample_pages": [i+1 for i in ids], "mode": args.clean_mode,
                 "max_remove_ratio": args.max_remove_ratio,
                 "summary": {"pages": len(pages),
                             "removed_glyphs": sum(e["removed_glyphs"] for e in audits),
                             "rollback_pages": [e["page"] for e in audits if "rollback" in e["status"]],
                             "needs_review_pages": [e["page"] for e in audits if e["needs_review"]]},
                 "pages": audits, "original_pdf_modified": False}
        if auditpath:
            write_file(auditpath, json.dumps(audit, ensure_ascii=False, indent=2))
            print(f"水印审计: {auditpath}")
        else:
            print(json.dumps(audit["summary"], ensure_ascii=False), file=sys.stderr)


# ---------- Rendering, including bounded-memory crops and tiled high-DPI output ----------

def rect_clip(page, arg, clip_rel=None):
    if arg and clip_rel:
        raise ValueError("--clip 和 --clip-rel 不能同时使用")
    if clip_rel:
        if len(clip_rel) != 4 or any(not 0 <= x <= 1 for x in clip_rel):
            raise ValueError("--clip-rel 接受 0..1 的 x0 y0 x1 y1")
        arg = (clip_rel[0]*page.rect.width, clip_rel[1]*page.rect.height,
               clip_rel[2]*page.rect.width, clip_rel[3]*page.rect.height)
    if not arg:
        return page.rect
    if len(arg) != 4:
        raise ValueError("--clip 接受 4 个 PDF 点坐标: x0 y0 x1 y1")
    r = fitz.Rect([float(v) for v in arg])
    r = r & page.rect
    if r.is_empty or r.is_infinite or r.width <= 0 or r.height <= 0:
        raise ValueError("--clip 裁切区域无效或完全在页面以外")
    return r


def rects_tiled(rect, tiles: int):
    for iy in range(tiles):
        for ix in range(tiles):
            x0 = rect.x0 + ix * rect.width / tiles
            y0 = rect.y0 + iy * rect.height / tiles
            x1 = rect.x0 + (ix+1) * rect.width / tiles
            y1 = rect.y0 + (iy+1) * rect.height / tiles
            yield ix, iy, fitz.Rect(x0, y0, x1, y1)


def save_image(image, file: Path, fmt: str, quality: int):
    file.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jpeg":
        image.convert("RGB").save(file, "JPEG", quality=quality, optimize=True)
    else:
        image.save(file, "PNG", optimize=True)


def render_image(page, dpi: int, clip=None, engine="mupdf", pdfium_page=None, hide_annots=False):
    if engine == "pdfium":
        if pdfium_page is None:
            raise ValueError("pdfium_page is required")
        if clip is not None and clip != page.rect:
            raise ValueError("pdfium 裁切渲染不可用，请使用 --engine mupdf --clip")
        return pdfium_page.render(scale=dpi/72.0, may_draw_forms=not hide_annots).to_pil().copy().convert("RGB")
    from PIL import Image
    pm = page.get_pixmap(dpi=dpi, clip=clip, colorspace=fitz.csRGB, alpha=False,
                         annots=not hide_annots)
    return Image.frombytes("RGB", (pm.width, pm.height), pm.samples)


def cmd_render(doc, args):
    if args.tile < 1 or args.dpi <= 0 or args.max_pixels <= 0:
        raise ValueError("DPI、tile 和 max-pixels 必须为正数")
    if args.engine == "pdfium" and args.hide_annots:
        raise ValueError("--hide-annots 当前仅支持 --engine mupdf；PDFium 表单开关并不保证隐藏所有注释")
    if args.engine == "pdfium" and (args.clip or args.clip_rel or args.tile > 1):
        raise ValueError("--engine pdfium 仅支持整页渲染；裁切/切片使用 mupdf")
    pdfium = None
    if args.engine == "pdfium":
        try:
            import pypdfium2
            pdfium = pypdfium2.PdfDocument(str(args.pdf))
        except ImportError:
            raise RuntimeError("缺少备用渲染器: pip install pypdfium2")
    outdir = Path(args.out or (str(args.pdf) + ".renders"))
    # Never auto-rasterize an entire 200-page dissertation by omission.
    pages = parse_pages(args.pages, len(doc)) if args.pages else sample_ids(len(doc), cap=6)
    if len(pages) > args.max_render_pages and not args.force_many:
        raise ValueError(f"渲染 {len(pages)} 页超过默认上限 {args.max_render_pages}; 如确需批量渲染，加 --force-many")
    outdir.mkdir(parents=True, exist_ok=True)
    ext = "jpg" if args.image_format == "jpeg" else "png"
    manifest = []
    rep_counts, rep_ids = repeated_patterns(doc) if args.mark_watermarks else ({}, [])
    try:
        for idx in pages:
            page = doc[idx]
            clip = rect_clip(page, args.clip, args.clip_rel)
            if args.mark_watermarks:
                reps, _ = audit_entry(page, rep_counts, len(rep_ids), args.clean_mode)
                wm_boxes = [fitz.Rect(r["bbox"]) for r in reps if r["remove"]]
            else:
                wm_boxes = []
            for tx, ty, region in rects_tiled(clip, args.tile):
                est = math.ceil(region.width * args.dpi / 72) * math.ceil(region.height * args.dpi / 72)
                if est > args.max_pixels:
                    raise RuntimeError(f"第 {idx+1} 页单张图片约 {est:,} 像素，超过 --max-pixels={args.max_pixels:,}; 可提高 --tile 或降低 --dpi")
                label = f"page-{idx+1:04d}" + (f"-tile-{ty+1}-{tx+1}" if args.tile > 1 else "")
                fn = outdir / f"{label}.{ext}"
                image = render_image(page, args.dpi, region, args.engine,
                                     pdfium[idx] if pdfium is not None else None,
                                     hide_annots=args.hide_annots)
                if wm_boxes:
                    from PIL import ImageDraw
                    draw = ImageDraw.Draw(image)
                    for box in wm_boxes:
                        if not (box & region).is_empty:
                            xy = ((box.x0 - region.x0) * args.dpi / 72,
                                  (box.y0 - region.y0) * args.dpi / 72,
                                  (box.x1 - region.x0) * args.dpi / 72,
                                  (box.y1 - region.y0) * args.dpi / 72)
                            draw.rectangle(xy, outline=(255, 35, 35), width=3)
                save_image(image, fn, args.image_format, args.quality)
                manifest.append({"page": idx+1, "path": str(fn), "engine": args.engine,
                                 "dpi": args.dpi, "size": list(image.size),
                                 "watermark_candidate_boxes": len(wm_boxes),
                                 "clip_pdf_points": [round(float(v), 2) for v in region]})
                print(f"渲染: {fn.name} ({image.size[0]} x {image.size[1]})")
    finally:
        if pdfium is not None:
            pdfium.close()
    write_file(outdir/"render_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"渲染完成: {len(manifest)} 张，清单: {outdir/'render_manifest.json'}")


def cmd_parity(doc, args):
    try:
        import pypdfium2
        import numpy as np
        from PIL import Image, ImageChops
    except ImportError as e:
        raise RuntimeError(f"parity 需要 pypdfium2 / numpy / Pillow: {e}")
    ids = parse_pages(args.pages, len(doc)) if args.pages else sample_ids(len(doc), cap=3)
    outdir = Path(args.out or (str(args.pdf) + ".parity"))
    outdir.mkdir(parents=True, exist_ok=True)
    pdfium = pypdfium2.PdfDocument(str(args.pdf))
    report = []
    try:
        for idx in ids:
            page = doc[idx]
            pixels = page.rect.width * page.rect.height * (args.dpi/72)**2
            if pixels > args.max_pixels:
                raise RuntimeError("parity 页面的像素预算超限；请降低 --dpi")
            a = render_image(page, args.dpi)
            b = render_image(page, args.dpi, engine="pdfium", pdfium_page=pdfium[idx])
            if a.size != b.size:
                b = b.resize(a.size, Image.Resampling.BILINEAR)
            array_a = np.asarray(a, dtype=np.int16)
            array_b = np.asarray(b, dtype=np.int16)
            mae = float(np.abs(array_a - array_b).mean())
            diff_fraction = float((np.abs(array_a - array_b).max(axis=2) > 32).mean())
            prefix = f"page-{idx+1:04d}"
            save_image(a, outdir/f"{prefix}-mupdf.png", "png", 90)
            save_image(b, outdir/f"{prefix}-pdfium.png", "png", 90)
            d = ImageChops.difference(a, b)
            save_image(d, outdir/f"{prefix}-diff.png", "png", 90)
            report.append({"page": idx+1, "mean_absolute_pixel_diff": round(mae, 4),
                           "fraction_pixels_diff_above_32": round(diff_fraction, 6)})
            print(f"对照第{idx+1}页: MAE={mae:.3f}, 差异像素比例={diff_fraction:.2%}")
    finally:
        pdfium.close()
    write_file(outdir/"parity.json", json.dumps(report, ensure_ascii=False, indent=2))
    print("提示: 字体抗锯齿差异不代表 PDF 本身出错；请目测差异区域。")


def cmd_tables(doc, args):
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("表格提取需要 pdfplumber: pip install pdfplumber")
    pages = parse_pages(args.pages, len(doc))
    tables = []
    with pdfplumber.open(str(args.pdf)) as pdf:
        for idx in pages:
            for j, tb in enumerate(pdf.pages[idx].extract_tables(), 1):
                tables.append({"page": idx+1, "table": j, "rows": tb})
    if args.out:
        write_file(args.out, json.dumps(tables, ensure_ascii=False, indent=2))
        print(f"提取表格 {len(tables)} 个 -> {args.out}")
    else:
        print(json.dumps(tables, ensure_ascii=False, indent=2))
    print("注意: 表格单元格与数字需对照页面渲染验证，尤其是合并单元格和水印覆盖的表格。", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="pdfenv v2.2: PDF 水印审计、文字提取、局部/整页高清渲染与 OCR")
    ap.add_argument("cmd", choices=["info", "sample", "full", "tables", "audit", "render", "parity"])
    ap.add_argument("pdf", type=Path)
    ap.add_argument("-o", "--out", help="文本/审计输出文件；render/parity 为输出目录")
    ap.add_argument("--pages", help="PDF 物理页码，从1起，支持 4-8,12,15-17")
    ap.add_argument("--head", type=int, default=5)
    ap.add_argument("--limit", type=int, default=800)
    ap.add_argument("--json", action="store_true", help="info 输出 JSON")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--clean", action="store_true", help="根据透明度/方向/跨页几何证据预先排除水印字符")
    ap.add_argument("--clean-mode", choices=["conservative", "balanced"], default="conservative")
    ap.add_argument("--max-remove-ratio", type=float, default=.35,
                    help="单页预测删除字符比例超过阈值时回退到原始文字（默认0.35）")
    ap.add_argument("--audit", help="full + --clean 时额外输出 JSON 审计文件")
    ap.add_argument("--format", choices=["txt", "md", "json"], default="txt")
    ap.add_argument("--ocr", choices=["off", "auto", "force"], default="off")
    ap.add_argument("--ocr-lang", default="chi_sim+eng")
    ap.add_argument("--ocr-dpi", type=int, default=260)
    ap.add_argument("--ocr-min-chars", type=int, default=45)
    ap.add_argument("--ocr-min-image", type=float, default=.65)
    ap.add_argument("--ocr-timeout", type=int, default=120, help="Tesseract 每页最大秒数")
    ap.add_argument("--ocr-preprocess", choices=["none", "suppress-light"], default="none")
    ap.add_argument("--light-threshold", type=int, default=210)
    ap.add_argument("--psm", type=int, default=3)
    ap.add_argument("--dpi", type=int, default=144, help="render/parity 页面 DPI")
    ap.add_argument("--engine", choices=["mupdf", "pdfium"], default="mupdf")
    ap.add_argument("--clip", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"), help="页面左上角原点的 PDF pt 区域")
    ap.add_argument("--clip-rel", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"),
                    help="0..1 页面归一化裁切坐标；与 --clip 二选一")
    ap.add_argument("--tile", type=int, default=1, help="高分辨率渲染将指定区域切成 N×N 张")
    ap.add_argument("--mark-watermarks", action="store_true", help="仅在渲染图上用红框审查被识别的水印，不修改 PDF")
    ap.add_argument("--hide-annots", action="store_true", help="渲染时隐藏注释层；不会隐藏直接绘制在页面上的水印")
    ap.add_argument("--max-render-pages", type=int, default=30)
    ap.add_argument("--force-many", action="store_true", help="明确允许渲染超过默认页数上限")
    ap.add_argument("--max-pixels", type=int, default=26_000_000)
    ap.add_argument("--image-format", choices=["png", "jpeg"], default="png")
    ap.add_argument("--quality", type=int, default=92)
    args = ap.parse_args()
    if not (0 < args.max_remove_ratio <= 1):
        ap.error("--max-remove-ratio 必须在 (0,1] 之间")
    if args.ocr_timeout < 1 or not 0 < args.ocr_min_image <= 1:
        ap.error("OCR 超时或图像覆盖度阈值无效")
    if not args.pdf.is_file():
        ap.error(f"PDF 文件不存在: {args.pdf}")
    try:
        with fitz.open(str(args.pdf)) as doc:
            if doc.needs_pass:
                raise RuntimeError("PDF 已加密且需要密码；请在有权限的情况下先解锁文件")
            commands = {"info": cmd_info, "sample": cmd_sample, "full": cmd_full,
                        "audit": cmd_audit, "tables": cmd_tables,
                        "render": cmd_render, "parity": cmd_parity}
            commands[args.cmd](doc, args)
    except (ValueError, RuntimeError, fitz.FileDataError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

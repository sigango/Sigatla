"""Contact sheets: several slides in one image, so an assistant can look at a part of a deck in a few images.

An image costs tokens by its pixel count. A slide drawn 784 px wide on a 2x2 sheet costs about a third of the
same slide at 1280 px, and formulas, subscripts and plot labels stay readable. Slides whose extracted text is
complete (no formula, picture or drawing) are left off the sheets: the slide text in the pack is enough for them.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .project import Project, Source, write_json

try:
    import pymupdf
except ImportError:  # pragma: no cover - reported by extract
    pymupdf = None

SHEET_W = 1568          # sheet width in px (the longest image side many models take without downscaling)
SHEET_MAX_H = 1200      # rows are added while the sheet stays this tall or less
COLS = 2

MATH_FONT = re.compile(r"^(CMMI|CMSY|CMEX|CMBSY|MSAM|MSBM|EUSM|EUFM|EUEX|RSFS|STIX|LMMath|LatinModernMath|"
                       r"CambriaMath|Cambria-Math|Symbol|MT-?Extra|wasy|esint|stmary)", re.I)


def _math_char(ch: str) -> bool:
    o = ord(ch)
    return (0x370 <= o <= 0x3FF or 0x2190 <= o <= 0x22FF or 0x2070 <= o <= 0x209F or 0x27C0 <= o <= 0x27FF
            or 0x2980 <= o <= 0x2AFF or 0x1D400 <= o <= 0x1D7FF or 0xE000 <= o <= 0xF8FF)


def _key(kind: str, r) -> tuple:
    return kind, round(r.x0 / 12), round(r.y0 / 12), round(r.x1 / 12), round(r.y1 / 12)


def page_visuals(doc) -> dict[int, list[str]]:
    """For every page: why its image is worth looking at ("formula", "picture", "drawing"); [] when its text
    says everything. Graphics repeated on many pages (logos, header bars) are template, not content."""
    per, seen = {}, Counter()
    for pno, page in enumerate(doc, start=1):
        area = max(page.rect.width * page.rect.height, 1)
        h = page.rect.height
        rects = []
        for info in page.get_image_info():
            r = pymupdf.Rect(info["bbox"]) & page.rect
            if r.width * r.height >= 0.01 * area:
                rects.append(("picture", r))
        try:
            for r in page.cluster_drawings():
                r = pymupdf.Rect(r) & page.rect
                if r.width > 25 and r.height > 15:
                    rects.append(("drawing", r))
        except Exception:  # older PyMuPDF or a malformed page
            pass
        rects = [(k, r) for k, r in rects if not (r.y1 < 0.08 * h or r.y0 > 0.92 * h)]
        math_font = math_chars = 0
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    t = span.get("text", "").strip()
                    if MATH_FONT.match(span.get("font", "").split("+", 1)[-1]):
                        math_font += len(t)
                    math_chars += sum(_math_char(c) for c in t)
        per[pno] = (rects, math_font, math_chars, area)
        seen.update({_key(k, r) for k, r in rects})
    template = {k for k, c in seen.items() if c >= max(3, 0.3 * len(per))}
    out = {}
    for pno, (rects, math_font, math_chars, area) in per.items():
        keep = [(k, r) for k, r in rects if _key(k, r) not in template]
        why = ["formula"] if math_font >= 3 or math_chars >= 3 else []
        for kind in ("picture", "drawing"):
            if sum(r.width * r.height for k, r in keep if k == kind) >= 0.04 * area:
                why.append(kind)
        out[pno] = why
    return out


def slide_visuals(project: Project, src: Source, manifest: dict) -> dict[int, list[str]]:
    """page_visuals for a deck, kept in its manifest so the PDF is analysed once."""
    cached = manifest.get("visuals")
    if isinstance(cached, dict) and len(cached) == manifest.get("pages"):
        return {int(k): v for k, v in cached.items()}
    with pymupdf.open(project.source_path(src)) as doc:
        vis = page_visuals(doc)
    manifest["visuals"] = {str(k): v for k, v in vis.items()}
    write_json(project.manifest_path(src.id), manifest)
    return vis


def write_sheets(project: Project, src: Source, manifest: dict, slides: list[int], tag: str) -> list[dict]:
    """Draw the given slides, in order, onto sheets build/<id>/sheets/<tag>-<n>.jpg, each slide labeled with its
    PDF slide number. Returns [{"file": "sheets/<tag>-<n>.jpg", "slides": [...]}]."""
    out_dir = project.build_dir(src.id) / "sheets"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{tag}-*.jpg"):
        old.unlink()
    if not slides:
        return []
    size = {f["page"]: (f.get("w") or 1280, f.get("h") or 960) for f in manifest.get("figures", [])}
    w, h = size.get(slides[0], (1280, 960))
    cell_w = SHEET_W // COLS
    cell_h = round(cell_w * h / max(w, 1))
    rows = max(1, min(3, SHEET_MAX_H // max(cell_h, 1)))
    per_sheet = COLS * rows
    sheets = []
    for n, start in enumerate(range(0, len(slides), per_sheet), start=1):
        batch = slides[start:start + per_sheet]
        used_rows = (len(batch) + COLS - 1) // COLS
        doc = pymupdf.open()
        page = doc.new_page(width=SHEET_W, height=used_rows * cell_h)
        for i, slide in enumerate(batch):
            x, y = (i % COLS) * cell_w, (i // COLS) * cell_h
            cell = pymupdf.Rect(x, y, x + cell_w, y + cell_h)
            img = project.build_dir(src.id) / "figures" / f"slide-{slide}.jpg"
            if img.exists():
                page.insert_image(cell, filename=str(img), keep_proportion=True)
            page.draw_rect(cell, color=(0.6, 0.6, 0.6), width=1)
            label = f"slide {slide}"
            lw = 11 * len(label) + 12
            page.draw_rect(pymupdf.Rect(cell.x1 - lw, cell.y0, cell.x1, cell.y0 + 30), color=None, fill=(0.75, 0.1, 0.1))
            page.insert_text((cell.x1 - lw + 6, cell.y0 + 22), label, fontsize=20, color=(1, 1, 1))
        name = f"{tag}-{n}.jpg"
        page.get_pixmap(alpha=False).save(str(out_dir / name), jpg_quality=85)
        doc.close()
        sheets.append({"file": f"sheets/{name}", "slides": batch})
    return sheets


def sheet_of(sheets: list[dict]) -> dict[int, str]:
    return {s: sh["file"] for sh in sheets for s in sh["slides"]}


def sheet_lines(sheets: list[dict], text_only: list[int], folder: Path) -> list[str]:
    """The pack's list of sheets and of slides that need no image."""
    lines = [f"Sheets, in `{folder}`:"] if sheets else []
    lines += [f"- {Path(sh['file']).name}: slides {', '.join(map(str, sh['slides']))}" for sh in sheets]
    if text_only:
        lines.append(f"- No image needed (plain text, complete above): slides {', '.join(map(str, text_only))}")
    return lines

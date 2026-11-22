"""Extract page text, figures, tables and slide images from a PDF into build/<id>/.

Papers: figures and tables are found from their captions ("Figure 3:", "Table 2.", "Hình 1:", ...).
The graphics nearest the caption (embedded images and clusters of vector drawings) are cropped and
rendered; a table is cropped from the region under its caption. Large uncaptioned images are kept too.
Slides: every slide is rendered as an image, and its text is kept.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from .project import AtlasError, Project, Source, read_json, write_json

try:
    import pymupdf
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
except ImportError:  # pragma: no cover - reported at run time
    pymupdf = None

MANIFEST_VERSION = 2
ZOOM = 2.0                 # 144 dpi for figure crops
MAX_FIG_PX = 1800          # longest side of a figure image
SLIDE_PX = 1280            # width of a rendered slide
CAPTION_RE = re.compile(
    r"^\s*(Figure|Fig\.?|Table|Tab\.?|Hình|Bảng|Abbildung|Abb\.?|Tabelle)[ \t]*([A-Z]?\d+[a-z]?|[IVXL]+)[ \t]*(?:[:.|]|\n)",
    re.IGNORECASE,
)
TABLE_WORDS = ("table", "tab", "bảng", "tabelle")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(text: str) -> str:
    """Drop NUL and other control characters that some PDFs put in their text (they make tools treat the
    text files as binary)."""
    return CONTROL_RE.sub("", text or "")


def _need_pymupdf():
    if pymupdf is None:
        raise AtlasError("PyMuPDF is missing. Install it with: pip install pymupdf")


def _union(rects):
    r = pymupdf.Rect(rects[0])
    for x in rects[1:]:
        r |= x
    return r


def _graphics(page) -> list:
    """Embedded images and clusters of vector drawings, minus thin header and footer marks."""
    H = page.rect.height
    rects = []
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"])
        if r.width > 20 and r.height > 20:
            rects.append(r)
    try:
        for r in page.cluster_drawings():
            if r.width > 25 and r.height > 15:
                rects.append(pymupdf.Rect(r))
    except Exception:  # older PyMuPDF or a malformed page
        pass
    return [r for r in rects if not (r.y1 < 0.06 * H or r.y0 > 0.94 * H) and r.width < 0.98 * page.rect.width]


def _band(page, cap):
    """The column a caption belongs to: full width, left half or right half."""
    W = page.rect.width
    mid = W / 2
    if cap.width > 0.55 * W or (cap.x0 < mid - 20 and cap.x1 > mid + 20):
        return 0.0, W
    return (0.0, mid + 4) if cap.x1 <= mid + 20 else (mid - 4, W)


def _in_band(r, band) -> bool:
    x0, x1 = band
    overlap = min(r.x1, x1) - max(r.x0, x0)
    return overlap > 0.5 * min(r.width, x1 - x0)


def _grow(seed, pool, gap: float):
    """Add rectangles from pool that touch the region vertically (within gap) and overlap it horizontally."""
    region = pymupdf.Rect(seed)
    changed = True
    while changed:
        changed = False
        for r in pool:
            if region.contains(r):
                continue
            v_gap = max(r.y0 - region.y1, region.y0 - r.y1, 0)
            h_overlap = min(r.x1, region.x1) - max(r.x0, region.x0)
            if v_gap <= gap and h_overlap > -gap:
                region |= r
                changed = True
    return region


def _clean_caption(raw: str) -> str:
    """Join lines and undo end-of-line hyphenation ("ad-\njustments" -> "adjustments")."""
    return " ".join(re.sub(r"(\w)-\n([a-z])", r"\1\2", raw).split())


def _captions(page):
    blocks = [b for b in page.get_text("blocks") if b[6] == 0]
    out = []
    for i, b in enumerate(blocks):
        m = CAPTION_RE.match(b[4])
        text = _clean_caption(b[4])
        if not m or len(text) <= len(" ".join(m.group(0).split())) + 3:
            continue
        rect = pymupdf.Rect(b[:4])
        # a caption that continues in the next block(s): same column, directly below
        for nb in blocks[i + 1:i + 4]:
            r = pymupdf.Rect(nb[:4])
            if 0 <= r.y0 - rect.y1 < 4 and min(r.x1, rect.x1) - max(r.x0, rect.x0) > 0.8 * min(r.width, rect.width) \
                    and not CAPTION_RE.match(nb[4]):
                rect |= r
                text += " " + _clean_caption(nb[4])
            else:
                break
        word = m.group(1).lower()
        kind = "table" if word.startswith(TABLE_WORDS) else "figure"
        out.append((rect, text, kind, m.group(2)))
    return out


def _figure_region(page, cap, band, caps, graphics, text_blocks):
    """Graphics directly above a figure caption."""
    above_caps = [c[0] for c in caps if c[0].y1 <= cap.y0 and _in_band(c[0], band) and c[0] != cap]
    floor = max([c.y1 for c in above_caps] + [0.0])
    pool = [r for r in graphics if _in_band(r, band) and r.y1 <= cap.y0 + 6 and r.y0 >= floor - 2]
    if pool:
        seed = max(pool, key=lambda r: r.y1)
        if seed.y1 >= cap.y0 - 120:
            region = _grow(seed, pool, gap=24)
            # include short text blocks (axis labels, legends) lying inside the region's vertical span
            for t in text_blocks:
                if _in_band(t, band) and t.y0 >= region.y0 - 2 and t.y1 <= cap.y0 + 2 and len(t.text) < 120:
                    if min(t.x1, region.x1) - max(t.x0, region.x0) > -10:
                        region |= t.rect
            return region
    # no graphics: take the space between the last paragraph above and the caption
    paras = [t for t in text_blocks if _in_band(t, band) and t.y1 <= cap.y0 - 2 and len(t.text) > 150]
    top = max([t.y1 for t in paras] + [floor, page.rect.height * 0.06])
    if cap.y0 - top > 40:
        return pymupdf.Rect(band[0] + 6, top, band[1] - 6, cap.y0)
    return None


def _tabular(t) -> bool:
    """Short lines or many digits: looks like table cells rather than a paragraph."""
    lines = [ln for ln in t.raw.splitlines() if ln.strip()]
    if not lines:
        return False
    digits = sum(c.isdigit() for c in t.text) / max(len(t.text), 1)
    return digits > 0.12 or sum(len(ln) for ln in lines) / len(lines) < 38


def _table_region(page, cap, band, caps, graphics, text_blocks):
    """A table next to its caption: below it (usual in CS papers) or above it, whichever side holds the table."""
    H = page.rect.height
    near_above = [g for g in graphics if _in_band(g, band) and g.y1 <= cap.y0 + 4 and cap.y0 - g.y1 < 40]
    near_above += [t.rect for t in text_blocks if _in_band(t, band) and t.y1 <= cap.y0 + 2 and cap.y0 - t.y1 < 30 and _tabular(t)]
    stop = H * 0.94
    for c in caps:
        if c[0].y0 > cap.y1 and _in_band(c[0], band):
            stop = min(stop, c[0].y0)
    below = []
    for t in sorted((t for t in text_blocks if _in_band(t, band) and t.y0 >= cap.y1 - 2), key=lambda t: t.y0):
        if t.y0 > stop or not _tabular(t):
            break
        below.append(t.rect)
    below += [g for g in graphics if _in_band(g, band) and g.y0 >= cap.y1 - 4 and g.y1 <= stop + 4]
    if below and (not near_above or min(r.y0 for r in below) - cap.y1 < 30):
        region = _union(below)
        if region.height > 20:
            return region
    return _figure_region(page, cap, band, caps, graphics, text_blocks) if near_above else None


class _TB:
    __slots__ = ("rect", "text", "raw", "x0", "x1", "y0", "y1")

    def __init__(self, b):
        self.rect = pymupdf.Rect(b[:4])
        self.raw = b[4]
        self.text = " ".join(b[4].split())
        self.x0, self.y0, self.x1, self.y1 = self.rect

    @property
    def width(self):
        return self.rect.width


def _render(page, clip, path: Path) -> tuple[int, int]:
    clip = pymupdf.Rect(clip) & page.rect
    zoom = min(ZOOM, MAX_FIG_PX / max(clip.width, clip.height, 1))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(path))
    return pix.width, pix.height


def _paper_figures(doc, sid: str, out: Path) -> list[dict]:
    figures, seen = [], set()
    for pno, page in enumerate(doc, start=1):
        caps = _captions(page)
        graphics = _graphics(page)
        text_blocks = [_TB(b) for b in page.get_text("blocks") if b[6] == 0]
        regions = []
        for cap, text, kind, num in caps:
            name = f"{'tab' if kind == 'table' else 'fig'}-{num.lower()}"
            if name in seen:
                continue
            band = _band(page, cap)
            finder = _table_region if kind == "table" else _figure_region
            region = finder(page, cap, band, caps, graphics, text_blocks)
            if region is None or region.width < 40 or region.height < 25:
                continue
            region = pymupdf.Rect(region.x0 - 4, region.y0 - 4, region.x1 + 4, region.y1 + 4)
            if region.y1 <= cap.y0 + 10:      # crop above the caption: never include the caption itself
                region.y1 = min(region.y1, cap.y0 - 1)
            elif region.y0 >= cap.y1 - 10:    # crop below the caption
                region.y0 = max(region.y0, cap.y1 + 1)
            w, h = _render(page, region, out / "figures" / f"{name}.png")
            seen.add(name)
            regions.append(region)
            figures.append({"id": f"{sid}/{name}", "file": f"figures/{name}.png", "page": pno, "kind": kind,
                            "caption": text[:1500], "w": w, "h": h})
        # large images nobody captioned (photos, screenshots)
        area = page.rect.width * page.rect.height
        k = 0
        for info in page.get_image_info():
            r = pymupdf.Rect(info["bbox"]) & page.rect
            if r.width * r.height < 0.12 * area or any((r & g).get_area() > 0.5 * r.get_area() for g in regions):
                continue
            k += 1
            name = f"img-p{pno}-{k}"
            w, h = _render(page, r, out / "figures" / f"{name}.png")
            regions.append(r)
            figures.append({"id": f"{sid}/{name}", "file": f"figures/{name}.png", "page": pno, "kind": "image",
                            "caption": "", "w": w, "h": h})
    return figures


COUNTER_RE = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
VIDEO_ANNOTS = {"Movie", "RichMedia", "Screen", "Sound"}


def _lines(text: str) -> list[str]:
    return [" ".join(ln.split()) for ln in text.splitlines() if ln.strip()]


def _boilerplate(page_lines: list[list[str]]) -> set[str]:
    """Lines repeated on most slides (logo, lecturer, course, date) are not content."""
    if len(page_lines) < 5:
        return set()
    counts: dict[str, int] = {}
    for lines in page_lines:
        for ln in set(lines):
            counts[ln] = counts.get(ln, 0) + 1
    return {ln for ln, c in counts.items() if c >= 0.4 * len(page_lines)}


def _slide_title(page, boiler: set[str]) -> str:
    """The text in the largest font in the upper part of the slide."""
    spans = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for sp in line.get("spans", []):
                t = " ".join(sp.get("text", "").split())
                if t and sp["bbox"][1] < page.rect.height * 0.4 and t not in boiler and not COUNTER_RE.match(t):
                    spans.append((round(sp["size"], 1), sp["bbox"][1], t))
    if not spans:
        return ""
    top = max(sz for sz, _, _ in spans)
    parts = [t for sz, y, t in sorted(spans, key=lambda x: x[1]) if sz >= top - 0.5]
    return " ".join(parts)[:160]


def _sections(toc: list, n_pages: int) -> list[tuple[str, str]]:
    """(section, subsection) for every page, from the PDF outline."""
    out = [("", "")] * (n_pages + 1)
    entries = sorted((e for e in toc if len(e) >= 3 and isinstance(e[2], int) and e[2] >= 1), key=lambda e: e[2])
    sec = sub = ""
    i = 0
    for pno in range(1, n_pages + 1):
        while i < len(entries) and entries[i][2] <= pno:
            level, title = entries[i][0], " ".join(str(entries[i][1]).split())
            if level == 1:
                sec, sub = title, ""
            elif level == 2:
                sub = title
            i += 1
        out[pno] = (sec, sub)
    return out


def slide_frames(doc, page_text: list[str]) -> list[dict]:
    """Group pages into slides: a page whose content is contained in the next page with the same title is a
    build-up step (an overlay or animation step) of that slide."""
    page_lines = [_lines(t) for t in page_text]
    boiler = _boilerplate(page_lines)
    content = [[ln for ln in lines if ln not in boiler and not COUNTER_RE.match(ln)] for lines in page_lines]
    titles = [_slide_title(page, boiler) for page in doc]
    sections = _sections(doc.get_toc(simple=True), len(doc))
    words = [set(" ".join(c).split()) for c in content]
    frames, start = [], 0
    for i in range(len(doc)):
        nxt = i + 1
        is_step = (nxt < len(doc) and titles[i] == titles[nxt] and words[i]
                   and len(words[i] & words[nxt]) >= 0.9 * len(words[i])
                   and len(" ".join(content[nxt])) >= len(" ".join(content[i])))
        if is_step:
            continue
        pages = list(range(start + 1, i + 2))
        video, links = False, []
        for pno in pages:
            page = doc[pno - 1]
            for a in page.annots() or []:
                if a.type[1] in VIDEO_ANNOTS:
                    video = True
            for ln in page.get_links():
                uri = ln.get("uri") or ""
                if uri.startswith("http") and uri not in links:
                    links.append(uri)
        sec, sub = sections[i + 1]
        frames.append({"slide": i + 1, "pages": [pages[0], pages[-1]], "title": titles[i], "section": sec,
                       "subsection": sub, "video": video, "links": links[:5], "text": "\n".join(content[i])})
        start = i + 1
    return frames


def _slide_images(doc, sid: str, out: Path, frames: list[dict]) -> list[dict]:
    """Every page is rendered; the last page of each slide is the slide's figure, the others are its steps."""
    final_of = {}
    for fr in frames:
        for pno in range(fr["pages"][0], fr["pages"][1] + 1):
            final_of[pno] = fr
    figures = []
    for pno, page in enumerate(doc, start=1):
        zoom = SLIDE_PX / max(page.rect.width, 1)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        path = out / "figures" / f"slide-{pno}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(path), jpg_quality=82)
        fr = final_of.get(pno)
        last = fr is None or fr["slide"] == pno
        caption = (fr or {}).get("title", "") if last else f"build-up step of slide {fr['slide']}"
        figures.append({"id": f"{sid}/slide-{pno}", "file": f"figures/slide-{pno}.jpg", "page": pno,
                        "kind": "slide" if last else "step", "caption": caption, "w": pix.width, "h": pix.height})
    return figures


def write_slides_text(manifest: dict, path: Path, label: str = "", page_range: tuple[int, int] | None = None,
                      look: dict[int, str] | None = None) -> str:
    """The slides as text, one section per slide, for readers (people or AIs) that work from text. `look` adds,
    per slide, where to look at it (a contact sheet) or that its text is enough."""
    frames = [f for f in manifest.get("frames", [])
              if page_range is None or page_range[0] <= f["slide"] <= page_range[1]]
    out = [f"# Slides of {label or manifest.get('title') or manifest['id']}", "",
           "Text extracted from the PDF. Formulas and diagrams are garbled or missing in this text: read them from "
           "the slide images listed with each slide.", ""]
    page_text = manifest.get("page_text") or []
    for f in frames:
        where = " › ".join(x for x in (f["section"], f["subsection"]) if x)
        span = f"page {f['slide']}" if f["pages"][0] == f["pages"][1] else f"pages {f['pages'][0]}–{f['pages'][1]}"
        out.append(f"## Slide {f['slide']} ({span}){' · ' + where if where else ''}")
        out.append(f"Title: {clean_text(f['title']) or '(none)'}")
        out.append(f"Image: figures/slide-{f['slide']}.jpg (figure id {manifest['id']}/slide-{f['slide']})"
                   + (f" · look: {look[f['slide']]}" if look and f["slide"] in look else ""))
        steps = list(range(f["pages"][0], f["pages"][1]))
        if steps:
            out.append("Build-up steps: " + ", ".join(f"figures/slide-{p}.jpg" for p in steps))
            final = set(_lines(page_text[f["slide"] - 1])) if len(page_text) >= f["slide"] else set()
            only = []
            for p in steps:
                for ln in _lines(page_text[p - 1]) if len(page_text) >= p else []:
                    if ln not in final and ln not in only:
                        only.append(ln)
            if only:
                out.append("Text only on build-up steps (gone from the final slide): "
                           + clean_text(" | ".join(only))[:600])
        if f.get("video"):
            out.append("Video: this slide embeds a video that is not available here.")
        if f.get("links"):
            out.append("Links: " + ", ".join(f["links"]))
        out += ["", clean_text(f["text"]) or "(no text)", ""]
    text = "\n".join(out)
    if path is not None:
        path.write_text(text, encoding="utf-8")
    return text


def extract(project: Project, src: Source, force: bool = False, log=print) -> dict:
    """Write build/<id>/manifest.json and the figure images; returns the manifest."""
    _need_pymupdf()
    pdf = project.source_path(src)
    if not pdf.exists():
        raise AtlasError(f"{src.id}: file not found: {pdf}")
    out = project.build_dir(src.id)
    mpath = project.manifest_path(src.id)
    old = read_json(mpath)
    if old and not force and old.get("version") == MANIFEST_VERSION and old.get("mtime") == pdf.stat().st_mtime:
        log(f"{src.id}: already extracted ({len(old['figures'])} figures); use --force to redo")
        return old
    if (out / "figures").exists():
        for f in (out / "figures").iterdir():
            f.unlink()
    doc = pymupdf.open(pdf)
    page_text = [clean_text(page.get_text("text")) for page in doc]
    frames = []
    if src.kind == "slides":
        frames = slide_frames(doc, page_text)
        figures = _slide_images(doc, src.id, out, frames)
    else:
        figures = _paper_figures(doc, src.id, out)
    if sum(len(t.strip()) for t in page_text) < 50 * max(len(doc), 1):
        log(f"{src.id}: warning: very little text found; the PDF may be scanned. Claude still reads the page images.")
    manifest = {
        "version": MANIFEST_VERSION, "id": src.id, "kind": src.kind, "file": str(pdf), "mtime": pdf.stat().st_mtime,
        "pages": len(doc), "title": (doc.metadata or {}).get("title") or "", "toc": doc.get_toc(simple=True)[:80],
        "page_text": page_text, "frames": frames, "figures": figures,
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_json(mpath, manifest)
    if frames:
        write_slides_text(manifest, out / "slides.md", src.label)
    kinds = {}
    for f in figures:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    if frames:
        steps = len(doc) - len(frames)
        log(f"{src.id}: {len(doc)} pages, {len(frames)} slides" + (f" ({steps} build-up steps merged)" if steps else "")
            + f"; text in {out / 'slides.md'}")
    else:
        log(f"{src.id}: {len(doc)} pages, "
            + (", ".join(f"{v} {k}{'' if v == 1 else 's'}" for k, v in kinds.items()) or "no figures"))
    return manifest


def load_manifest(project: Project, src: Source) -> dict:
    m = read_json(project.manifest_path(src.id))
    if not m:
        raise AtlasError(f"{src.id}: not extracted yet. Run `sigatla extract {src.id}` first.")
    return m

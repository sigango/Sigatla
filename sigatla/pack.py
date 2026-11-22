"""Reading packs: the reading task written as one Markdown file that any AI assistant can follow.

`sigatla pack <id>` writes build/<id>/READING_PACK.md (or READING_PACK.part-<k>.md for a long deck read in parts).
The assistant reads the pack, the PDF and the slide or figure images, writes the notes file the pack names, and
runs `sigatla check`. No API key is needed: the reading happens in the assistant the user already has.

A pack is the whole task, so the assistant needs no other guide, and it is written to keep the assistant's
context small: slides are shown on contact sheets (several per image), slides whose text is complete need no
image, and lookups go through `sigatla ids --grep` and `sigatla show` instead of whole notes files.
"""
from __future__ import annotations

import json
import re

from . import prompts
from .claude import backup_shared, connect_input, known_ids
from .extract import clean_text, load_manifest, write_slides_text
from .project import AtlasError, Project, Source
from .sheets import sheet_lines, sheet_of, slide_visuals, write_sheets

PART_SLIDES = 45      # a deck with more slides than this (with 30% tolerance) is read in parts
PART_PAGES = 30       # same for papers and other documents, in pages
CLOSING_MAX = 8       # closing slides shown to part 1 for the deck's summary

_BACK_MATTER = re.compile(r"appendix|anhang|reference|bibliograph|literatur|backup|extra slides|acknowledg", re.I)
_WRAP_UP = re.compile(r"wrap[- ]?up|summary|conclusion|self[- ]?test|recap|take[- ]?home|key points|review|"
                      r"zusammenfassung|fazit|tổng kết|tóm tắt|kết luận", re.I)

_FOOTER_READ = """\
## Steps
In these steps, `sigatla` means `{atlas}`.

1. Read {what}. {how}
2. Write the notes as JSON to `{out}`, matching the output schema below exactly (top-level keys {keys}). Write
   only this file, in one go (from a small script is easiest).
   Just before writing, look up what other readers may have added meanwhile: `sigatla ids --related` for related
   work, `sigatla ids --grep "<regex>"` for notes on a topic, `sigatla show <id> [<id> ...]` to read particular
   notes. Reuse a related-work id when it is the same work, and link to their notes where they fit.
3. Run `sigatla check --source {sid}`. Fix every error and every hint with small edits to the file (do not write
   it again from scratch).
4. {next_step}

This pack is the whole task: you do not need AGENTS.md, docs/ or the tool's source code. Keep your context
small: do not print whole notes files, and do not open `site/` or files in `build/` other than the ones named here.
"""

_FOOTER_CONNECT = """\
## Steps
In these steps, `sigatla` means `{atlas}`.

1. Read the index above. To see what notes say, run `sigatla show --brief <id> <id> ...` (many ids in one call:
   type, title, one-line summary and links), or `sigatla show <id>` for a whole note. Do not print whole notes
   files.
2. Write the JSON to `{out}`, matching the schema below (top-level keys `axis`, `nodes`, `links`).
3. Run `sigatla check --source shared`, fix what it reports with small edits, then `sigatla build`.
"""


def _atlas_cmd(project: Project) -> str:
    """The exact command to run, so an assistant does not have to find the tool or the project."""
    import shutil
    import sys
    exe = shutil.which("sigatla") or f"{sys.executable} -m sigatla"
    return f"{exe} -C {project.root}"


# parts
def plan_parts(manifest: dict, kind: str) -> list[dict]:
    """Split a long source into reading parts at section boundaries (slides) or page boundaries (documents)."""
    if kind == "slides" and manifest.get("frames"):
        units = [(f["slide"], f["section"] or "") for f in manifest["frames"]]
        limit = PART_SLIDES
    else:
        units = [(p, "") for p in range(1, manifest.get("pages", 0) + 1)]
        limit = PART_PAGES
    if len(units) <= limit * 1.3:   # a little over the limit is still one reading
        return [{"part": 1, "first": units[0][0] if units else 1, "last": units[-1][0] if units else 1,
                 "sections": sorted({s for _, s in units if s}, key=lambda s: [u[1] for u in units].index(s))}]
    # group consecutive units by section, then pack whole sections into parts of at most `limit` units
    groups: list[list] = []
    for u in units:
        if groups and groups[-1][-1][1] == u[1]:
            groups[-1].append(u)
        else:
            groups.append([u])
    chunks, cur = [], []
    for g in groups:
        while len(g) > limit:                      # a section longer than a part is split
            if cur:
                chunks.append(cur)
                cur = []
            chunks.append(g[:limit])
            g = g[limit:]
        if cur and len(cur) + len(g) > limit:
            chunks.append(cur)
            cur = []
        cur = cur + g
    if cur:
        chunks.append(cur)
    # fold small leftovers (an intro or a wrap-up section) into a neighbouring part
    merged = True
    while merged and len(chunks) > 1:
        merged = False
        for i, c in enumerate(chunks):
            if len(c) >= limit / 3:
                continue
            j = i - 1 if i > 0 else i + 1
            if len(chunks[j]) + len(c) <= limit * 1.4:
                lo, hi = sorted((i, j))
                chunks[lo:hi + 1] = [chunks[lo] + chunks[hi]]
                merged = True
                break
    parts = []
    for k, c in enumerate(chunks, start=1):
        secs = []
        for _, s in c:
            if s and s not in secs:
                secs.append(s)
        parts.append({"part": k, "first": c[0][0], "last": c[-1][0], "sections": secs})
    return parts


def closing_frames(frames: list[dict], after: int) -> list[dict]:
    """The deck's closing slides after slide `after`: its wrap-up or summary section if it has one, else its last
    section that is not back matter (appendix, references, backup)."""
    tail = [f for f in frames if f["slide"] > after]
    secs = []
    for f in tail:
        if f["section"] and f["section"] not in secs:
            secs.append(f["section"])
    pick = next((x for x in reversed(secs) if _WRAP_UP.search(x) and not _BACK_MATTER.search(x)), None) \
        or next((x for x in reversed(secs) if not _BACK_MATTER.search(x)), None)
    if pick:
        return [f for f in tail if f["section"] == pick][-CLOSING_MAX:]
    if secs:   # every later section is back matter
        return []
    # no outline: the last slides whose titles do not look like back matter
    return [f for f in tail if not _BACK_MATTER.search(f.get("title") or "")][-CLOSING_MAX:]


def part_notes_path(project: Project, sid: str, part: int):
    return project.notes_path(sid) if part == 1 else project.root / "notes" / f"{sid}.part-{part}.json"


def source_nodes(project: Project, sid: str) -> list[dict]:
    """Notes already written for a source, across its parts."""
    return project.all_nodes(sid)


# reading packs
def write_reading_pack(project: Project, src: Source, part: int | None = None, log=print) -> str | None:
    manifest = load_manifest(project, src)
    parts = plan_parts(manifest, src.kind)
    if part is None:
        todo = [p for p in parts if not part_notes_path(project, src.id, p["part"]).exists()]
        if not todo:
            log(f"{src.id}: every part has notes. Pass --part N to write a pack again.")
            return None
        info = todo[0]
    else:
        if not 1 <= part <= len(parts):
            raise AtlasError(f"{src.id} has {len(parts)} part(s).")
        info = parts[part - 1]
    k, n = info["part"], len(parts)
    rw, shared = known_ids(project, exclude=src.id)
    pdf = project.source_path(src)
    out_notes = part_notes_path(project, src.id, k)
    is_slides = src.kind == "slides" and manifest.get("frames")
    unit = "slides" if is_slides else "pages"
    span = (info["first"], info["last"])

    sections = [f"# Reading pack: {src.label} ({src.id})" + (f", part {k} of {n}" if n > 1 else ""), ""]
    sections.append(prompts.read_system(src.kind, src.id, project.language, pdf=f"the PDF file `{pdf}`"))
    if n > 1:
        earlier = [x for x in source_nodes(project, src.id)]
        count = sum(1 for f in manifest.get("frames", []) if span[0] <= f["slide"] <= span[1]) if is_slides \
            else span[1] - span[0] + 1
        lo, hi = max(4, count // 2), max(6, count)
        lines = [f"## This part: {k} of {n}",
                 f"This source is long, so it is read in {n} parts. Write notes only for the {unit} numbered "
                 f"{span[0]} to {span[1]} ({count} {unit}"
                 + (" after merging build-up steps" if is_slides else "") + ")"
                 + (f", sections: {'; '.join(info['sections'])}" if info["sections"] else "") + ".",
                 "- Note ids still use the prefix of the source, and must not repeat ids from earlier parts.",
                 f"- For this part, aim for about {lo} to {hi} notes (not counting related notes)."]
        if k == 1:
            lines.append("- This first part also writes `source` for the whole source: use the outline below and "
                         "the closing slides at the end of this pack for the summary, the learning goals and "
                         "source.questions.")
        else:
            lines.append("- Do not write `source` again; it was written in part 1. The output has only `nodes`.")
        if earlier:
            lines += ["", "Notes written by earlier parts (link to them where the content builds on them; do not "
                      "write them again):"]
            lines += [f"- {x.get('id')} ({x.get('type')}): {x.get('title')}" for x in earlier]
        rest = [p for p in parts if p["part"] > k]
        if rest:
            lines += ["", "Later parts will cover: " + "; ".join(
                f"part {p['part']}: {unit} {p['first']}–{p['last']}" for p in rest) + "."]
        sections += lines + [""]
    sections += ["## Input",
                 prompts.read_user_text(src.label, src.kind, src.id, manifest, rw, shared,
                                        other_sources=[s.id for s in project.sources if s.id != src.id],
                                        closing=False, page_range=span if n > 1 else None),
                 ""]
    look, sheets, text_only = {}, [], []
    if is_slides:
        vis = slide_visuals(project, src, manifest)
        in_span = [f["slide"] for f in manifest["frames"] if span[0] <= f["slide"] <= span[1]]
        visual = [s for s in in_span if vis.get(s)]
        text_only = [s for s in in_span if not vis.get(s)]
        sheets = write_sheets(project, src, manifest, visual, f"part-{k}" if n > 1 else "deck")
        where = sheet_of(sheets)
        look = {s: f"{where[s]} ({', '.join(vis[s])})" for s in visual}
        look.update({s: "not needed, the text below is complete" for s in text_only})
        sections += ["## Looking at the slides",
                     f"Look at the slides on these contact sheets ({len(sheets)} images, up to "
                     f"{len(sheets[0]['slides']) if sheets else 4} slides each, labeled with the PDF slide number). "
                     "Open several sheets in one step. Open a full slide image (`figures/slide-<n>.jpg`, 1280 px) "
                     "only for small print you cannot read on a sheet, and a build-up step image only when the "
                     "slide text says a step shows something the final slide does not.", "",
                     *sheet_lines(sheets, text_only, project.build_dir(src.id) / "sheets"), ""]
        sections += ["## Text of the slides in this " + ("part" if n > 1 else "deck"),
                     "(Use it to find things quickly. It is extracted from the PDF, so formulas and diagrams are "
                     "garbled or missing: read them from the sheets.)", "",
                     write_slides_text(manifest, None, src.label, span, look=look).split("\n", 3)[-1], ""]
    if is_slides and n > 1 and k == 1:
        closing = closing_frames(manifest["frames"], span[1])
        if closing:
            name = closing[0]["section"] or "last slides"
            sections += [f"## Closing slides of the deck ({name})",
                         "(For `source` only: the summary, the learning goals and source.questions. Their notes "
                         "belong to the last part.)", "",
                         write_slides_text(manifest, None, src.label,
                                           (closing[0]["slide"], closing[-1]["slide"])).split("\n", 3)[-1], ""]
    figdir = project.build_dir(src.id) / "figures"
    if is_slides:
        what = f"slides {span[0]} to {span[1]} of the deck" if n > 1 else "the deck"
        how = ("Work from the slide text above and the contact sheets listed under \"Looking at the slides\"; read "
               f"every formula and diagram from the sheets. You do not need to open the PDF (`{pdf}`). Full slide "
               f"images (1280 px) are in `{figdir}` as slide-<page>.jpg, for small print.")
    else:
        what = f"pages {span[0]} to {span[1]} of the PDF `{pdf}`" if n > 1 else f"the PDF `{pdf}` (in page ranges if long)"
        how = f"Extracted figures and tables are in `{figdir}`; open them when a figure matters for a note."
    if k < n:
        next_step = (f"Stop here and report. Part {k + 1} is a separate job (`sigatla pack {src.id} --part {k + 1}`) "
                     "for a fresh assistant, a subagent or a new conversation: reading it here would carry this "
                     "part's context along into every step.")
    else:
        next_step = ("Run `sigatla build` and give the user the path to `site/index.html`, unless other sources are "
                     "being read at the same time (then build once all notes exist).")
    keys = "`source` and `nodes`" if k == 1 else "`nodes` only"
    sections += [_FOOTER_READ.format(what=what, how=how, out=out_notes, keys=keys, atlas=_atlas_cmd(project),
                                     sid=src.id, next_step=next_step),
                 "## Output schema", "```json",
                 json.dumps(prompts.READ_SCHEMA if k == 1 else prompts.PART_SCHEMA, indent=1), "```"]
    name = "READING_PACK.md" if n == 1 or k == 1 else f"READING_PACK.part-{k}.md"
    out = project.build_dir(src.id) / name
    out.write_text(clean_text("\n".join(sections)), encoding="utf-8")
    log(f"{src.id}: reading pack" + (f" for part {k} of {n} ({unit} {span[0]}–{span[1]})" if n > 1 else "")
        + f" written to {out}")
    return str(out)


def write_connect_pack(project: Project, log=print) -> str:
    out = project.root / "build" / "CONNECT_PACK.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    backup_shared(project)
    text = "\n".join([
        "# Connecting the sources",
        "",
        prompts.connect_system(project.language),
        "## Input",
        connect_input(project, embed_current=False, compact=True),
        "",
        _FOOTER_CONNECT.format(out=project.shared_path, atlas=_atlas_cmd(project)),
        "## Output schema",
        "```json",
        json.dumps(prompts.CONNECT_SCHEMA, indent=1),
        "```",
    ])
    out.write_text(clean_text(text), encoding="utf-8")
    log(f"connect pack written to {out}")
    return str(out)

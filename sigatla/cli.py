"""Command line: sigatla init | add | extract | read | pack | connect | build | check | serve | status | update |
next | ids | show | form | guide."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .project import KINDS, AtlasError, Project, read_json


def _project(args) -> Project:
    return Project.load(getattr(args, "dir", None) or ".")


def cmd_init(args):
    from .guide import write_guide
    p = Project.create(args.path, title=args.title, language=args.lang, subtitle=args.subtitle or "")
    print(f"Created {p.root / 'atlas.json'}.")
    write_guide(p)
    print("Next: sigatla add <file.pdf> --kind paper|slides")


def cmd_guide(args):
    from .guide import write_guide
    write_guide(_project(args))


def cmd_next(args):
    from .guide import next_steps
    print("\n".join(next_steps(_project(args))))


def cmd_add(args):
    p = _project(args)
    src = p.add_source(args.file, args.kind, sid=args.id, label=args.label, copy=not args.no_copy, short=args.short)
    print(f"Added '{src.id}' ({src.kind}): {src.file}")
    if not args.no_extract:
        from .extract import extract
        extract(p, src)
    print(f"Next: sigatla next   (what to read, as commands for your AI assistant)   or   sigatla read {src.id}   "
          "(Claude API)")


def cmd_extract(args):
    from .extract import extract
    p = _project(args)
    for src in p.pick(args.ids):
        extract(p, src, force=args.force)


def _to_read(p: Project, ids, all_sources: bool):
    if ids:
        return p.pick(ids)
    return [s for s in p.sources if all_sources or not p.notes_path(s.id).exists()]


def cmd_read(args):
    from .claude import read_source
    from .extract import extract
    p = _project(args)
    todo = _to_read(p, args.ids, args.all)
    if not todo:
        print("Every source has notes already. Name a source, or pass --all to read them again.")
        return
    for src in todo:
        extract(p, src)
        read_source(p, src, model=args.model, effort=args.effort, yes=args.yes, dry_run=args.dry_run,
                    fallbacks=not args.no_fallback)


def cmd_pack(args):
    from .extract import extract
    from .pack import write_connect_pack, write_reading_pack
    p = _project(args)
    if args.connect:
        write_connect_pack(p)
        return
    for src in p.pick(args.ids) if args.ids else _to_read(p, None, args.all) or p.sources:
        extract(p, src)
        write_reading_pack(p, src, part=args.part)


def cmd_connect(args):
    from .claude import connect
    p = _project(args)
    connect(p, model=args.model, effort=args.effort, yes=args.yes, dry_run=args.dry_run,
            fallbacks=not args.no_fallback)


def cmd_build(args):
    from .build import build_site
    p = _project(args)
    build_site(p, out=Path(args.out).resolve() if args.out else None)


def cmd_check(args):
    from .build import check
    return 1 if check(_project(args), only=args.source) else 0


def cmd_serve(args):
    from .build import build_site
    from .serve import serve
    p = _project(args)
    if not args.no_build:
        build_site(p)
    serve(p, port=args.port)


def cmd_form(args):
    """Print a blank note (or file) to fill in; with --doc / --schema, regenerate the form docs."""
    import json
    from . import form
    if args.doc:
        print(form.render_doc())
        return
    if args.schema:
        out = Path(args.schema)
        out.mkdir(parents=True, exist_ok=True)
        for name, schema in form.schema_files().items():
            (out / name).write_text(json.dumps(schema, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"schemas written to {out}")
        return
    sid = args.id or "<source-id>"
    if args.what in (None, "source", "file"):
        data = form.blank_notes_file(sid, args.lang, args.kind)
    elif args.what in form.SKELETON:
        data = form.blank_note(args.what, sid, args.lang)
    else:
        raise AtlasError(f"sigatla form takes 'source' or a note type: {', '.join(form.SKELETON)}")
    print(json.dumps(data, ensure_ascii=False, indent=1))


def cmd_ids(args):
    """Every note id in the atlas with its title, to link to and to avoid duplicates."""
    import re
    from .pack import source_nodes
    p = _project(args)
    try:
        pattern = re.compile(args.grep, re.I) if args.grep else None
    except re.error as e:
        raise AtlasError(f"--grep is not a valid regular expression: {e}")
    rows = []
    for src in p.sources:
        if args.source and src.id != args.source:
            continue
        rows.append((src.id, "planet", src.label))
        rows += [(n.get("id"), n.get("type"), n.get("title")) for n in source_nodes(p, src.id)]
    shared = read_json(p.shared_path, {}) or {}
    if not args.source:
        rows += [(n.get("id"), n.get("type"), n.get("title")) for n in shared.get("nodes", [])]
    seen = set()
    for nid, typ, title in rows:
        if nid in seen or (args.related and not str(nid).startswith("rw.")) \
                or (pattern and not pattern.search(f"{nid} {title}")):
            continue
        seen.add(nid)
        print(f"{nid}\t{typ}\t{title}")


def cmd_show(args):
    """Print notes by id: the whole note, or with --brief its type, title, one-line summary and links."""
    import difflib
    import json
    p = _project(args)
    found: dict[str, tuple[str, dict]] = {}
    files = [(p.notes_path(s.id), s) for s in p.sources] + [(f, s) for s in p.sources for f in p.part_paths(s.id)]
    for path, src in files:
        data = read_json(path, {}) or {}
        if path == p.notes_path(src.id) and data.get("source"):
            meta = data["source"]
            gist = " ".join(str(meta.get("summary", "")).replace("#", " ").split())[:240]
            found.setdefault(src.id, (path.name, {"id": src.id, "type": "planet", "title": src.label, "short": gist,
                                                  "source": meta}))
        for n in data.get("nodes", []):
            found.setdefault(str(n.get("id")), (path.name, n))
    for n in (read_json(p.shared_path, {}) or {}).get("nodes", []):
        found.setdefault(str(n.get("id")), (p.shared_path.name, n))
    missing = 0
    for nid in args.ids:
        if nid not in found:
            close = difflib.get_close_matches(nid, list(found), n=3, cutoff=0.6)
            print(f"{nid}: not found" + (f" (did you mean {', '.join(close)}?)" if close else ""))
            missing += 1
            continue
        where, n = found[nid]
        if args.brief:
            links = "; ".join(f"{l.get('rel')} {l.get('to')}" for l in n.get("links") or [])
            print(f"{nid} | {n.get('type')} | {n.get('title')} | {' '.join(str(n.get('short', '')).split())}"
                  + (f" | links: {links}" if links else ""))
        else:
            print(f"## {nid}  (notes/{where})\n{json.dumps(n, ensure_ascii=False, indent=1)}\n")
    return 1 if missing else 0


def cmd_status(args):
    p = _project(args)
    print(f"{p.title}  ({p.root}, notes in {p.language}, model {p.model})")
    if not p.sources:
        print("No sources yet. Add one with: sigatla add <file.pdf> --kind paper|slides")
    for s in p.sources:
        m = read_json(p.manifest_path(s.id))
        from .pack import plan_parts, source_nodes
        nodes = source_nodes(p, s.id)
        if m and m.get("frames"):
            ext = f"{m['pages']} pages, {len(m['frames'])} slides"
        else:
            ext = f"{m['pages']} pages, {len(m['figures'])} figures" if m else "not extracted"
        parts = len(plan_parts(m, s.kind)) if m else 1
        done = sum(1 for k in range(1, parts + 1)
                   if (p.notes_path(s.id) if k == 1 else p.root / "notes" / f"{s.id}.part-{k}.json").exists())
        notes = (f"{len(nodes)} notes" if nodes else "no notes") + (f" ({done}/{parts} parts)" if parts > 1 else "")
        print(f"  {s.id:<18} {s.kind:<7} {ext:<26} {notes:<22} {s.label}")
    sh = read_json(p.shared_path)
    n_links = len(sh.get("links", [])) + sum(len(n.get("links", [])) for n in sh.get("nodes", [])) if sh else 0
    print(f"  connections: {len(sh.get('nodes', []))} shared notes, {n_links} links" if sh
          else "  connections: not written yet (sigatla connect, or sigatla pack --connect)")


def cmd_update(args):
    """extract everything, read sources without notes, connect, build."""
    from .build import build_site
    from .claude import connect, read_source
    from .extract import extract
    p = _project(args)
    for src in p.sources:
        extract(p, src)
    for src in [s for s in p.sources if not p.notes_path(s.id).exists()]:
        read_source(p, src, model=args.model, effort=args.effort, yes=args.yes)
    if len([s for s in p.sources if p.notes_path(s.id).exists()]) >= 2 and not args.no_connect:
        connect(p, model=args.model, effort=args.effort, yes=args.yes)
    build_site(p)


def _utf8_output():
    """Print UTF-8 even where the stream defaults to another code page (a pipe on Windows), so Vietnamese
    notes and math symbols reach the assistant that reads the output."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower().replace("-", "") != "utf8":
            stream.reconfigure(encoding="utf-8")


def main(argv=None) -> int:
    _utf8_output()
    ap = argparse.ArgumentParser(prog="sigatla", description="Turn papers and lecture slides into an interactive 3D map of notes.")
    ap.add_argument("--version", action="version", version=f"sigatla {__version__}")
    ap.add_argument("-C", "--dir", help="project folder (default: the current folder or a parent with atlas.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a project folder")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--title", default="Sigatla")
    s.add_argument("--subtitle")
    s.add_argument("--lang", default="en", help="language of the notes and the interface (en, vi, ...)")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("add", help="add a PDF (paper or exported slides)")
    s.add_argument("file")
    s.add_argument("--kind", choices=KINDS, default="paper")
    s.add_argument("--id", help="short id, e.g. gradcam (default: from the file name)")
    s.add_argument("--label", help="name shown on the planet, e.g. 'Grad-CAM · ICCV 2017'")
    s.add_argument("--short", help="short name for the jump bar, e.g. 'Grad-CAM' (default: the label)")
    s.add_argument("--no-copy", action="store_true", help="do not copy the PDF into sources/")
    s.add_argument("--no-extract", action="store_true")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("extract", help="extract text, figures, tables and slide images")
    s.add_argument("ids", nargs="*")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_extract)

    def api_flags(s, effort="high"):
        s.add_argument("--model", help="Claude model (default: atlas.json, else claude-opus-5-5)")
        s.add_argument("--effort", default=effort, choices=["low", "medium", "high", "xhigh", "max"])
        s.add_argument("--yes", "-y", action="store_true", help="do not ask before spending API credits")
        s.add_argument("--dry-run", action="store_true", help="write the request to build/ without sending it")
        s.add_argument("--no-fallback", action="store_true", help="do not use server-side model fallbacks")

    s = sub.add_parser("read", help="Claude reads sources through the API and writes notes/<id>.json")
    s.add_argument("ids", nargs="*", help="default: every source without notes")
    s.add_argument("--all", action="store_true", help="read every source again")
    api_flags(s)
    s.set_defaults(func=cmd_read)

    s = sub.add_parser("pack", help="write a reading pack for any AI assistant instead of calling the API")
    s.add_argument("ids", nargs="*")
    s.add_argument("--all", action="store_true")
    s.add_argument("--connect", action="store_true", help="write the pack for connecting sources")
    s.add_argument("--part", type=int, help="for a long source read in parts: which part (default: the next unread)")
    s.set_defaults(func=cmd_pack)

    s = sub.add_parser("connect", help="Claude links the sources: the axis, shared concepts, cross links")
    api_flags(s)
    s.set_defaults(func=cmd_connect)

    s = sub.add_parser("build", help="write the website to site/")
    s.add_argument("--out", help="another output folder")
    s.set_defaults(func=cmd_build)

    s = sub.add_parser("check", help="validate the notes")
    s.add_argument("--source", help="report on this source only ('shared' for notes/_shared.json)")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("serve", help="open the site locally, with Q&A through your Claude API key")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--no-build", action="store_true")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("form", help="print a blank note or notes file to fill in (the notes form)")
    s.add_argument("what", nargs="?", help="'source' for a whole file, or a note type (concept, formula, ...)")
    s.add_argument("--id", help="source id to put in the ids")
    s.add_argument("--lang", default="en", help="language of the section headings (en, vi)")
    s.add_argument("--kind", default="paper", choices=["paper", "slides", "notes"], help="for 'source': the summary layout")
    s.add_argument("--doc", action="store_true", help="print docs/NOTES_FORM.md")
    s.add_argument("--schema", metavar="DIR", help="write the JSON Schema files to DIR")
    s.set_defaults(func=cmd_form)

    s = sub.add_parser("ids", help="list note ids with their titles (to link to them and avoid duplicates)")
    s.add_argument("--source", help="only this source")
    s.add_argument("--related", action="store_true", help="only related-work notes (rw.*)")
    s.add_argument("--grep", metavar="REGEX", help="only ids or titles matching this (case-insensitive) regex")
    s.set_defaults(func=cmd_ids)

    s = sub.add_parser("show", help="print notes by id (several ids in one call)")
    s.add_argument("ids", nargs="+")
    s.add_argument("--brief", action="store_true", help="one line per note: type, title, summary, links")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("next", help="print the next steps (as commands) for you or your AI assistant")
    s.set_defaults(func=cmd_next)

    s = sub.add_parser("guide", help="write AGENTS.md and CLAUDE.md for AI assistants into the project folder")
    s.set_defaults(func=cmd_guide)

    s = sub.add_parser("status", help="show sources, notes and figures")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("update", help="extract, read new sources, connect and build in one go")
    s.add_argument("--model")
    s.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    s.add_argument("--yes", "-y", action="store_true")
    s.add_argument("--no-connect", action="store_true")
    s.set_defaults(func=cmd_update)

    args = ap.parse_args(argv)
    try:
        return args.func(args) or 0
    except AtlasError as e:
        print(f"sigatla: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

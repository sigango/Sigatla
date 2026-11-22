"""Check and merge the notes, copy figures, and write the website (site/index.html + site/assets/)."""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from . import __version__
from .prompts import NODE_TYPES, RELS
from .project import AtlasError, Project, Source, read_json, slugify, write_json

TEMPLATE = Path(__file__).parent / "web" / "index.html"
PLANETS = ["gas", "ocean", "rock", "ice"]
PALETTE = {
    "night":    ["#f2b35b", "#46c9bc", "#ff806f", "#a993f7", "#6cb8ff", "#e3d46a", "#ff8fc8", "#86e08f"],
    "paper":    ["#c27612", "#0e8a7e", "#d4473a", "#6a4fd0", "#1f6fd1", "#a38a00", "#c43d87", "#2e9a48"],
    "contrast": ["#ffbf4d", "#2ff0dc", "#ff7863", "#c0a8ff", "#5cc0ff", "#fff05a", "#ff7ad1", "#6cff86"],
}
KIND_LABEL = {
    "en": {"paper": "Paper", "slides": "Lecture slides", "notes": "Document", "shared": "Shared concepts",
           "related": "Related work", "cited": "Cited by", "unread": "Not read yet. Run `sigatla read {id}`."},
    "vi": {"paper": "Bài báo", "slides": "Slide bài giảng", "notes": "Tài liệu", "shared": "Khái niệm chung",
           "related": "Related work", "cited": "Được trích trong", "unread": "Chưa đọc. Chạy `sigatla read {id}`."},
}
ID_RE = re.compile(r"[^a-z0-9.\-]+")
FIG_MD_RE = re.compile(r"!\[([^\]]*)\]\(fig:([^)\s]+)\)")


def _labels(lang: str) -> dict:
    return KIND_LABEL.get(lang, KIND_LABEL["en"])


def _clean_id(raw: str, prefix: str) -> str:
    nid = ID_RE.sub("-", str(raw).strip().lower()).strip("-.")
    if not nid:
        return ""
    if not (nid.startswith(prefix + ".") or nid.startswith("rw.") or nid.startswith("shared.")):
        nid = f"{prefix}.{nid}"
    return nid


def _first_sentence(md: str) -> str:
    lines = [ln for ln in (md or "").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    text = re.sub(r"\$[^$]*\$|[#*_`>\[\]]", "", "\n".join(lines))
    text = " ".join(text.split())
    m = re.match(r"(.{20,240}?[.!?])(\s|$)", text)
    return (m.group(1) if m else text[:200]).strip()


def _fig_id(ref: str, sid: str, valid: set) -> str | None:
    ref = str(ref).strip()
    if ref in valid:
        return ref
    if f"{sid}/{ref}" in valid:
        return f"{sid}/{ref}"
    return None


def _fix_body_figs(body: str, sid: str, valid: set) -> str:
    def sub(m):
        fid = _fig_id(m.group(2), sid, valid)
        return f"![{m.group(1)}](fig:{fid})" if fid else (f"*{m.group(1)}*" if m.group(1) else "")
    return FIG_MD_RE.sub(sub, body or "")


def _norm_node(n: dict, prefix: str, fig_ids: set, max_page: int | None, warn, hubs: frozenset = frozenset()) -> dict | None:
    nid = _clean_id(n.get("id", ""), prefix)
    if not nid:
        warn(f"{prefix}: a note without an id was dropped")
        return None
    ntype = n.get("type") if n.get("type") in NODE_TYPES else "concept"
    if nid.startswith("rw."):
        ntype = "related"
    figs = []
    for ref in n.get("figures") or []:
        fid = _fig_id(ref, prefix, fig_ids)
        if fid and fid not in figs:
            figs.append(fid)
        elif not fid:
            warn(f"{nid}: unknown figure '{ref}' dropped")
    pages = sorted({int(p) for p in (n.get("pages") or []) if str(p).lstrip("-").isdigit()
                    and int(p) >= 1 and (max_page is None or int(p) <= max_page)})
    links = []
    for l in n.get("links") or []:
        raw_to = ID_RE.sub("-", str(l.get("to", "")).strip().lower()).strip("-")
        # dotted ids (own notes, rw.*, shared.*, other sources' notes) and source or axis ids are kept;
        # a bare slug is taken as one of this source's notes
        to = raw_to if ("." in raw_to or raw_to in (prefix, "axis") or raw_to in hubs) else _clean_id(raw_to, prefix)
        if not to or to == nid:
            continue
        rel = l.get("rel") if l.get("rel") in RELS else "uses"
        if rel == "part_of" and to == prefix:
            continue  # attached to its source automatically
        links.append({"to": to, "rel": rel, "note": str(l.get("note") or "").strip()})
    body = _fix_body_figs(str(n.get("body") or ""), prefix, fig_ids).strip()
    for m in FIG_MD_RE.finditer(body):  # a figure shown in the body also belongs to the note's figure list
        if m.group(2) not in figs:
            figs.append(m.group(2))
    return {
        "id": nid, "type": ntype, "title": str(n.get("title") or nid).strip(), "short": str(n.get("short") or "").strip(),
        "body": body,
        "questions": [str(q) for q in (n.get("questions") or []) if str(q).strip()][:4],
        "figures": figs[:6], "pages": pages, "links": links,
    }


def normalize_source_notes(data: dict, src: Source, manifest: dict | None, log=print, hubs=()) -> dict:
    """Clean notes from Claude (API or Claude Code) for one source: ids, types, figures, pages, links."""
    warn = lambda m: log(f"  warning: {m}")
    fig_ids = {f["id"] for f in (manifest or {}).get("figures", [])}
    max_page = (manifest or {}).get("pages")
    meta = data.get("source") or {}
    nodes, seen = [], {}
    for n in data.get("nodes") or []:
        node = _norm_node(n, src.id, fig_ids, max_page, warn, frozenset(hubs))
        if not node:
            continue
        nid = node["id"]
        if nid in seen and nid.startswith("rw."):   # the same related work written twice (e.g. in two parts)
            first = seen[nid]
            first["links"] += [l for l in node["links"] if l not in first["links"]]
            first["pages"] = sorted(set(first["pages"]) | set(node["pages"]))
            warn(f"{nid} is written twice for this source; the copies are merged (write it once)")
            continue
        if nid in seen:
            k = 2
            while f"{nid}-{k}" in seen:
                k += 1
            node["id"] = f"{nid}-{k}"
            warn(f"{nid} is used twice (in the notes file and its part files); the second note is renamed "
                 f"{node['id']} and links to {nid} go to the first. Give the second a new id.")
        seen[node["id"]] = node
        nodes.append(node)
    return {
        "source": {
            "id": src.id, "kind": src.kind, "label": src.label,
            "title": str(meta.get("title") or src.label), "authors": str(meta.get("authors") or ""),
            "year": str(meta.get("year") or ""),
            "summary": _fix_body_figs(str(meta.get("summary") or ""), src.id, fig_ids),
            "questions": [str(q) for q in (meta.get("questions") or [])][:6],
        },
        "nodes": nodes,
    }


def normalize_shared(data: dict, project: Project, log=print) -> dict:
    warn = lambda m: log(f"  warning: {m}")
    axis = data.get("axis") or {}
    nodes = []
    for n in data.get("nodes") or []:
        n = dict(n)
        if not str(n.get("id", "")).startswith("shared."):
            n["id"] = "shared." + slugify(str(n.get("id", "")).split(".")[-1])
        node = _norm_node(n, "shared", set(), None, warn)
        if node:
            nodes.append(node)
    links = []
    for l in data.get("links") or []:
        a, b = str(l.get("from", "")).lower(), str(l.get("to", "")).lower()
        if a and b and a != b:
            links.append({"from": a, "to": b, "rel": l.get("rel") if l.get("rel") in RELS else "complements",
                          "note": str(l.get("note") or "").strip()})
    return {"axis": {k: axis.get(k, "" if k != "questions" else []) for k in ("title", "short", "body", "questions")},
            "nodes": nodes, "links": links}


# merging everything
def assemble(project: Project, log=print) -> tuple[dict, list[tuple[Path, Path]], list[str]]:
    """Returns (DATA for the page, figure files to copy (from, to), warnings)."""
    warnings: list[str] = []
    warn = lambda m: warnings.append(m)
    lang = project.language
    L = _labels(lang)
    groups, nodes, links, figures, copies = [], [], [], {}, []
    by_id: dict[str, dict] = {}

    def add_node(n: dict) -> bool:
        if n["id"] in by_id:
            return False
        by_id[n["id"]] = n
        nodes.append(n)
        return True

    shared = read_json(project.shared_path, {}) or {}
    if shared:   # the same cleaning as the API path, so a hand-written file cannot break the build
        shared = normalize_shared(shared, project, log=lambda m: warn(m.strip().removeprefix("warning: ")))
    ax = shared.get("axis") or {}
    add_node({"id": "axis", "type": "axis", "group": "shared",
              "title": ax.get("title") or project.title,
              "short": ax.get("short") or project.config.get("subtitle", ""),
              "body": ax.get("body") or f"### {project.title}\n\n{project.config.get('subtitle', '')}",
              "questions": ax.get("questions") or [], "figures": [], "pages": []})

    cited_by: dict[str, list[str]] = {}
    for idx, src in enumerate(project.sources):
        manifest = read_json(project.manifest_path(src.id))
        groups.append({"id": src.id, "label": src.label, "kind": src.kind, "planet": PLANETS[idx % len(PLANETS)],
                       "color": {t: PALETTE[t][idx % 8] for t in PALETTE}, **({"short": src.short} if src.short else {})})
        fig_list = []
        for f in (manifest or {}).get("figures", []):
            src_file = project.build_dir(src.id) / f["file"]
            if not src_file.exists():
                continue
            rel = f"assets/{src.id}/{Path(f['file']).name}"
            figures[f["id"]] = {"src": rel, "caption": f.get("caption", ""), "page": f.get("page"),
                                "kind": f.get("kind", "figure"), "w": f.get("w"), "h": f.get("h"), "_file": src_file}
            if f.get("kind") != "step":   # build-up steps are shown only when a note uses them
                fig_list.append(f["id"])
        raw = read_json(project.notes_path(src.id))
        part_files = project.part_paths(src.id)
        if not raw and not part_files:
            fig_list = []   # an unread source is a bare planet: its slides are not copied into the site
        if part_files:   # a long source read in parts: part 1 is notes/<id>.json, the rest add nodes
            raw = dict(raw or {"source": {}, "nodes": []})
            raw["nodes"] = list(raw.get("nodes", []))
            for pf in part_files:
                raw["nodes"] += (read_json(pf, {}) or {}).get("nodes", [])
        notes = normalize_source_notes(raw, src, manifest, log=lambda m: warn(m.strip()),
                                       hubs=[s.id for s in project.sources]) if raw else None
        meta = (notes or {}).get("source", {})
        header = " · ".join(x for x in (L.get(src.kind, src.kind), meta.get("authors"), meta.get("year")) if x)
        body = f"*{header}*\n\n{meta.get('summary', '')}" if notes else f"*{header}*\n\n{L['unread'].format(id=src.id)}"
        add_node({"id": src.id, "type": "hub", "group": src.id, "title": meta.get("title") or src.label,
                  "short": _first_sentence(meta.get("summary", "")) or src.label, "body": body,
                  "questions": meta.get("questions", []), "figures": fig_list, "pages": []})
        if not notes:
            warn(f"{src.id}: no notes yet (notes/{src.id}.json)")
            continue
        for n in notes["nodes"]:
            group = "related" if n["id"].startswith("rw.") else "shared" if n["id"].startswith("shared.") else src.id
            node = {**{k: v for k, v in n.items() if k != "links"}, "group": group, "_links": n["links"]}
            if n["id"].startswith("rw."):
                cited_by.setdefault(n["id"], []).append(src.label)
            if not add_node(node):
                by_id[n["id"]]["_links"] = by_id[n["id"]].get("_links", []) + n["links"]
            elif not n["id"].startswith(("rw.", "shared.")) and not any(l["to"] == src.id for l in n["links"]):
                links.append({"source": n["id"], "target": src.id, "rel": "part_of", "note": ""})

    for n in shared.get("nodes") or []:
        add_node({**{k: v for k, v in n.items() if k != "links"}, "group": "shared", "_links": n.get("links", [])})
    for rid, labels in cited_by.items():
        if len(labels) > 1:
            by_id[rid]["body"] += f"\n\n### {L['cited']}\n" + "\n".join(f"- {x}" for x in labels)

    for n in nodes:
        for l in n.pop("_links", []) or []:
            links.append({"source": n["id"], "target": l["to"], "rel": l["rel"], "note": l.get("note", "")})
    for l in shared.get("links") or []:
        links.append({"source": l["from"], "target": l["to"], "rel": l["rel"], "note": l.get("note", "")})
    for src in project.sources:   # the sun frames every planet
        links.append({"source": "axis", "target": src.id, "rel": "frames", "note": ""})

    # related work nobody links to hangs off the first source that cites it
    linked = {x for l in links for x in (l["source"], l["target"])}
    for rid, labels in cited_by.items():
        if rid not in linked:
            sid = next(s.id for s in project.sources if s.label == labels[0])
            links.append({"source": sid, "target": rid, "rel": "cites", "note": ""})

    # validate links: no dangling ends, one link per pair
    clean, pairs = [], {}
    for l in links:
        if l["source"] not in by_id or l["target"] not in by_id:
            warn(f"link {l['source']} -> {l['target']} ({l['rel']}) points to an unknown note; dropped")
            continue
        key = tuple(sorted((l["source"], l["target"])))
        if l["source"] == l["target"]:
            continue
        if key in pairs:   # one link per pair; `sigatla check` tells the writer which reverse links to remove
            continue
        pairs[key] = l
        clean.append(l)

    for n in nodes:
        n["figures"] = [f for f in n.get("figures", []) if f in figures]
    used = {f for n in nodes for f in n["figures"]} | {m.group(2) for n in nodes for m in FIG_MD_RE.finditer(n["body"])}
    shown = used | {f for n in nodes if n["type"] == "hub" for f in n["figures"]}
    for fid in list(figures):
        info = figures[fid]
        src_file = info.pop("_file")
        if fid not in shown:
            del figures[fid]
            continue
        copies.append((src_file, project.site_dir / info["src"]))
    groups += [{"id": "shared", "label": L["shared"], "kind": "shared", "color": {}},
               {"id": "related", "label": L["related"], "kind": "related", "color": {}}]
    data = {
        "meta": {"title": project.title, "subtitle": project.config.get("subtitle", ""), "lang": lang,
                 "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "version": __version__},
        "groups": groups, "nodes": nodes, "links": clean, "figures": figures,
        "qa": read_json(project.qa_path, []) or [],
    }
    return data, copies, warnings


def inject(data: dict, template: Path = TEMPLATE) -> str:
    html = template.read_text(encoding="utf-8")
    if "__DATA__" not in html:
        raise AtlasError(f"{template} has no __DATA__ placeholder.")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return html.replace("__DATA__", payload)


def build_site(project: Project, out: Path | None = None, log=print) -> dict:
    data, copies, warnings = assemble(project, log)
    out = out or project.site_dir
    if out != project.site_dir:
        copies = [(a, out / b.relative_to(project.site_dir)) for a, b in copies]
    assets = out / "assets"
    if assets.exists():
        shutil.rmtree(assets)
    for a, b in copies:
        b.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(a, b)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(inject(data), encoding="utf-8")
    write_json(out / "atlas-data.json", data)
    for w in warnings:
        log(f"warning: {w}")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    log(f"site: {len(data['nodes'])} notes, {len(data['links'])} links, {len(data['figures'])} figures, "
        f"{size / 1e6:.1f} MB -> {out / 'index.html'}")
    return data


WORD_FACTOR = {"vi": 1.5}   # Vietnamese separates syllables with spaces
DOLLAR_RE = re.compile(r"(?<!\\)\$")


def _dollars_balanced(text: str) -> bool:
    return len(DOLLAR_RE.findall(text or "")) % 2 == 0


def raw_hints(raw: dict, sid: str, hubs=()) -> list[str]:
    """Problems the normalization would fix silently: say them, so the writer can fix the source file."""
    hints = []
    meta = raw.get("source") or {}
    if len(meta.get("questions") or []) > 6:
        hints.append(f"source.questions has {len(meta['questions'])} items; only 6 are kept")
    for n in raw.get("nodes") or []:
        nid = n.get("id", "?")
        if len(n.get("questions") or []) > 4:
            hints.append(f"{nid}: {len(n['questions'])} questions; only 4 are kept")
        if len(n.get("figures") or []) > 3:
            hints.append(f"{nid}: {len(n['figures'])} figures; at most 3")
        for key in ("title", "short"):
            if "$" in str(n.get(key, "")) or "**" in str(n.get(key, "")):
                hints.append(f"{nid}: {key} is plain text; remove the math or Markdown")
        if not _dollars_balanced(n.get("body", "")):
            hints.append(f"{nid}: odd number of $ in the body (write a literal dollar sign as \\$)")
        for l in n.get("links") or []:
            to = str(l.get("to", ""))
            if to and "." not in to and to != sid and to not in hubs and to != "axis":
                hints.append(f"{nid}: link target '{to}' has no prefix; it is read as '{sid}.{to}' "
                             "(write the full id, or a source id for a planet)")
            if not _dollars_balanced(l.get("note", "")):
                hints.append(f"{nid} -> {to}: odd number of $ in the link note")
    # a pair of notes gets one link: the reverse of an existing link is dropped by the build
    seen = {}
    for n in raw.get("nodes") or []:
        for l in n.get("links") or []:
            a, b = str(n.get("id", "")), str(l.get("to", ""))
            key = tuple(sorted((a, b)))
            if key in seen and seen[key] != (a, b):
                hints.append(f"{a} -> {b}: the reverse link {b} -> {a} already exists; the build keeps only one "
                             "link per pair, so keep the one that reads best and remove the other")
            seen.setdefault(key, (a, b))
    return hints


def quality_hints(notes: dict, lang: str = "en") -> list[str]:
    """Things a careful writer would fix; reported by `sigatla check`, never fatal."""
    hints = []
    f = WORD_FACTOR.get(lang, 1.0)
    meta = notes.get("source", {})
    for key in ("authors", "year"):
        if str(meta.get(key, "")).strip().lower() in ("not stated", "unknown", "n/a", "none", "không rõ"):
            hints.append(f"source.{key} is '{meta.get(key)}': leave it empty when the source does not give it")
    words = len((meta.get("summary") or "").split())
    if words < 60 * f:
        hints.append(f"source.summary is short ({words} words)")
    elif words > 450 * f:
        hints.append(f"source.summary has {words} words; keep it under about {int(400 * f)}")
    for n in notes.get("nodes", []):
        nid, words = n.get("id"), len(str(n.get("body", "")).split())
        if n.get("type") != "related":
            if words < 40 * f:
                hints.append(f"{nid}: body has only {words} words")
            elif words > 650 * f:
                hints.append(f"{nid}: body has {words} words; split it into two notes or shorten it")
            if len(n.get("questions") or []) < 2:
                hints.append(f"{nid}: fewer than 2 questions")
            if not n.get("links"):
                hints.append(f"{nid}: no links to other notes")
        if n.get("type") == "formula" and "$$" not in str(n.get("body", "")):
            hints.append(f"{nid}: formula note without display math ($$...$$)")
        if n.get("type") == "result" and not re.search(r"\d", str(n.get("body", ""))):
            hints.append(f"{nid}: result note without any number")
        for l in n.get("links") or []:
            if not str(l.get("note", "")).strip():
                hints.append(f"{nid} -> {l.get('to')}: link without a note")
    return hints


def check(project: Project, log=print, only: str | None = None) -> int:
    """Validate every notes file; returns the number of errors. Warnings and hints are printed. `only` limits the
    report to one source id, or to "shared" for notes/_shared.json."""
    if only and only != "shared":
        project.source(only)   # an unknown id is an error, not an empty report
    problems, n_hints, n_warn = 0, 0, 0
    for src in project.sources:
        if only and src.id != only:
            continue
        path = project.notes_path(src.id)
        part_files = project.part_paths(src.id)
        if not path.exists() and not part_files:
            continue
        raw = {"source": {}, "nodes": []}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                log(f"error: {path}: invalid JSON: {e}")
                problems += 1
                continue
            missing = [k for k in ("source", "nodes") if k not in raw]
            for key in missing:
                log(f"error: {path}: missing '{key}'")
                problems += 1
            if missing:
                continue
        else:
            log(f"warning: {src.id}: part files exist but {path.name} (part 1, with `source`) does not yet; "
                "the planet has no summary until it is written")
            n_warn += 1
        for pf in part_files:
            try:
                part = json.loads(pf.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                log(f"error: {pf}: invalid JSON: {e}")
                problems += 1
                continue
            if not isinstance(part.get("nodes"), list):
                log(f"error: {pf}: missing 'nodes'")
                problems += 1
                continue
            if "source" in part:
                log(f"hint: {src.id}: {pf.name} has `source`; only part 1 ({path.name}) writes it, so it is ignored")
                n_hints += 1
            raw = {**raw, "nodes": raw.get("nodes", []) + part["nodes"]}
        dropped = []
        notes = normalize_source_notes(raw, src, read_json(project.manifest_path(src.id)),
                                       log=lambda m: dropped.append(m.strip().removeprefix("warning: ")),
                                       hubs=[s.id for s in project.sources])
        for h in dropped + raw_hints(raw, src.id, [s.id for s in project.sources]) + quality_hints(notes, project.language):
            log(f"hint: {src.id}: {h}")
            n_hints += 1
    shared_raw = read_json(project.shared_path) if not only or only == "shared" else None
    if shared_raw:
        for key in ("axis", "nodes", "links"):
            if key not in shared_raw:
                log(f"error: {project.shared_path}: missing '{key}'")
                problems += 1
        for h in raw_hints({"nodes": shared_raw.get("nodes", [])}, "shared", [s.id for s in project.sources]):
            log(f"hint: shared: {h}")
            n_hints += 1
        from .claude import notes_cross_links
        in_notes = {tuple(sorted((x, y))): f"{x} {rel} {y}" for x, rel, y in notes_cross_links(project)[0]}
        pairs = {}
        for x, y in ([(l.get("from"), l.get("to")) for l in shared_raw.get("links", [])]
                     + [(n.get("id"), l.get("to")) for n in shared_raw.get("nodes", []) for l in n.get("links") or []]):
            key = tuple(sorted((str(x), str(y))))
            if key in in_notes:
                log(f"hint: shared: link {x} -> {y} repeats '{in_notes[key]}' from a notes file; the build keeps "
                    "that one, so remove this link or move its note there")
                n_hints += 1
            elif key in pairs:
                log(f"hint: shared: {x} and {y} are linked twice in _shared.json; keep one link per pair")
                n_hints += 1
            pairs[key] = True
        for l in shared_raw.get("links", []):
            if not l.get("from") or not l.get("to") or l.get("rel") not in RELS:
                log(f"hint: shared: link {l.get('from')} -> {l.get('to')}: needs from, to and a known rel")
                n_hints += 1
            elif not str(l.get("note", "")).strip():
                log(f"hint: shared: link {l['from']} -> {l['to']}: no note")
                n_hints += 1
    if problems:
        log(f"{problems} errors")
        return problems
    _, _, warnings = assemble(project, log)
    if only:
        warnings = [w for w in warnings if re.search(rf"(?<![\w.-]){re.escape(only)}[.:\s/]", w)]
    for w in warnings:
        log(f"warning: {w}")
    log(f"{problems} errors, {len(warnings) + n_warn} warnings, {n_hints} hints")
    return problems

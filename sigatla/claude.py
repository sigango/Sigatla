"""Calls to the Claude API: reading a source, connecting sources, and answering questions for `sigatla serve`.

Credentials are resolved by the Anthropic SDK (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login`
profile). Every reading call shows a token estimate and asks before spending money, unless --yes is given.
"""
from __future__ import annotations

import base64
import json
import sys
import time
from typing import Iterator

from . import prompts
from .project import AtlasError, Project, Source, read_json, write_json

MAX_PDF_BYTES = 30 * 1024 * 1024      # request limit is 32 MB; keep room for the rest of the request
MAX_PDF_PAGES = 600
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1"}
PRICES = {  # USD per million tokens (input, output), for the estimate only
    "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-2": (8.0, 40.0), "claude-opus-5": (5.0, 25.0),
}


def _anthropic():
    try:
        import anthropic
    except ImportError as e:
        raise AtlasError("The Anthropic SDK is missing. Install it with: pip install anthropic") from e
    return anthropic


def get_client():
    return _anthropic().Anthropic()


# the PDF attachment
def pdf_block(path, log=print) -> dict:
    """The PDF as a base64 document block; very large files are re-rendered as lighter page images."""
    data = path.read_bytes()
    pages = None
    try:
        import pymupdf
        doc = pymupdf.open(path)
        pages = len(doc)
    except ImportError:
        doc = None
    if pages and pages > MAX_PDF_PAGES:
        raise AtlasError(f"{path.name} has {pages} pages; the API reads at most {MAX_PDF_PAGES}. Split the PDF first.")
    if len(data) > MAX_PDF_BYTES:
        if doc is None:
            raise AtlasError(f"{path.name} is larger than 30 MB. Install pymupdf so sigatla can shrink it, or split it.")
        log(f"{path.name} is {len(data) / 1e6:.0f} MB; re-rendering pages as JPEG to fit the request limit")
        data = _lighten(doc)
        if len(data) > MAX_PDF_BYTES:
            raise AtlasError(f"{path.name} is still {len(data) / 1e6:.0f} MB after shrinking. Split it into parts.")
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                           "data": base64.standard_b64encode(data).decode("ascii")},
            "cache_control": {"type": "ephemeral"}}


def _lighten(doc, dpi: int = 110) -> bytes:
    import pymupdf
    out = pymupdf.open()
    for page in doc:
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        img = pix.tobytes("jpeg", jpg_quality=70)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=img)
    return out.tobytes(garbage=3, deflate=True)


# one structured call
def _request(model: str, system: str, content: list, schema: dict, effort: str, max_tokens: int) -> dict:
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    }


def _redacted(params: dict) -> dict:
    """The request with attachments shortened, for --dry-run output."""
    p = json.loads(json.dumps(params))
    for block in p["messages"][0]["content"]:
        src = block.get("source") or {}
        if src.get("type") == "base64":
            src["data"] = f"<{len(src['data'])} base64 characters>"
    return p


def _estimate(client, params: dict, log) -> int:
    try:
        count = client.messages.count_tokens(model=params["model"], system=params["system"],
                                             messages=params["messages"]).input_tokens
    except Exception as e:  # counting is a convenience; never block on it
        log(f"(could not count tokens: {e.__class__.__name__})")
        return 0
    price = PRICES.get(params["model"])
    cost = f", about ${count * price[0] / 1e6 + 30000 * price[1] / 1e6:.2f} with ~30k output tokens" if price else ""
    log(f"input: {count:,} tokens{cost}")
    return count


def _confirm(yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        raise AtlasError("This call spends API credits. Re-run with --yes to confirm.")
    answer = input("Send this request to Claude? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        raise AtlasError("Cancelled.")


def _stream(client, params: dict, use_fallbacks: bool, log):
    anthropic = _anthropic()
    try:
        if use_fallbacks and params["model"] in FALLBACK_MODELS:
            manager = client.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **params)
        else:
            manager = client.messages.stream(**params)
        chars, last = 0, 0.0
        with manager as stream:
            for text in stream.text_stream:
                chars += len(text)
                now = time.monotonic()
                if now - last > 2:
                    last = now
                    log(f"\r  writing… {chars:,} characters", end="")
            final = stream.get_final_message()
        log("")
        return final
    except anthropic.AuthenticationError as e:
        raise AtlasError("Claude API credentials are missing or invalid. Set ANTHROPIC_API_KEY, "
                         "or use the Claude Code path: `sigatla pack`.") from e
    except anthropic.PermissionDeniedError as e:
        raise AtlasError(f"The API key cannot use {params['model']}: {e.message}") from e
    except anthropic.NotFoundError as e:
        raise AtlasError(f"Model not found: {params['model']}. Change `model` in atlas.json.") from e
    except anthropic.RateLimitError as e:
        raise AtlasError("Rate limited by the API. Wait a minute and run the command again.") from e
    except anthropic.BadRequestError as e:
        raise AtlasError(f"The API rejected the request: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise AtlasError(f"API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise AtlasError("Could not reach the Claude API. Check the network and try again.") from e


def _parse(final) -> dict:
    if final.stop_reason == "refusal":
        cat = getattr(getattr(final, "stop_details", None), "category", None)
        raise AtlasError(f"Claude declined this request{f' ({cat})' if cat else ''}.")
    if final.stop_reason == "max_tokens":
        raise AtlasError("The notes did not fit in the output limit. Try --effort medium, or split the PDF.")
    text = "".join(b.text for b in final.content if getattr(b, "type", "") == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise AtlasError(f"Claude's answer was not valid JSON ({e}).") from e


def _usage_line(final) -> str:
    u = final.usage
    cached = getattr(u, "cache_read_input_tokens", 0) or 0
    return f"used {u.input_tokens:,} input (+{cached:,} cached) and {u.output_tokens:,} output tokens"


# reading one source
def known_ids(project: Project, exclude: str | None = None) -> tuple[list[str], list[str]]:
    rw, shared = set(), set()
    for s in project.sources:
        if s.id == exclude:
            continue
        for n in project.all_nodes(s.id):
            if str(n.get("id", "")).startswith("rw."):
                rw.add(n["id"])
    for n in (read_json(project.shared_path, {}) or {}).get("nodes", []):
        shared.add(n.get("id"))
    return sorted(rw), sorted(x for x in shared if x)


def read_source(project: Project, src: Source, *, model: str | None = None, effort: str = "high",
                max_tokens: int = 128000, yes: bool = False, dry_run: bool = False, fallbacks: bool = True,
                log=print) -> dict | None:
    from .build import normalize_source_notes
    from .extract import load_manifest

    manifest = load_manifest(project, src)
    model = model or project.model
    rw, shared = known_ids(project, exclude=src.id)
    system = prompts.read_system(src.kind, src.id, project.language)
    content = [
        pdf_block(project.source_path(src), log),
        {"type": "text", "text": prompts.read_user_text(
            src.label, src.kind, src.id, manifest, rw, shared,
            other_sources=[s.id for s in project.sources if s.id != src.id])},
    ]
    params = _request(model, system, content, prompts.READ_SCHEMA, effort, max_tokens)
    if dry_run:
        path = project.build_dir(src.id) / "request.json"
        write_json(path, _redacted(params))
        log(f"{src.id}: dry run, request written to {path}")
        return None
    client = get_client()
    log(f"{src.id}: asking {model} to read {project.source_path(src).name}")
    _estimate(client, params, log)
    _confirm(yes)
    final = _stream(client, params, fallbacks, log)
    data = _parse(final)
    notes = normalize_source_notes(data, src, manifest, log=log, hubs=[s.id for s in project.sources])
    write_json(project.notes_path(src.id), notes)
    log(f"{src.id}: {len(notes['nodes'])} notes written to {project.notes_path(src.id)}; {_usage_line(final)}")
    return notes


# connecting sources
def _clip(text: str, limit: int) -> str:
    """Shorten at a sentence or word boundary."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[:end + 1] if end > limit * 0.6 else cut[:cut.rfind(" ")] + " …").strip()


def connect_input(project: Project, embed_current: bool = True, compact: bool = False) -> str:
    """The connect task's input. `compact` (for the connect pack) lists notes by id, type and title only: an
    assistant with a shell reads the summaries it needs through `sigatla show --brief`, which keeps its context small."""
    lines = [f"Atlas title: {project.title}", f"Subtitle: {project.config.get('subtitle', '')}", "",
             "Sources with notes (connect these):"]
    unread = []
    for s in project.sources:
        notes = read_json(project.notes_path(s.id), {}) or {}
        if not notes and not project.part_paths(s.id):
            unread.append(f"{s.id} ({s.label})")
            continue
        meta = notes.get("source", {})
        summary = _clip(meta.get("summary", "").replace("#", ""), 1200)
        lines.append(f"- id {s.id} ({s.kind}) {s.label}: {meta.get('title', '')}. {summary}")
    if unread:
        lines += ["", "Sources without notes yet (leave them out of the axis, the shared notes and the links): "
                  + "; ".join(unread)]
    lines += ["", "Index of notes (id | type | title" + ("" if compact else " | summary") + "):"]
    seen = set()
    for s in project.sources:
        for n in project.all_nodes(s.id):
            if n.get("id") in seen:
                continue
            seen.add(n.get("id"))
            row = f"{n.get('id')} | {n.get('type')} | {n.get('title')}"
            lines.append(row if compact else f"{row} | {' '.join(str(n.get('short', '')).split())}")
    cross, shared_used = notes_cross_links(project)
    if cross:
        lines += ["", "Links between sources that the notes files already have (the build draws them; do not "
                  "repeat these pairs in either direction):"]
        lines += [f"- {a} {rel} {b}" for a, rel, b in cross]
    if shared_used:
        lines += ["", "Shared ids that notes files link to (keep these ids, or those links break): "
                  + ", ".join(f"{k} ({v})" for k, v in sorted(shared_used.items()))]
    current = read_json(project.shared_path, {}) or {}
    if current:
        inner = sum(len(n.get("links") or []) for n in current.get("nodes", []))
        where = "is below" if embed_current else f"is `{project.shared_path}`: open and read it first"
        lines += ["", f"The atlas is already connected: the current notes/_shared.json {where} (a copy is kept in "
                  f"build/_shared.previous.json). It has {len(current.get('nodes', []))} shared notes, "
                  f"{len(current.get('links', []))} top-level links and {inner} links inside the shared notes. "
                  "Update it rather than starting over: keep the axis, the shared notes and the links that still "
                  "hold (with their notes), fix or drop the others, and add what the new sources bring. Links a "
                  "shared note already has in its own `links` may stay there; write new links in the top-level "
                  "`links`."]
        if embed_current:
            lines += ["```json", json.dumps(current, ensure_ascii=False, indent=1), "```"]
    return "\n".join(lines)


def notes_cross_links(project: Project) -> tuple[list[tuple[str, str, str]], dict[str, int]]:
    """Links in the notes files that cross sources (note to another source's note or planet, or to a shared
    concept), and how many notes link to each shared id."""
    ids = {s.id for s in project.sources}
    owner = lambda x: x if x in ids else x.split(".", 1)[0]
    cross, used = [], {}
    for s in project.sources:
        for n in project.all_nodes(s.id):
            for l in n.get("links") or []:
                to = str(l.get("to", ""))
                if to.startswith("shared."):
                    used[to] = used.get(to, 0) + 1
                    cross.append((str(n.get("id")), str(l.get("rel")), to))
                elif owner(to) in ids and owner(to) != s.id:
                    cross.append((str(n.get("id")), str(l.get("rel")), to))
    return cross, used


def backup_shared(project: Project) -> None:
    """Keep the current notes/_shared.json in build/ before it is rewritten."""
    if project.shared_path.exists():
        dest = project.root / "build" / "_shared.previous.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(project.shared_path.read_bytes())


def connect(project: Project, *, model: str | None = None, effort: str = "high", max_tokens: int = 64000,
            yes: bool = False, dry_run: bool = False, fallbacks: bool = True, log=print) -> dict | None:
    from .build import normalize_shared

    have = [s for s in project.sources if project.notes_path(s.id).exists()]
    if len(have) < 1:
        raise AtlasError("No notes yet. Run `sigatla read` (or the Claude Code path) for at least one source first.")
    model = model or project.model
    params = _request(model, prompts.connect_system(project.language),
                      [{"type": "text", "text": connect_input(project) + "\n\nWrite the axis, shared notes and links now."}],
                      prompts.CONNECT_SCHEMA, effort, max_tokens)
    if dry_run:
        path = project.root / "build" / "connect-request.json"
        write_json(path, _redacted(params))
        log(f"dry run, request written to {path}")
        return None
    client = get_client()
    log(f"asking {model} to connect {len(have)} sources")
    _estimate(client, params, log)
    _confirm(yes)
    final = _stream(client, params, fallbacks, log)
    shared = normalize_shared(_parse(final), project, log=log)
    backup_shared(project)
    write_json(project.shared_path, shared)
    log(f"{len(shared['nodes'])} shared notes and {len(shared['links'])} links written to {project.shared_path}; "
        f"{_usage_line(final)}")
    return shared


# questions from the website (sigatla serve)
def ask_stream(model: str, system: str, messages: list[dict], fallbacks: bool = True) -> Iterator[str]:
    client = get_client()
    params = {"model": model, "max_tokens": 16000, "system": system, "messages": messages,
              "output_config": {"effort": "medium"}}
    if fallbacks and model in FALLBACK_MODELS:
        manager = client.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **params)
    else:
        manager = client.messages.stream(**params)
    with manager as stream:
        for text in stream.text_stream:
            yield text
        final = stream.get_final_message()
    if final.stop_reason == "refusal":
        yield "\n\n*(Claude declined to answer this question.)*"
    elif final.stop_reason == "max_tokens":
        yield "\n\n*(The answer was cut off. Ask a narrower question.)*"


def has_credentials() -> bool:
    """Best effort: True when the SDK would find credentials without prompting."""
    import os
    from pathlib import Path
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    return (Path.home() / ".config" / "anthropic").exists()

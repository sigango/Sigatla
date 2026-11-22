"""What an AI assistant needs in a project folder: a short AGENTS.md (with a CLAUDE.md that imports it) and
`sigatla next`, which works out the next steps from the files so the assistant does not have to.

Both are written to keep an assistant's context small. The guide is read once per session, the commands print
only what the next step needs, and each reading job is meant for a fresh assistant with a small context.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from .project import AtlasError, Project, read_json

MARK = "<!-- Written by `sigatla guide`. Run it again to update this file; remove this line to keep your own. -->"

GUIDE = """\
{mark}
# Sigatla project

This folder is a Sigatla project. `atlas.json` lists the sources (PDFs of papers and lecture slides),
`notes/` holds the notes, and `sigatla build` turns them into `site/index.html`, a 3D map of the notes. The `sigatla`
tool does the mechanical work; you read the sources and write the notes.

In this file, `sigatla` means `{exe}`.

## What to do

Run `sigatla next`. It prints the next commands; follow them.

A reading job is `sigatla pack <id>` (with `--part <k>` for a long deck). It writes a reading pack: one Markdown
file with the whole task (the rules, the slide text, which images to look at, the output schema). Read the pack
and follow it. You do not need any other guide.

To add a source: `sigatla add <file.pdf> --kind paper|slides|notes --label "<name>"`, then `sigatla next`.

## Keep the token cost low

- **One reading job per fresh assistant or subagent.** Jobs can run in parallel. Do not read two parts in one
  conversation: everything you have read stays in your context and is sent again with every step.
- **Look at slides on the contact sheets** the pack lists (several slides per image). Open a full slide image only
  for small print, and open several images in one step.
- **Look things up narrowly:** `sigatla ids --grep "<regex>"`, `sigatla show <id> [<id> ...]`,
  `sigatla check --source <id>`. Do not print whole notes files. Do not open `site/` or the tool's source code.
- **Write a notes file once,** then fix what `sigatla check` reports with small edits, not by writing it again.

## Rules that matter most (each pack has the full rules)

- Numbers, datasets and claims come only from the source. Never invent a number, a citation or a result.
- Your own explanations start with the interpretation marker of the notes' language.
- Figure ids, slide ids and link targets must exist; the pack lists them.
- Every link has a one- or two-sentence `note` saying how or why the relation holds.
- Do not edit `site/` or `build/` by hand; they are regenerated.
{more}"""


def atlas_exe() -> str:
    """The command that runs this atlas, as an assistant's shell can call it."""
    exe = shutil.which("sigatla")
    return str(Path(exe).resolve()) if exe else f"{sys.executable} -m sigatla"


def repo_guide() -> Path | None:
    """The repository's AGENTS.md when the tool runs from a checkout (pip install -e)."""
    path = Path(__file__).resolve().parent.parent / "AGENTS.md"
    return path if path.exists() else None


def write_guide(project: Project, log=print) -> list[Path]:
    """Write AGENTS.md and CLAUDE.md into the project folder. A file the user wrote (without the mark) is kept."""
    full = repo_guide()
    more = f"\nThe full guide (adding notes by hand, the notes form) is `{full}`; you rarely need it.\n" if full else ""
    files = {
        "AGENTS.md": GUIDE.format(mark=MARK, exe=atlas_exe(), more=more),
        "CLAUDE.md": f"{MARK}\n@AGENTS.md\n",
    }
    written = []
    for name, text in files.items():
        path = project.root / name
        if path.exists() and MARK not in path.read_text(encoding="utf-8"):
            log(f"kept your own {path} (it has no `sigatla guide` mark); add the line `@AGENTS.md` to it by hand"
                if name == "CLAUDE.md" else f"kept your own {path} (it has no `sigatla guide` mark)")
            continue
        path.write_text(text, encoding="utf-8")
        written.append(path)
    if written:
        log("wrote " + ", ".join(str(p) for p in written))
    return written


# next steps
def _mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


def _unconnected(project: Project, read: list[str]) -> list[str]:
    """Sources with notes that notes/_shared.json does not link to yet."""
    shared = read_json(project.shared_path, {}) or {}
    ends = [l.get(k) for l in shared.get("links", []) for k in ("from", "to")]
    ends += [l.get("to") for n in shared.get("nodes", []) for l in n.get("links") or []]
    owners = {str(e).split(".", 1)[0] for e in ends if e}
    return [sid for sid in read if sid not in owners]


def next_steps(project: Project) -> list[str]:
    """The next commands for this project, worked out from its files."""
    from .build import check
    from .pack import part_notes_path, plan_parts

    cmd = f"{atlas_exe()} -C {project.root}"
    if not project.sources:
        return ["No sources yet. Add each PDF (export PowerPoint or Keynote slides to PDF first):",
                f"  {cmd} add <file.pdf> --kind paper|slides|notes --label \"<name on the planet>\"",
                "Then run `sigatla next` again."]
    jobs = []
    for src in project.sources:
        manifest = read_json(project.manifest_path(src.id))
        parts = plan_parts(manifest, src.kind) if manifest else [{"part": 1}]
        for p in parts:
            if not part_notes_path(project, src.id, p["part"]).exists():
                jobs.append(f"{cmd} pack {src.id}" + (f" --part {p['part']}" if len(parts) > 1 else ""))
    if jobs:
        return [f"Reading jobs left: {len(jobs)}. Give each one to a fresh assistant or subagent (they can run in "
                "parallel). Each runs its command, reads the pack the command writes, and follows it:",
                *[f"  {j}" for j in jobs],
                "When every job is done, run `sigatla next` again."]

    lines: list[str] = []
    try:
        errors = check(project, log=lambda m: lines.append(m))
    except AtlasError as e:   # a notes file that is not valid JSON stops the cross-source checks
        return [f"Fix this first: {e}", "Then run `sigatla next` again."]
    if errors:
        shown = [m for m in lines if m.startswith("error:")][:8]
        return [f"Fix the {errors} error(s) `sigatla check` finds, with small edits to the notes files:",
                *[f"  {m}" for m in shown],
                f"Then run `sigatla next` again (`{cmd} check --source <id>` shows one source)."]
    read = [s.id for s in project.sources]
    if len(read) >= 2:
        missing = _unconnected(project, read)
        if not project.shared_path.exists() or missing:
            what = "Connect the sources" if not project.shared_path.exists() else \
                f"Connect the sources again ({', '.join(missing)} not linked yet)"
            return [f"{what}: run the command below, read the pack it writes and follow it.",
                    f"  {cmd} pack --connect",
                    "Then run `sigatla next` again."]
    site = project.site_dir / "index.html"
    newest = max([_mtime(project.root / "atlas.json")] + [_mtime(p) for p in project.root.glob("notes/*.json")])
    if _mtime(site) < newest:
        return ["Build the site:", f"  {cmd} build", f"Then give the user the path {site}."]
    hints = sum(1 for m in lines if m.startswith("hint:"))
    out = [f"Everything is read, connected and built: {site}"]
    if hints:
        out.append(f"`sigatla check` still has {hints} hint(s) worth a look: {cmd} check")
    out.append(f"To add a source: {cmd} add <file.pdf> --kind paper|slides|notes, then `sigatla next`.")
    return out

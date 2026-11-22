"""The notes form: field descriptions, a body skeleton for every note type, and generators for
docs/NOTES_FORM.md and schema/*.json. `sigatla form <type>` prints a blank note to fill in.

The reading rules themselves live in prompts.py; the form says what goes in each field.
"""
from __future__ import annotations

import json

from . import prompts

HEADINGS = {
    "en": {"formula": "Formula", "symbols": "Symbols", "meaning": "Meaning", "why_form": "Why this form",
           "what": "What it is", "intuition": "Intuition", "why": "Why it matters here", "steps": "How it works",
           "inputs": "Inputs and outputs", "choice": "What was chosen", "rules_out": "What it rules out",
           "reason": "Why", "numbers": "Numbers", "setting": "Setting", "claim": "What it shows",
           "affects": "What it affects", "remedy": "What could address it", "facts": "Facts", "use": "How it is used",
           "setup": "Setup", "walk": "Steps", "outcome": "Outcome", "lesson": "Lesson", "citation": "Citation",
           "idea": "What the source says about it", "relation": "Relation to this source",
           "summary": "Summary", "contributions": "Contributions", "organization": "How the notes are organized",
           "overview": "Overview", "goals": "Learning goals", "prereq": "Prerequisites", "main_points": "Main points",
           "question": "The question", "contributes": "How each source contributes", "threads": "Threads",
           "per_source": "How each source uses it", "differences": "Differences"},
    "vi": {"formula": "Công thức", "symbols": "Ký hiệu", "meaning": "Ý nghĩa", "why_form": "Vì sao dạng này",
           "what": "Định nghĩa", "intuition": "Trực giác", "why": "Vì sao quan trọng ở đây", "steps": "Cách hoạt động",
           "inputs": "Đầu vào và đầu ra", "choice": "Lựa chọn", "rules_out": "Loại trừ điều gì", "reason": "Lý do",
           "numbers": "Số liệu", "setting": "Thiết lập", "claim": "Cho thấy điều gì", "affects": "Ảnh hưởng tới",
           "remedy": "Cách khắc phục", "facts": "Thông tin", "use": "Được dùng thế nào", "setup": "Bài toán",
           "walk": "Các bước", "outcome": "Kết quả", "lesson": "Bài học", "citation": "Trích dẫn",
           "idea": "Nguồn nói gì về công trình này", "relation": "Quan hệ với nguồn",
           "summary": "Tóm tắt", "contributions": "Đóng góp", "organization": "Cách sắp xếp ghi chú",
           "overview": "Tổng quan", "goals": "Mục tiêu học tập", "prereq": "Kiến thức cần có", "main_points": "Ý chính",
           "question": "Câu hỏi", "contributes": "Mỗi nguồn đóng góp gì", "threads": "Các mạch chung",
           "per_source": "Mỗi nguồn dùng thế nào", "differences": "Khác biệt"},
}

SKELETON = {
    "concept": ["what", "intuition", "why"],
    "method": ["inputs", "steps", "why"],
    "formula": ["formula", "symbols", "meaning", "why_form"],
    "design": ["choice", "rules_out", "reason"],
    "result": ["numbers", "setting", "claim"],
    "limitation": ["what", "affects", "remedy"],
    "data": ["facts", "use"],
    "example": ["setup", "walk", "outcome", "lesson"],
    "related": ["citation", "idea", "relation"],
}

SUMMARY = {   # layout of source.summary (the planet's body), by kind: (heading key, what goes in it)
    "paper": [("summary", "problem, approach, main findings; the first sentence is a standalone one-line summary"),
              ("contributions", "a list"), ("organization", "one or two sentences")],
    "slides": [("overview", "what the lecture covers; the first sentence is a standalone one-line summary"),
               ("goals", "a list; a slide of objectives or learning goals counts as stated; when the slides state "
                         "none, infer them and start the section with the interpretation marker"),
               ("prereq", "only if the slides state them")],
    "notes": [("summary", "the first sentence is a standalone one-line summary"), ("main_points", "a list")],
}
AXIS = [("question", "the question that ties the sources together"),
        ("contributes", "one bullet per source, using its label"), ("threads", "3 to 5 recurring ideas")]
SHARED_NODE = [("what", "a definition"), ("per_source", "one bullet per source, using its label"),
               ("differences", "how the sources differ in using it")]
EXTRA = {"formula": "plus a closing section on the choice of this form when the source discusses it",
         "result": "a small Markdown table when there are several numbers",
         "related": "no links of its own"}

HINTS = {
    "formula": "$$ display math (LaTeX) $$", "symbols": "- $x$: what it is (one item per symbol)",
    "numbers": "| setting | value |\n| --- | --- |\n| ... | as reported |",
}

FIELDS = [
    # (field, type, required, rule)
    ("id", "string", "yes", "`<source-id>.<slug>`, or `rw.<slug>` for related work; lowercase letters, digits, "
                            "hyphens; unique; never change it once other notes link to it"),
    ("type", "enum", "yes", "concept, method, formula, design, result, limitation, data, example, related"),
    ("title", "string", "yes", "short and specific, at most about 8 words"),
    ("short", "string", "yes", "one standalone sentence, at most about 30 words; shown in tooltips and lists"),
    ("body", "Markdown", "yes", "`###` sections as in the skeleton for the type; $...$ and $$...$$ for math; "
                                "no HTML; usually 120–400 words"),
    ("questions", "list of strings", "yes (may be empty for related)", "2–4 exam, review or interview questions"),
    ("figures", "list of figure ids", "yes (may be empty)", "ids from the source's figure or slide list, at most 3; "
                                                            "every figure shown in the body is listed here too"),
    ("pages", "list of integers", "yes (may be empty)", "PDF page numbers counted from 1 (not printed page "
                                                        "numbers); for slides, the slide's page"),
    ("links", "list of {to, rel, note}", "yes (may be empty)", "relations to other notes, read as "
                                                              "\"this note REL target\"; every link has a note"),
]


def skeleton_body(ntype: str, lang: str = "en") -> str:
    h = HEADINGS.get(lang, HEADINGS["en"])
    out = []
    for key in SKELETON[ntype]:
        out.append(f"### {h[key]}\n{HINTS.get(key, '...')}")
    return "\n\n".join(out)


def blank_note(ntype: str, source_id: str = "<source-id>", lang: str = "en") -> dict:
    if ntype not in SKELETON:
        raise ValueError(f"unknown type {ntype!r}; one of {', '.join(SKELETON)}")
    nid = f"rw.<slug>" if ntype == "related" else f"{source_id}.<slug>"
    return {
        "id": nid, "type": ntype, "title": "<at most about 8 words>", "short": "<one standalone sentence>",
        "body": skeleton_body(ntype, lang), "questions": ["<question 1>", "<question 2>"],
        "figures": [f"{source_id}/<figure or slide id>"], "pages": [1],
        "links": [] if ntype == "related" else [{"to": "<another note id>", "rel": "uses",
                                                  "note": "<one or two sentences: how or why the relation holds>"}],
    }


def layout(keys, lang: str) -> str:
    """'"### Tóm tắt" (what goes in it), ...' for a list of (heading key, description)."""
    h = HEADINGS.get(lang, HEADINGS["en"])
    return ", ".join(f'"### {h[k]}" ({d})' for k, d in keys)


def sections_block(lang: str) -> str:
    """The body layout of every note type, with headings in the notes' language (used in reading packs)."""
    h = HEADINGS.get(lang, HEADINGS["en"])
    lines = ["## Sections of each note type",
             "Use these headings exactly, in this order. Leave out a section the source gives nothing for."]
    for t, keys in SKELETON.items():
        extra = f" ({EXTRA[t]})" if t in EXTRA else ""
        lines.append(f"- {t}: " + ", ".join(f"### {h[k]}" for k in keys) + extra)
    return "\n".join(lines)


def blank_notes_file(source_id: str = "<source-id>", lang: str = "en", kind: str = "paper") -> dict:
    h = HEADINGS.get(lang, HEADINGS["en"])
    summary = "\n\n".join(f"### {h[k]}\n<{d}>" for k, d in SUMMARY.get(kind, SUMMARY["paper"]))
    return {
        "source": {"title": "<title of the paper or deck>", "authors": "<as printed, or empty>",
                   "year": "<four-digit year if printed, or empty>", "summary": summary,
                   "questions": ["<question about the whole source>"]},
        "nodes": [blank_note("concept", source_id, lang)],
    }


# generated files
def schema_files() -> dict[str, dict]:
    base = {"$schema": "https://json-schema.org/draft/2020-12/schema"}
    return {
        "notes.schema.json": {**base, "title": "notes/<source-id>.json", **prompts.READ_SCHEMA},
        "notes-part.schema.json": {**base, "title": "notes/<source-id>.part-<k>.json", **prompts.PART_SCHEMA},
        "shared.schema.json": {**base, "title": "notes/_shared.json", **prompts.CONNECT_SCHEMA},
        "qa.schema.json": {**base, "title": "notes/_qa.json", "type": "array", "items": {
            "type": "object", "required": ["node", "q", "a"], "properties": {
                "node": {"type": "string"}, "link": {"type": "string"}, "path": {"type": "string"},
                "q": {"type": "string"}, "a": {"type": "string"}, "source": {"type": "string"},
                "at": {"type": "string"}}}},
    }


def render_doc() -> str:
    """docs/NOTES_FORM.md."""
    out = ["# The notes form", "",
           "<!-- Generated by `sigatla form --doc`. Edit sigatla/form.py, not this file. -->", "",
           "Every source in an atlas has one notes file, `notes/<source-id>.json` (plus "
           "`notes/<source-id>.part-<k>.json` when a long source was read in parts). This page is the form for "
           "filling it in: the fields, the sections each note type needs, and the relations. The reading rules "
           "(faithfulness, marking interpretations, language) are in every reading pack that `sigatla pack` writes.",
           "", "Machine-readable versions: [schema/](../schema/) (JSON Schema). A blank form for one note: "
           "`sigatla form <type>`; for a whole file: `sigatla form source`.", "",
           "## The file", "", "```json", json.dumps(blank_notes_file(), ensure_ascii=False, indent=1), "```", "",
           "`source` describes the paper or deck as a whole and becomes the body of its planet. Its fields: "
           "`title`; `authors` and `year` as printed, or empty strings; `summary` in Markdown whose first sentence "
           "is the planet's one-line description; `questions` about the whole source.", "",
           "## A note", "", "| field | type | required | what goes in it |", "| --- | --- | --- | --- |"]
    out += [f"| `{f}` | {t} | {r} | {d} |" for f, t, r, d in FIELDS]
    out += ["", "## Sections of the body, by type", "",
            "Write the headings in the notes' language (`sigatla form <type> --lang vi` prints Vietnamese ones). "
            "Leave out a section the source gives nothing for, rather than filling it with guesses.", ""]
    descriptions = {
        "concept": "an idea, definition or problem",
        "method": "a method, algorithm, model or architecture",
        "formula": "an equation shown in the source; explain every symbol",
        "design": "a design choice of a study, or a rule the source recommends, with the reason",
        "result": "a finding with the numbers as reported",
        "limitation": "a limitation, threat to validity or open question",
        "data": "a dataset, benchmark or model checkpoint",
        "example": "a worked example",
        "related": "another work the source cites (id `rw.<slug>`); it has no links of its own",
    }
    for t, keys in SKELETON.items():
        out += [f"### `{t}`: {descriptions[t]}", "", "```markdown", skeleton_body(t), "```", ""]
    out += ["## Relations", "", "A link is read as \"this note REL target\". Its `note` says concretely how or why "
            "the relation holds, in one or two sentences.", "",
            "| rel | meaning |", "| --- | --- |"]
    meanings = {
        "part_of": "is a component or step of the target", "uses": "needs or applies the target",
        "extends": "builds on the target and changes or generalizes it", "motivates": "gives the reason for the target",
        "contrasts": "differs from the target in an instructive way", "measures": "quantifies the target",
        "tests": "is an experiment or check that examines the target",
        "supports": "is evidence for the target", "refutes": "is evidence against the target",
        "limits": "restricts where the target holds or how well it works",
        "cites": "refers to the target without a stronger relation", "frames": "sets the context for the target",
        "complements": "covers a different side of the same topic",
    }
    out += [f"| `{r}` | {meanings[r]} |" for r in prompts.RELS]
    out += ["", "## Other files", "",
            "- `notes/_shared.json` (`sigatla pack --connect`): `axis` (the central question, the sun of the map), "
            "`nodes` (shared concepts, ids `shared.<slug>`), `links` (`from`, `to`, `rel`, `note`) across sources.",
            "- `notes/_qa.json`: saved questions and answers, a list of `{node, q, a, source, at}` "
            "(plus `link` or `path` when the question was about a link or a path).", "",
            "## Before you finish", "",
            "- `sigatla check` reports 0 errors, and no hints for the notes you wrote.",
            "- Every number comes from the source; every explanation beyond it starts with the interpretation "
            "marker (\"Interpretation:\", \"Diễn giải:\").",
            "- Every figure id and link target exists; every link has a note.",
            "- `sigatla build`, then open `site/index.html` and look at a few notes.", ""]
    return "\n".join(out)

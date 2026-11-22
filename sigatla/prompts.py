"""Instructions and output schemas for the two reading passes.

`read`    one source (a paper or a lecture deck) -> notes/<id>.json
`connect` all sources -> the axis, shared concepts and links across sources -> notes/_shared.json

The same text is used by the Claude API path (`sigatla read`) and the Claude Code path (`sigatla pack`),
so notes look the same whichever way they were written.
"""
from __future__ import annotations

NODE_TYPES = ["concept", "method", "formula", "design", "result", "limitation", "data", "example", "related"]
RELS = ["part_of", "uses", "extends", "motivates", "contrasts", "measures", "tests", "limits",
        "supports", "refutes", "cites", "frames", "complements"]

LANG_NAMES = {"en": "English", "vi": "Vietnamese", "de": "German", "fr": "French", "es": "Spanish",
              "ja": "Japanese", "zh": "Chinese", "ko": "Korean"}
INTERPRETATION = {"en": "Interpretation:", "vi": "Diễn giải:", "de": "Interpretation:", "fr": "Interprétation :"}


def lang_name(code: str) -> str:
    return LANG_NAMES.get(code, code)


def interpretation_marker(code: str) -> str:
    return INTERPRETATION.get(code, "Interpretation:")


# schemas
_STR = {"type": "string"}
_STRS = {"type": "array", "items": {"type": "string"}}
_LINK = {
    "type": "object", "additionalProperties": False, "required": ["to", "rel", "note"],
    "properties": {"to": _STR, "rel": {"type": "string", "enum": RELS}, "note": _STR},
}
_NODE = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "type", "title", "short", "body", "questions", "figures", "pages", "links"],
    "properties": {
        "id": _STR, "type": {"type": "string", "enum": NODE_TYPES}, "title": _STR, "short": _STR, "body": _STR,
        "questions": _STRS, "figures": _STRS, "pages": {"type": "array", "items": {"type": "integer"}},
        "links": {"type": "array", "items": _LINK},
    },
}
READ_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["source", "nodes"],
    "properties": {
        "source": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "authors", "year", "summary", "questions"],
            "properties": {"title": _STR, "authors": _STR, "year": _STR, "summary": _STR, "questions": _STRS},
        },
        "nodes": {"type": "array", "items": _NODE},
    },
}
PART_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["nodes"],
    "properties": {"nodes": {"type": "array", "items": _NODE}},
}
CONNECT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["axis", "nodes", "links"],
    "properties": {
        "axis": {
            "type": "object", "additionalProperties": False, "required": ["title", "short", "body", "questions"],
            "properties": {"title": _STR, "short": _STR, "body": _STR, "questions": _STRS},
        },
        "nodes": {"type": "array", "items": _NODE},
        "links": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["from", "to", "rel", "note"],
            "properties": {"from": _STR, "to": _STR, "rel": {"type": "string", "enum": RELS}, "note": _STR},
        }},
    },
}

# shared rules
_RULES = """\
## What the notes are for
The notes become an interactive 3D map. Each source is a planet; each note is a body orbiting it; links between notes
are drawn as lines with their relation and a short explanation. A reader opens a note to understand one idea deeply,
follows links to see how ideas depend on each other, and asks Claude questions about a note. Readers use the map to
learn a course, to prepare for an exam, a thesis defense or a research interview, and to see how several papers
fit together.

## Language
Write every title, short, body, question and link note in {language}. Keep established technical terms, method
names, dataset names and symbols in their original form (usually English).

## Faithfulness (most important)
- Take numbers, datasets, settings and claims only from the source, exactly as reported. Never invent a number,
  a dataset, a citation or a result. If the source does not say something, say that it does not.
- Numbers printed in the text, a table or inside a figure count as reported. A value you read off a plot (a bar
  height, a point on a curve) is approximate: write it with "≈", name the figure, and mark it (see "How to mark").
- When you add an explanation, an intuition, a consequence, a comparison or a fact from outside the source, start
  that sentence or paragraph with "{marker}" so the reader can tell it apart. Outside facts are allowed only when
  marked this way, and never replace what the source reports about its own work. If the source contradicts itself
  or a well-known fact, say so, marked the same way.
- When the source defers to material that is not in the PDF (an appendix, supplementary material), say that it is
  not included in this PDF instead of guessing its content.
- Prefer the source's own terms and notation. Quote at most a few words at a time; explain in your own words.
  When the source switches notation (f_c on one page, y_c on the next), keep each page's notation and point out
  the switch.

## How to mark
- A "{marker}" covers the rest of the paragraph, list item, table cell or link note from where it appears. Put
  it first, or right after a list item's label (for example "- $Z$: {marker} ..."). Link notes that interpret
  need it too. A marker does not reach past its paragraph: a marked sentence that introduces a list does not
  cover the list's items, so mark each item that interprets.
- Standard textbook background needed to follow the source (a definition, the range of a correlation, a
  well-known identity) needs no marker. Mark your reading of what the source means, consequences and comparisons
  it does not state, facts about specific works or datasets, and numbers you derive from the source's numbers
  (show the arithmetic).
- A schematic plot without values is illustrative: describe what it shows, never read numbers from it.

## Markdown and math
- title and short are plain text: no Markdown and no math there (write "pi" or the symbol itself, not $\\pi$).
- In a body or a link note, math goes between $...$ or $$...$$. A literal dollar sign is written \\$ (in the JSON
  string: "\\\\$"). Every opening $ needs its closing $.
- Lengths are given in English words. In Vietnamese, count about 1.5 times as many syllables.

## Node types
- concept: an idea, definition or problem. Body: what it is, an intuition, why it matters in this source.
- method: a method, algorithm, model or architecture. Body: inputs, steps, outputs, what it is for, why it is built
  this way.
- formula: one equation or a small group of them, in display math ($$...$$), with every symbol explained in a
  list, what it computes and why, and why this form when the source discusses it.
- design: an experimental or study design choice (baseline, control, split, the choice of a metric, ablation,
  sample size, a rule the source recommends). Body: what was chosen, what it rules out, and the reason (from the
  source; mark your own reasoning). A metric taught as a procedure is a method; the decision to use it is a design.
- result: a finding. Body: the numbers as reported (a small Markdown table when there are several), the setting,
  and which claim or method it supports or refutes. A claim with no evidence shown belongs in the note it is
  about, not in a result note.
- limitation: a limitation, a threat to validity or an open question; what it affects and what could address it.
- data: a dataset, benchmark or model checkpoint, with the facts the source gives (size, splits, labels).
- example: a worked example: the setup, the steps, the outcome, the lesson.
- related: another work this source builds on, compares with or argues against; courses, books and websites the
  source cites count too. Id "rw.<first author's surname>-<year>" (for example "rw.selvaraju-2017"), or
  "rw.<short-name>" for a work without a person as author (a course, a website). Two sources that use the same id
  share one note. Body: the citation as the source gives it, what this source says about the work, and how this
  source uses or differs from it. A short description of the work beyond what this source says is allowed when
  marked with "{marker}". pages: where the source cites it.
  One note per work: a course or a book is one note however many of its chapters the source cites (name the
  chapter in the body and in the link note). A website, a video or a tool page goes in the body of the note that
  cites it, not in a related note of its own, unless the source discusses it at length. A citation shown without
  a year (for example inside a video frame) is mentioned in the note it belongs to, not as a related note. When
  the source misspells a well-known author, use the correct spelling in the id and point out the printed
  spelling, marked; use the year as printed, and if you know it is wrong, say so, marked. A related note has no links of its own: link to it
  from this source's notes instead (cites, extends, contrasts, uses). If its id is in the list of existing ids,
  do not write the note again; just link to it.

## Every note
- id: "{prefix}.<slug>" (lowercase letters, digits, hyphens) for this source's notes; "rw.<slug>" for related work.
  Ids must be unique.
- title: short and specific (at most about 8 words). short: one sentence of at most about 30 words that stands
  alone.
- body: Markdown with "###" section headings, lists, **bold**, tables, inline math $...$ and display math $$...$$
  (LaTeX). No HTML. Typically 120 to 400 words; formulas and results may be longer.
- questions: 2 to 4 questions an examiner, a reviewer or an interviewer would ask about this note, answerable from
  the notes, in {language}.
- figures: ids from the figure list that show this note's content (at most 3; leave empty if none fits). A figure
  you place in the body as ![short caption](fig:<figure id>) must also be in this list. Use only ids from the list.
- pages: page numbers of the PDF file, counted from 1 at its first page (not the page numbers printed on the
  pages). For slides, the slide numbers from the slide list (a built-up slide is numbered by its final page); the
  numbers printed on the slides themselves often differ.
- links: relations from this note to other notes, read as "this note REL target". Link to notes of this source,
  to rw.* notes and to the existing ids you are given. Each link has a note: one or two sentences in {language}
  saying concretely how or why the relation holds. 2 to 6 meaningful links per note is typical; do not add a link
  just to connect everything. A pair of notes gets one link: write it from the note it reads best from, and do not
  also write the reverse. Every note is automatically attached to its source, so do not write part_of links
  to "{prefix}". You may link to another source's planet (its source id) or to a note of another source (see
  `sigatla ids`) when this note clearly builds on it; most links between sources are written later, when the
  sources are connected.

## Relations
| rel | meaning (this note → target) | example |
| --- | --- | --- |
| part_of | is a component or step of the target | a loss term part_of the training objective |
| uses | needs or applies the target | a method uses a formula; an experiment uses a dataset |
| extends | builds on the target and changes or generalizes it | a method extends a related work |
| motivates | gives the reason for the target | a limitation of prior work motivates a design choice |
| contrasts | differs from the target in an instructive way | two methods that answer the same need differently |
| measures | quantifies the target | a metric measures a property |
| tests | is an experiment or check that examines the target | an ablation tests a component |
| supports | is evidence for the target (a claim, method or design) | a result supports a method |
| refutes | is evidence against the target | a result refutes an assumption |
| limits | restricts where the target holds or how well it works | a limitation limits a result |
| cites | refers to the target without a stronger relation | a note cites a related work |
| frames | sets the context or question for the target | the problem statement frames the method |
| complements | covers a different side of the same topic | two analyses that answer related questions |
"""

_READ_PAPER = """\
## Your task: read one research paper
Write the notes for the paper in {pdf}.
- Cover: the problem and why it matters; the key concepts; the method (one note per main component); every
  important formula; the main design choices of the study and the reasons for them; every main result with its
  numbers; the limitations; the datasets; and the 5 to 12 most important related works.
- Aim for 18 to 40 notes of the paper itself (not counting related notes); up to 50 for a long paper.
- source.title: the paper's title. source.authors: the authors as printed, shortened to "First Author et al." when
  there are more than three. source.year: the four-digit year if the PDF or the label states it, otherwise ""
  (never "not stated").
- source.summary is the body of the paper's own planet, about 200 to 400 words. Its first sentence (under the
  first heading) is shown as the planet's one-line description, so make it a standalone summary. Sections:
  {summary}.
- source.questions: 3 to 6 questions about the paper as a whole.
"""

_READ_SLIDES = """\
## Your task: read one set of lecture slides
Write the notes for the lecture in {pdf}. Every slide is also available as an image with the figure id
"{prefix}/slide-<number>".
- Cover: every concept and definition; every method or algorithm; every equation shown (explain each symbol); the
  worked examples (type example); the pitfalls and limitations the lecturer points out; the lecturer's
  recommendations for how to do things (type design); and the works or datasets the slides cite (as related or
  data notes).
- Slides are terse: explain each idea fully enough that a student who missed the lecture can learn it, and mark
  everything the slides do not say with "{marker}". Explain symbols as the slides use them; when a symbol is left
  undefined, give its most likely meaning, marked the same way.
- A formula note is only for an equation the slides show. A formalization you add yourself goes inside the
  method or concept note, marked as above.
- The slides may teach a well-known method without citing it: you may name where it comes from (marked as above),
  but write related notes only for works the slides cite.
- Put the slide numbers in pages, and attach the slides that show the content as figures (at most 3 per note),
  also when the slide is only text.
- A slide whose video is not available: describe only what its text and image show, and say the video is not
  included; do not guess what the video shows. Without numbers it is not a result note.
- When the slides set exercises or homework, you may solve them in the note they belong to, marked as above and
  with the steps shown. If the slides contradict themselves (an index, a title, a symbol), point it out, marked.
- The title slide and the summary or exam-question slides are not notes of their own. Reuse the slides' own exam
  questions in source.questions and in the notes they are about; where the slides do not answer them, answer
  them in the relevant note, marked as above.
- Aim for about one note per one to two slides with content (at least 6), not counting related notes. Merge
  slides that build one idea.
  The page numbers of build-up steps (the same slide exported several times as it fills in) are merged in the
  slide list: use the final slide's figure id and number. The step images exist too (<prefix>/slide-<page>), and
  the slide text lists them with any text that appears only on a step. A step can show something the final slide
  removes (an intermediate result, a question answered later): look at it, and attach a step image only when it
  shows something the final slide does not.
- source.title: the deck's own title (from its title slide), else the label. source.authors: the lecturer or
  course as the slides give them; source.year: the four-digit year; otherwise empty strings (never "not stated").
- source.summary is the body of the lecture's own planet, about 150 to 300 words. Its first sentence is shown as
  the planet's one-line description, so make it a standalone summary of the lecture. Sections: {summary}.
- source.questions: 3 to 6 exam-style questions about the whole lecture. Translate the slides' own exam questions
  into {language}; put the broad ones here and the rest in the notes they are about.
"""

_READ_NOTES = """\
## Your task: read one document
Write the notes for {pdf}. Cover its main concepts, methods, formulas, examples, results and limitations, with the
same structure as for a paper. Aim for 12 to 35 notes.
- source.summary: {summary}. source.questions: 3 to 6 questions about it.
"""

_CONNECT = """\
## Your task: connect the sources
You receive the list of all sources in this atlas and an index of all their notes (id, type, title, one-line
summary). Write:
1. axis: the central question or theme that ties the sources together (the sun of the map). For research papers,
   the research question they share; for a course, the course's main thread. body sections: {axis}.
2. nodes: 3 to 10 shared concepts (up to about 12 in an atlas of many sources; type concept, id
   "shared.<slug>") that appear across two or more sources, such as a method family, an evaluation idea or a
   mathematical tool. Body sections: {shared}; write a bullet only for the sources that use the concept. A body
   may run to about 600 words when many sources use the concept. Explanations that go
   beyond the given summaries start with "{marker}". pages and figures stay empty for shared nodes, and their own
   `links` may stay empty: the notes that use a shared concept link to it in item 3.
3. links (each {{from, to, rel, note}}, read as "from REL to"):
   - from the notes that use each shared concept to it: "<note> uses shared.<slug>" when the note applies the
     concept, "part_of" when it is one instance of it (one algorithm of a family);
   - 10 to 40 links between notes of different sources, up to about 6 per source in a large atlas, counting the
     links the notes files already have between sources (listed in the input; do not repeat those pairs). For
     example a method in one source tested by a result in another, a limitation in one source addressed by a
     method in another, or two sources reaching different conclusions;
   - links between the sources' own planets (ids are the source ids): complements, extends, contrasts or motivates.
   Use only ids from the index (any note id, including rw.*) and the new shared ids.
Ground every claim in the index; do not invent details about a source. The rules below about figures and pages do
not apply to this step.
"""


def read_system(kind: str, prefix: str, language: str, pdf: str = "the attached PDF") -> str:
    """`pdf` names the document: "the attached PDF" for the API, a file path for a reading pack."""
    from .form import SUMMARY, layout, sections_block
    task = {"slides": _READ_SLIDES, "notes": _READ_NOTES}.get(kind, _READ_PAPER)
    text = "You write study notes for Sigatla, an interactive map of papers and lecture notes.\n\n"
    text += task + "\n" + _RULES
    text = text.format(language=lang_name(language), marker=interpretation_marker(language), prefix=prefix, pdf=pdf,
                       summary=layout(SUMMARY.get(kind, SUMMARY["paper"]), language))
    return text + "\n" + sections_block(language) + "\n"


def connect_system(language: str) -> str:
    from .form import AXIS, layout, sections_block
    text = "You write study notes for Sigatla, an interactive map of papers and lecture notes.\n\n"
    text += _CONNECT + "\n" + _RULES
    from .form import SHARED_NODE
    text = text.format(language=lang_name(language), marker=interpretation_marker(language), prefix="shared",
                       pdf="", summary="", axis=layout(AXIS, language), shared=layout(SHARED_NODE, language))
    # the reading rule about part_of links to the note's own source does not apply to shared concepts
    text = text.replace(' Every note is automatically attached to its source, so do not write part_of links\n  to "shared".', "")
    return text + "\n" + sections_block(language) + "\n"


def figure_list(manifest: dict, page_range: tuple[int, int] | None = None) -> str:
    """The figure catalogue given next to the PDF. For slides: one line per slide (build-up steps merged)."""
    inside = (lambda p: True) if page_range is None else (lambda p: page_range[0] <= p <= page_range[1])
    lines = []
    if manifest.get("frames"):
        for f in manifest["frames"]:
            if not inside(f["slide"]):
                continue
            where = " › ".join(x for x in (f["section"], f["subsection"]) if x)
            steps = f" (build-up of pages {f['pages'][0]}–{f['pages'][1]})" if f["pages"][0] != f["pages"][1] else ""
            extra = " [has a video]" if f.get("video") else ""
            lines.append(f"- {manifest['id']}/slide-{f['slide']}{steps}: {f['title'] or '(no title)'}"
                         f"{' — ' + where if where else ''}{extra}")
        return "\n".join(lines) or "(no slides in this range)"
    for f in manifest.get("figures", []):
        if not inside(f["page"]):
            continue
        cap = " ".join((f.get("caption") or "").split())
        lines.append(f"- {f['id']} (page {f['page']}, {f['kind']}){': ' + cap if cap else ''}")
    return "\n".join(lines) or "(no figures were extracted)"


def outline(manifest: dict) -> str:
    toc = manifest.get("toc") or []
    return "\n".join(f"{'  ' * (lvl - 1)}- {' '.join(str(t).split())} (page {pg})" for lvl, t, pg, *_ in toc
                     if lvl <= 2) or "(the PDF has no outline)"


def read_user_text(source_label: str, kind: str, prefix: str, manifest: dict, known_rw: list[str],
                   known_shared: list[str], other_sources: list[str] = (), closing: bool = True,
                   page_range: tuple[int, int] | None = None) -> str:
    frames = manifest.get("frames") or []
    size = f"{manifest.get('pages', '?')} pages in the PDF file"
    if frames:
        size += f", {len(frames)} slides after merging build-up steps (overlays exported as separate pages)"
    parts = [
        f"Source id (prefix for note ids): {prefix}",
        f"Source label: {source_label}",
        f"Kind: {kind}, {size}",
        "",
        "Outline of the whole source:",
        outline(manifest),
        "",
        ("Slides (figure ids; attach the slides that show a note's content):" if frames else
         "Figures and tables extracted from the PDF (use these ids in `figures` and in ![](fig:<id>)):"),
        figure_list(manifest, page_range),
        "",
        "Existing ids in the atlas (do not redefine them; link to them where they fit):",
        "- related work: " + (", ".join(sorted(known_rw)) or "none"),
        "- shared concepts: " + (", ".join(sorted(known_shared)) or "none"),
        "- other sources (their planets): " + (", ".join(other_sources) or "none"),
    ]
    if closing:
        parts += ["", "Write the notes now, following the schema."]
    return "\n".join(parts)

# Instructions for AI assistants

You are helping a user build or extend a **Sigatla atlas**: a 3D map of study notes made from their papers and
lecture slides. The `sigatla` tool in this repository does the mechanical work (extracting text, figures and slide
images, checking notes, building the website). **You write the notes**: you read the source and fill in the notes
form. This file tells you how. Follow it with any assistant (Claude Code, Codex, Cursor, Copilot, Gemini, ...).

## Quick path

1. In the user's project folder (the folder with `atlas.json`), run `sigatla next`. It prints the next commands.
2. A reading job is `sigatla pack <id>` (with `--part <k>` for a long deck). It writes a reading pack, one
   Markdown file with the whole task. Read the pack, follow it, and stop: the pack is complete, so you do not
   need the rest of this file or the tool's source code.
3. Run `sigatla next` again until it says everything is read, connected and built.

## Keep the token cost low

Every step an assistant takes sends its whole conversation again. The cost of a reading job is roughly what is
in the context times the number of steps, so keep both small:

- **One reading job per fresh assistant or subagent.** Jobs can run in parallel. Do not read two parts or two
  sources in one conversation: the first part's slides and notes would ride along with every step of the second.
- **Look at slides on the contact sheets** the pack lists (`build/<id>/sheets/`, four slides per image at about a
  third of the cost of four full slide images). Slides the pack marks "not needed" are plain text. Open a full
  slide image only for small print, and open several images in one step.
- **Look things up narrowly:** `sigatla ids --grep "<regex>"` finds notes on a topic, `sigatla show <id> [<id> ...]`
  prints particular notes (`--brief` for one line each), `sigatla check --source <id>` checks one source. Do not
  print whole notes files, and do not open `site/` or `build/` files the pack does not name.
- **Write a notes file once** (from a small script is easiest), then fix what `sigatla check` reports with small
  edits, not by writing the file again.
- **When you hand out jobs to subagents,** give each one only its command line from `sigatla next` and this
  instruction: "Run it, read the pack it writes and follow it. Report the file you wrote, the number of notes and
  the last line of `sigatla check --source <id>`." Ask for a short report.
- **Reading is most of the cost.** If the user wants to spend less, a smaller model can do the reading jobs and
  the strongest model the connecting; check a few notes from the smaller model first.

## 1. Set up (once)

```bash
python3 -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
sigatla --version
```

`sigatla` works on a **project folder**: the folder that contains `atlas.json`. It is usually not this repository.
Run commands inside it, or pass `-C <project folder>` before the subcommand. Reading packs print the full command
to run (the path to the tool and the project included); use it exactly as printed.

If the user has no project yet, ask for a title and the language of the notes, then:

```bash
sigatla init <folder> --title "<title>" --lang <en|vi|...>
sigatla -C <folder> add <file.pdf> --kind <paper|slides|notes> --label "<name on the planet>"
```

`sigatla init` also writes a short `AGENTS.md` (and a `CLAUDE.md` that imports it) into the project folder, so an
assistant started there knows what to do. For a project made with an older version, run `sigatla guide`.

Slides from PowerPoint, Keynote or Google Slides must be exported to PDF first. Lecture slides use
`--kind slides`: every page is kept as an image, and build-up steps (a slide exported several times as it fills
in) are merged into one slide.

## 2. Read a source into notes

1. `sigatla pack <id>` writes a **reading pack**, `build/<id>/READING_PACK.md` (for a long deck read in parts,
   `READING_PACK.part-<k>.md`). The pack is the complete task: the rules, the outline of the source, the slide
   text, the contact sheets to look at, the figure or slide ids you may use, the ids that already exist, and the
   output schema. **Read the whole pack before you start, and follow it exactly.**
2. For slides, work from the slide text in the pack and the contact sheets it lists; read formulas, diagrams and
   plot axes from the sheets. For a paper, read the PDF (in page ranges if it is long) and open the extracted
   figures when a figure matters.
3. Write the notes file the pack names (`notes/<id>.json`, or `notes/<id>.part-<k>.json` for later parts).
   Write valid JSON (escape backslashes in LaTeX: `"\\frac"`). Just before writing, look up what other readers
   may have added meanwhile (`sigatla ids --related`, `sigatla ids --grep`): reuse the id of a related work another
   reader already added, and link to their notes.
4. Run `sigatla check --source <id>`. Fix every **error** and every **hint** about the notes you wrote.
5. A long source is read in parts, and each part is its own job: `sigatla pack <id> --part <k>`. Parts can go to
   different readers in parallel. Part 1 writes `source` for the whole source.
6. When every source is read, `sigatla build` writes `site/index.html`. Tell the user the path.

## 3. Connect the sources

With two or more sources read: `sigatla pack --connect`, then follow `build/CONNECT_PACK.md`. It lists every note
of every read source by id, type and title (`sigatla show --brief <id> ...` prints what notes say, many in one
call) and says which sources have no notes yet (leave those out). Write `notes/_shared.json`: the central
question (`axis`), shared concepts (`shared.<slug>`), and `links` across sources, including links from notes to
the shared concepts they use. Then `sigatla check --source shared` and `sigatla build`.

Connecting again after adding sources updates `notes/_shared.json`. The pack points to the current file, lists
the links between sources that the notes files already have (do not repeat those pairs), and the shared ids that
notes link to (keep those ids). Writing the pack keeps a copy of the current file in `build/_shared.previous.json`.

## 4. Fill in more later

The notes are plain JSON, meant to grow. To add or improve notes:

- **Form:** [docs/NOTES_FORM.md](docs/NOTES_FORM.md) lists every field and the sections each note type needs.
  `sigatla form <type> --id <source-id> --lang <lang>` prints a blank note to fill in; `sigatla form source` prints a
  whole file. JSON Schema: [schema/](schema/).
- **Add a note:** append it to `nodes` in the source's notes file (or the part file that covers its pages). Use a
  new id `<source-id>.<slug>`; link it to existing notes and, where useful, add a link from an existing note to it.
- **Never rename an id** that other notes link to. Edit its text instead.
- **Add a question and answer** to a note: append `{node, q, a, source, at}` to `notes/_qa.json`.
- **A new source** that relates to existing ones: `sigatla add` it, then follow `sigatla next`.
- After any change: `sigatla check`, then `sigatla build`.

## 5. Rules that matter most

The reading pack has the full rules; these are the ones not to break:

- **Faithful.** Numbers, datasets and claims come only from the source, exactly as reported. Never invent a
  number, a citation or a result.
- **Marked.** Your own explanations, intuitions, comparisons and outside facts start with the interpretation
  marker of the notes' language ("Interpretation:" in English, "Diễn giải:" in Vietnamese).
- **Real ids.** Figure ids, slide ids and link targets must exist (the pack lists them). Page numbers are PDF
  page numbers counted from 1.
- **Every link explained.** Each link has a one- or two-sentence `note` saying how or why the relation holds.
  A pair of notes gets one link; do not also write the reverse.
- **Plain titles.** `title` and `short` have no Markdown or math. In bodies, a literal dollar sign is `\$`.
- **The notes' language** is the project's (`atlas.json` → `language`); technical terms stay in their original form.

## 6. Do not

- edit `site/` or `build/` by hand (they are regenerated);
- read the tool's source to work out a message from `sigatla check`: the message says what to fix, and if it does
  not, tell the user;
- put the user's PDFs, notes or built sites into this repository (they may be unpublished or copyrighted);
- spend API credits (`sigatla read`, `sigatla connect`, `sigatla update`) without the user's go-ahead: those call the
  Claude API with the user's key. The pack workflow above needs no key.

## 7. Where things are

| Path | What |
| --- | --- |
| `sigatla/` | the tool (`extract.py`, `pack.py`, `prompts.py` with the reading rules, `build.py`, `serve.py`) |
| `sigatla/sheets.py` | which slides need an image, and the contact sheets |
| `sigatla/guide.py` | `sigatla next` and the project's `AGENTS.md` |
| `sigatla/web/index.html` | the website template |
| `docs/NOTES_FORM.md`, `schema/` | the notes form and JSON Schema (generated: `sigatla form --doc`, `sigatla form --schema schema`) |
| `docs/DATA_FORMAT.md` | the project folder and the data injected into the page |
| `examples/demo/` | a small project: two lecture decks and their notes |
| `skills/sigatla-read/` | the same workflow as a Claude Code skill |

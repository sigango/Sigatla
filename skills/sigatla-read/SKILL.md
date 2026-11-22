---
name: sigatla-read
description: Read papers or lecture slides into a Sigatla project and build its 3D study map. Use when the user wants Claude Code to read a PDF (paper, lecture slides, notes) into notes for `sigatla`, to connect the sources of an atlas, or to rebuild the atlas website, without calling the Claude API from sigatla.
---

# Reading sources into Sigatla

Sigatla (`sigatla` command) turns PDFs into an interactive 3D map of study notes. The tool extracts text,
figures, tables and slide images; the notes themselves are written by Claude. In Claude Code, you write them:
no API key is needed. The full guide is `AGENTS.md` at the root of the sigatla repository; this skill is
its short form.

## Before you start
- Find the project: the folder with `atlas.json`. If there is none, ask the user for a title and the notes'
  language, then run `sigatla init <folder> --title "..." --lang en|vi`.
- Add each PDF the user gives you: `sigatla add <file.pdf> --kind paper|slides|notes [--label "..."]`.
  Slides from PowerPoint, Keynote or Google Slides must be exported to PDF first.

## The loop
1. Run `sigatla next`. It prints the next commands: reading jobs, then connecting, then building.
2. Give each reading job (`sigatla pack <id>`, or `sigatla pack <id> --part <k>` for a part of a long deck) to its
   own subagent, in parallel, with only this instruction: "Run `<command>`, read the pack it writes and follow
   it. Report the file you wrote, the number of notes and the last line of `sigatla check --source <id>`."
   With one job, you can do it yourself.
3. When the jobs are done, run `sigatla next` again: it asks for `sigatla pack --connect` (follow
   `build/CONNECT_PACK.md`, write `notes/_shared.json`), then for `sigatla build`.
4. Give the user the path to `site/index.html`, or run `sigatla serve` to open it at http://127.0.0.1:8000.

## Keep the token cost low
- One reading job per subagent; never read two parts in one conversation.
- Look at slides on the contact sheets the pack lists; open a full slide image only for small print, and open
  several images in one step. Slides the pack marks "not needed" are plain text.
- Look things up with `sigatla ids --grep "<regex>"`, `sigatla show <id> ...` and `sigatla check --source <id>`, not by
  printing whole notes files. Do not read `site/` or the tool's source code.
- Write a notes file once; fix check findings with small edits.

## Rules that matter most (the pack has the full list)
- Numbers, datasets and claims come only from the source. Mark your own explanations with the interpretation
  marker the pack names ("Interpretation:" or "Diễn giải:").
- Use only figure ids from the pack's list.
- Every link carries a one- or two-sentence note saying how or why the relation holds.

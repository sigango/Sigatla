# Contributing to Sigatla

Thank you for helping. Bug reports, fixes, new features, better reading rules, and documentation are all
welcome. This file explains how to set up the code, what a pull request needs, and how changes are reviewed.

## Before you start

- **Small fixes** (a typo, a clear bug with an obvious fix): open a pull request directly.
- **Larger changes** (a new command, a change to the notes form or the reading rules, a new page feature): open
  an issue first and describe what you want to do, so we can agree on the approach before you write the code.
- **Bugs:** use the bug report template. A minimal PDF that shows the problem helps most; do not attach PDFs you
  are not allowed to share.

## Set up

Python 3.10 or newer.

```bash
git clone https://github.com/<your-account>/Sigatla.git     # your fork
cd Sigatla
python3 -m venv .venv && . .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest
```

Work on a branch of your fork, for example `fix-figure-crop`, and open the pull request against `main`.

## Checks before a pull request

Continuous integration runs the same checks on Linux, macOS, and Windows with Python 3.10 and 3.14. Running them
locally first saves a round trip:

```bash
python -m pytest
cd examples/demo
sigatla extract && sigatla check && sigatla build
```

Depending on what you changed, also:

- **`sigatla/form.py` or `sigatla/prompts.py`:** regenerate the notes form and the JSON Schema. A test fails if
  they are out of date.

  ```bash
  sigatla form --doc > docs/NOTES_FORM.md
  sigatla form --schema schema
  ```

- **`sigatla/web/index.html`:** build the demo and look at `examples/demo/site/index.html` in a browser, in a
  wide window and a phone-sized one, in the 3D and the flat view. Open a note, a link, and a path.
- **Reading packs or `sigatla next`:** run `sigatla pack <id>` on the demo and read the pack as an assistant
  would. Everything the assistant needs should be in the pack.
- **A bug fix:** add a test that fails without the fix, in `tests/test_pipeline.py`.

## Guidelines

- **One change per pull request.** Small pull requests are reviewed faster.
- **English everywhere:** code, comments, messages, and docs, in American spelling. Notes in the demo are in
  English; the tool itself supports notes in other languages.
- **Few dependencies.** The tool depends on PyMuPDF and the Anthropic SDK only. Discuss a new runtime dependency
  in an issue first. The page is one self-contained HTML file that loads its libraries from public CDNs.
- **Short command output.** AI assistants read what `sigatla` prints and pay for every token at every step, so
  print what the next step needs and nothing more.
- **Reading rules affect every reader.** A change to the rules in `sigatla/prompts.py` changes the notes every
  assistant writes. Explain in the pull request what problem it fixes, ideally with a note written before and
  after the change.
- **Comments say why.** Explain assumptions and non-obvious choices; do not restate the code.
- **No material you may not share.** Never add PDFs, notes, or built sites made from unpublished, paywalled, or
  course material. Example projects need original or openly licensed sources.

## Commit messages

Use a short, imperative, lowercase summary of about 50 characters, for example `fix figure crop for two-column
captions`. Pull requests are squash-merged, so the pull request title becomes the commit message on `main`.

## Review

Every pull request needs passing checks on all platforms and a review from the maintainer before it is merged.
Expect questions or change requests; they are part of keeping the tool reliable for everyone.

## License

Sigatla is released under the [MIT License](LICENSE). By contributing, you agree that your contributions are
released under the same license.

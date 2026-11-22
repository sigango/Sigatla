"""End-to-end checks that need no network: extraction, note cleaning, building and the local server."""
import json
import threading
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

import pymupdf
import pytest

from sigatla.build import assemble, build_site, check, normalize_source_notes
from sigatla.extract import extract
from sigatla.project import AtlasError, Project, slugify


def make_paper(path):
    """A two-column page with a drawn figure above 'Figure 1:' and a table under 'Table 1.'."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(pymupdf.Rect(50, 60, 300, 200), "Introduction. " + "Body text of the paper. " * 30, fontsize=9)
    page.draw_rect(pymupdf.Rect(320, 80, 560, 220), color=(0, 0, 0), fill=(0.8, 0.9, 1))
    page.draw_line((330, 210), (550, 90), color=(1, 0, 0), width=2)
    page.insert_textbox(pymupdf.Rect(320, 228, 560, 260), "Figure 1: A drawn test figure with a line.", fontsize=9)
    page.insert_textbox(pymupdf.Rect(320, 300, 560, 330), "Table 1. Results on the test set.", fontsize=9)
    page.insert_textbox(pymupdf.Rect(320, 335, 560, 400), "Method  Acc\nA  0.91\nB  0.88\nC  0.75", fontsize=9)
    doc.save(path)


def make_slides(path, n=3):
    doc = pymupdf.open()
    for i in range(n):
        page = doc.new_page(width=960, height=540)
        page.insert_textbox(pymupdf.Rect(50, 40, 900, 100), f"Slide title {i + 1}", fontsize=28)
        page.insert_textbox(pymupdf.Rect(70, 120, 900, 300), "• point one\n• point two", fontsize=18)
    doc.save(path)


@pytest.fixture
def project(tmp_path):
    p = Project.create(tmp_path / "atlas", title="Test atlas", language="en")
    make_paper(tmp_path / "paper.pdf")
    make_slides(tmp_path / "deck.pdf")
    p.add_source(tmp_path / "paper.pdf", "paper", sid="paper", label="A paper", short="Paper")
    p.add_source(tmp_path / "deck.pdf", "slides", sid="deck", label="Lecture 1")
    return p


def test_slugify():
    assert slugify("Lecture 3: Optical Flow!") == "lecture-3-optical-flow"
    assert slugify("Bài giảng Đầu tiên") == "bai-giang-dau-tien"


def test_add_rejects_duplicates_and_non_pdf(project, tmp_path):
    with pytest.raises(AtlasError):
        project.add_source(tmp_path / "paper.pdf", "paper", sid="paper")
    (tmp_path / "x.pptx").write_bytes(b"x")
    with pytest.raises(AtlasError):
        project.add_source(tmp_path / "x.pptx", "slides")


def test_extract_paper_finds_figure_and_table(project):
    m = extract(project, project.source("paper"), log=lambda *a, **k: None)
    ids = {f["id"]: f for f in m["figures"]}
    assert "paper/fig-1" in ids and ids["paper/fig-1"]["kind"] == "figure"
    assert "paper/tab-1" in ids and ids["paper/tab-1"]["kind"] == "table"
    assert (project.build_dir("paper") / ids["paper/fig-1"]["file"]).exists()


def test_extract_slides_keeps_every_slide(project):
    m = extract(project, project.source("deck"), log=lambda *a, **k: None)
    assert [f["id"] for f in m["figures"]] == ["deck/slide-1", "deck/slide-2", "deck/slide-3"]
    assert m["figures"][0]["caption"] == "Slide title 1"


def test_normalize_cleans_ids_figures_and_links(project):
    m = extract(project, project.source("paper"), log=lambda *a, **k: None)
    raw = {"source": {"title": "T", "summary": "S ![x](fig:fig-1)", "questions": []}, "nodes": [
        {"id": "Core Idea", "type": "nonsense", "title": "Core", "short": "s", "body": "![a](fig:fig-9) text",
         "questions": ["q"], "figures": ["fig-1", "fig-99"], "pages": [1, 7, "x"],
         "links": [{"to": "other", "rel": "supports", "note": "n"}, {"to": "paper", "rel": "part_of", "note": ""},
                   {"to": "rw.gradcam", "rel": "zzz", "note": ""}]},
        {"id": "rw.gradcam", "type": "concept", "title": "Grad-CAM", "short": "", "body": "", "questions": [],
         "figures": [], "pages": [], "links": []},
    ]}
    notes = normalize_source_notes(raw, project.source("paper"), m, log=lambda *a: None)
    core, rw = notes["nodes"]
    assert core["id"] == "paper.core-idea" and core["type"] == "concept"
    assert core["figures"] == ["paper/fig-1"]
    assert core["pages"] == [1]
    assert "fig-9" not in core["body"] and "*a*" in core["body"]
    assert [l["to"] for l in core["links"]] == ["paper.other", "rw.gradcam"]
    assert core["links"][1]["rel"] == "uses"
    assert rw["type"] == "related"
    assert "fig:paper/fig-1" in notes["source"]["summary"]


def test_build_writes_site_with_data(project):
    for s in project.sources:
        extract(project, s, log=lambda *a, **k: None)
    notes = {"source": {"title": "A paper", "summary": "Summary.", "questions": []}, "nodes": [
        {"id": "paper.a", "type": "concept", "title": "A", "short": "a", "body": "b", "questions": [],
         "figures": ["paper/fig-1"], "pages": [1], "links": [{"to": "paper.b", "rel": "uses", "note": "why"}]},
        {"id": "paper.b", "type": "method", "title": "B", "short": "b", "body": "b", "questions": [],
         "figures": [], "pages": [], "links": [{"to": "rw.x", "rel": "extends", "note": ""}]},
        {"id": "rw.x", "type": "related", "title": "X", "short": "", "body": "", "questions": [],
         "figures": [], "pages": [], "links": []},
    ]}
    project.notes_path("paper").write_text(json.dumps(notes), encoding="utf-8")
    data = build_site(project, log=lambda *a: None)
    html = (project.site_dir / "index.html").read_text(encoding="utf-8")
    assert "__DATA__" not in html and '"paper.a"' in html
    ids = {n["id"]: n for n in data["nodes"]}
    assert {"axis", "paper", "deck", "paper.a", "rw.x"} <= set(ids)
    assert ids["paper"]["type"] == "hub" and ids["rw.x"]["group"] == "related"
    pairs = {(l["source"], l["target"], l["rel"]) for l in data["links"]}
    assert ("paper.a", "paper.b", "uses") in pairs and ("axis", "deck", "frames") in pairs
    assert ("paper.a", "paper", "part_of") in pairs        # attached to its planet automatically
    assert (project.site_dir / data["figures"]["paper/fig-1"]["src"]).exists()
    assert ids["deck"]["figures"] == []                     # an unread deck is a bare planet (no slides copied)
    assert set(ids["paper"]["figures"]) == {"paper/fig-1", "paper/tab-1"}   # a read source shows all its figures
    groups = {g["id"]: g for g in data["groups"]}
    assert groups["paper"]["short"] == "Paper" and "short" not in groups["deck"]   # jump-bar name, only when set


def test_assemble_drops_dangling_links(project):
    notes = {"source": {"title": "P", "summary": "", "questions": []}, "nodes": [
        {"id": "paper.a", "type": "concept", "title": "A", "short": "", "body": "", "questions": [], "figures": [],
         "pages": [], "links": [{"to": "paper.missing", "rel": "uses", "note": ""}]}]}
    project.notes_path("paper").write_text(json.dumps(notes), encoding="utf-8")
    data, _, warnings = assemble(project)
    assert not any(l["target"] == "paper.missing" for l in data["links"])
    assert any("paper.missing" in w for w in warnings)


def test_server_health_and_saved_answers(project, monkeypatch):
    from sigatla import serve as srv
    monkeypatch.setattr(srv, "has_credentials", lambda: False)
    build_site(project, log=lambda *a: None)
    srv.Handler.project = project
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(srv.Handler, directory=str(project.site_dir)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        health = json.load(urllib.request.urlopen(base + "/api/health"))
        assert health == {"ok": False, "model": project.model}
        req = urllib.request.Request(base + "/api/qa", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"node": "paper", "q": "Why?", "a": "Because."}).encode())
        saved = json.load(urllib.request.urlopen(req))
        assert saved["node"] == "paper" and "at" in saved
        assert json.load(urllib.request.urlopen(base + "/api/qa"))[0]["q"] == "Why?"
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "atlas-data" in page
    finally:
        httpd.shutdown()


def make_overlay_deck(path):
    """Slide 'Intro' shown in two build-up steps, then slide 'Next'."""
    doc = pymupdf.open()
    steps = [("Intro", "• first point"), ("Intro", "• first point\n• second point"), ("Next", "• other")]
    for title, body in steps:
        page = doc.new_page(width=960, height=540)
        page.insert_textbox(pymupdf.Rect(50, 40, 900, 100), title, fontsize=30)
        page.insert_textbox(pymupdf.Rect(70, 130, 900, 400), body, fontsize=18)
    doc.set_toc([[1, "Part A", 1], [1, "Part B", 3]])
    doc.save(path)


def test_slides_merge_build_up_steps(tmp_path):
    p = Project.create(tmp_path / "a", title="T")
    make_overlay_deck(tmp_path / "d.pdf")
    src = p.add_source(tmp_path / "d.pdf", "slides", sid="d")
    m = extract(p, src, log=lambda *a, **k: None)
    assert [(f["slide"], f["pages"], f["section"]) for f in m["frames"]] == [(2, [1, 2], "Part A"), (3, [3, 3], "Part B")]
    kinds = {f["id"]: f["kind"] for f in m["figures"]}
    assert kinds == {"d/slide-1": "step", "d/slide-2": "slide", "d/slide-3": "slide"}
    assert "## Slide 2 (pages 1–2)" in (p.build_dir("d") / "slides.md").read_text(encoding="utf-8")


def test_plan_parts_keeps_sections_together():
    from sigatla.pack import plan_parts
    frames = [{"slide": i, "section": "A" if i <= 30 else "B" if i <= 55 else "C"} for i in range(1, 91)]
    parts = plan_parts({"frames": frames}, "slides")
    assert [(x["first"], x["last"]) for x in parts] == [(1, 30), (31, 90)] or all(
        x["sections"] for x in parts)
    assert parts[0]["first"] == 1 and parts[-1]["last"] == 90
    assert plan_parts({"frames": frames[:45]}, "slides")[0]["last"] == 45   # a little over the limit: one part


def test_closing_slides_prefer_wrap_up_over_appendix():
    from sigatla.pack import closing_frames
    secs = ["Intro"] * 10 + ["Methods"] * 30 + ["Wrap-Up"] * 4 + ["Appendix"] * 6
    frames = [{"slide": i, "section": s, "title": f"t{i}"} for i, s in enumerate(secs, start=1)]
    assert [f["slide"] for f in closing_frames(frames, 20)] == [41, 42, 43, 44]
    no_wrap = [dict(f, section="Methods" if f["section"] == "Wrap-Up" else f["section"]) for f in frames]
    assert closing_frames(no_wrap, 20)[-1]["slide"] == 44          # last section that is not back matter
    flat = [dict(f, section="") for f in frames]
    assert closing_frames(flat, 20)[-1]["slide"] == 50              # no outline: the last slides


def test_text_is_cleaned_of_control_characters():
    from sigatla.extract import clean_text
    assert clean_text("a\x00b\x07c\nd\te") == "abc\nd\te"


def test_duplicate_ids_across_parts_are_reported(project):
    extract(project, project.source("deck"), log=lambda *a, **k: None)
    rw = {"id": "rw.smith-2020", "type": "related", "title": "Smith", "short": "", "body": "", "questions": [],
          "figures": [], "pages": [1], "links": []}
    own = {"id": "deck.idea", "type": "concept", "title": "Idea", "short": "", "body": "", "questions": [],
           "figures": [], "pages": [1], "links": [{"to": "rw.smith-2020", "rel": "cites", "note": "n"}]}
    (project.root / "notes" / "deck.part-2.json").write_text(json.dumps({"nodes": [own, dict(rw, pages=[2])]}))
    lines = []
    assert check(project, log=lines.append) == 0          # part 1 is missing: a warning, not an error
    assert any("part 1" in x for x in lines)
    project.notes_path("deck").write_text(json.dumps({"source": {"title": "T", "authors": "", "year": "",
                                                                 "summary": "S", "questions": []},
                                                      "nodes": [own, rw]}))
    lines = []
    assert check(project, log=lines.append) == 0
    assert any("deck.idea is used twice" in x for x in lines)
    assert any("rw.smith-2020 is written twice" in x for x in lines)
    data, _, _ = assemble(project, log=lambda *a, **k: None)
    smith = [n for n in data["nodes"] if n["id"].startswith("rw.smith")]
    assert len(smith) == 1 and smith[0]["pages"] == [1, 2]


def test_connect_pack_lists_existing_cross_links_and_flags_repeats(project):
    from sigatla.claude import connect_input
    node = {"id": "deck.idea", "type": "concept", "title": "Idea", "short": "", "body": "", "questions": [],
            "figures": [], "pages": [1], "links": [{"to": "paper.method", "rel": "uses", "note": "n"},
                                                   {"to": "shared.x", "rel": "uses", "note": "n"}]}
    project.notes_path("deck").write_text(json.dumps({"source": {"summary": "S"}, "nodes": [node]}))
    project.shared_path.write_text(json.dumps({"axis": {"title": "A"}, "nodes": [],
                                               "links": [{"from": "paper.method", "to": "deck.idea",
                                                          "rel": "supports", "note": "n"}]}))
    text = connect_input(project)
    assert "- deck.idea uses paper.method" in text and "shared.x (1)" in text and '"axis"' in text
    lines = []
    check(project, log=lines.append)
    assert any("repeats 'deck.idea uses paper.method'" in x for x in lines)


def make_visual_deck(path):
    """Four slides: text only, a large diagram, a formula (Symbol font), text only. A box drawn at the same place
    on every slide is template, not content."""
    doc = pymupdf.open()
    for i in range(4):
        page = doc.new_page(width=960, height=540)
        page.insert_textbox(pymupdf.Rect(50, 40, 900, 100), f"Topic {i + 1}", fontsize=28)
        page.insert_textbox(pymupdf.Rect(70, 120, 450, 300), f"• point {i + 1}", fontsize=18)
        page.draw_rect(pymupdf.Rect(20, 320, 320, 500), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        if i == 1:
            page.draw_rect(pymupdf.Rect(500, 150, 900, 450), color=(0, 0, 1), fill=(0.8, 0.9, 1))
            page.draw_line((520, 430), (880, 170), color=(1, 0, 0), width=3)
        if i == 2:
            page.insert_textbox(pymupdf.Rect(500, 150, 900, 250), "a + b = S", fontsize=24, fontname="symb")
    doc.save(path)


def test_slides_needing_an_image_go_on_contact_sheets(tmp_path):
    from sigatla.pack import write_reading_pack
    p = Project.create(tmp_path / "a", title="T")
    make_visual_deck(tmp_path / "v.pdf")
    src = p.add_source(tmp_path / "v.pdf", "slides", sid="v")
    extract(p, src, log=lambda *a, **k: None)
    pack = write_reading_pack(p, src, log=lambda *a, **k: None)
    from sigatla.project import read_json
    visuals = read_json(p.manifest_path("v"))["visuals"]
    assert visuals == {"1": [], "2": ["drawing"], "3": ["formula"], "4": []}
    sheets = sorted((p.build_dir("v") / "sheets").glob("*.jpg"))
    assert [s.name for s in sheets] == ["deck-1.jpg"]
    text = open(pack, encoding="utf-8").read()
    assert "- deck-1.jpg: slides 2, 3" in text
    assert "No image needed (plain text, complete above): slides 1, 4" in text
    assert "look: sheets/deck-1.jpg (drawing)" in text and "look: not needed" in text
    assert "`sigatla` means" in text and "You do not need to open the PDF" in text


def _notes(sid, links=()):
    return {"source": {"title": sid, "authors": "", "year": "", "summary": "S", "questions": []},
            "nodes": [{"id": f"{sid}.idea", "type": "concept", "title": f"Idea of {sid}", "short": f"{sid} short",
                       "body": "b", "questions": [], "figures": [], "pages": [1], "links": list(links)}]}


def test_next_walks_from_reading_to_building(project):
    from sigatla.guide import next_steps
    text = "\n".join(next_steps(project))
    assert "Reading jobs left: 2" in text and " pack paper" in text and " pack deck" in text
    project.notes_path("paper").write_text(json.dumps(_notes("paper")))
    project.notes_path("deck").write_text(json.dumps(_notes("deck")))
    assert " pack --connect" in "\n".join(next_steps(project))
    project.shared_path.write_text(json.dumps({"axis": {"title": "A"}, "nodes": [], "links": [
        {"from": "paper.idea", "to": "deck.idea", "rel": "supports", "note": "n"}]}))
    assert " build" in "\n".join(next_steps(project))
    build_site(project, log=lambda *a: None)
    assert "Everything is read, connected and built" in next_steps(project)[0]
    project.notes_path("deck").write_text("{not json")
    assert "not valid JSON" in "\n".join(next_steps(project))


def test_ids_grep_show_and_check_for_one_source(project, capsys):
    from sigatla.cli import main
    project.notes_path("paper").write_text(json.dumps(_notes("paper", [{"to": "deck.idea", "rel": "uses",
                                                                         "note": "n"}])))
    project.notes_path("deck").write_text(json.dumps(_notes("deck")))
    root = str(project.root)
    assert main(["-C", root, "ids", "--grep", "idea of DECK"]) == 0
    assert capsys.readouterr().out.strip() == "deck.idea\tconcept\tIdea of deck"
    assert main(["-C", root, "show", "--brief", "paper.idea", "deck"]) == 0
    out = capsys.readouterr().out
    assert "paper.idea | concept | Idea of paper | paper short | links: uses deck.idea" in out
    assert "deck | planet | Lecture 1" in out
    assert main(["-C", root, "show", "paper.ide"]) == 1
    assert "did you mean paper.idea" in capsys.readouterr().out
    lines = []
    check(project, log=lines.append, only="deck")
    assert lines and not any("paper" in x for x in lines[:-1])


def test_cli_prints_utf8_where_the_console_does_not(project, monkeypatch):
    import io
    import sys
    from sigatla.cli import main
    notes = _notes("deck")
    notes["nodes"][0]["title"] = "Ý tưởng chính: π và ∂"
    project.notes_path("deck").write_text(json.dumps(notes), encoding="utf-8")
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    assert main(["-C", str(project.root), "show", "--brief", "deck.idea"]) == 0
    sys.stdout.flush()
    assert "Ý tưởng chính: π và ∂" in raw.getvalue().decode("utf-8")


def test_guide_is_written_once_and_keeps_own_files(project):
    from sigatla.guide import MARK, write_guide
    write_guide(project, log=lambda *a: None)
    agents = (project.root / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.startswith(MARK) and "sigatla next" in agents and "Keep the token cost low" in agents
    assert (project.root / "CLAUDE.md").read_text(encoding="utf-8").strip().endswith("@AGENTS.md")
    (project.root / "CLAUDE.md").write_text("my own rules")
    write_guide(project, log=lambda *a: None)
    assert (project.root / "CLAUDE.md").read_text(encoding="utf-8") == "my own rules"


def test_generated_form_and_schema_are_up_to_date():
    from pathlib import Path
    from sigatla import form
    root = Path(__file__).resolve().parent.parent
    assert (root / "docs" / "NOTES_FORM.md").read_text(encoding="utf-8").strip() == form.render_doc().strip(), \
        "run: sigatla form --doc > docs/NOTES_FORM.md"
    for name, schema in form.schema_files().items():
        assert json.loads((root / "schema" / name).read_text(encoding="utf-8")) == schema, "run: sigatla form --schema schema"

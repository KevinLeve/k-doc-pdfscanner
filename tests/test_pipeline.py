"""Pilot conversion path: zone resolution, ledger checks, DOCX checks, end-to-end conversion."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from docx import Document

from pdf2word.analyze.sweep import ZoneBuilder
from pdf2word.ir import (CharInfo, Fate, FateKind, FontInfo, Line, PictureBlock, Token, Zone, ZoneKind)
from pdf2word.pipeline import convert
from pdf2word.validate.docx_check import check_docx
from pdf2word.validate.invariants import check_document, check_page
from tests.conftest import sample


def tok(i: int, x0: float, text: str = "word", suspicious: bool = False, y0: float = 100.0) -> Token:
    w = 5.0 * len(text)
    chars = [CharInfo(c, (x0 + 5 * k, y0, x0 + 5 * (k + 1), y0 + 10), (x0 + 5 * k, y0 + 8), False)
             for k, c in enumerate(text)]
    return Token(id=f"p1:t{i}", chars=chars, raw_text=text, ws_before=" ", ws_after_span_end=None,
                 bbox=(x0, y0, x0 + w, y0 + 10), origin=(x0, y0 + 8),
                 font=FontInfo("Times", 10, False, False, 0), span_flags=0, baseline_shift_pt=0.0,
                 size_ratio=1.0, script="none", src_order=(0, 0, i), parent_span="p1:s0",
                 suspicious=suspicious, protected=suspicious, line_id="p1:l0")


def builder(tokens: list[Token], paths: dict | None = None) -> ZoneBuilder:
    d = {t.id: t for t in tokens}
    line = Line(id="p1:l0", bbox=(0, 100, 400, 110), baseline=108, x_height=5, token_ids=list(d),
                path_ids=[], protection_band=(0, 94, 400, 116))
    return ZoneBuilder(1, d, set(d), [line], paths or {}, {}, set(paths or {}))


# ---------------------------------------------------------------- zone resolution
def test_tight_zone_absorbs_overlapping_neighbour_only() -> None:
    ts = [tok(0, 10), tok(1, 40, "x", True), tok(2, 60)]
    zb = builder(ts)
    zb.add(ZoneKind.INLINE_MATH, (39, 98.5, 46, 111.5), tokens=["p1:t1"], line_ids=["p1:l0"], reason="t")
    zb.resolve()
    (z,) = zb.zones.values()
    assert z.kind is ZoneKind.INLINE_MATH and z.token_ids == ["p1:t1"]
    assert "p1:t0" not in zb.claim and "p1:t2" not in zb.claim


def test_inline_escalates_to_line_after_three_prose_words() -> None:
    ts = [tok(i, 10 + 22 * i, "abcd") for i in range(6)]
    zb = builder(ts, {"p1:d0": (5, 95, 120, 112)})     # a path over five words
    zb.add(ZoneKind.INLINE_MATH, (5, 95, 120, 112), paths=["p1:d0"], line_ids=["p1:l0"], reason="t")
    zb.resolve()
    (z,) = zb.zones.values()
    assert z.kind is ZoneKind.LINE_PRESERVE and any(r.startswith("S3") for r in z.reasons)
    assert set(z.token_ids) == {t.id for t in ts}         # the whole line, nothing left half-imaged


def test_overlapping_zones_merge_and_figure_wins() -> None:
    zb = builder([tok(0, 300)], {"p1:d0": (0, 0, 50, 50), "p1:d1": (40, 40, 90, 90)})
    zb.add(ZoneKind.FIGURE, (0, 0, 50, 50), paths=["p1:d0"], reason="a")
    zb.add(ZoneKind.DISPLAY_MATH, (40, 40, 90, 90), paths=["p1:d1"], reason="b")
    zb.resolve()
    (z,) = zb.zones.values()
    assert z.kind is ZoneKind.FIGURE and set(z.path_ids) == {"p1:d0", "p1:d1"}


def test_unclaimed_protected_is_reported() -> None:
    zb = builder([tok(0, 10, "x", True)])
    zb.resolve()
    assert zb.unclaimed_protected({"p1:t0"}) == ["p1:t0"]


# ---------------------------------------------------------------- ledger checks
def test_check_page_detects_missing_fate_removed_protected_and_overlap() -> None:
    z = Zone(id="p1:z0", kind=ZoneKind.INLINE_MATH, page_no=1, bbox=(0, 0, 10, 10), token_ids=["p1:t1"])
    fates = {"p1:t1": Fate(FateKind.IN_ZONE, target="p1:z0"),
             "p1:t2": Fate(FateKind.REMOVED, reason="slug"),
             "p1:t3": Fate(FateKind.EMITTED_TEXT)}
    v = check_page({"p1:t1", "p1:t2", "p1:t3", "p1:t4"}, fates, {"p1:t2"}, {"p1:z0": z},
                   {"p1:t3": (5, 5, 20, 9)})
    joined = " ".join(v)
    assert "G1" in joined and "p1:t4" in joined
    assert "G2: protected p1:t2" in joined
    assert "G3: emitted token p1:t3" in joined


def test_check_document_detects_double_placement_and_lost_text() -> None:
    z = Zone(id="p1:z0", kind=ZoneKind.FIGURE, page_no=1, bbox=(0, 0, 10, 10))
    v = check_document([PictureBlock("p1:z0"), PictureBlock("p1:z0")], {"p1:z0": z}, {"p1:t0"}, set())
    assert any("placed 2 times" in x for x in v) and any("not written" in x for x in v)


def test_check_docx_rejects_broken_package(tmp_path: Path) -> None:
    bad = tmp_path / "bad.docx"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("word/document.xml", "<not-closed>")
    assert check_docx(bad, 0, 0)


# ---------------------------------------------------------------- end to end
def test_convert_basic_preserves_exact_text(tmp_path: Path, basic_fixture) -> None:
    res = convert(basic_fixture.path, tmp_path)
    assert res.docx is not None and res.status != "Failed", res.summary
    s = res.summary
    assert s["E_editable_text"] + s["V_image_preserved"] + s["P_prose_as_image"] + s["R_removed_by_rule"] == s["elements"]
    text = "\n".join(p.text for p in Document(str(res.docx)).paragraphs)
    assert basic_fixture.ligature_text in text                  # ligatures kept, not expanded
    assert "a b  c" in text                                     # exact whitespace kept
    assert basic_fixture.hyphen_line_2 in text


@pytest.mark.parametrize("name", ["corrupt", "encrypted"])
def test_unreadable_pdf_fails_with_report(tmp_path: Path, synthetic, name: str) -> None:
    res = convert(synthetic[name], tmp_path)
    assert res.status == "Failed" and res.docx is None
    assert res.report_txt is not None and res.report_txt.is_file()
    assert res.summary["error"]


@pytest.mark.parametrize("name", ["scanned", "broken_xref", "broken_content"])
def test_damaged_or_scanned_pdf_still_converts(tmp_path: Path, synthetic, name: str) -> None:
    res = convert(synthetic[name], tmp_path)
    assert res.docx is not None and res.docx.is_file(), res.summary
    assert not check_docx(res.docx, res.summary["pictures"], res.summary["paragraphs"])


def test_sample_ch01_converts_without_fallback(tmp_path: Path, samples) -> None:
    res = convert(sample(samples, "ch01"), tmp_path)
    assert res.docx is not None and res.summary["fallback_pages"] == []
    assert res.summary["E_editable_text"] > 3000


def test_line_with_four_inline_groups_is_kept_whole() -> None:
    ts = [tok(i, 10 + 20 * i, "+" if i % 2 else "12", suspicious=bool(i % 2)) for i in range(9)]
    zb = builder(ts)
    for t in ts:
        if t.suspicious:
            b = t.bbox
            zb.add(ZoneKind.INLINE_MATH, (b[0] - 1, b[1] - 1.5, b[2] + 1, b[3] + 1.5), tokens=[t.id],
                   line_ids=["p1:l0"], reason="t")
    zb.resolve()
    assert zb.escalate_crowded_lines()
    (z,) = zb.zones.values()
    assert z.kind is ZoneKind.LINE_PRESERVE and set(z.token_ids) == {t.id for t in ts}


# ---------------------------------------------------------------- R0: never overwrite
def test_existing_docx_and_reports_are_never_overwritten(tmp_path: Path, basic_fixture) -> None:
    stem = basic_fixture.path.stem
    mine = tmp_path / f"{stem}.docx"
    mine.write_bytes(b"employee reference document")          # someone's own file with the same name
    report = tmp_path / f"{stem}.report.txt"
    report.write_text("older report", encoding="utf-8")
    first = convert(basic_fixture.path, tmp_path)
    second = convert(basic_fixture.path, tmp_path)
    assert mine.read_bytes() == b"employee reference document"
    assert report.read_text(encoding="utf-8") == "older report"
    assert first.docx.name == f"{stem} (2).docx" and second.docx.name == f"{stem} (3).docx"
    assert first.summary["renamed_to_avoid_overwrite"] is True
    assert "left untouched" in first.report_txt.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.partial"))


def test_default_output_folder_is_separate_from_the_pdf(tmp_path: Path, basic_fixture) -> None:
    import shutil
    pdf = tmp_path / "book.pdf"
    shutil.copy(basic_fixture.path, pdf)
    (tmp_path / "book.docx").write_bytes(b"reference")
    res = convert(pdf)
    assert res.docx.parent == tmp_path / "K-Doc PDFScanner output" and res.docx.name == "book.docx"
    assert (tmp_path / "book.docx").read_bytes() == b"reference"


def test_failed_conversion_report_does_not_overwrite(tmp_path: Path, synthetic) -> None:
    stem = synthetic["corrupt"].stem
    (tmp_path / f"{stem}.report.json").write_text("{}", encoding="utf-8")
    res = convert(synthetic["corrupt"], tmp_path)
    assert res.status == "Failed" and res.report_json.name == f"{stem} (2).report.json"
    assert (tmp_path / f"{stem}.report.json").read_text(encoding="utf-8") == "{}"


def test_conversion_is_independent_of_hash_seed(tmp_path: Path, samples) -> None:
    """Repair release D-1: analysis must not depend on set iteration order (PYTHONHASHSEED)."""
    import os
    import subprocess
    import sys
    pdf = sample(samples, "ch08")
    ledgers = []
    for seed in ("1", "3"):
        out = tmp_path / seed
        code = ("import sys; from pathlib import Path; from pdf2word.pipeline import convert; "
                "convert(Path(sys.argv[1]), Path(sys.argv[2]), ledger=True)")
        env = dict(os.environ, PYTHONHASHSEED=seed)
        subprocess.run([sys.executable, "-c", code, str(pdf), str(out)], check=True, env=env)
        ledgers.append((out / f"{pdf.stem}.ledger.json").read_text(encoding="utf-8"))
    assert ledgers[0] == ledgers[1]


# ---------------------------------------------------------------- R2: edge decorations
def _edge_case(bbox, kind=None, protected=False, image=False, tokens=(), rect=True, text_box=(90, 60, 532, 700)):
    from pdf2word.analyze.figures import Cluster, is_edge_decoration
    from pdf2word.ir import ImageItem, PathItem, PathKind
    paths, images = {}, {}
    if image:
        images["p1:i0"] = ImageItem(id="p1:i0", bbox=bbox, ref=1, content_hash="h", px_size=(10, 10), ext="png",
                                    protected=protected)
        c = Cluster(image_ids=["p1:i0"], bbox=bbox)
    else:
        paths["p1:d0"] = PathItem(id="p1:d0", bbox=bbox, kind=kind or PathKind.ART, filled=True, stroke_width=None,
                                  n_segments=1, shape_key="k", protected=protected)
        c = Cluster(path_ids=["p1:d0"], bbox=bbox)
    return is_edge_decoration(c, paths, images, 630, 774, text_box, list(tokens), {"p1:d0"} if rect else set())


def test_edge_band_outside_text_is_decoration() -> None:
    assert _edge_case((556, 8, 627, 771))                       # thumb-tab band (ch01 p13 geometry)
    assert _edge_case((556, 8, 627, 771), image=True)           # its shadow raster


@pytest.mark.parametrize("kw", [
    dict(bbox=(300, 8, 627, 771)),                               # wide: could be a bleed photo
    dict(bbox=(556, 300, 627, 400)),                             # short: a margin icon or figure
    dict(bbox=(556, 8, 627, 771), kind=__import__("pdf2word.ir", fromlist=["PathKind"]).PathKind.GLYPH),
    dict(bbox=(556, 8, 627, 771), tokens=[(560, 100, 600, 110)]),   # text on it
    dict(bbox=(556, 8, 627, 771), protected=True),
    dict(bbox=(556, 8, 627, 771), rect=False),                   # curves: not a plain band
    dict(bbox=(500, 8, 560, 771)),                               # inside the text column
    dict(bbox=(556, 8, 600, 771)),                               # does not reach the page edge zone
    dict(bbox=(556, 8, 627, 771), text_box=None),                # page without text: never removed
])
def test_edge_decoration_rejects_anything_that_may_be_content(kw) -> None:
    assert not _edge_case(**kw)


# ---------------------------------------------------------------- R1: manuscript formatting
def test_manuscript_styles_are_readable_and_hierarchical(tmp_path: Path, basic_fixture) -> None:
    from pdf2word.write.docx_writer import manuscript_body_pt
    assert manuscript_body_pt(8.2) == 10.0 and manuscript_body_pt(11.0) == 11.0 and manuscript_body_pt(14) == 12.0
    res = convert(basic_fixture.path, tmp_path)
    d = Document(str(res.docx))
    body = d.styles["Normal"].font.size.pt
    sizes = [d.styles[n].font.size.pt for n in ("Title", "Heading 1", "Heading 2", "Heading 3")]
    assert body >= 10 and sizes == sorted(sizes, reverse=True) and sizes[-1] > body
    pf = d.styles["Normal"].paragraph_format
    assert pf.space_after.pt == 4 and pf.line_spacing == 1.0
    assert all(d.styles[n].paragraph_format.keep_with_next for n in ("Heading 1", "Heading 2", "Heading 3"))


# ---------------------------------------------------------------- R4: composite rules, ruled tables
def _rule(i: int, x0: float, x1: float, y: float):
    from pdf2word.ir import PathItem, PathKind
    return PathItem(id=f"p1:d{i}", bbox=(x0, y, x1, y), kind=PathKind.RULE, filled=False, stroke_width=1.0,
                    n_segments=1, shape_key="r")


def _line(i: int, x0: float, x1: float, y: float, words: list[str], tokens: dict):
    ids, x = [], x0
    step = (x1 - x0) / max(1, len(words))
    for k, w in enumerate(words):
        t = tok(100 * i + k, x, w, y0=y)
        t.line_id = f"p1:l{i}"
        tokens[t.id] = t
        ids.append(t.id)
        x += step
    return Line(id=f"p1:l{i}", bbox=(x0, y, x1, y + 10), baseline=y + 8, x_height=5, token_ids=ids, path_ids=[],
                protection_band=(x0, y - 6, x1, y + 16))


def test_dashed_segments_join_into_one_rule() -> None:
    from pdf2word.analyze.table_regions import composite_rules
    paths = {p.id: p for p in [_rule(0, 150, 200, 258), _rule(1, 200, 290, 258), _rule(2, 290, 540, 258.3),
                               _rule(3, 150, 540, 300)]}
    groups = sorted(composite_rules(paths), key=lambda g: g[1][1])
    assert [sorted(g[0]) for g in groups] == [["p1:d0", "p1:d1", "p1:d2"], ["p1:d3"]]
    assert groups[0][1][0] == 150 and groups[0][1][2] == 540


def test_ruled_table_with_columns_is_found_and_prose_between_rules_is_not() -> None:
    from pdf2word.analyze.table_regions import composite_rules, find_ruled_tables
    toks: dict = {}
    header = _line(1, 160, 530, 245, ["Millions", "Hundreds", "Tens", "Ones"], toks)
    cells = [_line(2 + k, 300 + 60 * k, 310 + 60 * k, 260, [str(k)], toks) for k in range(4)]
    paths = {p.id: p for p in [_rule(0, 150, 540, 257), _rule(1, 150, 540, 272)]}
    found = find_ruled_tables([header] + cells, toks, composite_rules(paths), 400, paths)
    assert len(found) == 1 and found[0][0][1] <= 245 and set(found[0][1]) == {"p1:d0", "p1:d1"}
    ptoks: dict = {}
    words = "this is an ordinary sentence of running text that fills the measure between".split()
    prose = _line(9, 160, 160 + sum(5 * len(w) + 3 for w in words), 260, words, ptoks)
    x = 160.0
    for t in (ptoks[i] for i in prose.token_ids):           # normal word spacing (3 pt)
        w = t.bbox[2] - t.bbox[0]
        t.bbox = (x, t.bbox[1], x + w, t.bbox[3])
        x += w + 3
    assert not find_ruled_tables([prose], ptoks, composite_rules(paths), 400, paths)


def test_ruled_table_rejects_far_rules_and_lone_rules() -> None:
    from pdf2word.analyze.table_regions import composite_rules, find_ruled_tables
    toks: dict = {}
    header = _line(1, 160, 530, 245, ["A", "B", "C", "D"], toks)
    cells = [_line(2 + k, 300 + 60 * k, 310 + 60 * k, 260, [str(k)], toks) for k in range(4)]
    far = {p.id: p for p in [_rule(0, 150, 540, 257), _rule(1, 150, 540, 340)]}        # 83 pt apart
    assert not find_ruled_tables([header] + cells, toks, composite_rules(far), 400, far)
    lone = {p.id: p for p in [_rule(0, 150, 540, 257)]}                               # one unsplit rule
    assert not find_ruled_tables([header], toks, composite_rules(lone), 400, lone)
    split = {p.id: p for p in [_rule(k, 150 + 78 * k, 228 + 78 * k, 257) for k in range(5)]}
    assert find_ruled_tables([header], toks, composite_rules(split), 400, split)       # empty answer grid
    assert not find_ruled_tables([], toks, composite_rules(split), 400, split)         # no header: not a table


# ---------------------------------------------------------------- R5: stacked arithmetic
def _num_line(i: int, x0: float, y: float, text: str, toks: dict, bold: bool = False) -> Line:
    t = tok(500 + i, x0, text, y0=y)
    if bold:
        t.font = FontInfo("Times-Bold", 10, True, False, 0)
    t.line_id = f"p1:l{i}"
    toks[t.id] = t
    return Line(id=f"p1:l{i}", bbox=t.bbox, baseline=y + 8, x_height=5, token_ids=[t.id], path_ids=[],
                protection_band=(t.bbox[0], y - 6, t.bbox[2], y + 16))


def test_column_sum_is_one_stack_with_its_rule() -> None:
    from pdf2word.analyze.display_math import find_arithmetic_stacks
    toks: dict = {}
    lines = [_num_line(k, 180, 300 + 13 * k, v, toks) for k, v in enumerate(["35", "26", "+142", "203"])]
    rule = _rule(0, 176, 196, 349)
    found = find_arithmetic_stacks(lines, toks, {rule.id: rule})
    assert len(found) == 1 and found[0][1] == [ln.id for ln in lines] and found[0][2] == ["p1:d0"]
    assert found[0][0][0] <= 176 and found[0][0][3] >= 349


def test_arithmetic_stack_rejections() -> None:
    from pdf2word.analyze.display_math import find_arithmetic_stacks, is_arithmetic_line
    toks: dict = {}
    two = [_num_line(k, 180, 300 + 13 * k, v, toks) for k, v in enumerate(["35", "26"])]
    assert not find_arithmetic_stacks(two, toks, {})                       # 2 lines, no rule
    col = [_num_line(k, 180, 300 + 13 * k, v, toks) for k, v in enumerate(["19", "25", "13"])]
    nxt = [_num_line(10 + k, 230, 300 + 13 * k, v, toks) for k, v in enumerate(["83", "86", "71"])]
    assert not find_arithmetic_stacks(col + nxt, toks, {})                 # numeric table columns
    bold = _num_line(20, 180, 400, "11,424", toks, bold=True)
    assert not is_arithmetic_line(bold, toks)                              # bold answer heading
    word = _num_line(21, 180, 420, "Subtract", toks)
    assert not is_arithmetic_line(word, toks)


# ---------------------------------------------------------------- D-2: folios follow the page sequence
def _span(page: int, n: int, text: str, y: float, x: float = 100.0, size: float = 9.0):
    from pdf2word.extract.backend import RawSpan
    return RawSpan(id=f"p{page}:s{n}", block_no=0, line_no=0, span_no=n, text=text, bbox=(x, y, x + 20, y + 10),
                   origin=(x, y + 8), font_name="Times", font_name_raw="Times", size=size, flags=0, color=0,
                   ascender=0.9, descender=-0.2, line_bbox=(x, y, x + 20, y + 10), line_dir=(1, 0), line_wmode=0)


def test_folio_must_follow_the_page_number_sequence() -> None:
    from pdf2word.analyze.cleanup import Cleanup
    h = 774.0
    # pages 1..6 print folios 7..12 at the foot; page 4 also has a body number "15" on its first line
    skims = {p: (h, [_span(p, 0, "15" if p == 4 else "Body text", 60), _span(p, 1, "Body", 300),
                     _span(p, 2, str(p + 6), 700)]) for p in range(1, 7)}
    cl = Cleanup(skims)
    assert cl.folio_offset == 6
    assert cl.is_folio("10", 4) and not cl.is_folio("15", 4) and not cl.is_folio("11", 4)


def test_no_consistent_folio_offset_removes_nothing() -> None:
    from pdf2word.analyze.cleanup import Cleanup
    h = 774.0
    skims = {p: (h, [_span(p, 0, "Body", 300), _span(p, 1, str(3 * p + 1), 700)]) for p in range(1, 7)}
    cl = Cleanup(skims)
    assert cl.folio_offset is None and not cl.is_folio("4", 1)


def test_small_body_text_in_the_margin_band_is_not_a_slug() -> None:
    from pdf2word.analyze.cleanup import Cleanup
    h = 774.0
    spans = {s.id: s for s in [_span(15, 0, "Body", 300), _span(15, 1, "10", 622, size=5.8),   # carry digit
                               _span(15, 2, "Last", 690), _span(15, 3, "Trim", 761, size=4.5)]}  # printer slug
    cl = Cleanup({15: (h, list(spans.values()))})
    toks = {}
    for i, sp in enumerate(spans.values()):
        t = tok(i, sp.bbox[0], sp.text, y0=sp.bbox[1])
        t.parent_span = sp.id
        toks[t.id] = t
    removed = cl.classify(toks, spans, h)
    assert removed == {"p1:t3": "slug"}            # only the slug beyond the body text


# ---------------------------------------------------------------- R3: figure label grouping
def test_axis_and_tick_labels_join_into_one_figure() -> None:
    from pdf2word.analyze.figures import Cluster, group_pictures
    axis = Cluster(path_ids=["p1:d0"], bbox=(150, 80, 520, 96), kind="display")
    labels = [Cluster(path_ids=[f"p1:d{k}"], bbox=(150 + 18 * k, 102, 155 + 18 * k, 110), kind="display")
              for k in range(1, 6)]
    groups = group_pictures([axis] + labels, token_boxes=[(150, 200, 300, 210)], line_boxes=[(150, 200, 300, 210)])
    assert len(groups) == 1 and len(groups[0]) == 6


def test_grouping_never_pulls_text_into_a_picture() -> None:
    from pdf2word.analyze.figures import Cluster, group_pictures
    a = Cluster(path_ids=["p1:d0"], bbox=(100, 100, 140, 110), kind="display")
    b = Cluster(path_ids=["p1:d1"], bbox=(100, 116, 300, 126), kind="display")      # 6 pt below a
    word = (200, 102, 230, 109)                                                      # text beside a, above b
    assert len(group_pictures([a, b], token_boxes=[word], line_boxes=[word])) == 2
    blocker = (95, 112, 305, 114)                                                   # a multi-line zone between
    assert len(group_pictures([a, b], token_boxes=[], line_boxes=[], blockers=[blocker])) == 2
    assert len(group_pictures([a, b], token_boxes=[], line_boxes=[])) == 1


# ---------------------------------------------------------------- R6: validated underlines
def _ul_case(rule_x0, rule_x1, rule_y=110.5, colour=(0, 0, 0), suspicious=False, second_line=False):
    from pdf2word.analyze.classify import find_underlines
    from pdf2word.extract.backend import RawPage, RawPath, RawSegment
    from pdf2word.ir import PathItem, PathKind
    toks = {}
    a = tok(0, 100, "1", suspicious=False); b = tok(1, 105, "64", suspicious=suspicious)   # "1" + "64" (5 pt per char)
    for t in (a, b):
        toks[t.id] = t
    lines = [Line(id="p1:l0", bbox=(100, 100, 115, 110), baseline=108, x_height=5, token_ids=[a.id, b.id],
                  path_ids=[], protection_band=(100, 94, 115, 116))]
    if second_line:
        lines.append(Line(id="p1:l1", bbox=(100, 100, 115, 110), baseline=108.5, x_height=5, token_ids=[],
                          path_ids=[], protection_band=(100, 94, 115, 116)))
    raw = RawPage(page_no=1, width=600, height=800, rotation=0, cropbox=(0, 0, 600, 800), mediabox=(0, 0, 600, 800),
                  spans=(), images=(), paths=(RawPath(id="p1:d0", bbox=(rule_x0, rule_y, rule_x1, rule_y), paint="s", fill=None,
                                                      stroke=colour, stroke_width=0.5, even_odd=False, close_path=False,
                                                      seqno=0, segments=(RawSegment("l", ((rule_x0, rule_y), (rule_x1, rule_y))),)),))
    item = PathItem(id="p1:d0", bbox=(rule_x0, rule_y, rule_x1, rule_y), kind=PathKind.RULE, filled=False,
                    stroke_width=0.5, n_segments=1, shape_key="u", protected=True, protection_reason="band:p1:l0")
    return find_underlines(raw, toks, lines, {"p1:d0": item})


def test_underline_under_a_whole_token_is_validated() -> None:
    assert _ul_case(105, 115) == {"p1:d0": ["p1:t1"]}


@pytest.mark.parametrize("kw", [
    dict(rule_x0=108, rule_x1=115),                 # under part of "64" only
    dict(rule_x0=100, rule_x1=112),                 # covers "1" and half of "64"
    dict(rule_x0=105, rule_x1=115, colour=(1, 0, 0)),   # red rule under black text
    dict(rule_x0=105, rule_x1=115, suspicious=True),    # maths token
    dict(rule_x0=105, rule_x1=115, second_line=True),   # two lines could own it
    dict(rule_x0=105, rule_x1=115, rule_y=113),         # 5 pt below the baseline
])
def test_underline_rejected_when_association_is_ambiguous(kw) -> None:
    assert _ul_case(**kw) == {}


# ---------------------------------------------------------------- RD-7: zero-advance spaces
def test_zero_advance_space_does_not_separate_words() -> None:
    from pdf2word.analyze.tokens import build_tokens
    from pdf2word.extract.backend import RawChar, RawPage, RawSpan
    # " 7 65": each space is drawn at the position of the next digit (no advance) → the reader sees "765"
    chars = (RawChar(" ", (165.0, 100, 167.5, 110), (165.0, 108), False), RawChar("7", (165.0, 100, 169.7, 110), (165.0, 108), False),
             RawChar(" ", (169.7, 100, 172.2, 110), (169.7, 108), False), RawChar("6", (169.7, 100, 175.2, 110), (169.7, 108), False),
             RawChar("5", (175.2, 100, 180.0, 110), (175.2, 108), False), RawChar(" ", (180.0, 100, 182.5, 110), (180.0, 108), False))
    span = RawSpan(id="p1:s0", block_no=0, line_no=0, span_no=0, text="".join(c.c for c in chars), bbox=(165, 100, 182.5, 110),
                   origin=(165, 108), font_name="Merriweather", font_name_raw="Merriweather", size=8.25, flags=0, color=0,
                   ascender=0.9, descender=-0.2, line_bbox=(165, 100, 182.5, 110), line_dir=(1, 0), line_wmode=0, chars=chars)
    page = RawPage(page_no=1, width=600, height=800, rotation=0, cropbox=(0, 0, 600, 800), mediabox=(0, 0, 600, 800),
                   spans=(span,), paths=(), images=())
    toks = list(build_tokens(page).values())
    assert [t.raw_text for t in toks] == ["7", "65"]          # characters unchanged, split as before
    assert toks[0].ws_before == "" and toks[1].ws_before == ""    # but the zero-advance spaces separate nothing
    assert toks[1].ws_after_span_end == " "                       # a trailing space with nothing after it is kept

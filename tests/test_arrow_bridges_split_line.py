"""An arrow joining two equations (e.g. "2 + 5 = 7  ->  5 + 2 = 7") sits in the wide gap
build_lines() splits one source row into two Line objects at (lines.py: a row is cut wherever a
horizontal gap exceeds ~18-27pt - exactly the gap a centred connector glyph like this creates).
The arrow then touched neither Line and was promoted to its own standalone picture, breaking the
equation onto its own paragraph mid-sentence (the defect fixed here) instead of staying inline."""

from __future__ import annotations

from pdf2word.analyze.figures import Cluster, classify_cluster
from pdf2word.ir import Line, PathItem, PathKind


def _line(lid: str, x0: float, x1: float, y0: float = 100.0, y1: float = 108.0) -> Line:
    return Line(id=lid, bbox=(x0, y0, x1, y1), baseline=y1 - 2.0, x_height=4.0,
                token_ids=[], path_ids=[], protection_band=(x0, y0 - 5, x1, y1 + 5))


def _arrow_cluster(x0: float, x1: float, y0: float = 101.0, y1: float = 105.0) -> Cluster:
    return Cluster(path_ids=["d0"], image_ids=[], bbox=(x0, y0, x1, y1))


def test_arrow_between_two_line_halves_is_inline_not_a_picture():
    left = _line("l0", 40.0, 90.0)
    right = _line("l1", 115.0, 170.0)
    c = _arrow_cluster(96.0, 109.0)      # 7pt gap from each line - an ordinary word gap
    paths = {"d0": PathItem(id="d0", bbox=c.bbox, kind=PathKind.GLYPH, filled=True,
                            stroke_width=None, n_segments=6, shape_key="arrow")}
    classify_cluster(c, paths, [left, right], line_h=10.0)
    assert c.kind == "inline"
    assert c.lines == ["l0"]             # the nearer of the two flanking segments


def test_a_truly_isolated_glyph_is_not_bridged():
    # no line on one side at all - must not be swept into a figure/display zone either way, but
    # specifically must not become "inline" by a false bridge match
    only = _line("l0", 40.0, 90.0)
    c = _arrow_cluster(96.0, 109.0)
    paths = {"d0": PathItem(id="d0", bbox=c.bbox, kind=PathKind.GLYPH, filled=True,
                            stroke_width=None, n_segments=6, shape_key="arrow")}
    classify_cluster(c, paths, [only], line_h=10.0)
    assert c.kind != "inline"


def test_a_genuinely_distant_glyph_is_not_bridged():
    # far enough from both line segments that it reads as a real standalone picture, not a
    # connector between them (e.g. a small diagram sitting between two paragraphs)
    left = _line("l0", 40.0, 90.0)
    right = _line("l1", 180.0, 230.0)
    c = _arrow_cluster(120.0, 150.0)     # 30pt gaps on both sides - past BRIDGE_GAP_PT
    paths = {"d0": PathItem(id="d0", bbox=c.bbox, kind=PathKind.GLYPH, filled=True,
                            stroke_width=None, n_segments=6, shape_key="arrow")}
    classify_cluster(c, paths, [left, right], line_h=10.0)
    assert c.kind != "inline"

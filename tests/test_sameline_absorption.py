"""A narrow decoration behind only the first few words of a bulleted line (e.g. a left-anchored
gradient strip) left a FIGURE zone covering only PART of a source line's width. The rest of that
line, reading as ordinary text past the decoration, got cut from the middle of a visual line and
relocated elsewhere as a stray, out-of-place paragraph (the defect this fixes). The zone now extends
to the whole line it already partly covers - but never creates a new overlap with unrelated text in
doing so (the bug a first version of this fix had: growing one zone's bbox could newly reach a
different, nearby line it never otherwise touches)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from pdf2word.analyze.cleanup import Cleanup
from pdf2word.extract import create_backend
from pdf2word.ir import FateKind, ZoneKind
from pdf2word.pipeline import analyze_page


def _analyze(path):
    backend = create_backend()
    backend.open(path)
    raw = backend.read_page(1)
    skims = {1: (raw.height, backend.skim_page_text(1))}
    cleanup = Cleanup(skims)
    return analyze_page(raw, cleanup)


def _gray_pixmap() -> pymupdf.Pixmap:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
    pix.set_rect(pix.irect, (200, 200, 200))
    return pix


def _decorated_line_pdf(tmp_path, *, second_line_y: float | None = None) -> Path:
    """A narrow grey RASTER decoration (a real image, like the real defect's left-anchored gradient
    strip - a vector-filled rect would instead classify as plain "background" behind text, removed
    outright, never becoming a picture at all) under the FIRST part of a line, with text continuing
    past its right edge on the same line at an ordinary word-gap distance (not a wide artificial gap,
    which PyMuPDF's own line segmentation would read as a second, separate line)."""
    head, fontsize = "short part", 11
    head_w = pymupdf.get_text_length(head, fontsize=fontsize)
    decoration_width = head_w + 10  # covers the head phrase plus a little padding, nothing more
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=400)
    page.insert_image(pymupdf.Rect(40, 60, 40 + decoration_width, 80), pixmap=_gray_pixmap(),
                       keep_proportion=False)
    page.insert_text((45, 74), head, fontsize=fontsize)
    page.insert_text((45 + head_w + 4, 74), "continues well past the decoration", fontsize=fontsize)
    if second_line_y is not None:
        page.insert_text((45, second_line_y), "an unrelated second line far enough away", fontsize=fontsize)
    path = tmp_path / "sameline.pdf"
    doc.save(str(path))
    return path


def test_text_continuing_past_a_narrow_decoration_is_absorbed(tmp_path):
    pdf = _decorated_line_pdf(tmp_path)
    pr = _analyze(pdf)
    tail_tids = [tid for tid, t in pr.tokens.items() if "continues" in t.raw_text or "decoration" in t.raw_text]
    assert tail_tids
    zone_ids = set()
    for tid in tail_tids:
        f = pr.fates[tid]
        assert f.kind is FateKind.IN_ZONE, f"{tid} was not absorbed"
        zone_ids.add(f.target)
    assert len(zone_ids) == 1
    assert pr.zones[zone_ids.pop()].kind is ZoneKind.FIGURE


def test_absorption_never_creates_an_overlap_with_unrelated_text(tmp_path):
    # an unrelated line close enough that a careless rectangular bbox union would reach it, were the
    # safety check not in place; this must still convert without a G3 violation / page fallback
    pdf = _decorated_line_pdf(tmp_path, second_line_y=90)
    pr = _analyze(pdf)
    assert not pr.fallback
    unrelated_tids = [tid for tid, t in pr.tokens.items() if "unrelated" in t.raw_text]
    assert unrelated_tids
    for tid in unrelated_tids:
        assert pr.fates[tid].kind is FateKind.EMITTED_TEXT


def test_a_genuinely_separate_column_is_not_absorbed(tmp_path):
    # text far to the right on the same line, well beyond the gap window - a second column, not a
    # continuation of the decorated line - must stay free text
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=400)
    page.insert_image(pymupdf.Rect(40, 60, 80, 80), pixmap=_gray_pixmap(), keep_proportion=False)
    page.insert_text((45, 74), "left", fontsize=11)
    page.insert_text((400, 74), "faraway", fontsize=11)
    path = tmp_path / "column.pdf"
    doc.save(str(path))
    pr = _analyze(path)
    far_tids = [tid for tid, t in pr.tokens.items() if "faraway" in t.raw_text]
    assert far_tids
    for tid in far_tids:
        assert pr.fates[tid].kind is FateKind.EMITTED_TEXT

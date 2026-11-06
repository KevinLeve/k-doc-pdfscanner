"""A chapter-opener band (a FIGURE zone spanning nearly the full page width, at the very top of the
page) can measure a touch short of its own wrapped bullet text. The overflow line then used to leak
out as stray, oddly-styled free text right below the picture - duplicating content the picture
already rasterises (the defect this fixes: "ten, hundred, or thousand" appearing as an orphaned
fragment under the "In This Chapter" band). Scoped narrowly (near-full-width AND near-top FIGURE
zone only, whole lines only) so a genuine new paragraph elsewhere is never pulled into a picture."""

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


def _opener_pdf(tmp_path, *, band_height: float, wrap_x: float = 40, wrap_y: float = 128) -> Path:
    """A synthetic chapter opener: a near-full-width filled band near the page top (``band_height``
    tall - short on purpose, so a second line of text sits just below it), and an ordinary paragraph
    well clear of the band, used to confirm that paragraph is never touched."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=500)
    page.draw_rect(pymupdf.Rect(5, 5, 395, 5 + band_height), color=(0, 0, 0), fill=(0, 0, 0))
    page.insert_text((40, wrap_y), "overflow line", fontsize=11)
    page.insert_text((40, 300), "An unrelated ordinary paragraph far below the band.", fontsize=11)
    path = tmp_path / "opener.pdf"
    doc.save(str(path))
    return path


def test_overflow_line_just_below_a_short_opener_band_is_absorbed(tmp_path):
    pdf = _opener_pdf(tmp_path, band_height=118, wrap_y=128)      # ~10pt gap: within the absorption window
    pr = _analyze(pdf)
    overflow_tids = [tid for tid, t in pr.tokens.items() if "overflow" in t.raw_text]
    assert overflow_tids
    for tid in overflow_tids:
        assert pr.fates[tid].kind is FateKind.IN_ZONE
        z = pr.zones[pr.fates[tid].target]
        assert z.kind is ZoneKind.FIGURE


def test_an_ordinary_paragraph_well_clear_of_the_band_is_never_touched(tmp_path):
    pdf = _opener_pdf(tmp_path, band_height=118, wrap_y=128)
    pr = _analyze(pdf)
    far_tids = [tid for tid, t in pr.tokens.items() if "unrelated" in t.raw_text]
    assert far_tids
    for tid in far_tids:
        assert pr.fates[tid].kind is FateKind.EMITTED_TEXT


def test_text_far_below_the_band_is_not_absorbed(tmp_path):
    # the same "overflow line" text, but far enough below the band that it is a real new paragraph,
    # not a wrapped continuation - must never be pulled into the picture
    pdf = _opener_pdf(tmp_path, band_height=60, wrap_y=120)        # band ends at 65; gap ~55pt
    pr = _analyze(pdf)
    overflow_tids = [tid for tid, t in pr.tokens.items() if "overflow" in t.raw_text]
    assert overflow_tids
    for tid in overflow_tids:
        assert pr.fates[tid].kind is FateKind.EMITTED_TEXT

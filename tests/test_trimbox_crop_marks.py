"""Print production marks (registration crosshairs, colour bars...) sit entirely outside the page's
own TrimBox, in the bleed margin between TrimBox and MediaBox/CropBox. They must never become a
picture in the document (the defect this fixes: a small circular mark showing up as a stray picture
in running text). ``ir.Fate.reason`` already anticipated "crop_mark" as a removal reason; this wires
it up end to end for the first time."""

from __future__ import annotations

import pymupdf
import pytest

from pdf2word.analyze.cleanup import Cleanup
from pdf2word.extract import create_backend
from pdf2word.extract.pymupdf_backend import _trimbox_in_page_space
from pdf2word.ir import FateKind, ZoneKind
from pdf2word.pipeline import analyze_page


def test_trimbox_shift_matches_the_real_document_that_exposed_the_bug():
    # the exact TrimBox/CropBox of PDFs for Equations/09_9781118533840-ch04.pdf, page 6: a crop mark
    # measured at bbox (312.5, 21.5, 326.5, 35.5) in pipeline space sits above this shifted TrimBox's
    # top edge (54) - that is how the original defect was found and diagnosed
    page = type("P", (), {})()
    page.trimbox = pymupdf.Rect(33.0, 54.0, 564.0, 720.0)
    page.cropbox = pymupdf.Rect(-21.0, 0.0, 618.0, 774.0)
    assert _trimbox_in_page_space(page) == (54.0, 54.0, 585.0, 720.0)


def _build(tmp_path, *, mediabox=None, trimbox=None, inside_rect=(50, 50, 100, 70),
          outside_rect=(5, 5, 15, 15)):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    if mediabox:
        page.set_mediabox(pymupdf.Rect(*mediabox))
    if trimbox:
        page.set_trimbox(pymupdf.Rect(*trimbox))
    # stroke-only outline, like a real registration crosshair (a small filled shape classifies as a
    # GLYPH and is protected, same as real body-text glyphs - deliberately not what this is testing)
    page.draw_rect(pymupdf.Rect(*outside_rect), color=(0, 0, 0))                       # the crop mark
    page.draw_rect(pymupdf.Rect(*inside_rect), color=(0, 0, 0), fill=(0.4, 0.4, 0.4))  # real content
    path = tmp_path / "t.pdf"
    doc.save(str(path))
    return path


def _analyze(path):
    backend = create_backend()
    backend.open(path)
    raw = backend.read_page(1)
    skims = {1: (raw.height, backend.skim_page_text(1))}
    cleanup = Cleanup(skims)
    return analyze_page(raw, cleanup)


def test_mark_outside_trimbox_is_removed_not_a_picture(tmp_path):
    pdf = _build(tmp_path, trimbox=(20, 20, 280, 280))
    pr = _analyze(pdf)
    crop_mark_ids = [eid for eid, f in pr.fates.items()
                    if f.kind is FateKind.REMOVED and f.reason == "crop_mark"]
    assert crop_mark_ids, "the outside-trimbox mark was not removed with reason crop_mark"
    # the only picture zone left is the real, in-trim content; none covers the outside-trim mark
    pic_zones = [z for z in pr.zones.values() if z.kind in (ZoneKind.FIGURE, ZoneKind.DISPLAY_MATH)]
    assert len(pic_zones) == 1
    assert all(z.bbox[0] >= 20 for z in pic_zones), "the crop mark still became a picture zone"


def test_content_inside_trimbox_is_unaffected(tmp_path):
    pdf = _build(tmp_path, trimbox=(20, 20, 280, 280))
    pr = _analyze(pdf)
    # the real (in-trim) rectangle must still become ordinary picture content, not be removed
    assert any(z.kind in (ZoneKind.FIGURE, ZoneKind.DISPLAY_MATH) for z in pr.zones.values())


def test_no_explicit_trimbox_removes_nothing_by_this_rule(tmp_path):
    # no page.set_trimbox() call: PyMuPDF falls back to the CropBox (PDF spec default), so trimbox
    # covers the whole page and this rule must never fire
    pdf = _build(tmp_path)
    pr = _analyze(pdf)
    assert not any(f.reason == "crop_mark" for f in pr.fates.values())


def test_a_marks_own_filled_centre_dot_does_not_protect_it(tmp_path):
    # the exact real-world case this guards: a real registration crosshair's small filled centre dot
    # classifies as PathKind.GLYPH on shape alone (small + filled), same as a real body-text
    # character - but no real text glyph can ever exist outside the TrimBox, so that classification
    # must not stop the mark (as a whole) from being removed. (First attempt at this fix excluded
    # ANY protected/glyph path unconditionally and regressed exactly this case.)
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    page.set_trimbox(pymupdf.Rect(20, 20, 280, 280))
    page.draw_rect(pymupdf.Rect(5, 5, 15, 15), color=(0, 0, 0))              # crosshair outline
    page.draw_rect(pymupdf.Rect(9, 9, 11, 11), color=(0, 0, 0), fill=(0, 0, 0))  # its filled centre dot
    page.draw_rect(pymupdf.Rect(50, 50, 100, 70), color=(0, 0, 0), fill=(0.4, 0.4, 0.4))
    path = tmp_path / "t.pdf"
    doc.save(str(path))
    pr = _analyze(path)
    assert sum(1 for f in pr.fates.values() if f.reason == "crop_mark") >= 2
    pic_zones = [z for z in pr.zones.values() if z.kind in (ZoneKind.FIGURE, ZoneKind.DISPLAY_MATH)]
    assert len(pic_zones) == 1 and all(z.bbox[0] >= 20 for z in pic_zones)


def test_real_text_adjacency_still_protects_an_element_outside_the_trim(tmp_path):
    # a path genuinely next to a real text line ("band:" reason - unlike the shape-only "glyph"
    # guess above) must still never be silently discarded, even if it straddles the trim edge
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    page.set_trimbox(pymupdf.Rect(20, 20, 280, 280))
    page.insert_text((10, 30), "x", fontsize=12)                    # text partly outside the trim
    page.draw_line(pymupdf.Point(8, 33), pymupdf.Point(16, 33), color=(0, 0, 0))  # its underline
    page.draw_rect(pymupdf.Rect(50, 50, 100, 70), color=(0, 0, 0), fill=(0.4, 0.4, 0.4))
    path = tmp_path / "t.pdf"
    doc.save(str(path))
    pr = _analyze(path)
    assert not any(f.reason == "crop_mark" for f in pr.fates.values())

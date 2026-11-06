"""PdfBackend contract and PyMuPDF adapter behaviour (spec §2, §4.2; D1–D3 in IMPLEMENTATION_LOG)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pdf2word.extract import create_backend
from pdf2word.extract.backend import (
    BackendError, FeatureUnavailable, PasswordRequired, PdfBackend, RawPage, SupportsDebugOverlay,
)
from pdf2word.extract.pymupdf_backend import TEXT_FLAGS, PyMuPDFBackend
from tests.conftest import sample

PNG_SIG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def backend():
    b = create_backend()
    yield b
    b.close()


def _png_size(png: bytes) -> tuple[int, int]:
    assert png[:8] == PNG_SIG
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def test_adapter_satisfies_protocols(backend) -> None:
    assert isinstance(backend, PdfBackend)
    assert isinstance(backend, SupportsDebugOverlay)
    assert isinstance(backend, PyMuPDFBackend)


def test_open_reports_docinfo(backend, basic_fixture) -> None:
    info = backend.open(basic_fixture.path)
    assert info.page_count == backend.page_count() == 1
    assert info.sha256 == hashlib.sha256(basic_fixture.path.read_bytes()).hexdigest()
    assert info.encrypted is False
    assert info.extraction_settings["text_flags"] == str(TEXT_FLAGS)


def test_text_flags_forbid_substitution() -> None:
    import pymupdf  # test-only: verifies the adapter's constant against PyMuPDF's flag bits

    for forbidden in ("TEXT_DEHYPHENATE", "TEXT_INHIBIT_SPACES", "TEXT_USE_CID_FOR_UNKNOWN_UNICODE",
                      "TEXT_USE_GID_FOR_UNKNOWN_UNICODE"):
        assert TEXT_FLAGS & getattr(pymupdf, forbidden) == 0, forbidden
    for required in ("TEXT_PRESERVE_LIGATURES", "TEXT_PRESERVE_WHITESPACE"):
        assert TEXT_FLAGS & getattr(pymupdf, required), required


def test_chars_are_delivered_verbatim(backend, basic_fixture) -> None:
    backend.open(basic_fixture.path)
    page = backend.read_page(1)
    texts = [s.text for s in page.spans]
    assert basic_fixture.ligature_text in texts          # U+FB01/U+FB02 not expanded
    assert basic_fixture.whitespace_text in texts        # double space not collapsed
    assert basic_fixture.hyphen_line_1 in texts          # line-end hyphen kept (no de-hyphenation)
    for s in page.spans:
        assert s.text == "".join(c.c for c in s.chars)
        assert all(c.glyph_id is None for c in s.chars)  # PyMuPDF 2.0 exposes no glyph id (§4.2)
        assert all(isinstance(c.synthetic, bool) for c in s.chars)
        assert not s.font_name.startswith("+") and "+" not in s.font_name[:7]


def test_paths_and_images(backend, basic_fixture) -> None:
    backend.open(basic_fixture.path)
    page = backend.read_page(1)
    fills = [p for p in page.paths if p.paint == "f"]
    strokes = [p for p in page.paths if p.paint == "s"]
    assert any(p.bbox == pytest.approx(basic_fixture.minus_rect, abs=0.01) for p in fills)
    assert any(p.bbox == pytest.approx(basic_fixture.stroke_line, abs=0.01) and p.stroke_width
               for p in strokes)
    assert {p.id for p in page.paths} == {f"p1:d{i}" for i in range(len(page.paths))}
    assert len(page.images) == 2
    a, b = page.images
    assert a.content_hash == b.content_hash                     # same image → same content hash
    assert a.bbox == pytest.approx(basic_fixture.image_rect, abs=0.01)
    raw = backend.extract_image(1, a.ref)
    assert hashlib.sha1(raw.data).hexdigest() == a.content_hash and raw.px_size == (8, 4)


def test_skim_matches_full_text(backend, basic_fixture) -> None:
    backend.open(basic_fixture.path)
    full = backend.read_page(1).spans
    skim = backend.skim_page_text(1)
    assert [s.text for s in skim] == [s.text for s in full]
    assert all(s.chars == () for s in skim)


def test_render_clip_and_overlay(backend, basic_fixture) -> None:
    backend.open(basic_fixture.path)
    w, h = _png_size(backend.render_clip(1, (0, 0, 72, 36), dpi=144))
    assert (w, h) == (144, 72)
    w, h = _png_size(backend.render_overlay(1, [((10, 10, 50, 50), (255, 0, 0))], dpi=72))
    assert (w, h) == (300, 200)
    with pytest.raises(BackendError):
        backend.render_clip(1, (5, 5, 5, 5), dpi=72)


def test_render_clip_transparent(backend, basic_fixture) -> None:
    """Release 3: transparent crops are RGBA PNGs of the same size; nothing painted → opaque RGB."""
    backend.open(basic_fixture.path)
    page = (0, 0, 300, 200)
    rgba = backend.render_clip(1, page, dpi=72, transparent=True)
    rgb = backend.render_clip(1, page, dpi=72)
    assert (rgba[25], rgb[25]) == (6, 2)                      # IHDR colour type: RGBA vs RGB
    assert _png_size(rgba) == _png_size(rgb) == (300, 200)
    assert backend.render_clip(1, page, dpi=72, transparent=True) == rgba   # deterministic
    blank = backend.render_clip(1, (0, 0, 2, 2), dpi=72, transparent=True)
    assert blank[25] == 2


def test_find_tables_is_deterministic_and_typed(backend, basic_fixture) -> None:
    backend.open(basic_fixture.path)
    assert backend.find_tables(1, (0, 0, 300, 200), "lines") == []
    with pytest.raises(BackendError):
        backend.find_tables(1, (0, 0, 300, 200), "magic")


def test_global_options_are_pinned(backend, basic_fixture) -> None:
    """D2: another package flipping MuPDF globals must not change our boxes."""
    import pymupdf

    backend.open(basic_fixture.path)
    before = backend.read_page(1)
    pymupdf.TOOLS.unset_quad_corrections(True)       # what pymupdf4llm does at import time
    pymupdf.TOOLS.set_small_glyph_heights(True)
    try:
        backend.open(basic_fixture.path)
        after = backend.read_page(1)
    finally:
        pymupdf.TOOLS.unset_quad_corrections(False)
        pymupdf.TOOLS.set_small_glyph_heights(False)
    assert after == before


def test_page_range_and_closed_document(backend, basic_fixture) -> None:
    with pytest.raises(BackendError, match="No document"):
        backend.read_page(1)
    backend.open(basic_fixture.path)
    for bad in (0, 2):
        with pytest.raises(BackendError, match="out of range"):
            backend.read_page(bad)


def test_password_and_corrupt_files(backend, synthetic) -> None:
    with pytest.raises(PasswordRequired):
        backend.open(synthetic["encrypted"])
    with pytest.raises(PasswordRequired):
        backend.open(synthetic["encrypted"], password="wrong")
    info = backend.open(synthetic["encrypted"], password="secret")
    assert info.encrypted and "confidential" in backend.read_page(1).spans[0].text
    with pytest.raises(BackendError):
        backend.open(synthetic["corrupt"])
    with pytest.raises(BackendError, match="not found"):
        backend.open(Path("does-not-exist.pdf"))


def test_inline_image_extraction_unavailable(backend, basic_fixture) -> None:
    backend.open(basic_fixture.path)
    with pytest.raises(FeatureUnavailable):
        backend.extract_image(1, 0)


# --------------------------------------------------------- real samples ----
def test_sample_ch01_p11_mixed_span_exact(backend, samples) -> None:
    """Verified anchor (spec §4.2 / TK-05): exact characters incl. NBSP, U+2014, U+2019."""
    backend.open(sample(samples, "ch01"))
    page: RawPage = backend.read_page(11)
    lines: dict[tuple[int, int], str] = {}
    for s in page.spans:
        lines[(s.block_no, s.line_no)] = lines.get((s.block_no, s.line_no), "") + s.text
    assert "into 9 — that is, what’s 9" in lines.values()


def test_sample_ch01_p6_tab_after_marker(backend, samples) -> None:
    backend.open(sample(samples, "ch01"))
    texts = [s.text for s in backend.read_page(6).spans]
    joined = "".join(texts)
    assert "Q.\t" in joined
    assert "�" not in joined


def test_sample_ch07_p7_has_vector_glyphs(backend, samples) -> None:
    backend.open(sample(samples, "ch07"))
    page = backend.read_page(7)
    assert page.width == pytest.approx(639.0) and page.height == pytest.approx(774.0)
    assert len(page.paths) > 100 and len(page.spans) > 50

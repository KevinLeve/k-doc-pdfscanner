"""MuPDF diagnostics, console silence and optional-extras behaviour (IMPLEMENTATION_LOG D16, D19)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pdf2word.extract import create_backend
from pdf2word.extract.backend import collapse_repeats

REPO = Path(__file__).resolve().parents[1]

XREF_REPAIR = ("format error: cannot recognize xref format", "trying to repair broken xref",
               "repairing PDF document")
CONTENT_ERRORS = ("syntax error: unknown keyword: 'zzBadOp'", "gstate underflow in content stream",
                  "syntax error: syntax error in content stream",
                  "encountered syntax errors; page may not be correct")


@pytest.fixture
def backend():
    b = create_backend()
    yield b
    b.close()


def _base(message: str) -> str:
    """Strip a collapse_repeats count suffix."""
    head, sep, tail = message.rpartition(" (×")
    return head if sep and tail.endswith(")") and tail[:-1].isdigit() else message


# 1 ----------------------------------------------------------------------
def test_display_settings_pinned(backend, basic_fixture) -> None:
    import pymupdf  # test-only: read the real globals

    pymupdf.TOOLS.mupdf_display_errors(True)     # PyMuPDF's own default
    pymupdf.TOOLS.mupdf_display_warnings(True)
    try:
        info = backend.open(basic_fixture.path)
        assert pymupdf.TOOLS.mupdf_display_errors() is False
        assert pymupdf.TOOLS.mupdf_display_warnings() is False
        s = info.extraction_settings
        assert (s["mupdf_display_errors"], s["mupdf_display_warnings"]) == ("False", "False")
        assert s["layout_recommendation_enabled"] == "False"
    finally:
        pymupdf.TOOLS.mupdf_display_errors(False)
        pymupdf.TOOLS.mupdf_display_warnings(False)


# 2 ----------------------------------------------------------------------
def test_open_diagnostics_preserved(backend, synthetic) -> None:
    info = backend.open(synthetic["broken_xref"])
    assert info.backend_warnings == XREF_REPAIR
    page = backend.read_page(1)                       # reading must not discard or duplicate them
    assert page.backend_warnings == ()
    assert "hello" in page.spans[0].text


# 3 ----------------------------------------------------------------------
def test_page_diagnostics_preserved_and_deduplicated(backend, synthetic) -> None:
    import pymupdf  # test-only ground truth: what MuPDF itself reports for the same operations

    pymupdf.TOOLS.mupdf_warnings(reset=True)
    doc = pymupdf.open(synthetic["broken_content"])
    p = doc[0]
    p.get_text("rawdict"); p.get_drawings(); p.get_image_info(xrefs=True)
    truth = [m for m in pymupdf.TOOLS.mupdf_warnings(reset=True).splitlines() if m]
    doc.close()

    backend.open(synthetic["broken_content"])
    got = backend.read_page(1).backend_warnings
    assert [_base(m) for m in got] == list(dict.fromkeys(truth))     # every distinct message, in order
    assert len({_base(m) for m in got}) == len(got) == len(CONTENT_ERRORS)  # each exactly once
    assert set(_base(m) for m in got) == set(CONTENT_ERRORS)
    assert all(m.endswith(f"(×{truth.count(_base(m))})") for m in got)  # repeat count retained


def test_collapse_repeats_keeps_distinct_messages() -> None:
    msgs = ["a", "b", "a", "a b", "b", "c", "a"]
    assert collapse_repeats(msgs) == ("a (×3)", "b (×2)", "a b", "c")
    assert collapse_repeats([]) == ()
    assert collapse_repeats(["x (×2)"]) == ("x (×2)",)   # pre-existing text untouched


def test_other_operations_and_failures_keep_diagnostics(backend, synthetic) -> None:
    import pymupdf

    backend.open(synthetic["broken_content"])
    backend.skim_page_text(1)
    backend.render_clip(1, (0, 0, 100, 100), 72)
    assert {_base(m) for m in backend.other_warnings()} == set(CONTENT_ERRORS)
    assert backend.other_warnings() == ()                              # drained once

    with pytest.raises(RuntimeError):                                  # failing operation
        with backend._diagnostics():
            pymupdf.open(synthetic["broken_content"])[0].get_text()
            raise RuntimeError("boom")
    assert {_base(m) for m in backend.other_warnings()} == set(CONTENT_ERRORS)


# 4 + 5 ------------------------------------------------------------------
def _probe(mode: str, tmp_path: Path, pdfs: list[Path]) -> tuple[dict, subprocess.CompletedProcess]:
    out = tmp_path / f"probe_{mode}.json"
    proc = subprocess.run([sys.executable, "-m", "tests.backend_probe", mode, str(out), *map(str, pdfs)],
                          capture_output=True, cwd=REPO)   # fd-level capture: also catches C-level writes
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return json.loads(out.read_text(encoding="utf-8")), proc


def _fixture_pdfs(synthetic: dict[str, Path]) -> list[Path]:
    return [synthetic[k] for k in ("basic", "scanned", "broken_xref", "broken_content")]


def test_no_console_output(tmp_path: Path, synthetic) -> None:
    for mode in ("normal", "absent"):
        result, proc = _probe(mode, tmp_path, _fixture_pdfs(synthetic))
        assert proc.stdout == b"" and proc.stderr == b"", (mode, proc.stdout, proc.stderr)
        files = result["files"]
        assert tuple(files["broken_xref.pdf"]["open_warnings"]) == XREF_REPAIR       # still captured
        assert {_base(m) for m in files["broken_content.pdf"]["pages"]["1"]["warnings"]} == set(CONTENT_ERRORS)


def test_extras_absent_equivalence(tmp_path: Path, synthetic) -> None:
    normal, _ = _probe("normal", tmp_path, _fixture_pdfs(synthetic))
    absent, _ = _probe("absent", tmp_path, _fixture_pdfs(synthetic))
    assert absent["loaded_extras"] == []                                 # simulation is faithful
    assert normal["files"] == absent["files"]                            # identical extraction + diagnostics


def test_extras_absent_equivalence_on_samples(tmp_path: Path, samples) -> None:
    """Same comparison on real sample pages (ch01, ch07 — both template families)."""
    pdfs = [samples[k] for k in ("ch01", "ch07") if k in samples]
    if not pdfs:
        pytest.skip("samples not present")
    normal, _ = _probe("normal", tmp_path, pdfs)
    absent, _ = _probe("absent", tmp_path, pdfs)
    assert normal["files"] == absent["files"]

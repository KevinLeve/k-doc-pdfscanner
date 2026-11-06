"""Runtime version reporting (spec §6, VR-01 / VR-03 groundwork)."""

from __future__ import annotations

import platform
from importlib import metadata

import pdf2word
from pdf2word.extract import create_backend
from pdf2word.version import collect_versions, format_versions


def test_versions_read_at_runtime() -> None:
    import pymupdf  # test-only cross-check against the library itself

    v = collect_versions(create_backend())
    assert v["app"] == pdf2word.__version__
    assert v["Python"] == platform.python_version()
    assert v["PyMuPDF"] == metadata.version("PyMuPDF")
    assert v["MuPDF"] == pymupdf.mupdf_version
    assert v["PyMuPDF bindings"] == pymupdf.pymupdf_version
    for dist in ("python-docx", "lxml", "psutil"):
        assert v[dist] == metadata.version(dist)


def test_pinned_versions_of_this_environment() -> None:
    """Spec §6 pins (verified 2026-11-02). Fails loudly if the venv drifts."""
    v = collect_versions(create_backend())
    assert (v["Python"], v["PyMuPDF"], v["MuPDF"], v["python-docx"], v["psutil"]) == (
        "3.12.10", "2.0", "1.28.5", "1.2.0", "7.2.2")


def test_format_versions() -> None:
    assert format_versions({"a": "1", "b": "2"}) == "a 1 · b 2"

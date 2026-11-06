"""PDF extraction layer. Callers obtain a backend through ``create_backend``."""

from __future__ import annotations

from pdf2word.extract.backend import PdfBackend


def create_backend(name: str = "pymupdf") -> PdfBackend:
    """Return a fresh backend instance. PyMuPDF is imported only on first use."""
    if name == "pymupdf":
        from pdf2word.extract.pymupdf_backend import PyMuPDFBackend

        return PyMuPDFBackend()
    raise ValueError(f"Unknown PDF backend {name!r}")

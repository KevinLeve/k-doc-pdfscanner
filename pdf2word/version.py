"""Runtime version reporting (spec §6, VR-01…VR-03).

All values are read at runtime — never hard-coded — from ``importlib.metadata``
and, for PyMuPDF/MuPDF, from the active backend's ``versions()``.
"""

from __future__ import annotations

import platform
from importlib import metadata

from pdf2word import __version__
from pdf2word.extract.backend import PdfBackend

#: Distribution names whose versions are always reported (besides the backend's).
REPORTED_DISTRIBUTIONS: tuple[tuple[str, str], ...] = (
    ("python-docx", "python-docx"),
    ("lxml", "lxml"),
    ("psutil", "psutil"),
)


def _dist_version(dist: str) -> str:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return "not installed"


def collect_versions(backend: PdfBackend) -> dict[str, str]:
    """Return ``{"app", "Python", "PyMuPDF", "MuPDF", ..., "python-docx", "lxml", "psutil"}``."""
    out: dict[str, str] = {"app": __version__, "Python": platform.python_version()}
    out.update(backend.versions())
    for label, dist in REPORTED_DISTRIBUTIONS:
        out[label] = _dist_version(dist)
    return out


def format_versions(versions: dict[str, str]) -> str:
    """One-line form used in report headers and the CLI."""
    return " · ".join(f"{k} {v}" for k, v in versions.items())

"""L7 import boundary and the approved-dependency rule (spec §6, PK-05 groundwork)."""

from __future__ import annotations

import ast
import subprocess
import sys
from importlib import metadata
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "pdf2word"
ALLOWED = PACKAGE / "extract" / "pymupdf_backend.py"
PDF_MODULES = {"pymupdf", "fitz"}
FORBIDDEN_DISTRIBUTIONS = ("numpy", "onnxruntime", "pymupdf4llm", "pymupdf_layout", "pymupdf-layout",
                           "Pillow", "networkx", "PyYAML", "tabulate", "opencv-python", "pdfplumber", "PySide6")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_only_the_adapter_imports_pymupdf() -> None:
    offenders = [str(p.relative_to(PACKAGE)) for p in PACKAGE.rglob("*.py")
                 if p != ALLOWED and _imported_modules(p) & PDF_MODULES]
    assert offenders == []
    assert _imported_modules(ALLOWED) & PDF_MODULES == {"pymupdf"}


def test_forbidden_distributions_not_installed() -> None:
    installed = []
    for dist in FORBIDDEN_DISTRIBUTIONS:
        try:
            metadata.version(dist)
            installed.append(dist)
        except metadata.PackageNotFoundError:
            pass
    assert installed == [], f"excluded by spec §6 but installed: {installed}"


#: Modules PyMuPDF 2.0 imports from its *bundled* optional packages at ``import pymupdf``
#: (IMPLEMENTATION_LOG D13/D17). Tolerated: plain Python code whose ``activate()`` fails
#: without onnxruntime, so no model is ever loaded. Any other extras module is a change.
TOLERATED_EXTRAS = {
    "pymupdf.layout.DocumentLayoutAnalyzer",
    "pymupdf.layout.MultiProcessWrapper",
    "pymupdf.layout.onnx",
    "pymupdf.layout.pymupdf_util",
    "pymupdf.layout.pymupdf_util_base",
    "pymupdf4llm.helpers",
}
NEVER_IMPORTED = ("numpy", "onnxruntime", "networkx", "yaml", "tabulate", "PIL", "pymupdf_layout")


def test_backend_use_loads_no_heavy_modules(basic_fixture) -> None:
    """Fresh interpreter, every backend operation: numpy/onnxruntime (and other excluded
    packages) are never imported — not even as failed/placeholder sys.modules entries —
    and the only modules from PyMuPDF's bundled extras are the tolerated, inert ones."""
    code = (
        "import json, sys; from pathlib import Path; from pdf2word.extract import create_backend\n"
        "b = create_backend(); b.open(Path(sys.argv[1])); b.read_page(1); b.skim_page_text(1)\n"
        "b.render_clip(1, (0,0,50,50), 72); b.find_tables(1, (0,0,300,200), 'lines')\n"
        "b.find_tables(1, (0,0,300,200), 'text'); b.other_warnings(); b.close()\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    out = subprocess.run([sys.executable, "-c", code, str(basic_fixture.path)], capture_output=True, text=True,
                         cwd=PACKAGE.parent, check=True)
    import json

    names = json.loads(out.stdout)
    assert [m for m in names if m.split(".")[0] in NEVER_IMPORTED] == []
    extras = {m for m in names if m.startswith(("pymupdf.layout", "pymupdf4llm"))}
    assert extras <= TOLERATED_EXTRAS, sorted(extras - TOLERATED_EXTRAS)
    assert "pymupdf.layout" not in extras  # the package itself failed to import (activate() needs onnxruntime)

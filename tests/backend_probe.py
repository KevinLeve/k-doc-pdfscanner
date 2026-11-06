"""Subprocess helper for tests (not collected by pytest).

Runs the pdf2word backend over the given PDFs and writes a JSON summary to a file,
so the caller can also assert that nothing at all was printed to stdout/stderr.

usage: python -m tests.backend_probe normal|absent <out.json> <pdf> [<pdf> ...]

``absent`` emulates genuine absence of PyMuPDF's bundled optional packages
(``pymupdf.layout``, ``pymupdf4llm``): with ``sys.modules[name] = None`` an import
raises ModuleNotFoundError and ``importlib.util.find_spec`` returns None — the same
observable behaviour as when the folders are not on disk. No installed file is touched.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

OPTIONAL_EXTRAS = ("pymupdf.layout", "pymupdf4llm")


def main(argv: list[str]) -> int:
    mode, out, pdfs = argv[0], Path(argv[1]), [Path(p) for p in argv[2:]]
    if mode == "absent":
        for name in OPTIONAL_EXTRAS:
            sys.modules[name] = None  # type: ignore[assignment]
    elif mode != "normal":
        raise SystemExit(f"unknown mode {mode!r}")
    before = set(sys.modules)

    from pdf2word.extract import create_backend
    from pdf2word.ir import to_json

    result: dict[str, object] = {"mode": mode, "files": {}}
    backend = create_backend()
    for pdf in pdfs:
        info = backend.open(pdf)
        pages = {}
        for n in range(1, info.page_count + 1):
            raw = backend.read_page(n)
            pages[str(n)] = {"digest": hashlib.sha1(to_json(raw).encode("utf-8")).hexdigest(),
                             "warnings": list(raw.backend_warnings)}
        width, height = raw.width, raw.height
        result["files"][pdf.name] = {
            "open_warnings": list(info.backend_warnings),
            "pages": pages,
            "skim": [s.text for s in backend.skim_page_text(1)],
            "render_sha1": hashlib.sha1(backend.render_clip(1, (0, 0, min(width, 200), min(height, 200)), 72)).hexdigest(),
            "tables_lines": [(t.n_rows, t.n_cols) for t in backend.find_tables(1, (0, 0, width, height), "lines")],
            "tables_text": [(t.n_rows, t.n_cols) for t in backend.find_tables(1, (0, 0, width, height), "text")],
            "other_warnings": list(backend.other_warnings()),
        }
        backend.close()
    new = set(sys.modules) - before
    result["loaded_extras"] = sorted(m for m in new if sys.modules.get(m) is not None
                                     and m.startswith(OPTIONAL_EXTRAS))
    result["heavy_modules"] = sorted(m for m in sys.modules
                                     if m.split(".")[0] in ("numpy", "onnxruntime"))
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

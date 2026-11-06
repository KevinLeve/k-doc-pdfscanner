"""G7: the written DOCX is a valid package that python-docx reopens with the expected content.

This is an automated structural check only. It is not Microsoft Word verification
(that is done separately by tools/word_regression.ps1 and reported under its own label).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DRAWING = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"


def check_docx(path: Path, expected_pictures: int, expected_paragraphs: int) -> list[str]:
    v: list[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad:
                v.append(f"G7: corrupt zip member {bad}")
            names = set(z.namelist())
            for required in ("[Content_Types].xml", "word/document.xml", "word/styles.xml",
                             "word/_rels/document.xml.rels"):
                if required not in names:
                    v.append(f"G7: missing part {required}")
            if v:
                return v
            doc = etree.fromstring(z.read("word/document.xml"))
            rels = etree.fromstring(z.read("word/_rels/document.xml.rels"))
            etree.fromstring(z.read("word/styles.xml"))
    except (zipfile.BadZipFile, etree.XMLSyntaxError, OSError) as exc:
        return [f"G7: cannot read package: {exc}"]
    targets = {r.get("Id"): r.get("Target") for r in rels}
    blips = doc.iter(DRAWING)
    n_pics = 0
    for b in blips:
        n_pics += 1
        rid = b.get(R_EMBED)
        target = targets.get(rid)
        if target is None:
            v.append(f"G7: picture relationship {rid} missing")
        elif "word/" + target.lstrip("/") not in names and target.lstrip("/") not in names:
            v.append(f"G7: picture target {target} missing from package")
    if n_pics != expected_pictures:
        v.append(f"G7: {n_pics} pictures in document.xml, expected {expected_pictures}")
    try:
        from docx import Document
        d = Document(str(path))
        n_par = len(d.paragraphs)
        if n_par != expected_paragraphs:
            v.append(f"G7: python-docx reopened {n_par} paragraphs, expected {expected_paragraphs}")
        if len(d.inline_shapes) != expected_pictures:
            v.append(f"G7: python-docx sees {len(d.inline_shapes)} inline shapes, expected {expected_pictures}")
    except Exception as exc:     # noqa: BLE001 — any reopen failure is a G7 violation
        v.append(f"G7: python-docx cannot reopen: {exc}")
    return v

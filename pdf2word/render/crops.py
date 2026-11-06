"""Zone crops rendered from the source page (spec §4.3.1, D1/D3: 1:1 size, 300 dpi, no shrinking)."""

from __future__ import annotations

import hashlib

from pdf2word.extract.backend import PdfBackend
from pdf2word.ir import BBox


def clamp(bbox: BBox, width: float, height: float) -> tuple[BBox, bool]:
    c = (max(0.0, bbox[0]), max(0.0, bbox[1]), min(width, bbox[2]), min(height, bbox[3]))
    return c, c != tuple(bbox)


def render(backend: PdfBackend, page_no: int, bbox: BBox, dpi: int,
           transparent: bool = False) -> tuple[bytes, str]:
    png = backend.render_clip(page_no, bbox, dpi, transparent=transparent)
    return png, hashlib.sha256(png).hexdigest()

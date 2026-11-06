"""PDF backend interface — implementation-spec v1.4 §2 (L7).

``PdfBackend`` is the complete contract a PDF library adapter must satisfy.
Only ``pymupdf_backend.py`` may import PyMuPDF; everything downstream consumes
the plain, immutable ``Raw*`` records defined here.

Extraction contract (§4.2): characters are delivered exactly as the PDF maps
them — no ligature expansion, no whitespace collapsing, no de-hyphenation,
no substitution — in backend extraction order. ``glyph_id`` is set only if a
backend exposes a genuine glyph id field (PyMuPDF 2.0 does not).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from pdf2word.ir import BBox

Point = tuple[float, float]
Color = tuple[float, ...]


class BackendError(Exception):
    """Any failure reported by a PDF backend (open, read, render)."""


class PasswordRequired(BackendError):
    """The PDF is encrypted and needs a password that was not supplied (FT-01)."""


class FeatureUnavailable(BackendError):
    """An optional backend capability (e.g. ``find_tables``) is not available."""


@dataclass(frozen=True)
class DocInfo:
    path: Path
    sha256: str
    page_count: int
    encrypted: bool                 # document uses encryption (possibly without a user password)
    metadata: dict[str, str]        # producer, creator, format, ... (empty values dropped)
    extraction_settings: dict[str, str]  # exact backend settings in force (recorded in dumps/bench)
    backend_warnings: tuple[str, ...] = ()  # diagnostics emitted while opening (e.g. xref repair), D19


@dataclass(frozen=True)
class RawChar:
    c: str
    bbox: BBox
    origin: Point
    synthetic: bool
    glyph_id: int | None = None


@dataclass(frozen=True)
class RawSpan:
    id: str                         # "p{page}:s{n}", n = running span index on the page
    block_no: int
    line_no: int                    # index of the line within its block
    span_no: int                    # index of the span within its line
    text: str                       # "".join(c.c for c in chars) — or the span text in skim mode
    bbox: BBox
    origin: Point
    font_name: str                  # subset prefix ("ABCDEF+") stripped
    font_name_raw: str
    size: float
    flags: int                      # backend span flags as delivered
    color: int                      # 0xRRGGBB
    ascender: float
    descender: float
    line_bbox: BBox
    line_dir: Point
    line_wmode: int
    chars: tuple[RawChar, ...] = ()  # empty in skim mode


@dataclass(frozen=True)
class RawSegment:
    op: str                         # "l" line, "c" cubic bezier, "re" rectangle, "qu" quad
    points: tuple[Point, ...]       # l: 2 pts, c: 4 pts, re: (x0,y0),(x1,y1), qu: 4 corners


@dataclass(frozen=True)
class RawPath:
    id: str                         # "p{page}:d{n}"
    bbox: BBox
    paint: str                      # "f" fill, "s" stroke, "fs" fill+stroke
    fill: Color | None
    stroke: Color | None
    stroke_width: float | None
    even_odd: bool
    close_path: bool
    seqno: int                      # painting order on the page
    segments: tuple[RawSegment, ...]


@dataclass(frozen=True)
class RawImagePlacement:
    id: str                         # "p{page}:i{n}"
    ref: int                        # backend image reference (xref)
    bbox: BBox                      # placement on the page
    transform: tuple[float, float, float, float, float, float]
    px_size: tuple[int, int]
    ext: str
    content_hash: str               # sha1 of the image stream returned by extract_image


@dataclass(frozen=True)
class RawPage:
    page_no: int                    # 1-based
    width: float
    height: float
    rotation: int
    cropbox: BBox
    mediabox: BBox
    spans: tuple[RawSpan, ...]
    paths: tuple[RawPath, ...]
    images: tuple[RawImagePlacement, ...]
    backend_warnings: tuple[str, ...] = field(default=())
    trimbox: BBox | None = None     # the page's own TrimBox, in the same (cropbox-relative) coordinate
                                     # system as spans/paths/images; None when the PDF sets none (then
                                     # it equals the CropBox, i.e. nothing is outside it to remove)


@dataclass(frozen=True)
class RawImage:
    ref: int
    ext: str
    data: bytes                     # original stream bytes as stored/decodable by the backend
    px_size: tuple[int, int]
    colorspace: str
    smask: int                      # 0 if none


@dataclass(frozen=True)
class RawTable:
    bbox: BBox
    n_rows: int
    n_cols: int
    cells: tuple[tuple[str | None, ...], ...]       # row-major text, None for merged/empty slots
    cell_bboxes: tuple[tuple[BBox | None, ...], ...]
    strategy: str


def collapse_repeats(messages: list[str]) -> tuple[str, ...]:
    """Collapse exact repeats of diagnostic lines, keeping first-seen order (D19).

    Only byte-identical lines are merged; every distinct message is kept. A line
    seen n > 1 times becomes ``"<line> (×n)"``. Example: one page whose content
    stream is interpreted three times (text, paths, images) yields each MuPDF
    warning once, marked ``(×3)``.
    """
    counts: dict[str, int] = {}
    for m in messages:
        counts[m] = counts.get(m, 0) + 1
    return tuple(m if n == 1 else f"{m} (×{n})" for m, n in counts.items())


@runtime_checkable
class PdfBackend(Protocol):
    """Contract from spec §2. ``find_tables`` is optional (may raise FeatureUnavailable)."""

    def open(self, path: Path, password: str | None = None) -> DocInfo: ...

    def versions(self) -> dict[str, str]: ...

    def page_count(self) -> int: ...

    def read_page(self, page_no: int) -> RawPage: ...

    def skim_page_text(self, page_no: int) -> list[RawSpan]: ...

    def extract_image(self, page_no: int, ref: int) -> RawImage: ...

    def render_clip(self, page_no: int, bbox: BBox, dpi: int, transparent: bool = False) -> bytes: ...

    def find_tables(self, page_no: int, clip: BBox, strategy: str) -> list[RawTable]: ...

    def close(self) -> None: ...


@runtime_checkable
class SupportsDebugOverlay(Protocol):
    """Optional debug capability used only by ``pdf2word dump`` (IMPLEMENTATION_LOG D5)."""

    def render_overlay(
        self, page_no: int, boxes: list[tuple[BBox, tuple[int, int, int]]], dpi: int
    ) -> bytes: ...

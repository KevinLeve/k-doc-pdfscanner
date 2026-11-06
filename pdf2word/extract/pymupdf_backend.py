"""PyMuPDF adapter — the ONLY module in pdf2word that imports PyMuPDF (spec L7).

Verified behaviour of PyMuPDF 2.0 / MuPDF 1.28.5 that this adapter pins down
(see IMPLEMENTATION_LOG.md D1–D4):

* Text flags are set explicitly instead of using PyMuPDF's default (199):
  ligatures and whitespace preserved, no de-hyphenation, and — unlike the
  default — **no** ``TEXT_USE_CID_FOR_UNKNOWN_UNICODE`` and no
  ``TEXT_USE_GID_FOR_UNKNOWN_UNICODE``. Both write glyph/CID numbers into the
  character value, which §4.2 forbids; unmapped glyphs must surface as U+FFFD.
* MuPDF global options that other packages (pymupdf4llm) change at import
  time are reset on every ``open``: quad corrections enabled, small glyph
  heights disabled. Character boxes therefore do not depend on what else
  happens to be installed.
* ``find_tables`` is always called with ``use_layout=False`` — the
  deterministic line/text finder. The layout path would run an ONNX model.
* MuPDF console output is switched off, but no diagnostic is lost (D19):
  every operation drains MuPDF's message store. Messages raised while opening
  go to ``DocInfo.backend_warnings``, those of ``read_page`` to
  ``RawPage.backend_warnings`` (exact repeats collapsed, distinct lines kept),
  and those of all other operations — or of a failed one — are kept until
  ``other_warnings()`` is called. The "install pymupdf_layout" stdout hint is
  disabled (D16).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import metadata
from pathlib import Path

import pymupdf

from pdf2word.extract.backend import (
    BackendError,
    DocInfo,
    FeatureUnavailable,
    PasswordRequired,
    RawChar,
    RawImage,
    RawImagePlacement,
    RawPage,
    RawPath,
    RawSegment,
    RawSpan,
    RawTable,
    collapse_repeats,
)
from pdf2word.ir import BBox

TEXT_FLAGS: int = (
    pymupdf.TEXT_PRESERVE_LIGATURES
    | pymupdf.TEXT_PRESERVE_WHITESPACE
    | pymupdf.TEXT_MEDIABOX_CLIP
)
"""= 67. Deliberately excludes DEHYPHENATE, INHIBIT_SPACES, PRESERVE_IMAGES
(images come from get_image_info) and the CID/GID-for-unknown-Unicode flags."""

_FORBIDDEN_TEXT_FLAGS = (
    pymupdf.TEXT_DEHYPHENATE
    | pymupdf.TEXT_INHIBIT_SPACES
    | pymupdf.TEXT_USE_CID_FOR_UNKNOWN_UNICODE
    | pymupdf.TEXT_USE_GID_FOR_UNKNOWN_UNICODE
)
assert TEXT_FLAGS & _FORBIDDEN_TEXT_FLAGS == 0

TABLE_STRATEGIES = ("lines", "lines_strict", "text")
_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


def _bbox(r) -> BBox:
    return (float(r[0]), float(r[1]), float(r[2]), float(r[3]))


def _trimbox_in_page_space(page) -> BBox:
    """``page.trimbox`` (native PDF coordinates) shifted into the same (0,0)-origin space as
    ``page.rect`` and everything get_text()/get_drawings()/get_image_info() return — i.e. by the
    CropBox's own origin, since ``page.rect == page.cropbox`` translated to start at (0, 0). When a
    PDF sets no explicit TrimBox, PyMuPDF falls back to the CropBox (PDF spec default), so this then
    equals the full page and nothing is treated as outside it."""
    t, c = page.trimbox, page.cropbox
    return (t.x0 - c.x0, t.y0 - c.y0, t.x1 - c.x0, t.y1 - c.y0)


def _pt(p) -> tuple[float, float]:
    return (float(p[0]), float(p[1]))


def _color(c) -> tuple[float, ...] | None:
    return None if c is None else tuple(float(v) for v in c)


def _pin_global_options() -> None:
    """Reset MuPDF globals that third-party imports are known to change (D2)."""
    pymupdf.TOOLS.unset_quad_corrections(False)
    pymupdf.TOOLS.set_small_glyph_heights(False)
    pymupdf.TOOLS.mupdf_display_errors(False)   # messages still reach the store (D19)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    pymupdf.no_recommend_layout()               # no stdout hint when pymupdf.layout is absent (D16)


def _drain_store() -> list[str]:
    """Return and clear MuPDF's accumulated warning/error messages."""
    return [line for line in pymupdf.TOOLS.mupdf_warnings(reset=True).splitlines() if line]


class PyMuPDFBackend:
    """``PdfBackend`` implementation on PyMuPDF. One open document per instance."""

    name = "pymupdf"

    def __init__(self) -> None:
        self._doc: pymupdf.Document | None = None
        self._info: DocInfo | None = None
        self._image_cache: dict[int, RawImage] = {}
        self._other_warnings: list[str] = []

    @contextmanager
    def _diagnostics(self) -> Iterator[list[str]]:
        """Collect the MuPDF messages produced inside the block into the yielded list.

        Messages already pending when the block starts were produced elsewhere and are
        kept for ``other_warnings()``. If the block raises, its own messages are kept
        there too, so a failed operation never loses diagnostics.
        """
        self._other_warnings.extend(_drain_store())
        own: list[str] = []
        try:
            yield own
        except BaseException:
            self._other_warnings.extend(_drain_store())
            raise
        own.extend(_drain_store())

    def other_warnings(self) -> tuple[str, ...]:
        """Return (and clear) diagnostics not attached to a DocInfo or RawPage."""
        self._other_warnings.extend(_drain_store())
        out = collapse_repeats(self._other_warnings)
        self._other_warnings.clear()
        return out

    # ------------------------------------------------------------ lifecycle
    def open(self, path: Path, password: str | None = None) -> DocInfo:
        self.close()
        path = Path(path)
        if not path.is_file():
            raise BackendError(f"File not found: {path}")
        _pin_global_options()
        sha = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                sha.update(chunk)
        with self._diagnostics() as open_messages:
            try:
                doc = pymupdf.open(path)
            except Exception as exc:  # FileDataError, EmptyFileError, RuntimeError ...
                raise BackendError(f"Cannot open PDF {path.name}: {exc}") from exc
            if not doc.is_pdf:
                doc.close()
                raise BackendError(f"Not a PDF file: {path.name}")
            encrypted = bool(doc.is_encrypted or doc.metadata.get("encryption"))
            if doc.needs_pass:
                if password is None or not doc.authenticate(password):
                    doc.close()
                    raise PasswordRequired(f"This PDF is password-protected: {path.name}")
            page_count = doc.page_count  # any xref repair happens by now, so it belongs to open
        self._doc = doc
        meta = {k: str(v) for k, v in (doc.metadata or {}).items() if v}
        self._info = DocInfo(
            path=path,
            sha256=sha.hexdigest(),
            page_count=page_count,
            encrypted=encrypted,
            metadata=meta,
            extraction_settings=self.extraction_settings(),
            backend_warnings=collapse_repeats(open_messages),
        )
        return self._info

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
        self._doc = None
        self._info = None
        self._image_cache.clear()

    # ------------------------------------------------------------- queries
    def versions(self) -> dict[str, str]:
        return {
            "PyMuPDF": metadata.version("PyMuPDF"),
            "MuPDF": str(pymupdf.mupdf_version),
            "PyMuPDF bindings": str(pymupdf.pymupdf_version),
        }

    @staticmethod
    def extraction_settings() -> dict[str, str]:
        return {
            "text_flags": str(TEXT_FLAGS),
            "quad_corrections_unset": str(bool(pymupdf.TOOLS.unset_quad_corrections())),
            "small_glyph_heights": str(bool(pymupdf.TOOLS.set_small_glyph_heights())),
            "mupdf_display_errors": str(bool(pymupdf.TOOLS.mupdf_display_errors())),
            "mupdf_display_warnings": str(bool(pymupdf.TOOLS.mupdf_display_warnings())),
            "layout_recommendation_enabled": str(bool(pymupdf._recommend_layout)),
            "find_tables_use_layout": "False",
        }

    def page_count(self) -> int:
        return self._require_doc().page_count

    # ---------------------------------------------------------- extraction
    def read_page(self, page_no: int) -> RawPage:
        with self._diagnostics() as page_messages:
            page = self._page(page_no)
            try:
                raw = page.get_text("rawdict", flags=TEXT_FLAGS)
                spans = self._spans(page_no, raw, with_chars=True)
                paths = self._paths(page_no, page)
                images = self._images(page_no, page)
            except BackendError:
                raise
            except Exception as exc:
                raise BackendError(f"Page {page_no}: extraction failed: {exc}") from exc
        return RawPage(
            page_no=page_no,
            width=float(page.rect.width),
            height=float(page.rect.height),
            rotation=int(page.rotation),
            cropbox=_bbox(page.cropbox),
            mediabox=_bbox(page.mediabox),
            spans=spans,
            paths=paths,
            images=images,
            backend_warnings=collapse_repeats(page_messages),
            trimbox=_trimbox_in_page_space(page),
        )

    def skim_page_text(self, page_no: int) -> list[RawSpan]:
        with self._diagnostics() as messages:
            page = self._page(page_no)
            try:
                d = page.get_text("dict", flags=TEXT_FLAGS)
            except Exception as exc:
                raise BackendError(f"Page {page_no}: text skim failed: {exc}") from exc
        self._other_warnings.extend(messages)
        return list(self._spans(page_no, d, with_chars=False))

    def extract_image(self, page_no: int, ref: int) -> RawImage:
        self._page(page_no)  # validates page number and open document
        if ref <= 0:
            raise FeatureUnavailable(f"Page {page_no}: inline image (ref {ref}) has no extractable stream")
        if ref not in self._image_cache:
            try:
                d = self._require_doc().extract_image(ref)
            except Exception as exc:
                raise BackendError(f"Image {ref}: extraction failed: {exc}") from exc
            if not d:
                raise BackendError(f"Image {ref}: not an image")
            self._image_cache[ref] = RawImage(
                ref=ref,
                ext=str(d.get("ext", "")),
                data=bytes(d["image"]),
                px_size=(int(d.get("width", 0)), int(d.get("height", 0))),
                colorspace=str(d.get("cs-name", "")),
                smask=int(d.get("smask", 0) or 0),
            )
        return self._image_cache[ref]

    def render_clip(self, page_no: int, bbox: BBox, dpi: int, transparent: bool = False) -> bytes:
        """PNG of ``bbox``. ``transparent=True`` renders onto an empty (alpha 0) canvas instead of white,
        so only what the PDF actually paints is opaque — white fills drawn by the PDF stay white and
        anti-aliased edges keep partial alpha. A crop in which nothing is painted falls back to the
        opaque render (it would otherwise be invisible)."""
        page = self._page(page_no)
        if dpi <= 0:
            raise BackendError(f"dpi must be positive, got {dpi}")
        clip = pymupdf.Rect(bbox)
        if clip.is_empty:
            raise BackendError(f"Page {page_no}: empty clip {bbox}")
        with self._diagnostics() as messages:
            try:
                png = None
                if transparent:
                    pix = page.get_pixmap(dpi=dpi, clip=clip, alpha=True)
                    alpha = pix.samples[pix.n - 1::pix.n]
                    if alpha.count(0) < len(alpha):
                        png = pix.tobytes("png")
                if png is None:
                    png = page.get_pixmap(dpi=dpi, clip=clip, alpha=False).tobytes("png")
            except Exception as exc:
                raise BackendError(f"Page {page_no}: render failed: {exc}") from exc
        self._other_warnings.extend(messages)
        return png

    def render_overlay(
        self, page_no: int, boxes: list[tuple[BBox, tuple[int, int, int]]], dpi: int
    ) -> bytes:
        """Debug only: full page at ``dpi`` with 1-px box outlines (D5)."""
        page = self._page(page_no)
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        scale = dpi / 72.0
        ox, oy = page.rect.x0, page.rect.y0
        for bbox, rgb in boxes:
            x0 = int((bbox[0] - ox) * scale)
            y0 = int((bbox[1] - oy) * scale)
            x1 = max(x0 + 1, int(round((bbox[2] - ox) * scale)))
            y1 = max(y0 + 1, int(round((bbox[3] - oy) * scale)))
            for edge in ((x0, y0, x1, y0 + 1), (x0, y1 - 1, x1, y1), (x0, y0, x0 + 1, y1), (x1 - 1, y0, x1, y1)):
                pix.set_rect(pymupdf.IRect(edge), rgb)
        return pix.tobytes("png")

    def find_tables(self, page_no: int, clip: BBox, strategy: str) -> list[RawTable]:
        if strategy not in TABLE_STRATEGIES:
            raise BackendError(f"Unknown table strategy {strategy!r}; use one of {TABLE_STRATEGIES}")
        page = self._page(page_no)
        with self._diagnostics() as messages:
            try:
                found = page.find_tables(clip=pymupdf.Rect(clip), strategy=strategy, use_layout=False)
            except Exception as exc:
                raise BackendError(f"Page {page_no}: find_tables failed: {exc}") from exc
            finally:
                _pin_global_options()
        self._other_warnings.extend(messages)
        tables: list[RawTable] = []
        for t in found.tables:
            texts = tuple(tuple(c for c in row) for row in t.extract())
            boxes = tuple(
                tuple(None if c is None else _bbox(c) for c in row.cells) for row in t.rows
            )
            tables.append(
                RawTable(
                    bbox=_bbox(t.bbox),
                    n_rows=int(t.row_count),
                    n_cols=int(t.col_count),
                    cells=texts,
                    cell_bboxes=boxes,
                    strategy=strategy,
                )
            )
        return tables

    # ------------------------------------------------------------- helpers
    def _require_doc(self) -> pymupdf.Document:
        if self._doc is None:
            raise BackendError("No document is open")
        return self._doc

    def _page(self, page_no: int) -> pymupdf.Page:
        doc = self._require_doc()
        if not 1 <= page_no <= doc.page_count:
            raise BackendError(f"Page {page_no} out of range 1..{doc.page_count}")
        try:
            return doc.load_page(page_no - 1)
        except Exception as exc:
            raise BackendError(f"Page {page_no}: cannot load: {exc}") from exc

    @staticmethod
    def _spans(page_no: int, d: dict, *, with_chars: bool) -> tuple[RawSpan, ...]:
        out: list[RawSpan] = []
        n = 0
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for li, line in enumerate(block.get("lines", [])):
                for si, s in enumerate(line.get("spans", [])):
                    chars: tuple[RawChar, ...] = ()
                    if with_chars:
                        chars = tuple(
                            RawChar(
                                c=ch["c"],
                                bbox=_bbox(ch["bbox"]),
                                origin=_pt(ch["origin"]),
                                synthetic=bool(ch.get("synthetic", False)),
                            )
                            for ch in s.get("chars", [])
                        )
                        text = "".join(ch.c for ch in chars)
                    else:
                        text = s.get("text", "")
                    raw_font = str(s.get("font", ""))
                    out.append(
                        RawSpan(
                            id=f"p{page_no}:s{n}",
                            block_no=int(block.get("number", 0)),
                            line_no=li,
                            span_no=si,
                            text=text,
                            bbox=_bbox(s["bbox"]),
                            origin=_pt(s["origin"]),
                            font_name=_SUBSET_PREFIX.sub("", raw_font),
                            font_name_raw=raw_font,
                            size=float(s.get("size", 0.0)),
                            flags=int(s.get("flags", 0)),
                            color=int(s.get("color", 0)),
                            ascender=float(s.get("ascender", 0.0)),
                            descender=float(s.get("descender", 0.0)),
                            line_bbox=_bbox(line["bbox"]),
                            line_dir=_pt(line.get("dir", (1.0, 0.0))),
                            line_wmode=int(line.get("wmode", 0)),
                            chars=chars,
                        )
                    )
                    n += 1
        return tuple(out)

    @staticmethod
    def _paths(page_no: int, page: pymupdf.Page) -> tuple[RawPath, ...]:
        out: list[RawPath] = []
        for n, d in enumerate(page.get_drawings()):
            segs: list[RawSegment] = []
            for item in d.get("items", []):
                op = item[0]
                if op == "l":
                    pts = (_pt(item[1]), _pt(item[2]))
                elif op == "c":
                    pts = (_pt(item[1]), _pt(item[2]), _pt(item[3]), _pt(item[4]))
                elif op == "re":
                    r = item[1]
                    pts = ((float(r.x0), float(r.y0)), (float(r.x1), float(r.y1)))
                elif op == "qu":
                    q = item[1]
                    pts = (_pt(q.ul), _pt(q.ur), _pt(q.lr), _pt(q.ll))
                else:
                    raise BackendError(f"Page {page_no}: unknown path operator {op!r}")
                segs.append(RawSegment(op=op, points=pts))
            width = d.get("width")
            out.append(
                RawPath(
                    id=f"p{page_no}:d{n}",
                    bbox=_bbox(d["rect"]),
                    paint=str(d.get("type", "")),
                    fill=_color(d.get("fill")),
                    stroke=_color(d.get("color")),
                    stroke_width=None if width is None else float(width),
                    even_odd=bool(d.get("even_odd", False)),
                    close_path=bool(d.get("closePath", False)),
                    seqno=int(d.get("seqno", n)),
                    segments=tuple(segs),
                )
            )
        return tuple(out)

    def _images(self, page_no: int, page: pymupdf.Page) -> tuple[RawImagePlacement, ...]:
        infos = page.get_image_info(xrefs=True)
        if any(int(i.get("xref", 0)) == 0 for i in infos):
            infos = page.get_image_info(xrefs=True, hashes=True)
        out: list[RawImagePlacement] = []
        for n, info in enumerate(infos):
            ref = int(info.get("xref", 0))
            if ref > 0:
                img = self.extract_image(page_no, ref)
                content_hash = hashlib.sha1(img.data).hexdigest()
                ext = img.ext
            else:  # inline image: no stream reference; hash MuPDF's pixel digest
                content_hash = "inline-md5:" + bytes(info.get("digest", b"")).hex()
                ext = "inline"
            out.append(
                RawImagePlacement(
                    id=f"p{page_no}:i{n}",
                    ref=ref,
                    bbox=_bbox(info["bbox"]),
                    transform=tuple(float(v) for v in info.get("transform", (1, 0, 0, 1, 0, 0))),
                    px_size=(int(info.get("width", 0)), int(info.get("height", 0))),
                    ext=ext,
                    content_hash=content_hash,
                )
            )
        return tuple(out)

"""Document inspection (spec §2): open checks, page count, scanned-page detection,
font fingerprint. Uses only the ``PdfBackend`` interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pdf2word.config import DEFAULTS, Thresholds
from pdf2word.extract.backend import DocInfo, PdfBackend


@dataclass(frozen=True)
class PageSummary:
    page_no: int
    n_chars: int                    # non-whitespace characters in the text skim
    image_area_ratio: float         # union-free sum of image placement areas / page area (0 if not measured)
    scanned: bool


@dataclass(frozen=True)
class InspectionReport:
    info: DocInfo
    pages: tuple[PageSummary, ...]
    font_fingerprint: tuple[str, ...]   # sorted distinct font names (subset prefix stripped)

    @property
    def scanned_pages(self) -> tuple[int, ...]:
        return tuple(p.page_no for p in self.pages if p.scanned)


def inspect_document(
    backend: PdfBackend, path: Path, password: str | None = None, thresholds: Thresholds = DEFAULTS
) -> InspectionReport:
    """Open ``path`` with ``backend`` (left open for the caller) and summarise it.

    Pass 1 is a cheap text skim of every page. Only pages with almost no text
    are read fully, to measure image coverage for scanned-page detection.
    """
    info = backend.open(path, password)
    fonts: set[str] = set()
    pages: list[PageSummary] = []
    for page_no in range(1, info.page_count + 1):
        spans = backend.skim_page_text(page_no)
        n_chars = sum(len(s.text) - sum(ch.isspace() for ch in s.text) for s in spans)
        fonts.update(s.font_name for s in spans if s.font_name)
        ratio = 0.0
        scanned = False
        if n_chars < thresholds.scanned_max_chars:
            raw = backend.read_page(page_no)
            area = raw.width * raw.height or 1.0
            ratio = sum(
                max(0.0, i.bbox[2] - i.bbox[0]) * max(0.0, i.bbox[3] - i.bbox[1]) for i in raw.images
            ) / area
            scanned = ratio >= thresholds.scanned_min_image_ratio
        pages.append(PageSummary(page_no, n_chars, round(ratio, 4), scanned))
    return InspectionReport(info=info, pages=tuple(pages), font_fingerprint=tuple(sorted(fonts)))

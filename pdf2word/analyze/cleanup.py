"""Printer slug lines, running heads and folios → REMOVED fates (spec §2 cleanup).

Pass 1 (document level) learns which text recurs on the first/last text line of pages;
pass 2 (per page) removes matching tokens. Every removal has a reason; nothing else is removed.

A running head or folio must be (a) on the topmost or bottommost non-slug text line of the page
(vertically overlapping it), (b) inside the outer ``MARGIN_FRACTION`` of the page height, and (c) repeated on
at least 30% of pages (digits normalised), or be a bare page number there. Wiley pages carry the
trimmed page inside a larger media box, so a fixed narrow margin misses the heads (S1 finding).

A bare number is a folio only if it equals the PDF page index plus the chapter's folio offset —
the offset shared by the edge numbers of at least 30% of pages (repair release D-2). Without a
consistent offset no folio is removed. Pilot 1 removed any bare number on the edge line band and
so deleted 57 body numbers (worked examples, answers) in 7 of the 12 samples.

A printer slug is tiny text (≤ ``SLUG_MAX_SIZE``) in the outer margin that lies wholly beyond the
vertical extent of the page's normal-size text (D-3: Pilot 1 also removed small carry digits of
worked sums that sat in the lower fifth of a page).
"""

from __future__ import annotations

import re
from collections import Counter

from pdf2word.extract.backend import RawSpan
from pdf2word.ir import Token

MARGIN_FRACTION = 0.20           # outer zone where running heads and folios may sit
SLUG_MAX_SIZE = 6.0              # printer slug text (e.g. "Trim size: 8 in × 10 in") is tiny
_DIGITS = re.compile(r"\d+")


def _key(text: str) -> str:
    return _DIGITS.sub("#", " ".join(text.split())).strip().lower()


class Cleanup:
    """Learns repeated margin text from the pass-1 skim of all pages."""

    def __init__(self, skims: dict[int, tuple[float, list[RawSpan]]]) -> None:
        self.pages = len(skims)
        counts: Counter[str] = Counter()
        offsets: Counter[int] = Counter()
        for page_no, (height, spans) in skims.items():
            seen = set()
            page_offsets = set()
            edge = self._edges(spans, height)
            for s in spans:
                if self._at_edge(s, height, edge) and s.text.strip():
                    seen.add(_key(s.text))
                    if s.text.strip().isdigit():
                        page_offsets.add(int(s.text.strip()) - page_no)
            counts.update(seen)
            offsets.update(page_offsets)
        threshold = max(2, int(0.3 * self.pages))
        self.repeated = {k for k, n in counts.items() if n >= threshold and k not in ("", "#")}
        best = offsets.most_common(1)
        self.folio_offset: int | None = best[0][0] if best and best[0][1] >= threshold else None

    @staticmethod
    def _in_margin(bbox, height: float) -> bool:
        return bbox[3] <= height * MARGIN_FRACTION or bbox[1] >= height * (1 - MARGIN_FRACTION)

    @staticmethod
    def _is_slug(s: RawSpan, height: float) -> bool:
        return s.size <= SLUG_MAX_SIZE and Cleanup._in_margin(s.bbox, height)

    @staticmethod
    def _edges(spans: list[RawSpan], height: float) -> tuple[float, float]:
        """(bottom of the topmost text line, top of the bottommost text line), slugs ignored."""
        body = [s for s in spans if s.text.strip() and not Cleanup._is_slug(s, height)]
        if not body:
            return (-1.0, -1.0)
        top = min(body, key=lambda s: s.bbox[1])
        bottom = max(body, key=lambda s: s.bbox[3])
        return (top.bbox[3], bottom.bbox[1])

    @staticmethod
    def _at_edge(s: RawSpan, height: float, edge: tuple[float, float]) -> bool:
        """On the first or last text line: overlaps that line's vertical band."""
        if not Cleanup._in_margin(s.bbox, height) or Cleanup._is_slug(s, height):
            return False
        top_line_bottom, bottom_line_top = edge
        return s.bbox[1] < top_line_bottom - 1 or s.bbox[3] > bottom_line_top + 1

    def is_folio(self, text: str, page_no: int) -> bool:
        t = text.strip()
        return self.folio_offset is not None and t.isdigit() and int(t) == page_no + self.folio_offset

    @staticmethod
    def _outside_body(s: RawSpan, spans: list[RawSpan]) -> bool:
        """Wholly above or below every normal-size (> SLUG_MAX_SIZE) text span of the page."""
        body = [x for x in spans if x.size > SLUG_MAX_SIZE and x.text.strip()]
        if not body:
            return True
        return s.bbox[1] >= max(x.bbox[3] for x in body) - 1 or s.bbox[3] <= min(x.bbox[1] for x in body) + 1

    def classify(self, tokens: dict[str, Token], spans: dict[str, RawSpan], page_height: float) -> dict[str, str]:
        """token id → removal reason for this page."""
        removed: dict[str, str] = {}
        page_no = int(next(iter(spans)).split(":")[0][1:]) if spans else 0
        all_spans = list(spans.values())
        by_span: dict[str, list[Token]] = {}
        for t in tokens.values():
            by_span.setdefault(t.parent_span, []).append(t)
        edge = self._edges(list(spans.values()), page_height)
        for sid, toks in by_span.items():
            span = spans[sid]
            if self._is_slug(span, page_height) and self._outside_body(span, all_spans):
                reason = "slug"
            elif self._at_edge(span, page_height, edge) and self.is_folio(span.text, page_no):
                reason = "folio"
            elif self._at_edge(span, page_height, edge) and _key(span.text) in self.repeated:
                reason = "running_head"
            else:
                continue
            for t in toks:
                removed[t.id] = reason
        return removed

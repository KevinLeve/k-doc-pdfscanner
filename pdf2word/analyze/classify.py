"""Paths and rasters → IR items with kind and provisional protection (spec §4.1).

Protection: every GLYPH path; every RULE/ART path and raster whose bbox intersects any text
line's protection band (no size exemptions). Pilot deviation PD-1 (docs/pilot-deviation-record.md,
IMPLEMENTATION_LOG): a *pale background fill* — fill-only rectangle, every colour channel ≥ 0.85,
area ≥ 400 pt², containing at least one text line — is decoration, not content; it is recorded as
``REMOVED(background)`` and counted under R, never silently dropped.
PD-1b extends this to any fill-only rectangle (any colour, so white-on-dark heading bars become
editable black text) and to stroke-only frame rectangles that contain a whole text line
(``REMOVED(frame)``). PD-4: long horizontal separator rules (≥ 120 pt, without math directly
above *and* below, i.e. not a fraction bar) are released as ``REMOVED(separator)``.
"""

from __future__ import annotations

import hashlib

from pdf2word.extract.backend import RawImagePlacement, RawPage, RawPath
from pdf2word.ir import BBox, ImageItem, Line, PathItem, PathKind

GLYPH_MAX_H, GLYPH_MAX_W = 30.0, 60.0


def _intersects(a: BBox, b: BBox) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _contains(outer: BBox, inner: BBox, tol: float = 0.5) -> bool:
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def path_kind(p: RawPath, body_size: float) -> PathKind:
    x0, y0, x1, y1 = p.bbox
    w, h = x1 - x0, y1 - y0
    if min(w, h) <= 1.5 and max(w, h) >= 4:
        return PathKind.RULE
    if p.fill is not None and h <= max(GLYPH_MAX_H, 2.5 * body_size) and w <= GLYPH_MAX_W and w * h > 0.1:
        return PathKind.GLYPH
    return PathKind.ART


def background_reason(p: RawPath, lines: list[Line]) -> str | None:
    """'background' (fill-only box) / 'frame' (stroke-only box) around a text line, else None."""
    if any(seg.op not in ("re", "l", "qu") for seg in p.segments):
        return None
    x0, y0, x1, y1 = p.bbox
    if (x1 - x0) * (y1 - y0) < 400 or not any(_contains(p.bbox, ln.bbox, 1.0) for ln in lines):
        return None
    if p.fill is not None and p.stroke is None and p.paint == "f":
        return "background"
    if p.fill is None and p.stroke is not None and p.paint == "s":
        return "frame"
    return None


def is_separator(item: PathItem, near: list[BBox]) -> bool:
    """Long horizontal rule that is not a fraction bar (no math within 14 pt above and below)."""
    x0, y0, x1, y1 = item.bbox
    if item.kind is not PathKind.RULE or (y1 - y0) > 1.5 or (x1 - x0) < 120:
        return False
    above = any(b[3] <= y0 + 0.5 and b[3] >= y0 - 14 and b[0] < x1 and x0 < b[2] for b in near)
    below = any(b[1] >= y1 - 0.5 and b[1] <= y1 + 14 and b[0] < x1 and x0 < b[2] for b in near)
    return not (above and below)


def classify_paths(page: RawPage, lines: list[Line], body_size: float) -> tuple[dict[str, PathItem], dict[str, str]]:
    """→ (path items, {path id: 'background' | 'frame'} for decoration boxes)."""
    items: dict[str, PathItem] = {}
    backgrounds: dict[str, str] = {}
    for p in page.paths:
        x0, y0, x1, y1 = p.bbox
        if x1 - x0 <= 0 and y1 - y0 <= 0:
            continue
        kind = path_kind(p, body_size)
        key = hashlib.sha1(repr([(s.op, [(round(a - x0, 1), round(b - y0, 1)) for a, b in s.points])
                                 for s in p.segments]).encode()).hexdigest()[:16]
        item = PathItem(id=p.id, bbox=p.bbox, kind=kind, filled=p.fill is not None,
                        stroke_width=p.stroke_width, n_segments=len(p.segments), shape_key=key)
        reason = background_reason(p, lines)
        if reason:
            backgrounds[p.id] = reason
        elif kind is PathKind.GLYPH:
            item.protected, item.protection_reason = True, "glyph"
        else:
            for ln in lines:
                if _intersects(p.bbox, ln.protection_band):
                    item.protected, item.protection_reason = True, f"band:{ln.id}"
                    break
        items[p.id] = item
    return items, backgrounds


def classify_images(page: RawPage, lines: list[Line]) -> dict[str, ImageItem]:
    out: dict[str, ImageItem] = {}
    for im in page.images:
        item = ImageItem(id=im.id, bbox=im.bbox, ref=im.ref, content_hash=im.content_hash,
                         px_size=im.px_size, ext=im.ext)
        if any(_intersects(im.bbox, ln.protection_band) for ln in lines):
            item.protected = True
        out[im.id] = item
    return out


def raw_image(page: RawPage, image_id: str) -> RawImagePlacement:
    return next(i for i in page.images if i.id == image_id)


# ------------------------------------------------------------------ R6 (repair release, RD-6)
UNDERLINE_MAX_DROP = 3.0       # pt below the baseline (spec v1.4 §4.1)


def _rgb(c) -> int | None:
    if c is None:
        return None
    if len(c) == 1:
        c = (c[0], c[0], c[0])
    if len(c) != 3:
        return None
    return (round(c[0] * 255) << 16) | (round(c[1] * 255) << 8) | round(c[2] * 255)


def _close_colour(a: int, b: int, tol: int = 24) -> bool:
    return all(abs(((a >> s) & 255) - ((b >> s) & 255)) <= tol for s in (16, 8, 0))


def find_underlines(page: RawPage, tokens: dict, lines: list[Line], paths: dict[str, PathItem]) -> dict[str, list[str]]:
    """Validated underlines → {path id: [token ids]} (spec v1.4 §4.1, repair release R6).

    A horizontal RULE is an underline of tokens only if ALL hold: it lies 0–3 pt below the baseline
    of exactly one text line; the tokens of that line that sit at least half over it cover ≥ 90%
    of its length; those tokens are not maths (suspicious) and have the rule's colour; and the rule
    spans whole tokens (first to last character, ±1.5 pt). Anything else keeps its protection and
    stays an image.
    """
    raw = {p.id: p for p in page.paths}
    out: dict[str, list[str]] = {}
    for pid, item in paths.items():
        x0, y0, x1, y1 = item.bbox
        if item.kind is not PathKind.RULE or (y1 - y0) > 1.5 or (x1 - x0) < 2:
            continue
        y = (y0 + y1) / 2
        cand = [ln for ln in lines if 0 <= y - ln.baseline <= UNDERLINE_MAX_DROP and ln.bbox[0] < x1 and x0 < ln.bbox[2]]
        if len(cand) != 1:
            continue
        ln = cand[0]
        under = [tokens[t] for t in ln.token_ids
                 if (min(x1, tokens[t].bbox[2]) - max(x0, tokens[t].bbox[0])) >= 0.5 * (tokens[t].bbox[2] - tokens[t].bbox[0])]
        if not under or any(t.suspicious for t in under):
            continue
        covered = sum(max(0.0, min(x1, t.bbox[2]) - max(x0, t.bbox[0])) for t in under)
        if covered < 0.9 * (x1 - x0):
            continue
        # whole tokens only: the rule starts at the first character of the first token and ends at
        # the last character of the last one. A rule under part of a token (a rounding digit in
        # "164") cannot be expressed as token formatting without inventing emphasis.
        under.sort(key=lambda t: t.bbox[0])
        if abs(x0 - under[0].chars[0].bbox[0]) > 1.5 or abs(x1 - under[-1].chars[-1].bbox[2]) > 1.5:
            continue
        colour = _rgb(raw[pid].stroke if raw[pid].stroke is not None else raw[pid].fill)
        if colour is None or not all(_close_colour(colour, t.font.color) for t in under):
            continue
        out[pid] = [t.id for t in under]
    return out

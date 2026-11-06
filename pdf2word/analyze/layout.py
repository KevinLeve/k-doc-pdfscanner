"""Reading order: bands and up to two columns (spec §4.6; pilot: ≤ 2 columns).

A gutter is a vertical strip in the middle half of the page that no text line crosses, with at
least three lines on each side. Units crossing the gutter are full width and split the page into
bands; within a band the left column is read before the right one, each top to bottom.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pdf2word.ir import BBox, Line


@dataclass
class Unit:
    kind: str                                   # "line" | "picture"
    page_no: int
    bbox: BBox
    line: Line | None = None
    items: list[tuple[str, str]] = field(default_factory=list)   # ("t", token id) | ("z", zone id)
    zone_id: str | None = None
    column: int = 0                             # 0 left/single, 1 right, -1 full width


def find_gutter(lines: list[Line], width: float) -> float | None:
    if len(lines) < 6:
        return None
    best = None
    x = 0.25 * width
    while x <= 0.75 * width:
        if not any(ln.bbox[0] < x < ln.bbox[2] for ln in lines):
            left = sum(1 for ln in lines if ln.bbox[2] <= x)
            right = sum(1 for ln in lines if ln.bbox[0] >= x)
            if left >= 3 and right >= 3:
                best = (best or []) + [x]
        x += 2.0
    if not best:
        return None
    # centre of the longest run of free positions
    runs, cur = [], [best[0]]
    for v in best[1:]:
        if v - cur[-1] <= 2.01:
            cur.append(v)
        else:
            runs.append(cur)
            cur = [v]
    runs.append(cur)
    run = max(runs, key=len)
    return (run[0] + run[-1]) / 2


def order_units(units: list[Unit], text_lines: list[Line], width: float) -> list[Unit]:
    gutter = find_gutter(text_lines, width)
    if gutter is None:
        return sorted(units, key=lambda u: (round(u.bbox[1], 0), u.bbox[0]))
    for u in units:
        u.column = 0 if u.bbox[2] <= gutter + 1 else 1 if u.bbox[0] >= gutter - 1 else -1
    out: list[Unit] = []
    band: list[Unit] = []
    for u in sorted(units, key=lambda u: (u.bbox[1], u.bbox[0])):
        if u.column == -1:
            out += _band(band)
            band = []
            out.append(u)
        else:
            band.append(u)
    return out + _band(band)


def _band(units: list[Unit]) -> list[Unit]:
    return sorted(units, key=lambda u: (u.column, u.bbox[1], u.bbox[0]))

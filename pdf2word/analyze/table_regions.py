"""Caption-anchored table regions → TABLE_IMAGE zones (spec §4.5, preservation tier; pilot simplification).

A line starting with "Table N-M" (or "Table N.M") anchors a region that extends down over the
horizontal rules that follow it (each within 120 pt of the previous), ending at the last rule.
Without at least two such rules the caption stays ordinary text and nothing is imaged.
Native table reconstruction (M10) is deferred in the pilot.
"""

from __future__ import annotations

import re

from pdf2word.ir import BBox, Line, PathItem, PathKind, Token

CAPTION = re.compile(r"^\s*table\s+\d+\s*[-–.]\s*\d+", re.I)


def line_text(line: Line, tokens: dict[str, Token]) -> str:
    return " ".join(tokens[t].raw_text for t in line.token_ids)


def find_table_regions(lines: list[Line], tokens: dict[str, Token], paths: dict[str, PathItem],
                       column_width: float) -> list[tuple[Line, BBox]]:
    out = []
    rules = sorted((p for p in paths.values() if p.kind is PathKind.RULE
                    and (p.bbox[2] - p.bbox[0]) >= 0.5 * column_width), key=lambda p: p.bbox[1])
    for ln in lines:
        if not CAPTION.match(line_text(ln, tokens)):
            continue
        y = ln.bbox[3]
        chain = []
        for r in rules:
            if r.bbox[1] < ln.bbox[1] - 2:
                continue
            last = chain[-1].bbox[3] if chain else y
            if r.bbox[1] - last > 120:
                break
            chain.append(r)
        if len(chain) >= 2:
            x0 = min(min(r.bbox[0] for r in chain), ln.bbox[0])
            x1 = max(max(r.bbox[2] for r in chain), ln.bbox[2])
            out.append((ln, (x0, y + 0.5, x1, chain[-1].bbox[3] + 1.0)))
    return out


# ------------------------------------------------------------------ R4 (repair release, RD-4)
RULE_Y_TOL = 0.6           # pt: segments of one rule share a y
RULE_X_JOIN = 3.0          # pt: and abut horizontally (dashed / per-cell borders)
ROW_GAP_MAX = 40.0         # pt between consecutive rules of one table
EXTENT_TOL = 8.0           # pt: rules of one table share their x-extent


def composite_rules(paths: dict[str, PathItem]) -> list[tuple[list[str], BBox]]:
    """Horizontal RULE paths that are collinear and abut → one rule (ids, bbox). Single rules too."""
    flat = sorted((p for p in paths.values() if p.kind is PathKind.RULE and (p.bbox[3] - p.bbox[1]) <= 1.5),
                  key=lambda p: (round(p.bbox[1], 1), p.bbox[0]))
    groups: list[tuple[list[str], BBox]] = []
    for p in flat:
        y = (p.bbox[1] + p.bbox[3]) / 2
        for i, (ids, b) in enumerate(groups):
            if (abs((b[1] + b[3]) / 2 - y) <= RULE_Y_TOL and p.bbox[0] <= b[2] + RULE_X_JOIN
                    and b[0] <= p.bbox[2] + RULE_X_JOIN):
                groups[i] = (ids + [p.id], (min(b[0], p.bbox[0]), min(b[1], p.bbox[1]),
                                            max(b[2], p.bbox[2]), max(b[3], p.bbox[3])))
                break
        else:
            groups.append(([p.id], p.bbox))
    return groups


def _columnar(row_lines: list[Line], tokens: dict[str, Token]) -> bool:
    """Column structure: ≥ 3 line segments on one baseline, or one line with ≥ 3 wide gaps."""
    by_base: dict[int, int] = {}
    for ln in row_lines:
        by_base[round(ln.baseline)] = by_base.get(round(ln.baseline), 0) + 1
        toks = sorted((tokens[t] for t in ln.token_ids), key=lambda t: t.bbox[0])
        wide = sum(1 for a, b in zip(toks, toks[1:]) if b.bbox[0] - a.bbox[2] > 1.5 * a.font.size)
        if wide >= 3:
            return True
    return any(n >= 3 for n in by_base.values())


def _shared_cell_borders(chain_rules: list[list[str]], paths: dict[str, PathItem]) -> int:
    """Internal segment boundaries (cell borders) present in every rule of the chain (1 pt grid)."""
    sets = []
    for ids in chain_rules:
        xs = sorted({round(paths[i].bbox[0]) for i in ids} | {round(paths[i].bbox[2]) for i in ids})
        sets.append(set(xs[1:-1]))
    common = set.intersection(*sets) if sets else set()
    return len(common)


def find_ruled_tables(lines: list[Line], tokens: dict[str, Token], rules: list[tuple[list[str], BBox]],
                      column_width: float, paths: dict[str, PathItem] | None = None
                      ) -> list[tuple[BBox, list[str], list[str]]]:
    """Uncaptioned ruled tables → [(region bbox, rule path ids, line ids)] (RD-4).

    ≥ 2 long parallel rules (≥ max(150 pt, 40% of the text width)) with the same x-extent, each
    ≤ 40 pt below the previous, enclosing (or directly topped by) text with column structure —
    or rules split into cells whose borders line up (≥ 2 shared internal segment boundaries).
    A single rule qualifies only when split into ≥ 5 cells with a header row directly above it
    (empty answer grids);
    every enclosed line must lie within the rules' x-extent. The region spans from the header
    line (if one sits ≤ 10 pt above the first rule) to the last rule.
    """
    long = sorted((r for r in rules if (r[1][2] - r[1][0]) >= max(150.0, 0.4 * column_width)),
                  key=lambda r: r[1][1])
    out, used = [], set()
    for i, (_, b) in enumerate(long):
        if i in used:
            continue
        chain = [i]
        for j in range(i + 1, len(long)):
            pb, nb = long[chain[-1]][1], long[j][1]
            if nb[1] - pb[3] > ROW_GAP_MAX:
                break
            if abs(nb[0] - b[0]) <= EXTENT_TOL and abs(nb[2] - b[2]) <= EXTENT_TOL:
                chain.append(j)
        single_grid = (len(chain) == 1 and paths is not None
                       and _shared_cell_borders([long[i][0]], paths) >= 4)   # one rule split into ≥ 5 cells
        if len(chain) < 2 and not single_grid:
            continue
        x0 = min(long[k][1][0] for k in chain)
        x1 = max(long[k][1][2] for k in chain)
        top, bottom = long[chain[0]][1][1], long[chain[-1]][1][3]
        inside = [ln for ln in lines if top < (ln.bbox[1] + ln.bbox[3]) / 2 < bottom]
        header = [ln for ln in lines if top - 10 <= ln.bbox[3] <= top + 1
                  and ln.bbox[0] >= x0 - 4 and ln.bbox[2] <= x1 + 4]
        rows = inside + header
        if single_grid and not header:          # an empty answer grid needs its header row
            continue
        if not rows or any(ln.bbox[0] < x0 - 4 or ln.bbox[2] > x1 + 4 for ln in inside):
            continue
        cells = _shared_cell_borders([long[k][0] for k in chain], paths) if paths else 0
        if not (_columnar(rows, tokens) or cells >= 2):      # text columns, or ruled cell borders
            continue
        y0 = min([top] + [ln.bbox[1] for ln in header])
        out.append(((x0, y0, x1, bottom + 0.5), [pid for k in chain for pid in long[k][0]],
                    [ln.id for ln in rows]))
        used.update(chain)
    return out

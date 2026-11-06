"""Tokens → text lines with baselines and protection bands (spec §4.1, §4.6).

Lines are rebuilt from token geometry instead of trusting the PDF's own line objects, because
vector glyphs split one visual line into several text objects (verified on ch07 p7).
Small raised/lowered tokens (scripts, footnote markers) attach to the line they sit on; a drop
cap is the first token of the first line to its right (its own box does not widen that line).
"""

from __future__ import annotations

import statistics

from pdf2word.ir import Line, Token, id_key, make_id

BAND_FACTOR = 0.6          # protection band: line height × 0.6 above and below (§4.1)


def build_lines(page_no: int, tokens: dict[str, Token], kept: set[str]) -> list[Line]:
    toks = [tokens[t] for t in sorted(kept, key=id_key)]
    if not toks:
        return []
    sizes = [t.font.size for t in toks]
    med = statistics.median(sizes)
    # drop caps: one large letter whose baseline sits on a later line of its paragraph
    caps = [t for t in toks if len(t.raw_text) == 1 and t.raw_text.isalpha() and t.font.size >= 2.2 * med]
    big = [t for t in toks if t.font.size >= 0.8 * med and t not in caps]
    small = [t for t in toks if t not in big and t not in caps]

    # 1. cluster full-size tokens by baseline
    big.sort(key=lambda t: (round(t.origin[1], 1), t.bbox[0]))
    rows: list[list[Token]] = []
    for t in big:
        tol = max(1.5, 0.3 * t.font.size)
        if rows and abs(rows[-1][0].origin[1] - t.origin[1]) <= tol:
            rows[-1].append(t)
        else:
            rows.append([t])

    # 2. split each row at large horizontal gaps (columns)
    segments: list[list[Token]] = []
    for row in rows:
        row.sort(key=lambda t: t.bbox[0])
        seg = [row[0]]
        for t in row[1:]:
            if t.bbox[0] - seg[-1].bbox[2] > max(18.0, 2.5 * t.font.size):
                segments.append(seg)
                seg = [t]
            else:
                seg.append(t)
        segments.append(seg)

    # 3. attach small tokens to the segment whose box contains them (else own segment)
    def box(seg):
        return (min(t.bbox[0] for t in seg), min(t.bbox[1] for t in seg),
                max(t.bbox[2] for t in seg), max(t.bbox[3] for t in seg))
    boxes = [box(s) for s in segments]
    for t in small:
        cx, cy = (t.bbox[0] + t.bbox[2]) / 2, (t.bbox[1] + t.bbox[3]) / 2
        best = None
        for i, (x0, y0, x1, y1) in enumerate(boxes):
            h = y1 - y0
            if x0 - 6 <= cx <= x1 + 6 and y0 - 0.6 * h <= cy <= y1 + 0.6 * h:
                d = abs(cy - (y0 + y1) / 2)
                if best is None or d < best[0]:
                    best = (d, i)
        if best is None:
            segments.append([t])
            boxes.append(box([t]))
        else:
            segments[best[1]].append(t)

    # a drop cap joins the first line to its right that starts within its height
    for t in caps:
        cands = [i for i, (x0, y0, x1, y1) in enumerate(boxes)
                 if 0 <= x0 - t.bbox[2] + 2 <= 24 and t.bbox[1] - 2 <= y0 <= t.bbox[3]]
        if cands:
            segments[min(cands, key=lambda i: boxes[i][1])].insert(0, t)
        else:
            segments.append([t])
            boxes.append(box([t]))

    lines: list[Line] = []
    for i, seg in enumerate(sorted(segments, key=lambda s: (box(s)[1], box(s)[0]))):
        seg.sort(key=lambda t: t.bbox[0])
        x0, y0, x1, y1 = box([t for t in seg if t not in caps] or seg)
        body = [t for t in seg if t not in caps] or seg
        main = [t for t in body if t.font.size >= 0.8 * max(s.font.size for s in body)] or body
        baseline = statistics.median(t.origin[1] for t in main)
        size = statistics.median(t.font.size for t in main)
        h = y1 - y0
        lid = make_id(page_no, "l", i)
        for t in seg:
            t.line_id = lid
            t.baseline_shift_pt = round(t.origin[1] - baseline, 2)
            t.size_ratio = round(t.font.size / size, 3) if size else 1.0
        lines.append(Line(id=lid, bbox=(x0, y0, x1, y1), baseline=baseline, x_height=0.5 * size,
                          token_ids=[t.id for t in seg], path_ids=[],
                          protection_band=(x0, y0 - BAND_FACTOR * h, x1, y1 + BAND_FACTOR * h)))
    return lines

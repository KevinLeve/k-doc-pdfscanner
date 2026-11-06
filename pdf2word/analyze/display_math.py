"""Stacked arithmetic blocks (repair release R5, RD-5).

Column additions, subtractions, multiplications and long divisions are laid out as short numeric
lines stacked over rules and brackets. Flowed as paragraphs they lose their meaning, so a whole
stack is preserved as one BLOCK_PRESERVE picture; no reconstruction is attempted.

A stack is ≥ 3 vertically adjacent, horizontally overlapping *arithmetic lines* (only numbers and
operators, not bold — bold numbers are answer headings —, ≤ 6 tokens, ≤ 150 pt wide), or ≥ 2
such lines plus a rule/bracket path between or around them. Stacks whose lines share a baseline with other short numeric segments nearby are
rejected: that is a numeric table column, not a sum.
"""

from __future__ import annotations

import re

from pdf2word.ir import BBox, Line, PathItem, Token

ARITH_TOKEN = re.compile(r"^[\d.,+\-–−×÷=()rR]+$")
MAX_TOKENS = 6
MAX_WIDTH = 150.0
STACK_GAP = 8.0            # pt between a line's bottom and the next line's top
PATH_MARGIN_X, PATH_MARGIN_Y = 6.0, 4.0
TABLE_NEIGHBOUR = 80.0     # pt: another numeric segment on the same baseline this close → table


def is_arithmetic_line(ln: Line, tokens: dict[str, Token]) -> bool:
    toks = [tokens[t] for t in ln.token_ids]
    return (0 < len(toks) <= MAX_TOKENS and (ln.bbox[2] - ln.bbox[0]) <= MAX_WIDTH
            and all(ARITH_TOKEN.match(t.raw_text) and not t.font.bold for t in toks))


def _overlap_x(a: BBox, b: BBox) -> bool:
    return a[0] < b[2] and b[0] < a[2]


def find_arithmetic_stacks(lines: list[Line], tokens: dict[str, Token], free_paths: dict[str, PathItem]
                           ) -> list[tuple[BBox, list[str], list[str]]]:
    """→ [(region bbox, line ids, path ids)] for every stacked arithmetic block."""
    arith = [ln for ln in lines if is_arithmetic_line(ln, tokens)]
    arith_ids = {ln.id for ln in arith}

    def tabular(ln: Line) -> bool:
        return any(o.id != ln.id and o.id in arith_ids and abs(o.baseline - ln.baseline) <= 1.0
                   and min(abs(o.bbox[0] - ln.bbox[2]), abs(ln.bbox[0] - o.bbox[2])) <= TABLE_NEIGHBOUR
                   for o in arith)

    out = []
    used: set[str] = set()
    for start in sorted(arith, key=lambda ln: (ln.bbox[1], ln.bbox[0])):
        if start.id in used:
            continue
        stack = [start]
        for ln in sorted(arith, key=lambda ln: ln.bbox[1]):
            if ln.id in used or ln in stack or ln.bbox[1] <= stack[-1].bbox[1]:
                continue
            if 0 <= ln.bbox[1] - stack[-1].bbox[3] <= STACK_GAP or (
                    ln.bbox[1] < stack[-1].bbox[3] and ln.baseline > stack[-1].baseline + 3):
                if _overlap_x(ln.bbox, stack[-1].bbox) or _overlap_x(ln.bbox, start.bbox):
                    stack.append(ln)
            elif ln.bbox[1] - stack[-1].bbox[3] > STACK_GAP:
                break
        if len(stack) < 2 or any(tabular(ln) for ln in stack):
            continue
        x0 = min(ln.bbox[0] for ln in stack); x1 = max(ln.bbox[2] for ln in stack)
        y0 = min(ln.bbox[1] for ln in stack); y1 = max(ln.bbox[3] for ln in stack)
        near = [p for p in free_paths.values()
                if p.bbox[0] >= x0 - PATH_MARGIN_X and p.bbox[2] <= x1 + PATH_MARGIN_X
                and p.bbox[1] >= y0 - PATH_MARGIN_Y and p.bbox[3] <= y1 + PATH_MARGIN_Y]
        if len(stack) < 3 and not near:
            continue
        box = (min([x0] + [p.bbox[0] for p in near]), min([y0] + [p.bbox[1] for p in near]),
               max([x1] + [p.bbox[2] for p in near]), max([y1] + [p.bbox[3] for p in near]))
        out.append((box, [ln.id for ln in stack], [p.id for p in near]))
        used.update(ln.id for ln in stack)
    return out

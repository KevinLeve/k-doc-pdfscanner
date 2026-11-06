"""Clusters of paths and rasters, and their classification (spec §4.4, §4.5; pilot simplification).

Clusters are built with a spatial grid (pages carry up to ~6,000 paths). Each cluster is then:
- ``rule``     unprotected rules only (header rules, crop marks)  → REMOVED(rule)
- ``inline``   small cluster sitting on one text line             → inline seed (inline_math)
- ``block``    cluster crossing the cores of two or more lines    → BLOCK seed (inline_math)
- ``figure`` / ``display``  large or line-free cluster            → FIGURE / DISPLAY_MATH zone
Icons are kept as small figures (pilot deviation PD-3: nothing is dropped).

R2 (repair release, RD-2): an *edge decoration* — a page-edge band such as a thumb tab with its
shadow — is removed with ``REMOVED(edge_decoration)`` only if ALL hold: no member is protected or a
GLYPH path; every path is a plain filled/stroked rectangle (``re``/``l`` segments only); it lies
wholly in a side (top/bottom) margin outside the body text; it reaches within ``EDGE_ZONE`` of the
page edge; it is a narrow band (≤ 15% of the page width/height) spanning ≥ 60% of the page
height/width; and it overlaps no kept text token. A bleed photo (wide), a margin icon (short) or
anything with glyph outlines fails these tests and stays a figure.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from pdf2word.ir import BBox, ImageItem, Line, PathItem, PathKind

GAP = 3.0                 # pt: items closer than this belong to the same cluster
EDGE_ZONE = 10.0          # pt from the page edge (bleed decorations stop just inside the media box)
CELL = 24.0
BRIDGE_GAP_PT = 24.0       # pt: an ordinary word-gap a small glyph may bridge between two split lines


@dataclass
class Cluster:
    path_ids: list[str] = field(default_factory=list)
    image_ids: list[str] = field(default_factory=list)
    bbox: BBox = (0, 0, 0, 0)
    kind: str = ""                     # rule | inline | block | figure | display
    lines: list[str] = field(default_factory=list)   # line ids whose core it touches


def _union(a: BBox, b: BBox) -> BBox:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _near(a: BBox, b: BBox, gap: float) -> bool:
    return a[0] - gap <= b[2] and b[0] - gap <= a[2] and a[1] - gap <= b[3] and b[1] - gap <= a[3]


def _overlap_h(a: BBox, b: BBox) -> float:
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def build_clusters(paths: dict[str, PathItem], images: dict[str, ImageItem], skip: set[str]) -> list[Cluster]:
    items = [(pid, p.bbox, "p") for pid, p in paths.items() if pid not in skip]
    items += [(iid, im.bbox, "i") for iid, im in images.items() if iid not in skip]
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for idx, (_, b, _) in enumerate(items):
        for gx in range(int((b[0] - GAP) // CELL), int((b[2] + GAP) // CELL) + 1):
            for gy in range(int((b[1] - GAP) // CELL), int((b[3] + GAP) // CELL) + 1):
                for other in grid[(gx, gy)]:
                    if find(other) != find(idx) and _near(items[other][1], b, GAP):
                        parent[find(other)] = find(idx)
                grid[(gx, gy)].append(idx)
    groups: dict[int, Cluster] = {}
    for idx, (iid, b, typ) in enumerate(items):
        c = groups.setdefault(find(idx), Cluster(bbox=b))
        c.bbox = _union(c.bbox, b)
        (c.path_ids if typ == "p" else c.image_ids).append(iid)
    return list(groups.values())


def classify_cluster(c: Cluster, paths: dict[str, PathItem], lines: list[Line], line_h: float) -> None:
    members = [paths[p] for p in c.path_ids]
    protected = any(p.protected for p in members) or bool(c.image_ids)
    if not protected and not c.image_ids and members and all(p.kind is PathKind.RULE for p in members):
        c.kind = "rule"
        return
    x0, y0, x1, y1 = c.bbox
    w, h = x1 - x0, y1 - y0
    thin = h < 2.0                      # a rule: "touches" the line whose box contains it
    touched = [ln for ln in lines if (_overlap_h(ln.bbox, c.bbox) > 1.0
                                      or thin and ln.bbox[1] <= y0 and y1 <= ln.bbox[3])
               and ln.bbox[0] - 2 <= x1 and x0 <= ln.bbox[2] + 2]
    if thin and len(touched) > 1:      # inside two overlapping line boxes: take the nearer baseline
        touched = [min(touched, key=lambda ln: abs(ln.baseline - y0))]
    if not touched:
        # a small glyph (e.g. an arrow joining two equations) can sit exactly in the horizontal
        # gap build_lines() split one source row into at - the gap is real (BAND_FACTOR), but the
        # glyph itself still reads as part of that row, not a standalone picture, when it is
        # flanked by a line segment on each side within an ordinary word-gap distance
        same_row = [ln for ln in lines if _overlap_h(ln.bbox, c.bbox) > 1.0]
        left = [ln for ln in same_row if ln.bbox[2] <= x0 + 2 and x0 - ln.bbox[2] <= BRIDGE_GAP_PT]
        right = [ln for ln in same_row if ln.bbox[0] >= x1 - 2 and ln.bbox[0] - x1 <= BRIDGE_GAP_PT]
        if left and right:
            touched = [min(left + right, key=lambda ln: min(abs(ln.bbox[2] - x0), abs(ln.bbox[0] - x1)))]
    c.lines = [ln.id for ln in touched]
    large = h > 2.5 * line_h or (w > 150 and h > 1.6 * line_h) or bool(c.image_ids) and (w * h > 900)
    if large or not touched:
        glyphs = sum(1 for p in members if p.kind is PathKind.GLYPH)
        c.kind = "display" if not c.image_ids and members and glyphs >= 0.6 * len(members) and not large else (
            "display" if not c.image_ids and glyphs >= 0.8 * max(1, len(members)) else "figure")
        return
    c.kind = "inline" if len(touched) == 1 else "block"


def is_edge_decoration(c: Cluster, paths: dict[str, PathItem], images: dict[str, ImageItem],
                       page_w: float, page_h: float, text_box: BBox | None,
                       kept_token_boxes: list[BBox], rect_only: set[str]) -> bool:
    """RD-2 test (see module docstring). ``rect_only``: path ids whose segments are all re/l."""
    if text_box is None:
        return False
    members = [paths[p] for p in c.path_ids]
    if any(p.protected or p.kind is PathKind.GLYPH or p.id not in rect_only for p in members):
        return False
    if any(images[i].protected for i in c.image_ids):
        return False
    x0, y0, x1, y1 = c.bbox
    w, h = x1 - x0, y1 - y0
    side = (x1 <= text_box[0] - 2 or x0 >= text_box[2] + 2) and w <= 0.15 * page_w and h >= 0.6 * page_h         and (x0 <= EDGE_ZONE or x1 >= page_w - EDGE_ZONE)
    top_bottom = (y1 <= text_box[1] - 2 or y0 >= text_box[3] + 2) and h <= 0.15 * page_h and w >= 0.6 * page_w         and (y0 <= EDGE_ZONE or y1 >= page_h - EDGE_ZONE)
    if not (side or top_bottom):
        return False
    return not any(b[0] < x1 and x0 < b[2] and b[1] < y1 and y0 < b[3] for b in kept_token_boxes)


# ------------------------------------------------------------------ R3 (repair release, RD-3)
LABEL_GAP = 8.0           # pt: picture clusters this close belong to one figure (axis + tick labels)
#: A margin icon (REMEMBER, TIP, WARNING...) is a small, roughly square badge on its own. Two such
#: badges stacked a few points apart (one per paragraph, as "For Dummies" books do) must never be
#: joined into one picture by the LABEL_GAP rule above: that rule exists for a chart merging with its
#: own small, non-square axis/tick labels, not for two complete, independent icons.
ICON_SIDE = (28.0, 80.0)         # pt: plausible width/height of one margin icon
ICON_ASPECT = (0.7, 1.43)        # width/height this close to square


def is_icon(b: BBox) -> bool:
    w, h = b[2] - b[0], b[3] - b[1]
    return (ICON_SIDE[0] <= w <= ICON_SIDE[1] and ICON_SIDE[0] <= h <= ICON_SIDE[1]
            and ICON_ASPECT[0] <= w / h <= ICON_ASPECT[1])


def _hits(box: BBox, token_boxes: list[BBox]) -> set[int]:
    """Tokens a zone with this box would absorb (same test as zone absorption)."""
    return {i for i, b in enumerate(token_boxes)
            if min(box[2], b[2]) - max(box[0], b[0]) > 0.0 and min(box[3], b[3]) - max(box[1], b[1]) > 0.5}


def _line_hits(box: BBox, line_boxes: list[BBox]) -> set[int]:
    return {i for i, b in enumerate(line_boxes) if min(box[2], b[2]) > max(box[0], b[0]) and min(box[3], b[3]) > max(box[1], b[1])}


def group_pictures(clusters: list[Cluster], token_boxes: list[BBox], line_boxes: list[BBox] | None = None,
                   gap: float = LABEL_GAP, pad_pt: float = 1.0,
                   blockers: list[BBox] | None = None) -> list[list[Cluster]]:
    """Join figure/display clusters lying within ``gap`` of each other (RD-3).

    A join is made only if the joined (padded) box touches no text token and no text line that
    neither part already touched — inline zones live on lines, and touching one would merge the
    picture with the line later. A joined group that touches a *blocker* (a cluster that becomes an
    inline or multi-line zone) is dissolved again: such a zone takes whole text lines when merged.
    Grouping can enlarge a picture but never pull body text into it.
    """
    blockers = blockers or []
    # lines widened by the inline-zone padding: an inline zone on a line reaches that far
    line_boxes = [(b[0] - 2, b[1] - 2, b[2] + 2, b[3] + 2) for b in (line_boxes or [])]

    def padded(b: BBox) -> BBox:
        return (b[0] - pad_pt, b[1] - pad_pt, b[2] + pad_pt, b[3] + pad_pt)

    groups = [[c] for c in sorted(clusters, key=lambda c: (c.bbox[1], c.bbox[0]))]
    boxes = [c[0].bbox for c in groups]
    hits = [_hits(padded(b), token_boxes) for b in boxes]
    lhits = [_line_hits(padded(b), line_boxes) for b in boxes]
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if not _near(boxes[i], boxes[j], gap):
                    continue
                if len(groups[i]) == 1 and len(groups[j]) == 1 and is_icon(boxes[i]) and is_icon(boxes[j]):
                    continue           # two complete icons, not a figure + its own label
                u = _union(boxes[i], boxes[j])
                h = _hits(padded(u), token_boxes)
                lh = _line_hits(padded(u), line_boxes)
                if not (h <= (hits[i] | hits[j]) and lh <= (lhits[i] | lhits[j])):
                    continue
                groups[i] += groups[j]; boxes[i] = u; hits[i] = h; lhits[i] = lh
                del groups[j], boxes[j], hits[j], lhits[j]
                merged = True
                break
            if merged:
                break
    # Groups whose padded boxes still overlap are merged later by zone resolution regardless of the
    # guard. Where that union would break the guard, dissolve the joined groups back into their
    # single clusters (exactly the behaviour before R3).
    changed = True
    while changed:
        changed = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if not _near(padded(boxes[i]), padded(boxes[j]), 0.0):
                    continue
                u = padded(_union(boxes[i], boxes[j]))
                if _hits(u, token_boxes) <= (hits[i] | hits[j]) and _line_hits(u, line_boxes) <= (lhits[i] | lhits[j]):
                    continue
                victims = [k for k in (i, j) if len(groups[k]) > 1]
                if not victims:
                    continue
                for k in sorted(victims, reverse=True):
                    singles = groups.pop(k); boxes.pop(k); hits.pop(k); lhits.pop(k)
                    for c in singles:
                        groups.append([c]); boxes.append(c.bbox)
                        hits.append(_hits(padded(c.bbox), token_boxes)); lhits.append(_line_hits(padded(c.bbox), line_boxes))
                changed = True
                break
            if changed:
                break
        if changed:
            continue
        for k in range(len(groups)):
            if len(groups[k]) > 1 and any(_near(padded(boxes[k]), b, 1.5) for b in blockers):
                singles = groups.pop(k); boxes.pop(k); hits.pop(k); lhits.pop(k)
                for c in singles:
                    groups.append([c]); boxes.append(c.bbox)
                    hits.append(_hits(padded(c.bbox), token_boxes)); lhits.append(_line_hits(padded(c.bbox), line_boxes))
                changed = True
                break
    return groups

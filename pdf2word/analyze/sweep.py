"""Zone resolution: seeds → absorbed, escalated, merged zones with a fate for every element (spec §4.3).

Pilot simplification (docs/pilot-deviation-record.md §2): one fixed-point loop does what the full
spec splits into escalation, merge and sweep stages.

Rules applied until nothing changes:
1. Zones whose boxes overlap are merged (figure wins; one line stays inline/line; else BLOCK).
2. A zone absorbs every unclaimed token that touches it horizontally (any overlap: a crop must
   not show a sliver of a glyph that is also emitted as text, S4 finding ch02) and overlaps it
   vertically by more than ``S1_PT``, and
   every unclaimed path/raster that intersects it; the box grows to contain what it absorbs, so a
   crop never cuts a protected primitive (S2).
3. An INLINE (TIGHT) zone that absorbs more than ``S3_WORDS`` prose words or grows taller than
   ``S4_FACTOR`` × line height escalates to LINE_PRESERVE (the whole line). A BLOCK zone that
   absorbs one token of a line absorbs the whole line.
Afterwards every protected primitive must be inside a zone; anything left is a bug and is raised
so the page falls back to an image (G2 is never traded for editability).
"""

from __future__ import annotations

from pdf2word.ir import BBox, Confidence, Escalation, Line, Token, Zone, ZoneKind, id_key, make_id

S1_PT = 0.5
S3_WORDS = 3
S4_FACTOR = 2.5
MAX_ROUNDS = 60


def union(a: BBox, b: BBox) -> BBox:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def overlap(a: BBox, b: BBox) -> tuple[float, float]:
    return (min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1]))


def intersects(a: BBox, b: BBox) -> bool:
    ox, oy = overlap(a, b)
    return ox > 0.01 and oy > 0.01


def core_box(t: Token) -> BBox:
    """Vertical extent of a token's ink estimated from baseline and size (0.8 em up, 0.22 em down),
    clipped to its font box. Font boxes of large type overlap neighbouring lines, which made
    absorption cascade through headings (S4 finding, ch01 p1); geometry tests use this box."""
    oy, size = t.origin[1], t.font.size
    return (t.bbox[0], max(t.bbox[1], oy - 0.8 * size), t.bbox[2], min(t.bbox[3], oy + 0.22 * size))


def pad(b: BBox, h: float, v: float) -> BBox:
    return (b[0] - h, b[1] - v, b[2] + h, b[3] + v)


class ZoneBuilder:
    """Holds the zones of one page and the claim of every element on it."""

    def __init__(self, page_no: int, tokens: dict[str, Token], kept: set[str], lines: list[Line],
                 path_boxes: dict[str, BBox], image_boxes: dict[str, BBox], free: set[str]) -> None:
        self.page_no = page_no
        self.tokens = tokens
        self.kept = kept                         # tokens not removed by cleanup
        self.lines = {ln.id: ln for ln in lines}
        self.boxes = {**path_boxes, **image_boxes}
        self.images = set(image_boxes)
        self.free = set(free)                    # path/image ids still without a fate
        self.claim: dict[str, str] = {}          # element id → zone id
        self.zones: dict[str, Zone] = {}
        self._n = 0

    # ------------------------------------------------------------- seeds
    def add(self, kind: ZoneKind, bbox: BBox, *, paths=(), images=(), tokens=(), line_ids=(),
            reason: str, escalation: Escalation = Escalation.TIGHT) -> Zone:
        zid = make_id(self.page_no, "z", self._n)
        self._n += 1
        z = Zone(id=zid, kind=kind, page_no=self.page_no, bbox=bbox, line_ids=list(line_ids),
                 escalation=escalation, reasons=[reason])
        self.zones[zid] = z
        for pid in list(paths) + list(images):
            self._take(z, pid)
        for tid in tokens:
            self._take_token(z, tid)
        return z

    def _take(self, z: Zone, eid: str) -> None:
        if eid in self.claim:
            return
        self.claim[eid] = z.id
        self.free.discard(eid)
        (z.image_ids if eid in self.images else z.path_ids).append(eid)
        z.bbox = union(z.bbox, self.boxes[eid])

    def _take_token(self, z: Zone, tid: str) -> None:
        if tid in self.claim:
            return
        self.claim[tid] = z.id
        z.token_ids.append(tid)
        z.bbox = union(z.bbox, core_box(self.tokens[tid]))

    def _line_tokens(self, lid: str) -> list[str]:
        return [t for t in self.lines[lid].token_ids if t in self.kept]

    # ------------------------------------------------------------ resolve
    def resolve(self) -> None:
        for _ in range(MAX_ROUNDS):
            changed = self._merge() | self._absorb()
            if not changed:
                return
        for z in self.zones.values():
            z.confidence = Confidence.LOW
            z.reasons.append("resolve_round_limit")

    def _merge(self) -> bool:
        changed = False
        ids = sorted(self.zones, key=lambda k: self.zones[k].bbox[1])
        i = 0
        while i < len(ids):
            a = self.zones.get(ids[i])
            if a is None:
                i += 1
                continue
            merged = False
            for j in range(i + 1, len(ids)):
                b = self.zones.get(ids[j])
                if b is None:
                    continue
                if b.bbox[1] > a.bbox[3]:
                    break
                if intersects(a.bbox, b.bbox):
                    self._merge_pair(a, b)
                    changed = merged = True
            if not merged:
                i += 1
        return changed

    def _merge_pair(self, a: Zone, b: Zone) -> None:
        kinds = {a.kind, b.kind}
        lines = list(dict.fromkeys(a.line_ids + b.line_ids))
        if ZoneKind.FIGURE in kinds:
            kind = ZoneKind.FIGURE
        elif ZoneKind.TABLE_IMAGE in kinds:
            kind = ZoneKind.TABLE_IMAGE
        elif kinds == {ZoneKind.INLINE_MATH} and len(lines) == 1:
            kind = ZoneKind.INLINE_MATH
        elif kinds <= {ZoneKind.INLINE_MATH, ZoneKind.LINE_PRESERVE} and len(lines) == 1:
            kind = ZoneKind.LINE_PRESERVE
        elif kinds == {ZoneKind.DISPLAY_MATH}:
            kind = ZoneKind.DISPLAY_MATH
        else:
            kind = ZoneKind.BLOCK_PRESERVE
        a.kind = kind
        a.escalation = {ZoneKind.INLINE_MATH: Escalation.TIGHT, ZoneKind.LINE_PRESERVE: Escalation.LINE}.get(
            kind, Escalation.BLOCK)
        a.bbox = union(a.bbox, b.bbox)
        a.line_ids = lines
        a.merged_from += [b.id] + b.merged_from
        a.reasons += b.reasons
        for eid in b.path_ids + b.image_ids + b.token_ids:
            self.claim[eid] = a.id
        a.path_ids += b.path_ids
        a.image_ids += b.image_ids
        a.token_ids += b.token_ids
        if b.confidence is Confidence.LOW:
            a.confidence = Confidence.LOW
        del self.zones[b.id]

    def _absorb(self) -> bool:
        changed = False
        free_tokens = [t for t in sorted(self.kept, key=id_key) if t not in self.claim]
        for z in list(self.zones.values()):
            before = (z.bbox, len(z.token_ids), len(z.path_ids), len(z.image_ids), z.kind)
            for tid in free_tokens:
                if tid in self.claim:
                    continue
                ox, oy = overlap(z.bbox, core_box(self.tokens[tid]))
                if ox > 0.0 and oy > S1_PT:
                    self._take_token(z, tid)
            for eid in sorted(self.free, key=id_key):
                if intersects(z.bbox, self.boxes[eid]):
                    self._take(z, eid)
            if z.kind is ZoneKind.INLINE_MATH:
                self._check_inline(z)
            if z.kind in (ZoneKind.BLOCK_PRESERVE, ZoneKind.LINE_PRESERVE):
                for tid in list(z.token_ids):
                    lid = self.tokens[tid].line_id
                    if lid and lid not in z.line_ids:
                        z.line_ids.append(lid)
                for lid in z.line_ids:
                    for tid in self._line_tokens(lid):
                        self._take_token(z, tid)
                if z.kind is ZoneKind.LINE_PRESERVE and len(z.line_ids) > 1:
                    z.kind, z.escalation = ZoneKind.BLOCK_PRESERVE, Escalation.BLOCK
                    z.reasons.append("line_merged_neighbour")
            if (z.bbox, len(z.token_ids), len(z.path_ids), len(z.image_ids), z.kind) != before:
                changed = True
        return changed

    def _check_inline(self, z: Zone) -> None:
        line = self.lines[z.line_ids[0]] if z.line_ids else None
        lh = (line.bbox[3] - line.bbox[1]) if line else 10.0
        prose = sum(1 for t in z.token_ids if not self.tokens[t].suspicious)
        other_lines = {self.tokens[t].line_id for t in z.token_ids} - set(z.line_ids)
        reason = None
        if other_lines:
            z.kind, z.escalation = ZoneKind.BLOCK_PRESERVE, Escalation.BLOCK
            z.line_ids += sorted(lid for lid in other_lines if lid)
            z.reasons.append("S2:multi_line")
            return
        if prose > S3_WORDS:
            reason = f"S3:{prose}_prose_words"
        elif (z.bbox[3] - z.bbox[1]) > S4_FACTOR * lh:
            reason = "S4:height"
        if reason:
            z.kind, z.escalation = ZoneKind.LINE_PRESERVE, Escalation.LINE
            z.reasons.append(reason)
            if line:
                z.bbox = union(z.bbox, line.bbox)

    def escalate_crowded_lines(self) -> bool:
        """Spec §4.3 LINE rule: a line with ≥ 4 inline groups, or two groups < 4 pt apart, is kept
        whole as LINE_PRESERVE (mixed text/crop equations render badly in Word, S4 finding)."""
        by_line: dict[str, list[Zone]] = {}
        for z in self.zones.values():
            if z.kind is ZoneKind.INLINE_MATH and len(z.line_ids) == 1:
                by_line.setdefault(z.line_ids[0], []).append(z)
        changed = False
        for lid, zs in by_line.items():
            zs.sort(key=lambda z: z.bbox[0])
            close = any(b.bbox[0] - a.bbox[2] < 4.0 for a, b in zip(zs, zs[1:]))
            if len(zs) >= 4 or close:
                for z in zs:
                    z.kind, z.escalation = ZoneKind.LINE_PRESERVE, Escalation.LINE
                    z.reasons.append("LINE:groups" if len(zs) >= 4 else "LINE:close_groups")
                    z.bbox = union(z.bbox, self.lines[lid].bbox)
                changed = True
        if changed:
            self.resolve()
        return changed

    # ------------------------------------------------------------ checks
    def unclaimed_protected(self, protected: set[str]) -> list[str]:
        return sorted(e for e in protected if e not in self.claim)

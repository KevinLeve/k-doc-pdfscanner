"""Runtime ledger checks G1–G6 (spec §7). A page-level violation sends the page to fallback.

The fate ledger and review status are kept apart: these checks look only at fates; "needs
checking" flags (report) never stand in for a fate.
"""

from __future__ import annotations

from collections import Counter

from pdf2word.ir import (FateKind, IconCallout, InlineImage, PageFallbackBlock, Paragraph, PictureBlock, TextRun,
                         Zone, ZoneKind)
from pdf2word.analyze.sweep import S1_PT, overlap

INLINE_KINDS = (ZoneKind.INLINE_MATH, ZoneKind.LINE_PRESERVE)


def check_page(element_ids: set[str], fates: dict, protected: set[str], zones: dict[str, Zone],
               emitted_boxes: dict[str, tuple]) -> list[str]:
    """G1 every element has exactly one fate; G2 protected elements are inside a zone;
    G3 no emitted token overlaps a zone (it would be printed twice)."""
    v: list[str] = []
    missing = element_ids - set(fates)
    extra = set(fates) - element_ids
    if missing:
        v.append(f"G1: {len(missing)} element(s) without fate, e.g. {sorted(missing)[:3]}")
    if extra:
        v.append(f"G1: {len(extra)} fate(s) for unknown elements, e.g. {sorted(extra)[:3]}")
    for eid in protected:
        f = fates.get(eid)
        if f is None or f.kind is not FateKind.IN_ZONE or f.target not in zones:
            v.append(f"G2: protected {eid} has fate {f.kind.value if f else None}")
    for z in zones.values():
        members = z.token_ids + z.path_ids + z.image_ids
        for eid in members:
            f = fates.get(eid)
            if f is None or f.target != z.id:
                v.append(f"G1: {eid} listed in {z.id} but fate target is {f.target if f else None}")
        for tid, box in emitted_boxes.items():
            ox, oy = overlap(z.bbox, box)
            if ox > S1_PT and oy > S1_PT:
                v.append(f"G3: emitted token {tid} overlaps zone {z.id}")
    return v


def check_document(blocks: list, zones: dict[str, Zone], emitted: set[str], fallback_pages: set[int]) -> list[str]:
    """G4/G5: every zone placed exactly once, every emitted token written exactly once;
    G6: one PageFallbackBlock per fallback page."""
    v: list[str] = []
    zone_uses: Counter[str] = Counter()
    tok_uses: Counter[str] = Counter()
    fb: Counter[int] = Counter()
    def _count_paragraph(p: Paragraph) -> None:
        for x in p.inlines:
            if isinstance(x, InlineImage):
                zone_uses[x.zone_id] += 1
            elif isinstance(x, TextRun):
                tok_uses.update(x.token_ids)

    for b in blocks:
        if isinstance(b, Paragraph):
            _count_paragraph(b)
        elif isinstance(b, IconCallout):
            zone_uses[b.icon_zone_id] += 1
            _count_paragraph(b.paragraph)
        elif isinstance(b, PictureBlock):
            zone_uses[b.zone_id] += 1
        elif isinstance(b, PageFallbackBlock):
            fb[b.page_no] += 1
            if b.zone_id:
                zone_uses[b.zone_id] += 1
    for zid in zones:
        if zone_uses[zid] != 1:
            v.append(f"G5: zone {zid} placed {zone_uses[zid]} times")
    for zid in set(zone_uses) - set(zones):
        v.append(f"G5: unknown zone {zid} placed")
    dup = [t for t, n in tok_uses.items() if n > 1]
    lost = emitted - set(tok_uses)
    unknown = set(tok_uses) - emitted
    if dup:
        v.append(f"G3: {len(dup)} token(s) written more than once, e.g. {dup[:3]}")
    if lost:
        v.append(f"G5: {len(lost)} emitted token(s) not written, e.g. {sorted(lost)[:3]}")
    if unknown:
        v.append(f"G1: {len(unknown)} written token(s) without EMITTED fate, e.g. {sorted(unknown)[:3]}")
    for p in fallback_pages:
        if fb[p] != 1:
            v.append(f"G6: fallback page {p} has {fb[p]} fallback blocks")
    for p in set(fb) - fallback_pages:
        v.append(f"G6: fallback block for non-fallback page {p}")
    return v

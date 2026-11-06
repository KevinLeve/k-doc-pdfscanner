"""Conversion pipeline: PDF → analysed pages → blocks → DOCX → checks → report (spec §2, §4, §7).

Pilot deviations (docs/pilot-deviation-record.md): blocks are kept in memory instead of a JSONL
file; the section page size is the source page size with margins taken from the body text, so
1:1 pictures fit; a page whose analysis raises, or whose ledger check fails, is emitted as one
page image (page fallback) — content is never dropped to keep a page editable.
"""

from __future__ import annotations

import hashlib
import logging
import os
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pdf2word.analyze.classify import classify_images, classify_paths, find_underlines, is_separator
from pdf2word.analyze.cleanup import Cleanup
from pdf2word.analyze.display_math import find_arithmetic_stacks
from pdf2word.analyze.figures import build_clusters, classify_cluster, group_pictures, is_edge_decoration
from pdf2word.analyze.layout import Unit, order_units
from pdf2word.analyze.lines import build_lines
from pdf2word.analyze.paragraphs import build_blocks
from pdf2word.analyze.styles import body_size
from pdf2word.analyze.sweep import S1_PT, ZoneBuilder, core_box, overlap, pad, union
from pdf2word.analyze.table_regions import composite_rules, find_ruled_tables, find_table_regions
from pdf2word.analyze.tokens import build_tokens
from pdf2word.config import DEFAULTS
from pdf2word.extract import create_backend
from pdf2word.extract.backend import BackendError, PdfBackend, RawPage
from pdf2word.ir import (id_key, Confidence, Escalation, Fate, FateKind, PageFallbackBlock, PathKind, Token, Zone,
                         ZoneKind, make_id)
from pdf2word.render.crops import clamp, render
from pdf2word.validate.docx_check import check_docx
from pdf2word.validate.invariants import check_document, check_page
from pdf2word.write.docx_writer import PageSetup, write_docx
from pdf2word.write.omml import build_omath_xml, can_build_omath

log = logging.getLogger("pdf2word")
FALLBACK_DPI = 150
PICTURE_KINDS = (ZoneKind.FIGURE, ZoneKind.DISPLAY_MATH, ZoneKind.BLOCK_PRESERVE, ZoneKind.TABLE_IMAGE)
#: Zone kinds eligible for a native Word equation instead of a picture (pdf2word.write.omml decides
#: per zone whether it is actually safe; everything else always stays a picture, unchanged).
OMATH_KINDS = (ZoneKind.INLINE_MATH, ZoneKind.DISPLAY_MATH)
#: Release 3: maths crops rendered with a transparent background (figures, table images, icons
#: and page fallbacks stay opaque). ``convert(transparent_math=False)`` / CLI ``--opaque-math``
#: restores the Release 2 rendering.
TRANSPARENT_KINDS = (ZoneKind.INLINE_MATH, ZoneKind.DISPLAY_MATH, ZoneKind.LINE_PRESERVE, ZoneKind.BLOCK_PRESERVE)


class Cancelled(Exception):
    pass


class PageCheckError(Exception):
    pass


@dataclass
class PageResult:
    page_no: int
    size: tuple[float, float]
    tokens: dict[str, Token] = field(default_factory=dict)
    fates: dict[str, Fate] = field(default_factory=dict)
    zones: dict[str, Zone] = field(default_factory=dict)
    units: list[Unit] = field(default_factory=list)
    protected: set[str] = field(default_factory=set)
    fallback: bool = False
    error: str = ""
    warnings: tuple[str, ...] = ()
    seconds: float = 0.0
    text_extent: tuple[float, float, float, float] | None = None
    decorations: list[dict] = field(default_factory=list)     # RD-2 evidence: what was removed and why
    element_boxes: dict[str, tuple] = field(default_factory=dict)   # path/image boxes (ledger audit)
    underlined: set[str] = field(default_factory=set)        # RD-6: tokens written with underline


# ------------------------------------------------------------------ page analysis
def analyze_page(raw: RawPage, cleanup: Cleanup) -> PageResult:
    t0 = time.perf_counter()
    pno = raw.page_no
    res = PageResult(pno, (raw.width, raw.height), warnings=raw.backend_warnings)
    tokens = build_tokens(raw)
    res.tokens = tokens
    spans = {s.id: s for s in raw.spans}
    removed = cleanup.classify(tokens, spans, raw.height)
    fates = res.fates
    for tid, reason in removed.items():
        fates[tid] = Fate(FateKind.REMOVED, reason=reason, stage="cleanup")
    kept = set(tokens) - set(removed)
    lines = build_lines(pno, tokens, kept)
    sizes = [tokens[t].font.size for t in kept]
    body = statistics.median(sizes) if sizes else 10.0
    line_h = statistics.median([ln.bbox[3] - ln.bbox[1] for ln in lines]) if lines else 12.0

    paths, backgrounds = classify_paths(raw, lines, body)
    images = classify_images(raw, lines)
    for p in raw.paths:
        if p.id not in paths:
            fates[p.id] = Fate(FateKind.REMOVED, reason="empty", stage="classify")
    for pid, reason in backgrounds.items():
        fates[pid] = Fate(FateKind.REMOVED, reason=reason, stage="classify")
    free = (set(paths) | set(images)) - set(backgrounds)
    zb = ZoneBuilder(pno, tokens, kept, lines, {k: v.bbox for k, v in paths.items()},
                     {k: v.bbox for k, v in images.items()}, free)

    # tables (caption-anchored, kept as an image; caption stays text)
    if lines:
        colw = max(ln.bbox[2] for ln in lines) - min(ln.bbox[0] for ln in lines)
        for cap, bbox in find_table_regions(lines, tokens, {k: paths[k] for k in sorted(zb.free, key=id_key) if k in paths}, colw / 2):
            inside = [e for e in sorted(zb.free, key=id_key) if _inside(zb.boxes[e], bbox)]
            toks = [t for t in sorted(kept, key=id_key) if t not in zb.claim and t not in cap.token_ids
                    and _centre_in(tokens[t].bbox, bbox)]
            z = zb.add(ZoneKind.TABLE_IMAGE, bbox, paths=[e for e in inside if e in paths],
                       images=[e for e in inside if e in images], tokens=toks, reason="table_caption",
                       escalation=Escalation.BLOCK)
            z.caption_token_ids = list(cap.token_ids)

    # R6 (RD-6): validated underlines (spec v1.4 §4.1) become underline formatting of their tokens
    underlines = find_underlines(raw, tokens, lines, {k: paths[k] for k in sorted(zb.free, key=id_key) if k in paths})
    for pid, tids in underlines.items():
        paths[pid].released_by = f"underline:{tids[0]}"
        fates[pid] = Fate(FateKind.EMITTED_TEXT, target=tids[0], reason="underline", stage="classify")
        zb.free.discard(pid)

    # R4 (RD-4): uncaptioned ruled tables, built on composite (dashed / per-cell) rules
    rules = composite_rules({k: paths[k] for k in sorted(zb.free, key=id_key) if k in paths})
    if lines:
        colw = max(ln.bbox[2] for ln in lines) - min(ln.bbox[0] for ln in lines)
        for bbox, rule_ids, line_ids in find_ruled_tables(lines, tokens, rules, colw, paths):
            toks = [t for lid in line_ids for t in zb.lines[lid].token_ids if t in kept and t not in zb.claim]
            z = zb.add(ZoneKind.TABLE_IMAGE, bbox, paths=[r for r in rule_ids if r in zb.free], tokens=toks,
                       reason="R4:ruled_table", escalation=Escalation.BLOCK)
            z.line_ids = list(line_ids)

    # R5 (RD-5): stacked arithmetic (column sums, long division) kept whole as one picture
    free_now = {k: paths[k] for k in sorted(zb.free, key=id_key) if k in paths}
    for bbox, line_ids, path_ids in find_arithmetic_stacks(lines, tokens, free_now):
        toks = [t for lid in line_ids for t in zb.lines[lid].token_ids if t in kept and t not in zb.claim]
        if not toks:
            continue
        zb.add(ZoneKind.BLOCK_PRESERVE, pad(bbox, 1.0, 1.0), paths=[q for q in path_ids if q in zb.free],
               tokens=toks, line_ids=line_ids, reason="R5:arithmetic", escalation=Escalation.BLOCK)

    # separator rules (PD-4), judged per path as in Pilot 1. Composite rules are used only for
    # table detection: judged as one rule they released chart ticks and table underlines (R4 check)
    near = [tokens[t].bbox for t in kept if tokens[t].suspicious]
    near += [p.bbox for p in paths.values() if p.kind is PathKind.GLYPH]
    for pid in sorted(zb.free, key=id_key):
        item = paths.get(pid)
        if item is not None and is_separator(item, near):
            item.released_by = "separator"
            fates[pid] = Fate(FateKind.REMOVED, reason="separator", stage="classify")
            zb.free.discard(pid)

    # clusters of the remaining paths and rasters
    free_paths = {k: paths[k] for k in sorted(zb.free, key=id_key) if k in paths}
    free_images = {k: images[k] for k in sorted(zb.free, key=id_key) if k in images}
    text_box = ((min(ln.bbox[0] for ln in lines), min(ln.bbox[1] for ln in lines),
                 max(ln.bbox[2] for ln in lines), max(ln.bbox[3] for ln in lines)) if lines else None)
    rect_only = {p.id for p in raw.paths if p.segments and all(sg.op in ("re", "l") for sg in p.segments)}
    kept_boxes = [tokens[t].bbox for t in kept]
    clusters = build_clusters(free_paths, free_images, set())
    for c in clusters:
        classify_cluster(c, paths, lines, line_h)
    decorations = [c for c in clusters
                   if is_edge_decoration(c, paths, images, raw.width, raw.height, text_box, kept_boxes, rect_only)]
    # print production marks (registration crosshairs, colour bars...) sit entirely outside the
    # page's own TrimBox, in the bleed margin; they are never part of the typeset page and must
    # never appear as a picture in the document (ir.Fate.reason already anticipates "crop_mark")
    def _near_real_text(reason: str | None) -> bool:
        # "band:<line id>" means the element actually sits next to a real text line - a meaningful,
        # trustworthy signal even in the bleed margin (e.g. content straddling the trim edge
        # slightly). The plain "glyph" classification is shape-only (small + filled): a registration
        # mark's filled centre dot trips it too, and no real text glyph can exist outside the
        # TrimBox at all, so that classification alone is not trusted to protect a mark from removal.
        return bool(reason) and reason.startswith("band:")

    crop_marks = [c for c in clusters if c not in decorations and raw.trimbox is not None
                 and not any(_near_real_text(paths[p].protection_reason) for p in c.path_ids)
                 and not any(images[i].protected for i in c.image_ids)
                 and (c.bbox[2] <= raw.trimbox[0] or c.bbox[0] >= raw.trimbox[2]
                      or c.bbox[3] <= raw.trimbox[1] or c.bbox[1] >= raw.trimbox[3])]
    decorations += crop_marks
    # R3 (RD-3): pieces of one figure (axis + tick labels, icon + label) are grouped up front; each
    # group's zone is created where its first cluster stands, so ungrouped clusters are handled in
    # exactly the order (and so with exactly the merges) of Pilot 1
    core_boxes = [core_box(tokens[t]) for t in sorted(kept, key=id_key) if t not in zb.claim]
    pictures = [c for c in clusters if c.kind in ("figure", "display") and c not in decorations]
    group_of = {}
    # blockers: the zones inline/multi-line clusters will become (a block spans its whole lines)
    blockers = []
    for c in clusters:
        if c.kind == "inline":
            blockers.append(pad(c.bbox, DEFAULTS.inline_pad_h_pt, DEFAULTS.inline_pad_v_pt))
        elif c.kind == "block":
            box = c.bbox
            for lid in c.lines:
                box = union(box, zb.lines[lid].bbox)
            blockers.append(pad(box, 1.0, 1.0))
    for group in group_pictures(pictures, core_boxes, [ln.bbox for ln in lines], blockers=blockers):
        group.sort(key=lambda c: clusters.index(c))
        for c in group:
            group_of[id(c)] = group
    for c in clusters:
        members = dict(paths=c.path_ids, images=c.image_ids)
        if c in decorations:
            is_crop_mark = c in crop_marks
            for eid in c.path_ids + c.image_ids:
                fates[eid] = Fate(FateKind.REMOVED, reason="crop_mark" if is_crop_mark else "edge_decoration",
                                  stage="figures")
                zb.free.discard(eid)
                if is_crop_mark and eid in paths:
                    # explicitly accounts for a path that classify_paths flagged "glyph" on shape
                    # alone (a mark's own small filled centre dot, indistinguishable from a real
                    # character by shape): G2 requires every protected path end up in some zone,
                    # and this one deliberately does not - it is being discarded, on purpose, because
                    # nothing can be a real text glyph outside the page's own TrimBox
                    paths[eid].released_by = "crop_mark"
            if not is_crop_mark:
                # crop marks are a plain geometric fact (outside the page's own TrimBox, never part
                # of the typeset page) and never need a human's review, unlike an edge_decoration
                # guess; they still count in the R/removed accounting via the Fate above
                res.decorations.append({"page": pno, "elements": c.path_ids + c.image_ids,
                                        "bbox": [round(v, 1) for v in c.bbox],
                                        "image_hashes": [images[i].content_hash for i in c.image_ids]})
            continue
        if c.kind == "rule":
            for pid in c.path_ids:
                fates[pid] = Fate(FateKind.REMOVED, reason="rule", stage="figures")
                zb.free.discard(pid)
        elif c.kind in ("figure", "display"):
            group = group_of[id(c)]
            if c is not group[0]:
                continue                      # already placed with its group
            kind = ZoneKind.FIGURE if any(g.kind == "figure" for g in group) else ZoneKind.DISPLAY_MATH
            box = pad(group[0].bbox, 1.0, 1.0)
            for g in group[1:]:
                box = union(box, pad(g.bbox, 1.0, 1.0))
            reason = f"cluster:{c.kind}" if len(group) == 1 else f"R3:label_group({len(group)})"
            zb.add(kind, box, paths=[q for g in group for q in g.path_ids],
                   images=[i for g in group for i in g.image_ids], reason=reason, escalation=Escalation.BLOCK)
        elif c.kind == "inline":
            zb.add(ZoneKind.INLINE_MATH, pad(c.bbox, DEFAULTS.inline_pad_h_pt, DEFAULTS.inline_pad_v_pt),
                   line_ids=c.lines[:1], reason="cluster:inline", **members)
        else:
            box = c.bbox
            for lid in c.lines:
                box = union(box, zb.lines[lid].bbox)
            zb.add(ZoneKind.BLOCK_PRESERVE, pad(box, 1.0, 1.0), line_ids=c.lines, reason="cluster:multi_line",
                   escalation=Escalation.BLOCK, **members)

    # suspicious tokens (math fonts, unmapped characters) not yet inside a zone → inline seeds
    for ln in lines:
        run: list[Token] = []
        for tid in ln.token_ids:
            t = tokens[tid]
            if t.id in kept and t.suspicious and t.id not in zb.claim:
                if run and t.bbox[0] - run[-1].bbox[2] > 3.0:
                    _seed_tokens(zb, run, ln.id)
                    run = []
                run.append(t)
            elif run:
                _seed_tokens(zb, run, ln.id)
                run = []
        if run:
            _seed_tokens(zb, run, ln.id)

    zb.resolve()
    zb.escalate_crowded_lines()
    # an underlined token that still ended inside a picture takes its rule along: the rule is then
    # preserved in that picture, not released (RD-6)
    retake = False
    for pid, tids in underlines.items():
        zid = next((zb.claim[t] for t in tids if t in zb.claim), None)
        if zid is None:
            res.underlined.update(tids)
            continue
        paths[pid].released_by = None
        del fates[pid]
        zb._take(zb.zones[zid], pid)
        retake = True
    if retake:
        zb.resolve()
        if any(t in zb.claim for tids in underlines.values() for t in tids if t in res.underlined):
            raise PageCheckError("underlined token absorbed after its rule was released")

    # a chapter-opener band/sidebar (a FIGURE zone spanning nearly the full page width, at the very
    # top of the page) sometimes measures a touch short of its own wrapped bullet text; the overflow
    # line then leaks out as stray, oddly-styled free text immediately below it, duplicating content
    # the picture already shows. Absorb such text back into the picture - narrowly scoped (near-
    # full-width AND near-top FIGURE zone only, text immediately adjacent and x-overlapping only) so
    # a genuine new paragraph elsewhere on the page is never at risk of being pulled into a picture.
    # Absorbed a whole source LINE at a time, never part of one: taking only some of a line's
    # tokens would grow the zone's bbox across the rest of that same line too, overlapping them
    # (G3 - they would then be printed both in the picture and as separate text).
    OPENER_GAP_PT = 16.0
    for z in sorted(zb.zones.values(), key=lambda z: z.id):
        if (z.kind is not ZoneKind.FIGURE or z.bbox[0] > 20.0 or z.bbox[1] > 20.0
                or (z.bbox[2] - z.bbox[0]) < 0.85 * raw.width):
            continue
        for ln in lines:
            if not (z.bbox[3] - 2.0 <= ln.bbox[1] <= z.bbox[3] + OPENER_GAP_PT):
                continue
            if not (ln.bbox[0] < z.bbox[2] and z.bbox[0] < ln.bbox[2]):   # x-overlaps the zone
                continue
            line_tids = [tid for tid in ln.token_ids if tid in kept and tid not in zb.claim]
            if not line_tids or len(line_tids) != sum(1 for tid in ln.token_ids if tid in kept):
                continue
            grown_bbox = z.bbox
            for tid in line_tids:
                grown_bbox = union(grown_bbox, core_box(tokens[tid]))
            still_free = kept - set(zb.claim) - set(line_tids)
            if any(ox > S1_PT and oy > S1_PT
                  for other in still_free
                  for ox, oy in [overlap(grown_bbox, core_box(tokens[other]))]):
                continue                        # would newly overlap unrelated kept text - skip this line
            for tid in sorted(line_tids, key=id_key):
                zb._take_token(z, tid)

    # a narrow decoration (e.g. a left-anchored gradient strip behind only the first few words of a
    # bulleted line) can leave a FIGURE zone covering only PART of a source line's width: the rest of
    # that same line, past the decoration, still reads as ordinary text over plain background in the
    # source, but gets cut from the middle of the visual line and relocated elsewhere as a stray,
    # out-of-place paragraph. Where a zone already holds SOME of a line's tokens (the zone genuinely
    # touches that line), extend it to the WHOLE line: the picture render re-rasterises the PDF at the
    # zone's own (now wider) bbox, so this is not approximated, it is an exact re-capture, and the
    # zone still only ever grows over a line it already partly covers - never a line, or a further
    # column, it was not already touching.
    SAMELINE_GAP_PT = 24.0
    for z in sorted(zb.zones.values(), key=lambda z: z.id):
        if z.kind is not ZoneKind.FIGURE:
            continue
        grown = True
        while grown:
            grown = False
            for ln in lines:
                line_kept = [tid for tid in ln.token_ids if tid in kept]
                claimed_here = [tid for tid in line_kept if zb.claim.get(tid) == z.id]
                if not claimed_here:
                    continue
                frontier = max(z.bbox[2], max(tokens[tid].bbox[2] for tid in claimed_here))
                for tid in sorted((t for t in line_kept if t not in zb.claim),
                                  key=lambda t: tokens[t].bbox[0]):
                    t = tokens[tid]
                    if t.bbox[0] > frontier + SAMELINE_GAP_PT:
                        break                                 # a real gap: a separate column, not a continuation
                    # growing the zone's bbox (a rectangle) can newly reach OTHER, unrelated kept
                    # tokens nearby (e.g. an adjacent line this zone never otherwise touches) even
                    # though this one specific token is a genuine same-line continuation; never take
                    # it if that would happen - G3 forbids a kept token left as plain text from
                    # overlapping any zone, on pain of the whole page falling back to one picture
                    grown_bbox = union(z.bbox, core_box(t))
                    conflict = any(
                        ox > S1_PT and oy > S1_PT
                        for other in kept - set(zb.claim) - {tid}
                        for ox, oy in [overlap(grown_bbox, core_box(tokens[other]))]
                    )
                    if conflict:
                        break
                    zb._take_token(z, tid)
                    frontier = max(frontier, t.bbox[2])
                    grown = True

    res.protected = {t for t in kept if tokens[t].protected}
    res.protected |= {k for k, v in paths.items() if v.protected and not v.released_by and k not in backgrounds}
    res.protected |= {k for k, v in images.items() if v.protected}
    left = zb.unclaimed_protected(res.protected)
    if left:
        raise PageCheckError(f"G2: protected element(s) outside every zone: {left[:5]}")

    for eid, zid in zb.claim.items():
        fates[eid] = Fate(FateKind.IN_ZONE, target=zid, stage="sweep")
    for tid in sorted(kept - set(zb.claim), key=id_key):
        fates[tid] = Fate(FateKind.EMITTED_TEXT, stage="paragraphs")
    for eid in sorted(zb.free, key=id_key):   # cannot happen: every free element is in a cluster
        fates[eid] = Fate(FateKind.REMOVED, reason="unassigned", stage="sweep")

    # finalise zones
    for z in zb.zones.values():
        z.bbox, z.page_clamped = clamp(z.bbox, raw.width, raw.height)
        z.preview_text = " ".join(tokens[t].raw_text for t in sorted(
            z.token_ids, key=lambda t: (tokens[t].line_id or "", tokens[t].bbox[0])))[:200]
        if z.kind in (ZoneKind.INLINE_MATH, ZoneKind.LINE_PRESERVE):
            if not z.line_ids:
                z.kind, z.escalation = ZoneKind.BLOCK_PRESERVE, Escalation.BLOCK
            else:
                z.baseline_offset_pt = z.bbox[3] - zb.lines[z.line_ids[0]].baseline
        if z.kind is ZoneKind.BLOCK_PRESERVE and len(z.token_ids) > 40:
            z.confidence = Confidence.LOW
    res.zones = dict(zb.zones)
    res.element_boxes = dict(zb.boxes)

    # units in reading order
    emitted_by_line: dict[str, list[str]] = {}
    for tid, f in fates.items():
        if f.kind is FateKind.EMITTED_TEXT and tid in tokens:     # underline paths are not tokens
            emitted_by_line.setdefault(tokens[tid].line_id, []).append(tid)
    inline_by_line: dict[str, list[Zone]] = {}
    units: list[Unit] = []
    for z in res.zones.values():
        if z.kind in PICTURE_KINDS:
            units.append(Unit("picture", pno, z.bbox, zone_id=z.id))
        else:
            inline_by_line.setdefault(z.line_ids[0], []).append(z)
    for ln in lines:
        items = [(tokens[t].bbox[0], ("t", t)) for t in emitted_by_line.get(ln.id, [])]
        items += [(z.bbox[0], ("z", z.id)) for z in inline_by_line.get(ln.id, [])]
        if not items:
            continue
        items.sort(key=lambda x: x[0])
        # the line box (drop caps excluded) plus any inline zones placed on it
        boxes = [ln.bbox] + [res.zones[e].bbox for _, (k, e) in items if k == "z"]
        box = boxes[0]
        for b in boxes[1:]:
            box = union(box, b)
        units.append(Unit("line", pno, box, line=ln, items=[it for _, it in items]))
    res.units = order_units(units, [ln for ln in lines if ln.id in emitted_by_line or ln.id in inline_by_line],
                            raw.width)
    text_lines = [ln for ln in lines if ln.id in emitted_by_line]
    if text_lines:
        res.text_extent = (min(ln.bbox[0] for ln in text_lines), min(ln.bbox[1] for ln in text_lines),
                           max(ln.bbox[2] for ln in text_lines), max(ln.bbox[3] for ln in text_lines))

    # page ledger (G1–G3)
    element_ids = set(tokens) | {p.id for p in raw.paths} | {i.id for i in raw.images}
    emitted_boxes = {t: core_box(tokens[t]) for t, f in fates.items()
                     if f.kind is FateKind.EMITTED_TEXT and t in tokens}
    violations = check_page(element_ids, fates, res.protected, res.zones, emitted_boxes)
    if violations:
        raise PageCheckError("; ".join(violations[:5]))
    res.seconds = time.perf_counter() - t0
    return res


def _seed_tokens(zb: ZoneBuilder, run: list[Token], line_id: str) -> None:
    box = core_box(run[0])
    for t in run[1:]:
        box = union(box, core_box(t))
    zb.add(ZoneKind.INLINE_MATH, pad(box, DEFAULTS.inline_pad_h_pt, DEFAULTS.inline_pad_v_pt),
           tokens=[t.id for t in run], line_ids=[line_id], reason="suspicious_token")


def _inside(b, region) -> bool:
    return b[0] >= region[0] - 1 and b[1] >= region[1] - 1 and b[2] <= region[2] + 1 and b[3] <= region[3] + 1


def _centre_in(b, region) -> bool:
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]


def fallback_page(raw_or_size, page_no: int, error: str, element_ids: set[str], tokens=None) -> PageResult:
    w, h = raw_or_size
    res = PageResult(page_no, (w, h), fallback=True, error=error, tokens=tokens or {})
    zid = make_id(page_no, "z", 9999)
    z = Zone(id=zid, kind=ZoneKind.PAGE_FALLBACK, page_no=page_no, bbox=(0.0, 0.0, w, h),
             escalation=Escalation.BLOCK, confidence=Confidence.LOW, reasons=[error[:300]])
    for eid in element_ids:
        res.fates[eid] = Fate(FateKind.IN_ZONE, target=zid, stage="fallback")
        (z.token_ids if ":t" in eid else z.image_ids if ":i" in eid else z.path_ids).append(eid)
    res.zones = {zid: z}
    return res


# ------------------------------------------------------------------ outputs (R0)
DEFAULT_OUT_SUBDIR = "K-Doc PDFScanner output"


@dataclass
class OutputPaths:
    docx: Path
    report_json: Path
    report_txt: Path
    ledger: Path
    partial: Path
    renamed: bool = False


def reserve_outputs(out_dir: Path, stem: str) -> OutputPaths:
    """First name set ``<stem>``, ``<stem> (2)``, … of which no file exists yet (R0).

    Existing files — an earlier conversion, or a person's own DOCX with the same name — are never
    replaced: the DOCX is moved into place with a rename that fails if the target exists, and
    reports are opened in exclusive-create mode.
    """
    for n in range(1, 10000):
        s = stem if n == 1 else f"{stem} ({n})"
        paths = OutputPaths(out_dir / f"{s}.docx", out_dir / f"{s}.report.json", out_dir / f"{s}.report.txt",
                            out_dir / f"{s}.ledger.json", out_dir / f"{s}.docx.partial", renamed=n > 1)
        if not any(x.exists() for x in (paths.docx, paths.report_json, paths.report_txt, paths.ledger,
                                        paths.partial)):
            return paths
    raise FileExistsError(f"no free output name for {stem!r} in {out_dir}")


def _place_no_overwrite(src: Path, dst: Path) -> None:
    if dst.exists():
        raise FileExistsError(f"refusing to overwrite {dst}")
    os.rename(src, dst)               # Windows: fails if dst appeared in the meantime


# ------------------------------------------------------------------ document
@dataclass
class ConversionResult:
    source: Path
    docx: Path | None
    report_json: Path | None
    report_txt: Path | None
    status: str
    summary: dict


def convert(pdf: Path, out_dir: Path | None = None, *, password: str | None = None,
            progress: Callable[[str, int, int], None] | None = None,
            should_cancel: Callable[[], bool] | None = None,
            backend: PdfBackend | None = None, ledger: bool = False,
            transparent_math: bool = True) -> ConversionResult:
    """Convert one PDF. ``ledger=True`` also writes ``<stem>.ledger.json`` (every element's fate,
    the protected set and zone membership) for independent auditing (tools/pilot_audit.py)."""
    from pdf2word.report.report import build_report, write_report
    from pdf2word.version import collect_versions

    t_start = time.perf_counter()
    pdf = Path(pdf)
    out_dir = Path(out_dir) if out_dir else pdf.parent / DEFAULT_OUT_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    outp = reserve_outputs(out_dir, pdf.stem)
    backend = backend or create_backend()
    progress = progress or (lambda msg, i, n: None)
    should_cancel = should_cancel or (lambda: False)
    docx_path = outp.docx
    try:
        info = backend.open(pdf, password)
        versions = collect_versions(backend)
        n = info.page_count
        skims = {}
        for p in range(1, n + 1):
            if should_cancel():
                raise Cancelled()
            skims[p] = (0.0, backend.skim_page_text(p))
        # page heights for the margin test come from the page reads below; the skim uses the
        # first page's height (the samples have one page size per chapter)
        first = backend.read_page(1)
        skims = {p: (first.height, s) for p, (_, s) in skims.items()}
        cleanup = Cleanup(skims)

        pages: dict[int, PageResult] = {}
        for p in range(1, n + 1):
            if should_cancel():
                raise Cancelled()
            progress(f"Analysing page {p} of {n}", p, n)
            pages[p] = _analyze_or_fallback(backend, p, cleanup)

        # document assembly, with one retry round for pages implicated by document-level checks
        for attempt in range(3):
            blocks, zones, emitted, fb, hyphens = _assemble(pages)
            violations = check_document(blocks, zones, emitted, fb)
            bad_pages = _pages_in(violations) - fb
            if not violations:
                break
            if not bad_pages or attempt == 2:
                raise PageCheckError("document checks failed: " + "; ".join(violations[:5]))
            for p in bad_pages:
                pages[p] = _fallback_for(backend, pages[p], "; ".join(v for v in violations if f"p{p}:" in v)[:500])

        progress("Rendering pictures", n, n)
        images: dict[str, bytes] = {}
        assets: dict[str, str] = {}
        for _ in range(2):
            render_failed = set()
            for z in zones.values():
                if z.id in images:
                    continue
                dpi = FALLBACK_DPI if z.kind is ZoneKind.PAGE_FALLBACK else DEFAULTS.inline_render_dpi
                try:
                    png, digest = render(backend, z.page_no, z.bbox, dpi,
                                         transparent=transparent_math and z.kind in TRANSPARENT_KINDS)
                except BackendError as exc:
                    if z.kind is ZoneKind.PAGE_FALLBACK:
                        raise
                    render_failed.add(z.page_no)
                    log.warning("render failed for %s: %s", z.id, exc)
                    continue
                images[z.id] = png
                z.asset_hash = digest
                assets[digest] = z.id
            if not render_failed:
                break
            for p in render_failed:
                pages[p] = _fallback_for(backend, pages[p], "zone render failed")
            blocks, zones, emitted, fb, hyphens = _assemble(pages)
            images = {k: v for k, v in images.items() if k in zones}

        setup = _page_setup(pages)
        setup.body_pt = body_size([pr.tokens[t] for pr in pages.values() for t, f in pr.fates.items()
                                   if f.kind is FateKind.EMITTED_TEXT and t in pr.tokens])
        # Native Word equations (OMML) instead of a picture, where it is safe to build one (§omml):
        # one line, no vector paths, only plain text/digits/Greek/operators. Everything else — and
        # every zone kind other than inline/display math — keeps today's picture, unchanged.
        all_tokens = {t: tok for pr in pages.values() for t, tok in pr.tokens.items()}
        omath: dict[str, str] = {}
        for z in zones.values():
            if z.kind not in OMATH_KINDS:
                continue
            toks = can_build_omath(z, all_tokens)
            if toks:
                omath[z.id] = build_omath_xml(toks)
        progress("Writing Word document", n, n)
        tmp = outp.partial
        wr = write_docx(tmp, blocks, zones, images, setup, title=pdf.stem, omath=omath)
        g7 = check_docx(tmp, wr.pictures, wr.paragraphs)
        if g7:
            raise PageCheckError("; ".join(g7))
        _place_no_overwrite(tmp, docx_path)
        if ledger:
            _write_ledger(outp.ledger, pages)
        report = build_report(pdf, info, versions, pages, blocks, zones, wr, docx_path,
                              time.perf_counter() - t_start, backend, hyphens=hyphens)
        report["summary"]["renamed_to_avoid_overwrite"] = outp.renamed
        report["summary"]["transparent_math_images"] = sum(png[25] == 6 for png in images.values())
    except Cancelled:
        backend.close()
        outp.partial.unlink(missing_ok=True)
        return ConversionResult(pdf, None, None, None, "Cancelled", {})
    except Exception as exc:    # noqa: BLE001 — every failure is reported, none is silent
        if isinstance(exc, BackendError):      # unreadable / encrypted input: no traceback needed
            log.error("conversion failed: %s: %s", pdf, exc)
        else:
            log.exception("conversion failed: %s", pdf)
        backend.close()
        outp.partial.unlink(missing_ok=True)       # no partial file is left behind
        report = build_report(pdf, None, {}, {}, [], {}, None, None, time.perf_counter() - t_start,
                              None, error=f"{type(exc).__name__}: {exc}")
        report["summary"]["renamed_to_avoid_overwrite"] = outp.renamed
        j, t = write_report(report, outp.report_json, outp.report_txt)
        return ConversionResult(pdf, None, j, t, "Failed", report["summary"])
    backend.close()
    j, t = write_report(report, outp.report_json, outp.report_txt)
    return ConversionResult(pdf, docx_path, j, t, report["summary"]["status"], report["summary"])


def _analyze_or_fallback(backend: PdfBackend, p: int, cleanup: Cleanup) -> PageResult:
    try:
        raw = backend.read_page(p)
    except BackendError as exc:
        # nothing could be read: the page image is the only way to keep its content
        return fallback_page(_page_size(backend, p), p, f"read failed: {exc}", set())
    try:
        return analyze_page(raw, cleanup)
    except Exception as exc:    # noqa: BLE001
        log.warning("page %d → fallback: %s", p, exc)
        ids = {s for s in build_tokens(raw)} | {x.id for x in raw.paths} | {i.id for i in raw.images}
        return fallback_page((raw.width, raw.height), p, f"{type(exc).__name__}: {exc}", ids)


def _fallback_for(backend: PdfBackend, pr: PageResult, error: str) -> PageResult:
    ids = set(pr.fates)
    return fallback_page(pr.size, pr.page_no, error, ids, pr.tokens)


def _page_size(backend: PdfBackend, p: int) -> tuple[float, float]:
    try:
        r = backend.read_page(1)
        return (r.width, r.height)
    except BackendError:
        return (612.0, 792.0)


def _pages_in(violations: list[str]) -> set[int]:
    import re
    return {int(m) for v in violations for m in re.findall(r"\bp(\d+):", v)}


def _assemble(pages: dict[int, PageResult]):
    zones: dict[str, Zone] = {}
    emitted: set[str] = set()
    fb: set[int] = set()
    stream: list = []
    all_tokens: dict[str, Token] = {}
    underlined: set[str] = set()
    for p in sorted(pages):
        pr = pages[p]
        zones.update(pr.zones)
        all_tokens.update(pr.tokens)
        if pr.fallback:
            fb.add(p)
            stream.append(PageFallbackBlock(page_no=p, zone_id=next(iter(pr.zones)), error=pr.error))
            continue
        emitted |= {t for t, f in pr.fates.items() if f.kind is FateKind.EMITTED_TEXT and t in pr.tokens}
        underlined |= pr.underlined
        stream += pr.units
    body = body_size([all_tokens[t] for t in emitted])
    first = [all_tokens[t].font.size for t in emitted
             if t.startswith(f"p{min(pages)}:") and len(all_tokens[t].raw_text) >= 3] if pages else []
    hyphens: list = []
    blocks = build_blocks(stream, all_tokens, zones, body, max(first) if first else None, hyphens, underlined)
    return blocks, zones, emitted, fb, hyphens


def _page_setup(pages: dict[int, PageResult]) -> PageSetup:
    sizes = [pr.size for pr in pages.values()]
    w, h = max(set(sizes), key=sizes.count)
    ext = [pr.text_extent for pr in pages.values() if pr.text_extent and pr.size == (w, h)]
    if not ext:
        return PageSetup(w, h, 54, 54, 54, 54)
    left = statistics.median(e[0] for e in ext)
    right = w - statistics.median(e[2] for e in ext)
    top = min(e[1] for e in ext)
    bottom = h - max(e[3] for e in ext)
    lim = lambda v: max(18.0, min(108.0, v - 4))      # noqa: E731
    return PageSetup(w, h, lim(left), lim(right), lim(top), lim(bottom))


def _write_ledger(path: Path, pages: dict[int, PageResult]) -> None:
    import json
    data = {}
    for p, pr in sorted(pages.items()):
        data[str(p)] = {
            "fallback": pr.fallback,
            "fates": {e: [f.kind.value, f.target, f.reason] for e, f in pr.fates.items()},
            "protected": sorted(pr.protected),
            "zones": {z.id: {"kind": z.kind.value, "tokens": z.token_ids, "paths": z.path_ids,
                             "images": z.image_ids, "bbox": list(z.bbox), "clamped": z.page_clamped,
                             "reasons": z.reasons, "merged_from": z.merged_from} for z in pr.zones.values()},
            # geometry for the containment audit: token core boxes (what absorption uses), path/image boxes
            "boxes": {e: list(core_box(pr.tokens[e]) if e in pr.tokens else pr.element_boxes.get(e, ()))
                      for z in pr.zones.values() for e in z.token_ids + z.path_ids + z.image_ids},
            "removed_text": {e: pr.tokens[e].raw_text for e, f in pr.fates.items()
                             if f.kind is FateKind.REMOVED and e in pr.tokens},
        }
    with open(path, "x", encoding="utf-8") as f:          # exclusive create (R0)
        f.write(json.dumps(data, ensure_ascii=False))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

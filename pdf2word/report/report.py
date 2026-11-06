"""Conversion report: fate accounting (E/V/P/R) and, separately, review flags (spec §7; pilot §4).

Fate accounting counts every source element by its one fate. Review flags ("needs checking")
are a separate list: they point at places worth a human look and never replace a fate.

  E  editable text      tokens with fate EMITTED_TEXT
  V  image-preserved    tokens of maths/unmapped characters, and paths/rasters, inside zones
  P  prose as image     ordinary words that ended up inside a zone (kept, but not editable)
  R  removed by rule    running heads, folios, slugs, rules, decoration boxes (with reasons)
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pdf2word import APP_NAME, __version__
from pdf2word.analyze.display_math import ARITH_TOKEN
from pdf2word.ir import Confidence, FateKind, ZoneKind

STATUS_DONE = "Done"
STATUS_CHECK = "Done – needs checking"
STATUS_PAGES = "Done – some pages kept as images"
STATUS_FAILED = "Failed"


def _is_arithmetic(z) -> bool:
    return any(r.startswith("R5:arithmetic") for r in z.reasons)


def _is_prose(pr, token_id: str, zone_id: str | None) -> bool:
    """P = an ordinary word kept as an image. Maths tokens are V; so are the numbers and operators
    of an R5 arithmetic block, which are maths laid out in two dimensions (RD-5)."""
    t = pr.tokens[token_id]
    if t.suspicious:
        return False
    z = pr.zones.get(zone_id) if zone_id else None
    if z is not None and _is_arithmetic(z) and ARITH_TOKEN.match(t.raw_text):
        return False
    return True


def build_report(pdf: Path, info, versions: dict, pages: dict, blocks: list, zones: dict, wr,
                 docx: Path | None, seconds: float, backend, error: str | None = None,
                 hyphens: list | None = None) -> dict:
    fate = Counter()
    removed = Counter()
    zone_kinds = Counter(z.kind.value for z in zones.values())
    flags: list[dict] = []
    elements = 0
    for p, pr in sorted(pages.items()):
        for eid, f in pr.fates.items():
            elements += 1
            is_token = eid in pr.tokens
            if f.kind is FateKind.EMITTED_TEXT:
                fate["E"] += 1
            elif f.kind in (FateKind.IN_ZONE, FateKind.IN_TABLE):
                fate["P" if is_token and _is_prose(pr, eid, f.target) else "V"] += 1
            elif f.kind is FateKind.REMOVED:
                fate["R"] += 1
                removed[f.reason or "unspecified"] += 1
        if pr.fallback:
            flags.append(dict(page=p, kind="page_fallback", detail=pr.error[:300]))
        for dec in getattr(pr, "decorations", []):      # RD-2: every removed edge decoration is listed
            flags.append(dict(page=p, kind="edge_decoration_removed",
                              detail=f"{len(dec['elements'])} element(s) at {dec['bbox']}"))
        for w in pr.warnings:
            flags.append(dict(page=p, kind="backend_warning", detail=w[:300]))
    for z in zones.values():
        if z.kind is ZoneKind.PAGE_FALLBACK:
            continue
        prose = sum(1 for t in z.token_ids if _is_prose(pages[z.page_no], t, z.id))
        if _is_arithmetic(z):           # RD-5: one review entry per arithmetic block
            flags.append(dict(page=z.page_no, kind="arithmetic_block_as_image", zone=z.id,
                              detail=f"{len(z.token_ids)} tokens: {z.preview_text[:80]}"))
        if prose:                       # every P item is listed (pilot record §4)
            flags.append(dict(page=z.page_no, kind="prose_kept_as_image", zone=z.id,
                              detail=f"{z.kind.value}, {prose} words: {z.preview_text[:80]}"))
        elif z.confidence is Confidence.LOW:
            flags.append(dict(page=z.page_no, kind="low_confidence_zone", zone=z.id, detail=z.kind.value))
        if z.page_clamped:
            flags.append(dict(page=z.page_no, kind="zone_clamped_to_page", zone=z.id, detail=""))
    for page, around in hyphens or []:
        flags.append(dict(page=page, kind="line_end_hyphen_kept", detail=around))
    if wr is not None:
        for zid in wr.scaled:
            flags.append(dict(page=zones[zid].page_no, kind="picture_scaled_to_fit", zone=zid, detail=""))

    fallback_pages = sorted(p for p, pr in pages.items() if pr.fallback)
    if error or docx is None:
        status = STATUS_FAILED
    elif fallback_pages:
        status = f"{STATUS_PAGES} ({len(fallback_pages)})"
    elif flags:
        status = f"{STATUS_CHECK} ({len(flags)})"
    else:
        status = STATUS_DONE
    summary = {
        "status": status,
        "source": str(pdf),
        "docx": str(docx) if docx else None,
        "pages": info.page_count if info else None,
        "fallback_pages": fallback_pages,
        "elements": elements,
        "E_editable_text": fate["E"],
        "V_image_preserved": fate["V"],
        "P_prose_as_image": fate["P"],
        "R_removed_by_rule": fate["R"],
        "removed_reasons": dict(removed),
        "zones": dict(zone_kinds),
        "pictures": wr.pictures if wr else 0,
        "paragraphs": wr.paragraphs if wr else 0,
        "needs_checking": len(flags),
        "seconds": round(seconds, 2),
        "error": error,
    }
    decorations = [d for _, pr in sorted(pages.items()) for d in getattr(pr, "decorations", [])]
    return {
        "app": f"{APP_NAME} {__version__}",
        "removed_edge_decorations": decorations,
        "summary": summary,
        "checks": {"G1-G3 page ledger": "passed on every non-fallback page" if docx else "not reached",
                   "G4-G6 document": "passed" if docx else "not reached",
                   "G7 package reopen (python-docx; not Microsoft Word)": "passed" if docx else "not reached"},
        "versions": versions,
        "source_sha256": info.sha256 if info else None,
        "needs_checking": flags,
    }


def write_report(report: dict, j: Path, t: Path) -> tuple[Path, Path]:
    """Write both reports in exclusive-create mode: an existing file is never replaced (R0)."""
    with open(j, "x", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=1))
    s = report["summary"]
    lines = [f"{APP_NAME} conversion report — {Path(s['source']).name}", "",
             f"Status: {s['status']}"]
    if s.get("error"):
        lines.append(f"Error: {s['error']}")
    if s.get("renamed_to_avoid_overwrite"):
        lines.append("Note: a file with the original name already existed; it was left untouched and "
                     "this output was given a numbered name.")
    lines += [f"Word document: {s['docx']}", f"Pages: {s['pages']}   Time: {s['seconds']} s", "",
              "Source element accounting (every element has exactly one outcome):",
              f"  E editable text ........ {s['E_editable_text']}",
              f"  V kept as image (maths, figures) {s['V_image_preserved']}",
              f"  P prose kept as image .. {s['P_prose_as_image']}",
              f"  R removed by rule ...... {s['R_removed_by_rule']}  {s['removed_reasons']}",
              f"  total .................. {s['elements']}",
              f"Pages kept as whole-page images: {s['fallback_pages'] or 'none'}",
              f"Pictures: {s['pictures']}   Paragraphs: {s['paragraphs']}   Zones: {s['zones']}", "",
              "Automated checks:"]
    lines += [f"  {k}: {v}" for k, v in report["checks"].items()]
    lines += ["", f"Needs checking ({len(report['needs_checking'])}) — review flags, separate from the accounting above:"]
    for f in report["needs_checking"][:500]:
        lines.append(f"  page {f['page']}: {f['kind']} {f.get('zone', '')} {f['detail']}")
    lines += ["", "Versions: " + ", ".join(f"{k} {v}" for k, v in report["versions"].items())]
    with open(t, "x", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return j, t

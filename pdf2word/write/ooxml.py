"""Small raw-OOXML helpers python-docx has no API for (spec §5; M1 decision D4)."""

from __future__ import annotations

from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def set_run_position(run, half_points: int) -> None:
    """``w:position``: raise (positive) or lower (negative) a run, in half-points."""
    if half_points == 0:
        return
    rpr = run._r.get_or_add_rPr()
    for old in rpr.findall(qn("w:position")):
        rpr.remove(old)
    pos = OxmlElement("w:position")
    pos.set(qn("w:val"), str(int(half_points)))
    rpr.append(pos)

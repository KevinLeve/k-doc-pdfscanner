"""Blocks → DOCX with python-docx (spec §5; pilot simplifications).

Pictures are placed at 1:1 source size (D1/D3). Only a picture wider or taller than the text area
is scaled down to fit (recorded as an issue). Inline pictures are lowered onto the text baseline
with ``w:position = -round(2·d)`` half-points, d = crop bottom − baseline (D4). The writer never
sets "exact" line spacing, so D5 (exact → at least) cannot be triggered.

R1 (repair release, RD-1): the python-docx template defaults (11 pt, 1.15 lines, 10 pt after every
paragraph) are replaced by a readable manuscript format: body = source body size clamped to
10–12 pt, single spacing, 4 pt after; headings keep a strictly descending size hierarchy with
space before and keep-with-next; picture-only paragraphs get 2 pt before/after.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Emu, Pt

from pdf2word.ir import IconCallout, InlineImage, PageFallbackBlock, Paragraph, PictureBlock, TextRun, Zone
from pdf2word.write.ooxml import set_run_position
from pdf2word.write.styles_map import resolve

EMU_PER_PT = 12700
ICON_COL_PT = 46.0          # width of an icon callout's left (picture) column


@dataclass
class PageSetup:
    width: float
    height: float
    left: float
    right: float
    top: float
    bottom: float
    body_pt: float = 10.0          # source body size (R1: Word body = clamp(source, 10, 12))

    @property
    def text_width(self) -> float:
        return self.width - self.left - self.right

    @property
    def text_height(self) -> float:
        return self.height - self.top - self.bottom


@dataclass
class WriteResult:
    pictures: int = 0
    paragraphs: int = 0
    equations: int = 0                                    # zones written as native Word equations
    scaled: list[str] = field(default_factory=list)      # zone ids scaled to fit the text area


def _fit(w: float, h: float, setup: PageSetup) -> tuple[float, float, bool]:
    s = min(1.0, setup.text_width / w if w else 1.0, (setup.text_height - 2) / h if h else 1.0)
    return w * s, h * s, s < 1.0


def write_docx(path: Path, blocks: list, zones: dict[str, Zone], images: dict[str, bytes],
               setup: PageSetup, title: str, omath: dict[str, str] | None = None) -> WriteResult:
    """``images``: zone id → PNG bytes; ``zones`` must hold display sizes and baseline offsets.
    ``omath``: zone id → ``<m:oMath>`` XML for zones to write as a native, editable equation instead
    of a picture (pdf2word.write.omml decides which zones qualify; everything else is a picture)."""
    omath = omath or {}
    doc = Document()
    doc.core_properties.title = title
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Pt(setup.width), Pt(setup.height)
    sec.left_margin, sec.right_margin = Pt(setup.left), Pt(setup.right)
    sec.top_margin, sec.bottom_margin = Pt(setup.top), Pt(setup.bottom)
    apply_manuscript_styles(doc, setup.body_pt)
    res = WriteResult()

    def picture(paragraph, zid: str):
        """Appends either a native equation (paragraph-level, sibling of the runs) or a picture run.
        Returns the picture run so callers can position it on the baseline, or None for an equation
        (Word places an inline m:oMath on the line itself; no manual baseline shift is needed)."""
        if zid in omath:
            paragraph._p.append(parse_xml(omath[zid]))
            res.equations += 1
            return None
        run = paragraph.add_run()
        z = zones[zid]
        w, h = z.bbox[2] - z.bbox[0], z.bbox[3] - z.bbox[1]
        w, h, scaled = _fit(w, h, setup)
        if scaled:
            res.scaled.append(zid)
        run.add_picture(io.BytesIO(images[zid]), width=Emu(int(w * EMU_PER_PT)), height=Emu(int(h * EMU_PER_PT)))
        res.pictures += 1
        return run

    def write_inlines(p, inlines: list) -> None:
        """Runs TextRun/InlineImage inlines into paragraph ``p`` - shared by an ordinary paragraph
        and an icon callout's text cell, so bold/italic/sub-superscript/equations behave identically
        in both places."""
        for x in inlines:
            if isinstance(x, TextRun):
                if not x.text:
                    continue
                r = p.add_run(x.text)
                r.bold = x.bold or None
                r.italic = x.italic or None
                if x.underline:
                    r.underline = True
                if x.superscript:
                    r.font.superscript = True
                elif x.subscript:
                    r.font.subscript = True
            elif isinstance(x, InlineImage):
                r = picture(p, x.zone_id)
                if r is not None:          # a picture needs the manual baseline shift; an
                    set_run_position(r, -round(2 * zones[x.zone_id].baseline_offset_pt))  # equation sits on it natively

    def icon_callout(b: IconCallout) -> None:
        """REMEMBER/TIP/WARNING icon beside its paragraph, as the source book lays it out: a
        borderless two-column table (icon | text) instead of a standalone centred picture with the
        paragraph above and below it."""
        text_w = max(72.0, setup.text_width - ICON_COL_PT)
        t = doc.add_table(rows=1, cols=2)
        t.autofit = False
        tblPr = t._tbl.tblPr
        tblPr.append(parse_xml(f'<w:tblLayout {nsdecls("w")} w:type="fixed"/>'))
        tblPr.append(parse_xml(f'<w:tblW {nsdecls("w")} w:w="{round((ICON_COL_PT + text_w) * 20)}" w:type="dxa"/>'))
        tblPr.append(parse_xml(
            f'<w:tblBorders {nsdecls("w")}><w:top w:val="nil"/><w:left w:val="nil"/><w:bottom w:val="nil"/>'
            f'<w:right w:val="nil"/><w:insideH w:val="nil"/><w:insideV w:val="nil"/></w:tblBorders>'))
        tblPr.append(parse_xml(
            f'<w:tblCellMar {nsdecls("w")}><w:top w:w="0" w:type="dxa"/><w:left w:w="0" w:type="dxa"/>'
            f'<w:bottom w:w="0" w:type="dxa"/><w:right w:w="90" w:type="dxa"/></w:tblCellMar>'))
        for gc, w in zip(t._tbl.tblGrid.findall(qn("w:gridCol")), (ICON_COL_PT, text_w)):
            gc.set(qn("w:w"), str(round(w * 20)))
        icon_cell, text_cell = t.rows[0].cells
        icon_cell.width, text_cell.width = Pt(ICON_COL_PT), Pt(text_w)
        icon_p = icon_cell.paragraphs[0]
        icon_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        icon_p.paragraph_format.space_before = icon_p.paragraph_format.space_after = Pt(0)
        picture(icon_p, b.icon_zone_id)
        text_p = text_cell.paragraphs[0]
        text_p.style = resolve(doc, b.paragraph.style_name)
        text_p.paragraph_format.space_before = text_p.paragraph_format.space_after = Pt(0)
        write_inlines(text_p, b.paragraph.inlines)
        after = doc.add_paragraph()
        after.paragraph_format.space_before = Pt(0)
        after.paragraph_format.space_after = Pt(4)
        res.paragraphs += 1     # the table's own cell paragraphs are not body-level (python-docx's
                                # Document.paragraphs, which G7 checks against, does not see them);
                                # only the trailing spacer paragraph is counted here

    for b in blocks:
        if isinstance(b, Paragraph):
            p = doc.add_paragraph(style=resolve(doc, b.style_name))
            if b.list_level:
                p.paragraph_format.left_indent = Pt(18)
            write_inlines(p, b.inlines)
            res.paragraphs += 1
        elif isinstance(b, IconCallout):
            icon_callout(b)
        elif isinstance(b, PictureBlock):
            p = doc.add_paragraph()
            _picture_spacing(p)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if b.centered else WD_ALIGN_PARAGRAPH.LEFT
            picture(p, b.zone_id)
            res.paragraphs += 1
        elif isinstance(b, PageFallbackBlock):
            p = doc.add_paragraph()
            _picture_spacing(p)
            if b.zone_id is not None:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                picture(p, b.zone_id)
            else:
                p.add_run(f"[Page {b.page_no} could not be converted or rendered: {b.error}]").bold = True
            res.paragraphs += 1
    doc.save(str(path))
    return res


# heading sizes relative to the body; strictly descending so the hierarchy stays visible
HEADING_SCALE = {"Title": 2.0, "Heading 1": 1.5, "Heading 2": 1.3, "Heading 3": 1.1}
HEADING_SPACE_BEFORE = {"Title": 0, "Heading 1": 14, "Heading 2": 10, "Heading 3": 8}


def manuscript_body_pt(source_body_pt: float) -> float:
    return min(12.0, max(10.0, round(source_body_pt * 2) / 2))


def apply_manuscript_styles(doc, source_body_pt: float) -> None:
    body = manuscript_body_pt(source_body_pt)
    normal = doc.styles["Normal"]
    normal.font.size = Pt(body)
    pf = normal.paragraph_format
    pf.space_before, pf.space_after, pf.line_spacing = Pt(0), Pt(4), 1.0
    for name, scale in HEADING_SCALE.items():
        try:
            st = doc.styles[name]
        except KeyError:
            continue
        st.font.size = Pt(round(body * scale * 2) / 2)
        st.paragraph_format.space_before = Pt(HEADING_SPACE_BEFORE[name])
        st.paragraph_format.space_after = Pt(4)
        st.paragraph_format.line_spacing = 1.0
        st.paragraph_format.keep_with_next = True
    try:
        cap = doc.styles["Caption"]
        cap.font.size = Pt(max(9.0, body - 1))
        cap.paragraph_format.space_before, cap.paragraph_format.space_after = Pt(2), Pt(6)
    except KeyError:
        pass


def _picture_spacing(p) -> None:
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)

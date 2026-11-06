"""Ordered units → paragraphs and picture blocks (spec §4.7; pilot simplifications).

Line breaks inside a paragraph become spaces, except after a line-end hyphen, which is kept and
joined without a space (no de-hyphenation in the pilot). Paragraphs continue across columns and
pages when the previous line does not end a sentence and the text signature is unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pdf2word.analyze.figures import is_icon
from pdf2word.analyze.layout import Unit
from pdf2word.analyze.sweep import pad
from pdf2word.config import DEFAULTS
from pdf2word.analyze.styles import BULLET, CAPTION, EXAMPLE, NUMBERED, STYLE_OF, role_for
from pdf2word.ir import (Block, IconCallout, InlineImage, PageFallbackBlock, Paragraph, PictureBlock, Role, TextRun,
                         Token, Zone, ZoneKind, make_id)

TERMINAL = (".", "!", "?", ":", ";", "”", "\"", ")")
MARKER = re.compile(r"^\s*([•▪■●◆♦▶➤✓»]|\d+[.)]\s|step\s+\d+|[QA]\.)", re.I)


@dataclass
class _Line:
    unit: Unit
    inlines: list
    text: str
    size: float
    bold: bool


def _set_script(t: Token, line_size: float) -> None:
    if t.size_ratio < 0.9 and t.baseline_shift_pt < -0.2 * line_size:
        t.script = "super"
    elif t.size_ratio < 0.9 and t.baseline_shift_pt > 0.12 * line_size:
        t.script = "sub"


def _main_size(toks: list[Token]) -> float:
    """Character-weighted most common size, so a drop cap does not set the line size."""
    if not toks:
        return 10.0
    weight: dict[float, int] = {}
    for t in toks:
        weight[t.font.size] = weight.get(t.font.size, 0) + len(t.raw_text)
    return max(weight, key=weight.get)


def _zone_ws(z: Zone, tokens: dict[str, Token]) -> tuple[str, str]:
    """Whitespace before the leftmost and after the rightmost token of a zone ("" without tokens)."""
    if not z.token_ids:
        return "", ""
    toks = sorted((tokens[t] for t in z.token_ids), key=lambda t: t.bbox[0])
    return toks[0].ws_before, toks[-1].ws_after_span_end or ""


def _line_inlines(u: Unit, tokens: dict[str, Token], zones: dict[str, Zone],
                  underlined: set[str] = frozenset()) -> _Line:
    toks = [tokens[i] for k, i in u.items if k == "t"]
    size = _main_size(toks)
    chars = sum(len(t.raw_text) for t in toks) or 1
    bold = bool(toks) and sum(len(t.raw_text) for t in toks if t.font.bold) / chars > 0.8
    inlines: list = []
    text = ""
    prev_box = None
    prev_tok: Token | None = None
    prev_ws = ""
    for kind, eid in u.items:
        # zone boxes include the TIGHT padding; measure gaps from the content inside it
        box = tokens[eid].bbox if kind == "t" else pad(zones[eid].bbox, -DEFAULTS.inline_pad_h_pt, 0.0)
        sep = ""
        if prev_box is not None:
            # source whitespace between items: after the previous item (its last token's span-end
            # whitespace) plus before this one; a zone carries the whitespace of its outer tokens
            ws = prev_ws + (tokens[eid].ws_before if kind == "t" else _zone_ws(zones[eid], tokens)[0])
            sep = ws.replace("\n", " ").replace("\r", " ")
            drop_cap = prev_tok is not None and len(prev_tok.raw_text) == 1 and prev_tok.font.size >= 2.2 * size
            if drop_cap:
                sep = ""                     # a drop cap and the rest of its word are one word
            elif not sep and box[0] - prev_box[2] > 0.15 * size:
                sep = " "
        if kind == "t":
            t = tokens[eid]
            _set_script(t, size)
            fmt = (t.font.bold, t.font.italic, t.script == "super", t.script == "sub", t.id in underlined)
            last = inlines[-1] if inlines else None
            if isinstance(last, TextRun) and (last.bold, last.italic, last.superscript, last.subscript,
                                              last.underline) == fmt:
                last.text += sep + t.raw_text
                last.token_ids.append(t.id)
            else:
                if sep and isinstance(last, TextRun):
                    last.text += sep
                elif sep:
                    inlines.append(TextRun(text=sep))
                inlines.append(TextRun(text=t.raw_text, bold=fmt[0], italic=fmt[1], superscript=fmt[2],
                                       subscript=fmt[3], underline=fmt[4], token_ids=[t.id]))
            text += sep + t.raw_text
            prev_tok = t
            prev_ws = t.ws_after_span_end or ""
        else:
            if sep:
                if isinstance(inlines[-1] if inlines else None, TextRun):
                    inlines[-1].text += sep
                else:
                    inlines.append(TextRun(text=sep))
            inlines.append(InlineImage(zone_id=eid))
            text += sep + "□"
            prev_tok = None
            prev_ws = _zone_ws(zones[eid], tokens)[1]
        prev_box = box
    return _Line(u, inlines, text, size, bold)


def build_blocks(stream: list, tokens: dict[str, Token], zones: dict[str, Zone], body: float,
                 title_size: float | None, hyphens: list | None = None,
                 underlined: set[str] = frozenset()) -> list[Block]:
    """``stream`` holds Units and PageFallbackBlocks in document order."""
    blocks: list[Block] = []
    cur: list[_Line] = []
    n = 0
    # a small margin icon (REMEMBER, TIP, WARNING...) is paired with the body paragraph it
    # introduces - icon | text side by side, as the source book lays it out - instead of a
    # standalone centred picture with that paragraph above and below it. Held here until the next
    # flush() produces that paragraph; if nothing suitable follows, it falls back to a plain
    # PictureBlock (appended where it was found) so an icon is never silently dropped.
    pending_icon: str | None = None
    pending_icon_index: int | None = None
    pending_icon_bbox: tuple | None = None

    def flush() -> None:
        nonlocal cur, n, pending_icon, pending_icon_index, pending_icon_bbox
        if not cur:
            return
        inlines: list = []
        for i, ln in enumerate(cur):
            if i and inlines:
                joiner = "" if cur[i - 1].text.endswith("-") else " "
                if not joiner and hyphens is not None:     # kept line-end hyphen → review flag
                    hyphens.append((cur[i - 1].unit.page_no, cur[i - 1].text[-25:] + ln.text[:20]))
                if joiner:
                    if isinstance(inlines[-1], TextRun):
                        inlines[-1].text += joiner
                    else:
                        inlines.append(TextRun(text=joiner))
            inlines += ln.inlines
        text = " ".join(ln.text for ln in cur)
        first = next((ln for ln in cur if any(isinstance(x, TextRun) and x.token_ids for x in ln.inlines)), cur[0])
        role = role_for(text, first.size, first.bold, body, len(text.split()), title_size)
        pages = (cur[0].unit.page_no, cur[-1].unit.page_no)
        para = Paragraph(id=make_id(pages[0], "g", n), role=role, style_name=STYLE_OF[role],
                         inlines=inlines, pages=pages,
                         list_level=1 if role in (Role.LIST_BULLET, Role.LIST_NUMBER) else 0,
                         contains_inline_image=any(isinstance(x, InlineImage) for x in inlines))
        n += 1
        cur = []
        # a true margin icon sits in its own column, to the LEFT of where the body text itself
        # starts (its centre is at or before the paragraph's own left edge); a small CENTRED
        # equation that happens to be icon-sized must never be mistaken for one and pulled out of
        # the page flow - its centre sits well inside the text column, not before it. Pairing is
        # only ever attempted against the paragraph immediately following the icon, win or lose.
        paired = False
        if pending_icon is not None and role is Role.BODY:
            text_x0 = first.unit.bbox[0]
            icon_cx = (pending_icon_bbox[0] + pending_icon_bbox[2]) / 2
            if icon_cx <= text_x0 + 8.0:
                blocks[pending_icon_index] = IconCallout(icon_zone_id=pending_icon, paragraph=para)
                paired = True
        if not paired:
            blocks.append(para)
        pending_icon = pending_icon_index = pending_icon_bbox = None

    for item in stream:
        if isinstance(item, PageFallbackBlock):
            flush()
            pending_icon = pending_icon_index = pending_icon_bbox = None
            blocks.append(item)
            continue
        u: Unit = item
        if u.kind == "picture":
            flush()
            pending_icon = pending_icon_index = pending_icon_bbox = None   # an icon with no paragraph after it stays its own picture
            z = zones[u.zone_id]
            picture_block = PictureBlock(zone_id=z.id, centered=z.kind in (ZoneKind.FIGURE, ZoneKind.DISPLAY_MATH))
            blocks.append(picture_block)
            # a margin icon can be classified either FIGURE or DISPLAY_MATH (depends on how its own
            # badge art is drawn); size/shape alone is why is_icon() exists, the left-of-text check
            # in flush() is what actually keeps a genuine small centred equation out of this path
            if z.kind in (ZoneKind.FIGURE, ZoneKind.DISPLAY_MATH) and is_icon(z.bbox):
                pending_icon, pending_icon_index, pending_icon_bbox = z.id, len(blocks) - 1, z.bbox
            continue
        ln = _line_inlines(u, tokens, zones, underlined)
        if cur and _breaks(cur, ln):
            flush()
        cur.append(ln)
    flush()
    return blocks


def _breaks(cur: list[_Line], ln: _Line) -> bool:
    prev = cur[-1]
    p, u = prev.unit, ln.unit
    ends = prev.text.rstrip().endswith(TERMINAL)
    image_only = not any(isinstance(x, TextRun) for x in ln.inlines)
    if MARKER.match(ln.text) or CAPTION.match(ln.text):
        return True
    caption = bool(CAPTION.match(cur[0].text))          # "Figure 7-1:" + its title keep one paragraph
    if not image_only and not caption and any(isinstance(x, TextRun) for x in prev.inlines):
        if abs(ln.size - prev.size) > 0.1 * prev.size or ln.bold != prev.bold:
            return True
    new_flow = u.page_no != p.page_no or u.column != p.column or u.bbox[1] < p.bbox[1] - 1
    if new_flow:
        return ends or ln.text[:1].isupper() and not prev.text.endswith("-")
    if u.bbox[1] - p.bbox[3] > max(4.0, 0.5 * prev.size):
        return True
    right = max(c.unit.bbox[2] for c in cur)
    if ends and (u.bbox[0] > p.bbox[0] + 6 or p.bbox[2] < right - 30):
        return True
    return False

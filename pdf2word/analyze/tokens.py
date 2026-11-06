"""Backend spans → word tokens with exact characters and whitespace ownership (spec §4.2).

Whitespace is never dropped: a whitespace run is stored in the following token's ``ws_before``
or, at the end of a span, in the last token's ``ws_after_span_end``. Characters are copied
unchanged (no ligature expansion, no substitution).

Repair release RD-7: a space character with zero advance — the next glyph starts where the space
starts — is drawn but occupies no room, so the reader sees no gap ("1·64" printed as "164").
Such a space still splits tokens, but it is not stored as separating whitespace; whether the
words are separated is then decided by the visible gap, as for text without space characters.
"""

from __future__ import annotations

import re

from pdf2word.extract.backend import RawPage, RawSpan
from pdf2word.ir import CharInfo, FontInfo, Token, make_id

_BOLD = re.compile(r"bold|black|heavy|semibold|demi", re.I)
_ITALIC = re.compile(r"italic|oblique", re.I)
ZERO_ADVANCE_PT = 0.3   # RD-7: a space whose next glyph starts within this of it has no visible width
#: Fonts whose glyphs are treated as mathematics (built-in profile, spec §4.1 "profile math font").
MATH_FONT = re.compile(r"symbol|math|stix|mtextra|cmmi|cmsy|cmex|euclid|mt ?extra", re.I)


def font_info(span: RawSpan) -> FontInfo:
    name = span.font_name
    return FontInfo(name=name, size=round(span.size, 2), bold=bool(_BOLD.search(name) or span.flags & 16),
                    italic=bool(_ITALIC.search(name) or span.flags & 2), color=span.color)


def is_suspicious_char(c: str) -> bool:
    o = ord(c)
    # U+FFFD, private use, controls, and anything XML 1.0 cannot carry (surrogates, U+FFFE/U+FFFF)
    return (c == "�" or 0xE000 <= o <= 0xF8FF or (o < 0x20 and c not in "\t\n\r")
            or 0xD800 <= o <= 0xDFFF or o in (0xFFFE, 0xFFFF))


def build_tokens(page: RawPage) -> dict[str, Token]:
    """All tokens of a page, keyed by id, in backend extraction order."""
    out: dict[str, Token] = {}
    n = 0
    for si, span in enumerate(page.spans):
        font = font_info(span)
        math_font = bool(MATH_FONT.search(span.font_name))
        groups: list[tuple[str, list]] = []          # (ws_before, chars)
        ws, cur = "", []
        chars = span.chars
        for i, ch in enumerate(chars):
            if ch.c.isspace():
                if cur:
                    groups.append((ws, cur))
                    ws, cur = "", []
                nxt = next((d for d in chars[i + 1:] if not d.c.isspace()), None)
                if nxt is None or nxt.bbox[0] > ch.bbox[0] + ZERO_ADVANCE_PT:
                    ws += ch.c
            else:
                cur.append(ch)
        if cur:
            groups.append((ws, cur))
            ws = ""
        trailing = ws                                   # whitespace after the last token of the span
        if not groups:
            continue
        for gi, (wsb, chars) in enumerate(groups):
            infos = [CharInfo(c.c, c.bbox, c.origin, c.synthetic) for c in chars]
            raw = "".join(c.c for c in infos)
            bbox = (min(c.bbox[0] for c in chars), min(c.bbox[1] for c in chars),
                    max(c.bbox[2] for c in chars), max(c.bbox[3] for c in chars))
            suspicious = any(is_suspicious_char(c.c) for c in chars) or math_font
            tid = make_id(page.page_no, "t", n)
            n += 1
            out[tid] = Token(
                id=tid, chars=infos, raw_text=raw, ws_before=wsb,
                ws_after_span_end=trailing if gi == len(groups) - 1 else None,
                bbox=bbox, origin=chars[0].origin, font=font, span_flags=span.flags,
                baseline_shift_pt=0.0, size_ratio=1.0, script="none",
                src_order=(span.block_no, si, gi), parent_span=span.id,
                suspicious=suspicious, protected=suspicious)
    return out

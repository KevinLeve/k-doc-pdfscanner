"""Native Word equations (OMML) for simple math zones, instead of a picture.

Conservative by design: a zone becomes a real, editable equation only when every signal says it is
safe — one line, no vector paths (a fraction bar, radical sign or bracket glyph is a path), and
every character is plain text/digits/Greek/standard operators. Anything else (fractions, square
roots, summations, integrals, matrices, multi-line display equations) is left exactly as it is
today: an opaque or transparent picture. We never guess at 2-D layout from flat text.

Sub/superscripts use the token's own ``script`` field (already classified upstream, §4.2) rather
than re-deriving it from baseline position, so this stays consistent with the rest of the pipeline.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

from pdf2word.ir import Token, Zone

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

#: Unicode ranges/characters a plain-text equation run may contain. Deliberately narrow: anything
#: outside this (radicals √, summation ∑, integral ∫, fraction layouts, arrows, large delimiters…)
#: falls back to a picture rather than being approximated.
_SAFE_CHAR = re.compile(
    "[A-Za-z0-9"
    "Ͱ-Ͽἀ-῿"           # Greek and Greek Extended
    r" .,:;!?'\"()\[\]{}"
    "+−‒-―×÷±=<>≠≤≥≈∼"  # + − × ÷ ± = ≠ ≤ ≥ ≈ ∼
    "%/\\^_|~°∞"                 # % / ^ _ | ~ ° ∞
    "]"
)
_MAX_TOKENS = 24


def _tokens_of(zone: Zone, tokens: dict[str, Token]) -> list[Token] | None:
    toks = [tokens[t] for t in zone.token_ids if t in tokens]
    return toks if toks and len(toks) == len(zone.token_ids) else None


#: A token counts as raised/lowered off the dominant baseline only when BOTH its measured vertical
#: shift and its size are clearly off: small jitter (kerning/rounding) must never be read as a
#: subscript. (The upstream ``Token.script`` field is not populated for these zones — confirmed by
#: inspection: real, visually correct subscripts here all carry ``script="none"`` — so classification
#: is done from the measured geometry instead, not from that field.)
_SHIFT_PT = 1.0
_SIZE_RATIO = 0.85


def _script_of(t: Token) -> str:
    if abs(t.baseline_shift_pt) > _SHIFT_PT and t.size_ratio < _SIZE_RATIO:
        return "sub" if t.baseline_shift_pt > 0 else "super"
    return "none"


def can_build_omath(zone: Zone, tokens: dict[str, Token]) -> list[Token] | None:
    """Return the zone's tokens in reading order if a native equation can be built, else None."""
    if zone.path_ids or zone.image_ids:
        return None                                    # a drawn glyph (radical, fraction bar, bracket)
    if len(zone.line_ids) > 1:
        return None                                    # multi-line: stacked/2-D layout, not safe to flatten
    toks = _tokens_of(zone, tokens)
    if not toks or len(toks) > _MAX_TOKENS:
        return None
    for t in toks:
        if any(not _SAFE_CHAR.match(ch) for ch in t.raw_text):
            return None
        if abs(t.baseline_shift_pt) > 7.0 or t.size_ratio < 0.4:
            return None                                # not a normal sub/superscript shift — play safe
    toks.sort(key=lambda t: t.src_order)
    return toks


def _run(text: str, font: "object", italic_override: bool | None = None) -> str:
    bold = "<m:b/>" if font.bold else "<m:b m:val=\"0\"/>"
    italic = font.italic if italic_override is None else italic_override
    ital = "<m:i/>" if italic else "<m:i m:val=\"0\"/>"
    rpr = f'<m:rPr>{bold}{ital}<m:sty m:val="p"/></m:rPr>'
    wrpr = f'<w:rPr><w:rFonts w:ascii="Cambria Math" w:hAnsi="Cambria Math"/>{"<w:b/>" if font.bold else ""}{"<w:i/>" if italic else ""}</w:rPr>'
    return f'<m:r>{rpr}{wrpr}<m:t xml:space="preserve">{escape(text)}</m:t></m:r>'


def _group(toks: list[Token]) -> list[tuple[Token, list[Token], list[Token]]]:
    """[(base, superscripts, subscripts)] — consecutive sub/sup tokens attach to the base before them."""
    groups: list[tuple[Token, list[Token], list[Token]]] = []
    for t in toks:
        sc = _script_of(t)
        if sc == "none" or not groups:
            groups.append((t, [], []))
        elif sc == "super":
            groups[-1][1].append(t)
        else:
            groups[-1][2].append(t)
    return groups


def build_omath_xml(toks: list[Token]) -> str:
    """``<m:oMath>…</m:oMath>`` for an inline equation built from ``toks`` (reading order)."""
    parts = []
    for base, sups, subs in _group(toks):
        inner = _run(base.raw_text, base.font)
        sup_text = "".join(s.raw_text for s in sups)
        sub_text = "".join(s.raw_text for s in subs)
        # each side keeps its OWN font (a subscript letter is often italic even when its base is not,
        # e.g. epsilon-sub-i): the first token of that side decides the run's bold/italic
        sub_run = _run(sub_text, subs[0].font) if subs else ""
        sup_run = _run(sup_text, sups[0].font) if sups else ""
        if sup_text and sub_text:
            parts.append(f'<m:sSubSup><m:e>{inner}</m:e><m:sub>{sub_run}</m:sub>'
                         f'<m:sup>{sup_run}</m:sup></m:sSubSup>')
        elif sub_text:
            parts.append(f'<m:sSub><m:e>{inner}</m:e><m:sub>{sub_run}</m:sub></m:sSub>')
        elif sup_text:
            parts.append(f'<m:sSup><m:e>{inner}</m:e><m:sup>{sup_run}</m:sup></m:sSup>')
        else:
            parts.append(inner)
    return (f'<m:oMath xmlns:m="{M_NS}" xmlns:w="{W_NS}">{"".join(parts)}</m:oMath>')

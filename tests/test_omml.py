"""Unit tests for pdf2word.write.omml: which math zones become native Word equations, and that the
equation XML it builds is correct (in particular, the subscript/superscript regression found while
building this: the upstream Token.script field is not populated for these zones, so classification
must come from the measured baseline_shift_pt/size_ratio, not from .script)."""

from __future__ import annotations

from pdf2word.ir import CharInfo, FontInfo, Token, Zone, ZoneKind
from pdf2word.write.omml import build_omath_xml, can_build_omath

FONT = FontInfo(name="Test", size=10.0, bold=False, italic=False, color=0)
ITALIC_FONT = FontInfo(name="Test-Italic", size=10.0, bold=False, italic=True, color=0)


def tok(id_: str, text: str, order: int, *, baseline_shift_pt: float = 0.0, size_ratio: float = 1.0,
        font: FontInfo = FONT) -> Token:
    chars = [CharInfo(c=c, bbox=(0, 0, 1, 1), origin=(0, 0), synthetic=False) for c in text]
    return Token(id=id_, chars=chars, raw_text=text, ws_before="", ws_after_span_end=None, bbox=(0, 0, 1, 1),
                origin=(0, 0), font=font, span_flags=0, baseline_shift_pt=baseline_shift_pt,
                size_ratio=size_ratio, script="none", src_order=(0, 0, order), parent_span="s")


def zone(token_ids: list[str], *, lines: int = 1, paths: int = 0) -> Zone:
    return Zone(id="z1", kind=ZoneKind.INLINE_MATH, page_no=1, bbox=(0, 0, 10, 10), token_ids=token_ids,
               line_ids=[f"l{i}" for i in range(lines)], path_ids=[f"p{i}" for i in range(paths)])


def test_simple_beta_subscript_is_accepted_and_built_as_sub():
    beta = tok("t1", "β", 0)
    zero = tok("t2", "0", 1, baseline_shift_pt=3.13, size_ratio=0.651)      # real, measured subscript
    tokens = {"t1": beta, "t2": zero}
    z = zone(["t1", "t2"])
    toks = can_build_omath(z, tokens)
    assert toks is not None
    xml = build_omath_xml(toks)
    assert "<m:sSub>" in xml and "<m:sup>" not in xml
    assert ">0<" in xml.split("<m:sub>")[1]       # the "0" landed inside m:sub, not as a plain run


def test_subscript_keeps_its_own_italic_font_not_the_bases():
    eps = tok("t1", "ε", 0, font=FONT)                                      # base: upright
    i = tok("t2", "i", 1, baseline_shift_pt=3.13, size_ratio=0.651, font=ITALIC_FONT)  # subscript: italic
    tokens = {"t1": eps, "t2": i}
    toks = can_build_omath(zone(["t1", "t2"]), tokens)
    xml = build_omath_xml(toks)
    sub_part = xml.split("<m:sub>")[1].split("</m:sub>")[0]
    assert "<m:i/>" in sub_part                   # the subscript run itself is italic


def test_jitter_is_not_mistaken_for_a_subscript():
    a = tok("t1", "x", 0)
    b = tok("t2", "y", 1, baseline_shift_pt=0.3, size_ratio=0.98)          # rounding noise, not a real shift
    toks = can_build_omath(zone(["t1", "t2"]), {"t1": a, "t2": b})
    assert toks is not None
    xml = build_omath_xml(toks)
    assert "<m:sSub>" not in xml and "<m:sSup>" not in xml                 # both stayed plain, same level


def test_zone_with_a_vector_path_is_rejected_fraction_bar_radical_etc():
    a = tok("t1", "x", 0)
    toks = can_build_omath(zone(["t1"], paths=1), {"t1": a})
    assert toks is None


def test_multi_line_zone_is_rejected_2d_layout():
    a = tok("t1", "x", 0)
    toks = can_build_omath(zone(["t1"], lines=2), {"t1": a})
    assert toks is None


def test_unsafe_character_falls_back_to_picture():
    # an integral sign, a radical, or any glyph outside the safe set must never be approximated
    weird = tok("t1", "∫", 0)      # ∫
    toks = can_build_omath(zone(["t1"]), {"t1": weird})
    assert toks is None


def test_extreme_shift_is_rejected_not_a_normal_sub_superscript():
    a = tok("t1", "x", 0)
    b = tok("t2", "y", 1, baseline_shift_pt=20.0, size_ratio=0.5)   # far too large to be a sub/superscript
    toks = can_build_omath(zone(["t1", "t2"]), {"t1": a, "t2": b})
    assert toks is None


def test_plain_text_with_no_shift_has_no_sub_sup_wrapper():
    a = tok("t1", "E", 0)
    b = tok("t2", "=", 1)
    c = tok("t3", "m", 2)
    toks = can_build_omath(zone(["t1", "t2", "t3"]), {"t1": a, "t2": b, "t3": c})
    xml = build_omath_xml(toks)
    assert "<m:sSub" not in xml and "<m:sSup" not in xml
    assert xml.count("<m:r>") == 3

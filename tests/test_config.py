"""Constants locked by the M1 Word spike must not drift silently (IMPLEMENTATION_LOG M1)."""

from __future__ import annotations

from pdf2word.config import DEFAULTS


def test_inline_image_constants_locked_by_m1() -> None:
    assert (DEFAULTS.inline_pad_h_pt, DEFAULTS.inline_pad_v_pt) == (1.0, 1.5)
    assert DEFAULTS.inline_render_dpi == 300
    assert DEFAULTS.inline_position_unit_pt == 0.5
    assert DEFAULTS.inline_exact_spacing_replacement == "at_least"


def test_position_formula_examples_from_m1() -> None:
    """w:position = -round(2·d): the values measured in Word during M1."""
    for d_pt, halfpoints in ((3.753, -8), (3.632, -7), (6.998, -14), (21.889, -44)):
        assert -round(2 * d_pt) == halfpoints
        assert abs(-halfpoints * DEFAULTS.inline_position_unit_pt - d_pt) <= 0.25

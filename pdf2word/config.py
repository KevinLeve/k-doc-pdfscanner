"""Default thresholds (spec v1.4). Profiles may override them from M11 on.

Only values that are already used, or fixed numerically by the spec, are
defined here. Constants locked by the M1 Word spike are added in M1.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Thresholds:
    # Scanned-page detection (inspect, FT-04): almost no text and mostly image.
    scanned_max_chars: int = 20
    scanned_min_image_ratio: float = 0.5
    # Page processing timeout (§4.9).
    page_timeout_s: float = 30.0
    # Containment tolerance for crop regions (§4.3.1).
    epsilon_pt: float = 0.25
    # Debug overlay resolution for `pdf2word dump`.
    dump_dpi: int = 110

    # ---- Inline equation images: locked by the M1 Word spike (IMPLEMENTATION_LOG M1) ----
    # TIGHT crop padding (spec §4.3 level 0). M1 confirmed these values; neighbouring tokens
    # that the padding touches must be absorbed by escalation (S1/S2), never by shrinking.
    inline_pad_h_pt: float = 1.0
    inline_pad_v_pt: float = 1.5
    # Crop render resolution. Word keeps it on save; 600 dpi gave no benefit (WS-05).
    inline_render_dpi: int = 300
    # Baseline placement: Word puts an inline picture's bottom edge on the text baseline, so the
    # picture is lowered with w:position = -round(2 * d) half-points, where
    # d = crop bottom − source text baseline (pt). Measured error ≤ 0.25 pt (WS-01).
    inline_position_unit_pt: float = 0.5
    # Paragraphs that contain an inline picture must not use "exact" line spacing (it clips the
    # picture and overlaps the next line, WS-02); the writer replaces it with "at least" and the
    # same value. Single, multiple and at-least are kept as they are.
    inline_exact_spacing_replacement: str = "at_least"


DEFAULTS = Thresholds()

"""IR conformance with implementation-spec v1.4 §3: fields, order, defaults, enums, guards."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from pdf2word import ir

# Field names in the exact order of spec §3.
SPEC_FIELDS: dict[type, list[str]] = {
    ir.FontInfo: ["name", "size", "bold", "italic", "color"],
    ir.CharInfo: ["c", "bbox", "origin", "synthetic", "glyph_id"],
    ir.Token: ["id", "chars", "raw_text", "ws_before", "ws_after_span_end", "bbox", "origin", "font",
               "span_flags", "baseline_shift_pt", "size_ratio", "script", "src_order", "parent_span",
               "suspicious", "protected", "repairs", "line_id"],
    ir.RepairRecord: ["char_index", "source", "replacement", "rule"],
    ir.PathItem: ["id", "bbox", "kind", "filled", "stroke_width", "n_segments", "shape_key", "protected",
                  "protection_reason", "released_by"],
    ir.ImageItem: ["id", "bbox", "ref", "content_hash", "px_size", "ext", "protected", "released_by"],
    ir.Fate: ["kind", "target", "reason", "stage"],
    ir.Line: ["id", "bbox", "baseline", "x_height", "token_ids", "path_ids", "protection_band", "band", "column"],
    ir.Asset: ["content_hash", "path", "px_size", "ext"],
    ir.Zone: ["id", "kind", "page_no", "bbox", "token_ids", "path_ids", "image_ids", "line_ids", "escalation",
              "page_clamped", "merged_from", "confidence", "reasons", "asset_hash", "replacement_text",
              "baseline_offset_pt", "display_width_pt", "caption_token_ids", "consumed_caption_token_ids",
              "preview_text"],
    ir.TableCell: ["row", "col", "row_span", "col_span", "content"],
    ir.Table: ["id", "page_no", "bbox", "n_rows", "n_cols", "col_widths_pt", "cells", "header_rows", "source",
               "confidence", "replaced_zone_id"],
    ir.PageIR: ["page_no", "size", "tokens", "paths", "images", "lines", "zones", "tables", "fates", "blocks",
                "issues", "timings_ms", "fallback", "extracted"],
    ir.TextRun: ["text", "bold", "italic", "underline", "superscript", "subscript", "token_ids"],
    ir.InlineImage: ["zone_id"],
    ir.InlineOperator: ["zone_id", "text"],
    ir.Paragraph: ["id", "role", "style_name", "inlines", "pages", "list_level", "style_confidence",
                   "contains_inline_image"],
    ir.PictureBlock: ["zone_id", "centered"],
    ir.TableBlock: ["table_id"],
    ir.IconCallout: ["icon_zone_id", "paragraph"],
    ir.PageFallbackBlock: ["page_no", "zone_id", "error"],
    ir.Issue: ["id", "page_no", "severity", "category", "message", "element_id", "bbox"],
    ir.DocumentIR: ["source", "source_sha256", "page_count", "profile_id", "template_used", "versions",
                    "blocks_file", "zones", "tables", "assets", "issues", "stats", "diagnostics", "timings_ms"],
}

SPEC_ENUMS: dict[type, dict[str, object]] = {
    ir.PathKind: {"GLYPH": "glyph", "RULE": "rule", "ART": "art"},
    ir.ZoneKind: {"INLINE_MATH": "inline_math", "DISPLAY_MATH": "display_math", "LINE_PRESERVE": "line_preserve",
                  "BLOCK_PRESERVE": "block_preserve", "FIGURE": "figure", "ICON": "icon",
                  "TABLE_IMAGE": "table_image", "PAGE_FALLBACK": "page_fallback"},
    ir.Escalation: {"TIGHT": 0, "EXPANDED": 1, "LINE": 2, "BLOCK": 3},
    ir.Confidence: {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"},
    ir.FateKind: {"EMITTED_TEXT": "emitted_text", "IN_ZONE": "in_zone", "IN_TABLE": "in_table",
                  "REMOVED": "removed"},
    ir.Severity: {"INFO": "info", "WARNING": "warning", "ERROR": "error"},
}
SPEC_ROLE_NAMES = ["CHAPTER_NUMBER", "CHAPTER_TITLE", "HEADING_1", "HEADING_2", "HEADING_3", "BODY",
                   "LIST_BULLET", "LIST_NUMBER", "CAPTION", "EXAMPLE_Q", "EXAMPLE_A", "OTHER"]


def test_every_ir_class_is_covered() -> None:
    assert set(SPEC_FIELDS) == set(ir.IR_CLASSES)


@pytest.mark.parametrize("cls", list(SPEC_FIELDS), ids=lambda c: c.__name__)
def test_field_names_and_order_match_spec(cls: type) -> None:
    assert [f.name for f in dataclasses.fields(cls)] == SPEC_FIELDS[cls]


@pytest.mark.parametrize("enum", list(SPEC_ENUMS), ids=lambda e: e.__name__)
def test_enum_members_match_spec(enum: type) -> None:
    assert {m.name: m.value for m in enum} == SPEC_ENUMS[enum]


def test_role_members_match_spec() -> None:
    assert [m.name for m in ir.Role] == SPEC_ROLE_NAMES
    assert len({m.value for m in ir.Role}) == len(SPEC_ROLE_NAMES)


@pytest.mark.parametrize("cls", [ir.FontInfo, ir.CharInfo, ir.RepairRecord], ids=lambda c: c.__name__)
def test_frozen_classes(cls: type) -> None:
    assert cls.__dataclass_params__.frozen


def test_defaults_per_spec() -> None:
    z = ir.Zone(id="p1:z0", kind=ir.ZoneKind.INLINE_MATH, page_no=1, bbox=(0, 0, 1, 1))
    assert (z.escalation, z.confidence, z.page_clamped, z.preview_text) == (
        ir.Escalation.TIGHT, ir.Confidence.MEDIUM, False, "")
    assert z.token_ids == [] and z.consumed_caption_token_ids == [] and z.merged_from == []
    assert ir.CharInfo("a", (0, 0, 1, 1), (0, 1), False).glyph_id is None
    assert ir.Fate(ir.FateKind.IN_ZONE).stage == ""
    p = ir.Paragraph(id="p1:g0", role=ir.Role.BODY, style_name="Normal", inlines=[], pages=(1, 1))
    assert (p.list_level, p.style_confidence, p.contains_inline_image) == (0, ir.Confidence.MEDIUM, False)
    assert ir.PictureBlock("p1:z0").centered is True


def _chars(text: str) -> list[ir.CharInfo]:
    return [ir.CharInfo(c, (i, 0, i + 1, 1), (i, 1), False) for i, c in enumerate(text)]


def _token(raw: str = "ﬁnd", **kw) -> ir.Token:
    base = dict(id="p1:t0", chars=_chars(raw), raw_text=raw, ws_before=" ", ws_after_span_end=None,
                bbox=(0, 0, 3, 1), origin=(0, 1), font=ir.FontInfo("F", 9.0, False, False, 0),
                span_flags=0, baseline_shift_pt=0.0, size_ratio=1.0, script="none",
                src_order=(0, 0, 0), parent_span="p1:s0")
    base.update(kw)
    return ir.Token(**base)


def test_token_raw_text_must_equal_joined_chars() -> None:
    assert _token().raw_text == "ﬁnd"
    with pytest.raises(ValueError, match="fidelity"):
        _token(raw_text="find")  # ligature silently expanded → rejected (§4.2)


def test_token_script_values() -> None:
    with pytest.raises(ValueError):
        _token(script="superscript")


def test_make_id() -> None:
    assert ir.make_id(6, "t", 42) == "p6:t42"
    for bad in ((0, "t", 1), (1, "", 1), (1, "t1", 1), (1, "t", -1)):
        with pytest.raises(ValueError):
            ir.make_id(*bad)


def test_document_ir_paths_are_paths(tmp_path: Path) -> None:
    d = ir.DocumentIR(source=tmp_path / "a.pdf", source_sha256="x", page_count=1, profile_id="builtin",
                      template_used=None, versions={}, blocks_file=tmp_path / "b.jsonl", zones={}, tables={},
                      assets={}, issues=[], stats={}, diagnostics={}, timings_ms={})
    assert isinstance(d.source, Path) and d.template_used is None

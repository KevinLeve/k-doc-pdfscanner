"""JSON (de)serialisation round trips for every IR class, unions and edge cases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf2word import ir

FONT = ir.FontInfo("CheltenhamStd-Book", 9.0, False, False, 0x231F20)


def _token(tid: str, raw: str, ws: str = " ", after: str | None = None) -> ir.Token:
    chars = [ir.CharInfo(c, (i, 0.0, i + 1.0, 1.0), (float(i), 1.0), synthetic=(c == " "))
             for i, c in enumerate(raw)]
    return ir.Token(id=tid, chars=chars, raw_text=raw, ws_before=ws, ws_after_span_end=after,
                    bbox=(0.0, 0.0, 3.0, 1.0), origin=(0.0, 1.0), font=FONT, span_flags=4,
                    baseline_shift_pt=-2.5, size_ratio=0.7, script="super", src_order=(3, 1, 0),
                    parent_span="p6:s1", suspicious=True, protected=True,
                    repairs=[ir.RepairRecord(0, "", "−", "T4:SymbolStd:U+E000")], line_id="p6:l2")


def sample_page() -> ir.PageIR:
    t1 = _token("p6:t0", "what’s", ws=" ", after="  ")
    t2 = _token("p6:t1", "ﬁ�")
    path = ir.PathItem("p6:d0", (200.0, 337.2, 204.6, 341.8), ir.PathKind.GLYPH, True, None, 12, "k1",
                       protected=True, protection_reason="glyph")
    img = ir.ImageItem("p6:i0", (1, 2, 3, 4), 17, "abc", (8, 4), "png", protected=True, released_by="figure:p6:z1")
    zone = ir.Zone("p6:z0", ir.ZoneKind.BLOCK_PRESERVE, 6, (1, 2, 3, 4), token_ids=["p6:t1"], path_ids=["p6:d0"],
                   escalation=ir.Escalation.BLOCK, page_clamped=True, merged_from=["p6:z9"],
                   confidence=ir.Confidence.LOW, reasons=["cross_line"], consumed_caption_token_ids=["p6:t0"])
    cell = ir.TableCell(0, 1, col_span=2, content=[ir.TextRun("x"), ir.InlineImage("p6:z0"),
                                                   ir.InlineOperator("p6:z2", "+")])
    table = ir.Table("p6:b0", 6, (0, 0, 9, 9), 2, 3, [3.0, 3.0, 3.0], [cell], 1, "lines", ir.Confidence.HIGH, "p6:z3")
    para = ir.Paragraph("p6:g0", ir.Role.EXAMPLE_Q, "Normal",
                        [ir.TextRun("Add 6", bold=True, token_ids=["p6:t0"]), ir.InlineImage("p6:z0")],
                        (6, 7), contains_inline_image=True)
    return ir.PageIR(
        page_no=6, size=(630.0, 774.0), tokens={t1.id: t1, t2.id: t2}, paths={path.id: path},
        images={img.id: img},
        lines=[ir.Line("p6:l2", (0, 0, 9, 1), 1.0, 0.5, ["p6:t0"], ["p6:d0"], (0, -1, 9, 2), band=1, column=1)],
        zones={zone.id: zone}, tables={table.id: table},
        fates={"p6:t0": ir.Fate(ir.FateKind.EMITTED_TEXT, stage="paragraphs"),
               "p6:t1": ir.Fate(ir.FateKind.IN_ZONE, "p6:z0"),
               "p6:d0": ir.Fate(ir.FateKind.IN_ZONE, "p6:z0"),
               "p6:i0": ir.Fate(ir.FateKind.REMOVED, reason="icon")},
        blocks=[para, ir.PictureBlock("p6:z0", centered=False), ir.TableBlock("p6:b0"),
                ir.PageFallbackBlock(6, None, "render failed")],
        issues=[ir.Issue("p6:x0", 6, ir.Severity.WARNING, "line_preserved", "Line kept as image", "p6:z0",
                         (1, 2, 3, 4)),
                ir.Issue("p6:x1", 6, ir.Severity.INFO, "hyphen_kept", "kept", None, None)],
        timings_ms={"extract": 12.5}, fallback=False, extracted=True,
    )


def test_page_round_trip_is_lossless() -> None:
    page = sample_page()
    back = ir.from_json(ir.to_json(page), ir.PageIR)
    assert back == page
    # Decoding coerces ints to the declared float type; from then on encoding is a fixpoint.
    text = ir.to_json(back)
    assert ir.to_json(ir.from_json(text, ir.PageIR)) == text


def test_union_members_are_restored_by_type_tag() -> None:
    back = ir.from_json(ir.to_json(sample_page()), ir.PageIR)
    assert [type(b).__name__ for b in back.blocks] == ["Paragraph", "PictureBlock", "TableBlock", "PageFallbackBlock"]
    assert [type(i).__name__ for i in back.blocks[0].inlines] == ["TextRun", "InlineImage"]
    assert [type(i).__name__ for i in back.tables["p6:b0"].cells[0].content] == ["TextRun", "InlineImage", "InlineOperator"]


def test_types_restored_exactly() -> None:
    back = ir.from_json(ir.to_json(sample_page()), ir.PageIR)
    tok = back.tokens["p6:t0"]
    assert isinstance(tok.bbox, tuple) and isinstance(tok.src_order, tuple)
    assert tok.font == FONT and isinstance(tok.repairs[0], ir.RepairRecord)
    assert back.zones["p6:z0"].escalation is ir.Escalation.BLOCK
    assert back.lines[0].band == 1


def test_unicode_kept_verbatim_in_json() -> None:
    text = ir.to_json(sample_page())
    for ch in (" ", "’", "ﬁ", "�", "", "−"):
        assert ch in text  # ensure_ascii=False: no escaping, no normalisation


def test_document_ir_round_trip(tmp_path: Path) -> None:
    page = sample_page()
    doc = ir.DocumentIR(source=tmp_path / "ch01.pdf", source_sha256="ab", page_count=16, profile_id="builtin",
                        template_used=None, versions={"PyMuPDF": "2.0"}, blocks_file=tmp_path / "b.jsonl",
                        zones=page.zones, tables=page.tables,
                        assets={"h": ir.Asset("h", tmp_path / "h.png", (10, 20), "png")}, issues=page.issues,
                        stats={"pages": 16}, diagnostics={"D1": 0.97}, timings_ms={"total": 1.0})
    assert ir.from_json(ir.to_json(doc), ir.DocumentIR) == doc


def test_type_tag_present_and_checked() -> None:
    data = json.loads(ir.to_json(ir.TableBlock("p1:b0")))
    assert data == {"_type": "TableBlock", "table_id": "p1:b0"}
    with pytest.raises(ir.IRSerializationError):
        ir.from_jsonable({"_type": "PictureBlock", "zone_id": "x"}, ir.TableBlock)


@pytest.mark.parametrize("bad, hint", [
    ({"_type": "Fate", "kind": "vanished"}, ir.Fate),                     # unknown enum value
    ({"_type": "Fate"}, ir.Fate),                                         # missing required field
    ({"_type": "Fate", "kind": "removed", "extra": 1}, ir.Fate),          # unknown field
    ({"_type": "PictureBlock", "zone_id": 5}, ir.PictureBlock),           # wrong primitive type
    ([1, 2, 3], tuple[float, float, float, float]),                       # wrong tuple length
    ({"_type": "Nope"}, ir.Block),                                        # tag not in union
    (True, int),                                                          # bool is not int
])
def test_decoder_rejects_invalid_data(bad, hint) -> None:
    with pytest.raises(ir.IRSerializationError):
        ir.from_jsonable(bad, hint)


def test_encoder_rejects_unsupported_values() -> None:
    with pytest.raises(ir.IRSerializationError):
        ir.to_jsonable(b"bytes")
    with pytest.raises(ir.IRSerializationError):
        ir.to_jsonable({1: "non-str key"})

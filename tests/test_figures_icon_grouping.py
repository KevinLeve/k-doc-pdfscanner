"""Two independent margin icons (REMEMBER, TIP, WARNING...) stacked close together must stay two
separate pictures, each free to land next to its own paragraph - not get fused into one oversized
block disconnected from both (the defect reported and fixed here)."""

from __future__ import annotations

from pdf2word.analyze.figures import Cluster, group_pictures


def icon_cluster(cid: str, x0: float, y0: float, side: float = 50.0) -> Cluster:
    return Cluster(path_ids=[], image_ids=[cid], bbox=(x0, y0, x0 + side, y0 + side), kind="figure")


def test_two_stacked_icons_stay_separate():
    # REMEMBER at y=100..150, WARNING right below it at y=155..205 (5pt gap, like the reported page)
    a = icon_cluster("remember", 20, 100)
    b = icon_cluster("warning", 20, 155)
    groups = group_pictures([a, b], token_boxes=[], line_boxes=[])
    assert len(groups) == 2
    assert {len(g) for g in groups} == {1}


def test_a_chart_still_joins_its_own_small_label_fragment():
    # the case LABEL_GAP exists for: a (non-square) graphic + a small, non-icon-sized label near it
    chart = Cluster(path_ids=[], image_ids=["chart"], bbox=(20, 100, 220, 260), kind="figure")   # 200x160, not square
    label = Cluster(path_ids=[], image_ids=["label"], bbox=(100, 262, 140, 272), kind="figure")  # 40x10, thin
    groups = group_pictures([chart, label], token_boxes=[], line_boxes=[])
    assert len(groups) == 1 and len(groups[0]) == 2


def test_an_icon_still_joins_a_non_icon_shaped_neighbour():
    # only BOTH sides being a complete icon blocks the join; one icon + one odd-shaped fragment can
    # still be a legitimate icon+its-own-tiny-label pairing
    icon = icon_cluster("remember", 20, 100)
    sliver = Cluster(path_ids=[], image_ids=["sliver"], bbox=(20, 152, 70, 158), kind="figure")  # 50x6, not square
    groups = group_pictures([icon, sliver], token_boxes=[], line_boxes=[])
    assert len(groups) == 1 and len(groups[0]) == 2


def test_two_icons_far_apart_were_never_joined_anyway():
    a = icon_cluster("remember", 20, 100)
    b = icon_cluster("warning", 20, 400)       # far beyond LABEL_GAP
    groups = group_pictures([a, b], token_boxes=[], line_boxes=[])
    assert len(groups) == 2

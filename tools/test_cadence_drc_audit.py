"""防止把未覆盖的 Cadence DRC / 自定义焊盘栈当成已通过。"""
from types import SimpleNamespace
from unittest.mock import patch

from src.brd_analysis.cat_drc import (cap_drc_check, cap_drc_clearance, cap_drc_open,
                                     cap_drc_short, cap_drc_via)
from src.brd_analysis.geom import classify, same_layer_gaps
from src.brd_analysis.imports import parse_drc_report


def ctx(drc=None):
    return SimpleNamespace(exports={"drc": drc}, design={}, segments=lambda _: [],
                           scale_confident=True, howto_drc_report=lambda: "提供报告")


def test_zero_drc_does_not_prove_short_or_open_checks_enabled():
    doc = dict(ok=True, items=[], count=0, by_class={}, source="drc.rpt", unrecognized_blocks=0)
    assert cap_drc_check(ctx(doc)).status == "unavailable"
    assert cap_drc_short(ctx(doc)).status == "unavailable"
    assert cap_drc_open(ctx(doc)).status == "unavailable"


def test_multiple_consecutive_drc_entries_are_not_merged(tmp_path):
    path = tmp_path / "drc.rpt"
    path.write_text("Allegro DRC Report\n\nCLASS: SHORT CIRCUIT\nNET: A\n"
                    "CLASS: UNCONNECTED PIN\nNET: B\n", encoding="utf-8")
    result = parse_drc_report(str(path))
    assert result["count"] == 2
    assert [it["class"] for it in result["items"]] == ["SHORT CIRCUIT", "UNCONNECTED PIN"]
    assert cap_drc_short(ctx(result)).status == "fail"


def test_partial_drc_geometry_never_promoted_to_pass():
    sub = SimpleNamespace(measured=True, status="pass", name_cn="线宽", status_label="通过", summary="局部")
    other = SimpleNamespace(measured=False, status="unavailable", name_cn="间距", status_label="不足", summary="缺")
    with patch("src.brd_analysis.cat_drc.cap_drc_width", return_value=sub), \
         patch("src.brd_analysis.cat_drc.cap_drc_clearance", return_value=other):
        result = cap_drc_check(ctx())
    assert result.status == "unavailable"
    assert "不能作为完整 DRC" in result.summary


def test_truncated_clearance_with_no_results_is_not_a_lower_bound():
    context = ctx()
    context.segments = lambda _: [dict(layer="TOP")]
    scan = dict(segment_count=1, examined=2, min_gap_mm=None, truncated=True)
    with patch("src.brd_analysis.cat_drc.geom.same_layer_gaps", return_value=scan):
        result = cap_drc_clearance(context)
    assert result.status == "unavailable"
    assert "不能确定" in result.summary


def test_padstack_name_never_becomes_via_pass():
    context = ctx()
    context.design = {"via_padstacks": [dict(name="VIA20D10", drill_mm=.254, pad_mm=.508,
                                            annular_ring_mm=.127, kind="unknown")]}
    result = cap_drc_via(context)
    assert result.status == "unavailable"
    assert "无法确认" in result.summary


def test_footprint_outline_is_not_board_outline():
    assert classify(dict(**{"class": "BOARD GEOMETRY", "subclass": "OUTLINE"})) == "outline"
    assert classify(dict(**{"class": "PACKAGE GEOMETRY", "subclass": "OUTLINE"})) != "outline"
    assert classify(dict(**{"class": "ROUTE KEEPOUT", "subclass": "OUTLINE"})) == "keepin"


def test_spatial_search_accounts_for_copper_width():
    segments = [dict(layer="TOP", bbox=(0, 0, 1, 0), pts=[(0, 0), (1, 0)], width_mm=1),
                dict(layer="TOP", bbox=(0, 1.05, 1, 1.05), pts=[(0, 1.05), (1, 1.05)], width_mm=1)]
    scan = same_layer_gaps(segments, .1)
    assert scan["min_gap_mm"] == .05
    assert len(scan["pairs"]) == 1

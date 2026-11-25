"""真实板批测发现的四类问题：缺测量、两种单位、线宽锚点、重复扫描。"""
from types import SimpleNamespace
from unittest.mock import patch
import io
import zipfile

import pytest

from src.brd_design import _resolve_units, _design_units, extract_brd_design
from src.brd_analysis.cat_drc import cap_drc_width
from src.brd_analysis.context import Context
from src.brd_analysis.facts import diff_pair_names
from src.pcb_locations import component_coordinates


def physical(width="3.15:2.10", extra=""):
    return (f'<root><o N="DEFAULT"><a N="MIN_LINE_WIDTH"><v G="{width}"/></a>'
            f'{extra}</o></root>').encode()


def units_objects(width, spacing=""):
    return {"objects/PhysicalCSet.xml": physical(width),
            "objects/SpacingCSet.xml": spacing.encode() or b"<root/>"}


def raw_scan(unit="mil"):
    return dict(units=dict(unit=unit, factor=.0254 if unit == "mil" else 1., source="banner"),
                quickview=dict(found=True, unit=unit))


def width_context(*rows):
    return SimpleNamespace(design={"min_line_width_mm": .1}, segments=lambda _: list(rows))


def segment(width=None, layer="TOP"):
    return dict(layer=layer, has_width=width is not None, width_mm=width or 0.)


def test_no_measured_width_is_unavailable_not_pass():
    result = cap_drc_width(width_context(segment(), segment(layer="BOTTOM")))
    assert result.status == "unavailable"
    assert result.missing
    assert "None" not in result.summary
    assert result.extra["measured_width_count"] == 0


def test_partial_width_coverage_cannot_pass():
    result = cap_drc_width(width_context(segment(.2), segment(layer="BOTTOM")))
    assert result.status == "unavailable"
    assert result.extra["missing_width_count"] == 1
    assert result.extra["complete"] is False


def test_measured_failure_survives_missing_width_and_counts_all():
    result = cap_drc_width(width_context(*([segment(.05)] * 100 + [segment()])))
    assert result.status == "warn"
    assert result.extra["violation_count"] == 100
    assert "100" in result.summary
    assert result.missing


def test_fully_measured_pass_still_works():
    result = cap_drc_width(width_context(segment(.2), segment(.15, "BOTTOM")))
    assert result.status == "unavailable"
    assert result.extra["complete"] is True


@pytest.mark.parametrize("width", [float("nan"), float("inf"), -1])
def test_nonfinite_or_negative_measured_width_is_not_pass(width):
    assert cap_drc_width(width_context(segment(width))).status == "unavailable"


def test_spacing_noise_cannot_override_mil_width_anchors():
    spacing = '<root><o N="DEFAULT">' + ''.join(
        f'<a N="RULE_{i}_SPACING"><v G="0.1:0.1"/></a>' for i in range(80)) + '</o></root>'
    result = _resolve_units(units_objects("3.15:2.10", spacing), raw_scan())
    assert result["unit"] == "mil"
    assert result["factor"] == .0254
    assert result["anchor_values"] == [2.1]


def test_mm_constraint_values_do_not_change_mil_design_units():
    result = _resolve_units(units_objects("0.09:0.09"), raw_scan())
    assert result["unit"] == "mm"
    actual = _design_units(raw_scan())
    assert actual["unit"] == "mil"
    row = component_coordinates({"components": [dict(refdes="U1", x_mm=2.54, y_mm=0)]}, actual["unit"])[0]
    assert row["command"] == "x 100.000000 0.000000"


def test_quickview_has_priority_and_unknown_design_units_stay_unknown():
    scan = raw_scan()
    scan["quickview"]["unit"] = "mm"
    assert _design_units(scan)["unit"] == "mm"
    assert _design_units({})["unit"] is None


def make_board(tmp_path, width):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Header.xml", '<root><revisionNumber physical="1"/></root>')
        archive.writestr("objects/PhysicalCSet.xml", physical(width))
        archive.writestr("objects/SubClass.xml", '<root><o N="Etch/TOP"/><o N="Etch/BOTTOM"/></root>')
        archive.writestr("objects/SpacingCSet.xml", '<root><o N="DEFAULT">' + ''.join(
            f'<a N="RULE_{i}_SPACING"><v G="0.1"/></a>' for i in range(80)) + '</o></root>')
    path = tmp_path / "board.brd"
    path.write_bytes(b"ALL UNITS ARE IN MILS\x00" + content.getvalue())
    return path


def test_extract_keeps_both_unit_contracts_and_recovers_declared_width(tmp_path):
    path = make_board(tmp_path, "3.15:2.10")
    result = extract_brd_design(str(path))
    assert result["ok"]
    assert result["units"] == "mil"
    assert result["constraint_units"] == "mil"
    assert result["min_line_width_mm"] == .0533
    assert result["constraint_declarations"][0]["G"] == "3.15:2.10"


def test_extract_mm_constraints_keeps_coordinate_units(tmp_path):
    result = extract_brd_design(str(make_board(tmp_path, ".09:.09")))
    assert result["units"] == "mil"
    assert result["constraint_units"] == "mm"
    assert result["min_line_width_mm"] == .09
    assert any("约束 XML" in warning for warning in result["warnings"])


def test_empty_diff_pair_fallback_is_scanned_only_once():
    ctx = Context("large.brd")
    ctx._objects = {}
    with patch("src.pcb_reader.extract_brd_info", return_value={"diff_pairs": []}) as scan:
        assert diff_pair_names(ctx) == []
        assert diff_pair_names(ctx) == []
        assert ctx.legacy_info == {"diff_pairs": []}
        scan.assert_called_once_with(ctx.brd_path)


def test_failed_diff_pair_fallback_is_cached():
    ctx = Context("large.brd")
    ctx._objects = {}
    with patch("src.pcb_reader.extract_brd_info", side_effect=OSError("broken")) as scan:
        assert diff_pair_names(ctx) == []
        assert diff_pair_names(ctx) == []
        assert ctx.legacy_info["error"] == "broken"
        scan.assert_called_once()


def test_archive_diff_pairs_need_no_legacy_scan_and_return_copy():
    ctx = Context("large.brd")
    ctx._objects = {"objects/DiffPair.xml": b'<root><o N="PAIR"/></root>'}
    with patch("src.pcb_reader.extract_brd_info") as scan:
        names = diff_pair_names(ctx)
        names.append("changed")
        assert diff_pair_names(ctx) == ["PAIR"]
        scan.assert_not_called()


def test_new_context_does_not_reuse_previous_board_fallback():
    first, second = Context("A.brd"), Context("B.brd")
    first._objects = second._objects = {}
    with patch("src.pcb_reader.extract_brd_info", side_effect=[{"diff_pairs": [{"name": "A"}]}, {"diff_pairs": []}]) as scan:
        assert diff_pair_names(first) == ["A"]
        assert diff_pair_names(second) == []
        assert scan.call_count == 2

"""Cadence 通孔/盲埋孔分类、尺寸来源与定位回归。"""
import io
import zipfile

import pytest

from src.brd_design import extract_brd_design, to_design_params
from src.brd_raw import _scan_padstacks, parse_via_name
from src.dfm_engine import analyze_design
from src.dfm_inputs import _max_drill
from src.pcb_hole_types import cadence_hole_kind, classify_hole, padstack_kind
from src.pick import locate_failures, parse_skill_csv


@pytest.mark.parametrize(("name", "kind"), [
    ("Thru Pin", "through"), ("thru_pin", "through"), ("Via", "unknown"),
    ("BBVia", "blind"), ("bb_via", "blind"), ("Microvia", "blind"),
    ("PIN", "unknown"), ("DRILL", "unknown"), ("other", "unknown"),
])
def test_cadence_types(name, kind):
    assert cadence_hole_kind(name) == kind


@pytest.mark.parametrize(("name", "kind"), [
    ("VIA20D5", "unknown"), ("Thru Pin_20D5", "through"),
    ("BBVIA20D30", "blind"), ("MICROVIA20D5", "blind"),
    ("SOMEVIA20D5", "unknown"), ("VIA_CUSTOM", "unknown"),
])
def test_padstack_type_prefix_is_not_substring(name, kind):
    assert padstack_kind(name) == kind


def test_span_and_object_type_conflicts_are_not_guessed():
    assert classify_hole("VIA20D10_3-10", span=(3, 10), layer_count=12)["kind"] == "blind"
    assert classify_hole("VIA20D10_1-12", span=(1, 12), layer_count=12)["kind"] == "through"
    conflict = classify_hole("arbitrary", cadence_type="BBVia", span=(1, 12), layer_count=12)
    assert conflict["kind"] == "unknown"
    assert "冲突" in conflict["reason"]
    assert classify_hole("unknown", cadence_type="PIN")["kind"] == "unknown"
    assert classify_hole("VIA20D10", cadence_type="VIA", is_through=False)["kind"] == "blind"
    assert classify_hole("MICROVIA20D10", cadence_type="VIA", is_through=True)["kind"] == "through"
    assert classify_hole("VIA20D4")["kind"] == "unknown"
    assert classify_hole("BBVIA20D4")["kind"] == "unknown"


def test_parser_recognises_types_without_treating_them_as_process():
    for name in ("VIA20D5", "BBVIA20D5_1-2.pad", "MicroVia20D5", "Thru Pin_20D5"):
        parsed = parse_via_name(name)
        assert parsed["drill_mil"] == 5
        assert parsed["pad_mil"] == 20
    assert parse_via_name("notVIA20D5") is None
    names = [row["name"] for row in _scan_padstacks(
        b"\x00VIA20D5\x00BBVIA20D5\x00MICROVIA20D5\x00THRU_PIN20D5\x00", .0254)["vias"]]
    assert len(names) == 4


def make_brd(tmp_path):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Header.xml", '<root><revisionNumber physical="1"/></root>')
        archive.writestr("objects/PhysicalCSet.xml", '<root><o N="DEFAULT">'
            '<a N="VIA_LIST"><v V="VIA20D4:THRU_PIN20D5:BBVIA30D25:MICROVIA10D5:unknown"/></a>'
            '</o></root>')
        archive.writestr("objects/SubClass.xml", '<root><o N="Etch/TOP"/><o N="Etch/BOTTOM"/></root>')
    path = tmp_path / "holes.brd"
    path.write_bytes(b"ALL UNITS ARE IN MILS\0" + payload.getvalue())
    return path


def test_real_extraction_preserves_hole_types_and_missing_technology(tmp_path):
    result = extract_brd_design(str(make_brd(tmp_path)))
    assert result["ok"]
    kinds = {row["name"]: row["kind"] for row in result["via_padstacks"]}
    assert all(kind == "unknown" for kind in kinds.values())
    assert result["min_drill_mm"] is None
    assert result["min_laser_via_mm"] is None
    assert result["min_annular_ring_laser_mm"] is None
    assert not result["via_process_unknown"]
    assert "unknown" in {row["name"] for row in result["via_kind_unknown"]}
    design = to_design_params(result)
    assert "min_laser_via" not in design
    analysis = analyze_design(design)
    assert any("焊盘栈孔型或尺寸" in reason for reason in analysis["verdict_reasons"])


def test_max_through_drill_excludes_partial_and_unknown_span():
    info = {"drill_rows": [dict(size_mm=.2, plating="PTH", span="TOP to BOTTOM"),
                           dict(size_mm=.8, plating="PTH", span="TOP to L2"),
                           dict(size_mm=1., plating="PTH", span="UNKNOWN")]}
    assert _max_drill(info) == .2


def test_exported_locations_do_not_mix_through_and_blind(tmp_path):
    path = tmp_path / "evan_holes.csv"
    path.write_text("UNITS,mm,3,1000\nKIND,PADSTACK,X_UU,Y_UU,NET,REFDES,DRILL_UU,IS_THROUGH,HOLE_TYPE\n"
                    "VIA,VIA20D5,1,0,GND,,0.1,YES,ThruVia\n"
                    "VIA,BBVIA20D5,2,0,GND,,0.1,NO,\n"
                    "PIN,custom,3,0,GND,U1,0.1,YES,Thru Pin\n"
                    "PIN,custom,4,0,GND,U2,0.1,,\n", encoding="utf-8")
    holes = parse_skill_csv(str(path))["items"]
    assert [row["hole_kind"] for row in holes] == ["unknown", "unknown", "unknown", "unknown"]
    result = {"items": [dict(field="min_mech_drill", design=.1, capability=.2, status="fail")]}
    located = locate_failures(result, {"holes": holes})[0]
    assert located["coords"] == []


def test_legacy_failed_isthrough_is_not_fabricated_as_blind(tmp_path):
    path = tmp_path / "evan_holes.csv"
    path.write_text("UNITS,mm,3,1000\nKIND,PADSTACK,X_UU,Y_UU,NET,REFDES,DRILL_UU,IS_THROUGH\n"
                    "VIA,unknown,1,0,GND,,0.1,NO\n"
                    "VIA,MICROVIA20D5,2,0,GND,,0.1,NO\n", encoding="utf-8")
    holes = parse_skill_csv(str(path))["items"]
    assert [row["hole_kind"] for row in holes] == ["unknown", "unknown"]

"""S-860-000900：VIA8-16 盲孔不能按机械通孔孔环能力误报。"""
import copy
import io
import zipfile

import pytest

from src.brd_design import extract_brd_design, to_design_params
from src.dfm_engine import analyze_design
from src.dfm_report import build_dfm_workbook
from src.pick import build_inventory, locate_failures


def ring_items(result):
    return [item for item in result["items"]
            if item.get("field") in ("min_annular_ring", "min_annular_ring_laser")]


def test_via8_16_without_span_is_not_a_mechanical_hole(tmp_path):
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Header.xml", '<root><revisionNumber physical="1"/></root>')
        archive.writestr("objects/PhysicalCSet.xml", '<root><o N="DEFAULT">'
                         '<a N="VIA_LIST"><v V="VIA8-16"/></a></o></root>')
        archive.writestr("objects/SubClass.xml", '<root><o N="Etch/TOP"/>'
                         '<o N="Etch/INNER"/><o N="Etch/BOTTOM"/></root>')
    path = tmp_path / "900.brd"
    path.write_bytes(b"ALL UNITS ARE IN MILS\0" + archive_bytes.getvalue())
    extracted = extract_brd_design(str(path))
    assert extracted["ok"]
    via = extracted["via_padstacks"][0]
    assert via["drill_mm"] == .2032
    assert via["annular_ring_mm"] == .1016
    assert via["kind"] == "unknown"
    assert extracted["min_annular_ring_mm"] is None
    assert extracted["min_annular_ring_laser_mm"] is None
    analysis = analyze_design(to_design_params(extracted), board_type="flex")
    assert all(item["status"] == "info" for item in ring_items(analysis))
    assert any("孔型或尺寸无法确认" in reason for reason in analysis["verdict_reasons"])


@pytest.mark.parametrize(("field", "source"), [
    ("min_annular_ring", "焊盘栈命名 (焊盘-钻孔)/2, 机械通孔 (VIA8-16)"),
    ("min_annular_ring_laser", "焊盘栈命名 (焊盘-钻孔)/2, 激光/盲埋孔 (VIA8-16)"),
    ("min_mech_drill", "焊盘栈命名推算"),
    ("min_laser_via", "按孔径 <0.15mm 归为激光孔"),
])
def test_legacy_inferred_values_do_not_reenter_checks_or_report(field, source):
    design = {field: .05, "_sources": {field: {"source": source, "confidence": "medium"}}}
    original = copy.deepcopy(design)
    analysis = analyze_design(design, board_type="flex")
    assert design == original  # 不修改调用方保存的输入。
    assert all(item["status"] == "info" for item in analysis["items"]
               if item.get("field") == field)
    assert not any(issue["severity"] == "危险" and "孔环" in issue["rule"]
                   for issue in analysis["issues"])
    assert any("旧版孔能力输入" in reason for reason in analysis["release_blockers"])
    assert not analysis["releasable"]
    workbook = build_dfm_workbook(analysis)
    summary = [str(cell.value or "") for row in workbook["Issue summary"] for cell in row]
    assert not any("单边孔环(机械孔): 设计" in value for value in summary)
    assert not any("最小激光孔环 0.05mm <" in value for value in summary)
    workbook.close()


@pytest.mark.parametrize("source", [None, "Allegro 实际机械通孔焊盘几何测量"])
def test_confirmed_mechanical_ring_still_reports_failure(source):
    design = {"min_annular_ring": .1016}
    if source:
        design["_sources"] = {"min_annular_ring": {"source": source}}
    analysis = analyze_design(design, board_type="flex")
    assert any(item["item"] == "单边孔环(机械孔)" and item["status"] == "fail"
               for item in analysis["items"])


def test_legacy_yes_flag_and_name_dimensions_cannot_locate_mechanical_ring(tmp_path):
    path = tmp_path / "evan_holes.csv"
    path.write_text("UNITS,mils,2,100\n"
                    "KIND,PADSTACK,X_UU,Y_UU,NET,REFDES,DRILL_UU,IS_THROUGH\n"
                    "VIA,VIA8-16,1198,121.01,PD_SB,,7.87,YES\n", encoding="utf-8")
    inventory = build_inventory("", files=[str(path)])
    hole = inventory["holes"][0]
    assert hole["hole_kind"] == "unknown"
    assert hole["name_pad_hint_mm"] == .4064
    assert "pad_mm" not in hole
    result = {"items": [{"field": "min_annular_ring", "design": .1016,
                         "capability": .1524, "status": "fail"}]}
    located = locate_failures(result, inventory)[0]
    assert located["coords"] == []
    assert located["count"] is None
    assert "工艺未确认" in located["note"]


def test_ring_locations_require_through_structure_and_mechanical_process():
    holes = [{"x_mm": index, "y_mm": 0, "pad_mm": .4, "drill_mm": .2,
              "hole_kind": kind, "technology": technology}
             for index, (kind, technology) in enumerate([
                 ("blind", "mechanical"), ("blind", "laser"), ("unknown", "mechanical"),
                 ("through", None), ("through", "laser"), ("through", "mechanical")])]
    result = {"items": [{"field": "min_annular_ring", "design": .1,
                         "capability": .1524, "status": "fail"}]}
    located = locate_failures(result, {"holes": holes})[0]
    assert located["count"] == 1
    assert located["coords"][0]["x_mm"] == 5

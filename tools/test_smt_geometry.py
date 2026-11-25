"""Object-level SMT geometry and evidence contracts."""
import json
from pathlib import Path

import pytest

from src.smt_engine import analyze_smt, load_smt_profile
from src.smt_geometry import analyze_objects, apply_object_results, load_evidence, sha256_file, _apply_panel_review

ROOT = Path(__file__).resolve().parents[1]
SUPPLIER_PROFILE = ROOT / "config/suppliers/smt/smt_capability_lijing.yaml"


@pytest.fixture
def profile():
    if not SUPPLIER_PROFILE.is_file():
        pytest.skip("本地供应商能力配置缺失: config/suppliers/smt/smt_capability_lijing.yaml")
    return load_smt_profile(SUPPLIER_PROFILE)


def component(ref, category, x, y, side="TOP", **extra):
    return dict(refdes=ref, category=category, side=side, boundary="body",
                outline=[[x, y], [x + 1, y], [x + 1, y + 1], [x, y + 1]], **extra)


def run(profile, components, **other):
    data = dict(_source="fixture.json", board_outline=[[0, 0], [20, 0], [20, 20], [0, 20]],
                components=components, complete={"components": True})
    data.update(other)
    evidence, errors = analyze_objects(profile, "automotive", data)
    result = apply_object_results(analyze_smt(profile, "automotive", {}), evidence, errors)
    return {item["field"]: item for item in result["items"]}, result


def test_mixed_spacing_strictest_and_opposite_side(profile):
    items, result = run(profile, [component("R1", "chip_0402", 5, 5),
                                  component("R2", "chip_0805", 6.35, 5),
                                  component("R3", "chip_0805", 5, 5, side="BOTTOM")])
    assert items["chip_0402_spacing"]["status"] == "fail"
    row = next(r for r in result["object_findings"] if r.get("refdes") == "R1" and r.get("other_refdes") == "R2")
    assert row["actual"] == pytest.approx(0.35)
    assert row["applied_limit"] == 0.4
    assert row["x_mm"] is not None and row["side"] == "TOP"
    assert not any(r.get("other_refdes") == "R3" for r in result["object_findings"])


def test_boundary_and_rotated_geometry(profile):
    rotated = dict(refdes="R2", category="chip_0402", side="TOP", boundary="body",
                   outline=[[6.3, 5], [7.3, 5], [7.3, 6], [6.3, 6]])
    items, _ = run(profile, [component("R1", "chip_0402", 5, 5), rotated])
    assert items["chip_0402_spacing"]["status"] == "pass"
    rotated["outline"] = [[6.299, 5], [7.299, 5], [7.299, 6], [6.299, 6]]
    items, _ = run(profile, [component("R1", "chip_0402", 5, 5), rotated])
    assert items["chip_0402_spacing"]["status"] == "fail"


def test_edge_and_unknown_never_pass(profile):
    items, _ = run(profile, [component("R1", "chip_0402", 0.5, 5),
                             component("U1", "unknown", 10, 10)])
    assert items["component_to_edge"]["status"] == "fail"
    assert items["chip_0402_spacing"]["status"] == "review"
    items, _ = run(profile, [dict(refdes="R1", side="TOP", category="chip_0402")])
    assert items["chip_0402_spacing"]["status"] == "review"


def test_qfn_0603_uses_conditional_larger_limit(profile):
    items, result = run(profile, [component("U1", "qfn", 5, 5), component("R1", "chip_0603", 6.37, 5)])
    assert items["qfn_spacing"]["status"] == "fail"
    assert any(row.get("applied_limit") == 0.4 for row in result["object_findings"] if row.get("field") == "qfn_spacing")


def test_object_evidence_must_replace_manual_pass(profile):
    facts = {("design", "mark_keepout"): dict(value=10, unit="mm", source="人工")}
    evidence, errors = analyze_objects(profile, "automotive", {"_source": "fixture", "complete": {}})
    result = apply_object_results(analyze_smt(profile, "automotive", facts), evidence, errors)
    assert next(i for i in result["items"] if i["field"] == "mark_keepout")["status"] == "missing"


def test_marks_and_tooling_identity(profile):
    marks = [dict(id="m1", global_confirmed=True, side="TOP", x_mm=5, y_mm=5, diameter_mm=1),
             dict(id="m2", global_confirmed=True, side="TOP", x_mm=6, y_mm=5, diameter_mm=1),
             dict(id="m3", global_confirmed=True, side="TOP", x_mm=7, y_mm=5, diameter_mm=1)]
    holes = [dict(id="h1", tooling_confirmed=True, x_mm=2, y_mm=5, diameter_mm=3.1, plated=True)]
    items, _ = run(profile, [component("R1", "chip_0402", 5, 8)], marks=marks, tooling_holes=holes,
                   complete={"components": True, "marks": True, "tooling_holes": True})
    assert items["mark_count"]["status"] == "review"
    assert items["tooling_hole_diameter"]["status"] == "fail"
    assert items["tooling_hole_to_edge"]["status"] == "fail"
    marks[2]["global_confirmed"] = False
    items, _ = run(profile, [component("R1", "chip_0402", 5, 8)], marks=marks,
                   complete={"components": True, "marks": True})
    assert items["mark_count"]["status"] == "review"


def test_hash_alignment_and_lijing_dimensions(profile, tmp_path):
    brd = tmp_path / "board.brd"
    brd.write_bytes(b"test")
    path = tmp_path / "evidence.json"
    data = dict(schema_version=1, brd_sha256=sha256_file(brd), coordinate_system="brd", unit="mm", coordinate_alignment_confirmed=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_evidence(path, brd)["unit"] == "mm"
    brd.write_bytes(b"different board")
    with pytest.raises(ValueError, match="SHA-256"):
        load_evidence(path, brd)
    rules = {r["field"]: r for r in profile["profiles"]["automotive"]["rules"]}
    assert rules["panel_size_x"]["max"] == 300
    assert rules["panel_size_y"]["max"] == 250
    facts = {("design", f): dict(value=v, unit="mm", source="test") for f, v in
             (("panel_size_x", 300), ("panel_size_y", 250), ("board_thickness", 1.0))}
    result = analyze_smt(profile, "automotive", facts)
    _apply_panel_review(result, facts)
    assert next(i for i in result["items"] if i["field"] == "panel_size_x")["status"] == "review"


def test_actual_board_coordinates_without_shape_are_review(profile, tmp_path):
    root = ROOT / "store-brd"
    brd = next(root.glob("*.brd"))
    coords = root / "定位清单/allegro_bridge/evan_components.csv"
    data = {"schema_version": 1, "brd_sha256": sha256_file(brd), "coordinate_system": "brd", "unit": "mm",
            "coordinate_alignment_confirmed": True, "components_file": str(coords),
            "components_file_sha256": sha256_file(coords), "complete": {"components": True}}
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_evidence(path, brd)
    evidence, errors = analyze_objects(profile, "automotive", loaded)
    result = apply_object_results(analyze_smt(profile, "automotive", {}), evidence, errors)
    items = {item["field"]: item for item in result["items"]}
    assert brd.is_file()
    assert items["component_to_edge"]["status"] == "review"


def test_verified_gerber_pad_silk_and_report(profile, tmp_path):
    from openpyxl import load_workbook
    from src.smt_engine import export_smt_report

    gerber = "%FSLAX36Y36*%\n%MOMM*%\n%ADD10C,0.100*%\nD10*\nX1000000Y1000000D02*\nX2000000Y1000000D01*\nM02*\n"
    mask = "%FSLAX36Y36*%\n%MOMM*%\n%ADD10C,0.500*%\nD10*\nX1500000Y1000000D03*\nM02*\n"
    silk_path, mask_path = tmp_path / "top.gto", tmp_path / "top.gts"
    silk_path.write_text(gerber, encoding="ascii")
    mask_path.write_text(mask, encoding="ascii")
    data = {"_source": "fixture.json", "gerber_alignment_confirmed": True,
            "gerber_sha256": {"top.gto": sha256_file(silk_path), "top.gts": sha256_file(mask_path)},
            "complete": {"no_silkscreen_bottom": True, "pads_top": True, "silkscreen_text": True},
            "pads": [{"side": "TOP", "confirmed": True, "refdes": "U1",
                      "outline": [[1.4, .9], [1.6, .9], [1.6, 1.1], [1.4, 1.1]]}],
            "silkscreen_text": [{"side": "TOP", "confirmed": True, "height_mm": 0.7, "x_mm": 1, "y_mm": 1}]}
    evidence, errors = analyze_objects(profile, "automotive", data, str(tmp_path))
    result = apply_object_results(analyze_smt(profile, "automotive", {}), evidence, errors)
    items = {item["field"]: item for item in result["items"]}
    assert not errors
    assert items["silkscreen_width"]["status"] == "fail"
    assert items["silkscreen_to_mask"]["status"] == "fail"
    assert items["silkscreen_clearance"]["status"] == "fail"
    assert items["silkscreen_char_height"]["status"] == "fail"
    assert any(row.get("x_mm") is not None for row in result["object_findings"] if row["field"] == "silkscreen_clearance")
    report = tmp_path / "smt.xlsx"
    export_smt_report(result, report)
    wb = load_workbook(report, read_only=True)
    assert wb["对象问题"].max_row > 1
    wb.close()

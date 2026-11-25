"""Lijing integration: no false pass, supplier/baseline distinction and report parity."""
import copy
import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from src.services.smt_analysis import analyze_smt_workflow, design_facts, evidence_template
from src.smt_engine import analyze_smt, compare_smt_baseline, export_smt_report, load_smt_profile
from src.smt_geometry import analyze_objects, apply_object_results
from tools.test_smt_geometry import component

ROOT = Path(__file__).resolve().parents[1]
SUPPLIER_PROFILE = ROOT / "config/suppliers/smt/smt_capability_lijing.yaml"
BASELINE_PROFILE = ROOT / "config/internal/smt/smt_design_rules.yaml"


@pytest.fixture
def profiles():
    missing = [str(p.relative_to(ROOT)) for p in (SUPPLIER_PROFILE, BASELINE_PROFILE) if not p.is_file()]
    if missing:
        pytest.skip("本地 SMT 配置缺失: " + ", ".join(missing))
    return (load_smt_profile(SUPPLIER_PROFILE), load_smt_profile(BASELINE_PROFILE))


def objects(doc, **extra):
    data = dict(_source="fixture.json", board_outline=[[0, 0], [20, 0], [20, 20], [0, 20]],
                components=[component("R1", "chip_0402", 5, 5)], complete={"components": True})
    data.update(extra)
    evidence, errors = analyze_objects(doc, "automotive", data)
    result = apply_object_results(analyze_smt(doc, "automotive", {}), evidence, errors)
    return {row["field"]: row for row in result["items"]}, result


def test_panel_arrays_shared_and_rotated_with_thickness(profiles):
    doc, base = profiles
    fields = [(r["scope"], r["field"]) for r in doc["profiles"]["automotive"]["rules"]]
    facts = design_facts(dict(panel_size=[250, 300], board_thickness=1.6), fields)
    result = analyze_smt_workflow(doc, "automotive", facts, baseline=base)
    rows = {r["field"]: r for r in result["items"]}
    assert rows["panel_size_x"]["actual"] == 300
    assert rows["panel_size_y"]["actual"] == 250
    assert rows["panel_size_x"]["status"] == "pass"
    facts[("design", "board_thickness")]["value"] = 1
    assert analyze_smt(doc, "automotive", facts)["items"][0]["status"] == "review"
    facts[("design", "panel_size_x")] = dict(value=300000, unit="um", source="test")
    facts[("design", "panel_size_y")] = dict(value=250000, unit="um", source="test")
    assert analyze_smt(doc, "automotive", facts)["items"][0]["actual"] == 300


def test_same_input_supplier_pass_internal_fail(profiles):
    doc, base = profiles
    facts = {("design", "bga_to_edge"): dict(value=2, unit="mm", source="confirmed")}
    result = analyze_smt_workflow(doc, "automotive", facts, baseline=base)
    get = lambda result: next(r for r in result["items"] if r["field"] == "bga_to_edge")
    assert get(result)["status"] == "pass"
    assert get(result["baseline_result"])["status"] == "fail"
    rows = {r["field"]: r for r in result["baseline_comparison"]}
    assert rows["bga_to_edge"]["status"] == "pass"
    assert rows["thermal_pad_aperture"]["status"] == "unconfigured"


def test_baseline_intervals_convert_units(profiles):
    doc, base = copy.deepcopy(profiles)
    rule = next(r for r in doc["profiles"]["automotive"]["rules"] if r["field"] == "qfp_spacing")
    rule.update(value=500, unit="um")
    rows = {r["field"]: r for r in compare_smt_baseline(doc, "automotive", base, "internal_standard")}
    assert rows["qfp_spacing"]["status"] == "fail"


def test_mark_counts_are_per_assembly_side(profiles):
    marks = [dict(id=str(i), side=side, global_confirmed=True, x_mm=x, y_mm=y, diameter_mm=1)
             for i, (side, x, y) in enumerate((("TOP", 5, 5), ("TOP", 15, 5), ("BOTTOM", 5, 15)))]
    rows, result = objects(profiles[0], marks=marks, assembly_sides=["TOP", "BOTTOM"],
                           complete={"components": True, "marks": True})
    assert rows["mark_count"]["status"] == "fail"
    assert {r["side"] for r in result["object_findings"] if r["field"] == "mark_count"} == {"TOP", "BOTTOM"}


def test_vcut_package_and_test_access(profiles):
    comps = [component("R1", "chip_0402", 5, 5, height_mm=4, package_dimensions_confirmed=True),
             component("U1", "bga", 10, 10, height_mm=8, pitch_mm=.3, ball_diameter_mm=.2,
                       body_length_mm=1, body_width_mm=1, package_dimensions_confirmed=True, tall=True)]
    rows, result = objects(profiles[0], components=comps,
        vcut_lines=[dict(confirmed=True, points=[[5.5, 0], [5.5, 20]])],
        test_pads=[dict(refdes="TP1", confirmed=True, side="TOP", x_mm=6.2, y_mm=5.5,
                        diameter_mm=.6, outline=[[6.1, 5.4], [6.3, 5.4], [6.3, 5.6], [6.1, 5.6]])],
        complete={"components": True, "vcut_lines": True, "test_pads": True, "shields": True})
    for field in ("chip_to_vcut", "bga_pitch", "test_pad_diameter", "test_pad_to_low_component"):
        assert rows[field]["status"] == "fail", field
    assert rows["bga_ball_diameter"]["status"] == "pass"
    assert rows["test_pad_to_mid_component"]["status"] == "not_applicable"
    assert any(r.get("x_mm") is not None for r in result["object_findings"] if r["field"] == "chip_to_vcut")


def test_absent_or_unconfirmed_objects_never_pass(profiles):
    rows, _ = objects(profiles[0], complete={})
    assert rows["bga_pitch"]["status"] == "missing"
    assert rows["test_pad_diameter"]["status"] == "missing"
    assert rows["component_to_vcut"]["status"] == "missing"
    rows, _ = objects(profiles[0], complete={"components": True, "vcut_lines": True, "test_pads": True})
    assert rows["component_to_vcut"]["status"] == "not_applicable"


def test_template_does_not_confirm_and_rejects_old_board(profiles, tmp_path):
    brd = tmp_path / "board.brd"
    brd.write_bytes(b"board")
    data = evidence_template(brd)
    assert data["coordinate_alignment_confirmed"] is False
    assert not any(data["complete"].values())
    path = tmp_path / "objects.json"
    data["coordinate_alignment_confirmed"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    result = analyze_smt_workflow(profiles[0], "automotive", {}, {"_input_brd": str(brd)}, str(path), baseline=profiles[1])
    assert result["_source_signature"]
    assert "几何检测" in result["inspection_mode"]
    brd.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256"):
        analyze_smt_workflow(profiles[0], "automotive", {}, {"_input_brd": str(brd)}, str(path))


def test_reports_preserve_all_54_conditions_and_baseline(profiles, tmp_path):
    result = analyze_smt_workflow(profiles[0], "automotive", {}, baseline=profiles[1])
    assert result["manual_review_count"] == 54
    path = tmp_path / "report.xlsx"
    export_smt_report(result, path)
    wb = load_workbook(path)
    assert wb["工艺条件评审"].max_row == 55
    assert wb["内部基线检测"].max_row == len(result["baseline_result"]["items"]) + 1
    assert "内部基线对比" in wb.sheetnames
    wb.close()


def test_all_numeric_pass_does_not_certify_manual_conditions(profiles):
    doc = profiles[0]
    facts = {(r["scope"], r["field"]): dict(value=r.get("value", r.get("min")), unit=r["unit"], source="confirmed")
             for r in doc["profiles"]["automotive"]["rules"]}
    facts[("design", "board_thickness")]["value"] = 1.6
    result = analyze_smt(doc, "automotive", facts)
    assert result["coverage"] == 1
    assert "待评审" in result["verdict"]


def test_builtin_ui_startup_and_analysis():
    from tools.test_workbench_ui import WorkbenchUI
    required = (ROOT / "config/suppliers/pcb/pcb_capability_hongban.yaml",
                ROOT / "config/suppliers/smt/smt_capability_lijing.yaml")
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        pytest.skip("本地配置缺失: " + ", ".join(missing))
    test = WorkbenchUI()
    test.setUp()
    try:
        assert test.dialog.smt.doc["meta"]["factory"] == "Lijing"
        assert len(test.dialog.smt.review_tree.get_children()) == 54
        test.import_board()
        test.dialog.params.entries["panel_size_x"].set("250")
        test.dialog.params.entries["panel_size_y"].set("300")
        test.dialog.params.entries["board_thickness"].set("1.6")
        test.dialog.smt.run()
        test.wait(lambda: test.dialog.smt.last_result is not None)
        assert test.dialog.smt.baseline_tree.get_children()
        assert test.dialog.smt.last_result["items"][0]["actual"] == 300
        test.dialog.smt.compare_baseline.set(False)
        assert test.dialog.smt.last_result is None
    finally:
        test.tearDown()

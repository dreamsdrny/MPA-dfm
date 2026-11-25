"""PCB 判定必须与实际选择的 YAML 规则和测量精度一致。"""
import copy
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.dfm_engine import (FACTORY_FILES, _load_yaml, _to_mm, analyze_design,
                            get_line_spacing_capability, list_board_types,
                            list_factories, list_levels, load_capability)
from src.dfm_report import build_dfm_workbook
from src.gerber_analysis import attach_gerber_result
from src.verdict import finalize_verdict


def test_all_existing_supplier_choices_are_loadable():
    for factory in list_factories():
        for board_type in list_board_types(factory):
            for level in list_levels(board_type, factory):
                assert isinstance(load_capability(board_type, level, factory), dict)
                result = analyze_design({}, board_type, level, factory)
                assert result["capability_level"] == level
                assert result["rule_source"].endswith(FACTORY_FILES[factory])
                assert len(result["rule_sha256"]) == 64


def test_unknown_selection_is_not_silently_replaced():
    with pytest.raises(ValueError, match="板厂"):
        analyze_design({}, factory="unknown")
    with pytest.raises(ValueError, match="板类型"):
        analyze_design({}, board_type="unknown")
    with pytest.raises(ValueError, match="等级"):
        analyze_design({}, capability_level="unknown")
    with pytest.raises(ValueError, match="不支持能力等级"):
        analyze_design({}, board_type="flex", factory="hongban", capability_level="automotive")


def test_units_and_values_are_compared_without_rounding():
    assert _to_mm({"value": 4, "unit": "mil"}) == pytest.approx(0.1016)
    assert _to_mm({"value": 100, "unit": "um"}) == pytest.approx(0.1)
    with pytest.raises(ValueError, match="单位"):
        _to_mm({"value": 0.1, "unit": "ratio"})
    result = analyze_design({"silkscreen_width": 0.10159}, factory="sihui")
    row = next(it for it in result["items"] if it.get("field") == "silkscreen_width")
    assert row["status"] == "fail"
    assert result["verdict_code"] == "fail"


def test_laser_capability_respects_yaml_unit():
    capability = copy.deepcopy(load_capability())
    capability["drill"]["min_laser_via"] = {"value": 0.1, "unit": "mm"}
    with patch("src.dfm_engine.load_capability", return_value=capability):
        result = analyze_design({"min_laser_via": 0.09})
    row = next(it for it in result["items"] if it.get("item") == "最小激光孔")
    assert row["capability"] == pytest.approx(0.1)
    assert row["status"] == "fail"


def test_gaps_are_unchecked_and_overlap_is_not_order_dependent():
    table = {"trace": {"min_line_spacing_by_copper": [
        {"copper_min_um": 0, "copper_max_um": 35, "line_um": 70, "space_um": 70},
        {"copper_min_um": 36, "copper_max_um": 70, "line_um": 100, "space_um": 100}]}}
    assert get_line_spacing_capability(table, 35.5) == (None, None)
    table["trace"]["min_line_spacing_by_copper"][1]["copper_min_um"] = 30
    with pytest.raises(ValueError, match="重叠"):
        get_line_spacing_capability(table, 32)


def test_yaml_duplicate_key_rejected():
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", encoding="utf-8", delete=False) as f:
        f.write("trace:\n  limit: 1\n  limit: 2\n")
        path = Path(f.name)
    try:
        with pytest.raises(ValueError, match="重复规则键"):
            _load_yaml(str(path))
    finally:
        path.unlink()


def test_supplemental_issues_are_review_not_false_pass():
    # Force only the other prerequisites clear, leaving the real report issue path intact.
    complete = dict(checked=15, total=15, ratio=1.0, level="高", missing_items=[])
    rows = [dict(index=i, copper_um=35, min_line_width=0.2, min_spacing=0.2,
                 solder_mask_bridge=0.2, pad_to_mask_clearance=0.2,
                 pad_to_mask_clearance_min=0.1) for i in (1, 2)]
    with patch("src.dfm_engine.compute_coverage", return_value=complete), \
         patch("src.dfm_coverage.coverage_rows", return_value=([], dict(total=0, missing=0, unmapped=0))):
        result = analyze_design(dict(stackup=rows, layer_count=2, solder_mask_bridge=0.2,
                                     min_component_spacing=0.1))
    assert result["error_count"] == 0
    assert result["verdict_code"] == "review"
    assert not result["releasable"]
    assert any(it.get("decision_scope") == "review" for it in result["issues"])
    assert result["risk"]["verdict"] == result["verdict"]
    wb = build_dfm_workbook(result)
    main = [cell.value for row in wb["Main"] for cell in row]
    assert result["verdict"] in main
    assert result["rule_sha256"] in main
    wb.close()


def test_different_or_missing_gerber_rule_fingerprint():
    result = analyze_design({})
    bundle = dict(checks=[], errors=[], parsed_layers=[], folder=".", copper_um=35,
                  mask_bridge_checks=[], copper_layer_checks=[], geometry_complete=False,
                  rule_sha256="different")
    with pytest.raises(ValueError, match="版本不一致"):
        attach_gerber_result(result, bundle)
    bundle.pop("rule_sha256")
    attached = attach_gerber_result(result, bundle)
    assert any("版本指纹" in reason for reason in attached["verdict_reasons"])


def test_single_verdict_also_updates_risk_on_pass():
    result = dict(items=[], risk={"verdict": "旧结论"})
    finalize_verdict(result)
    assert result["risk"]["verdict"] == result["verdict"]

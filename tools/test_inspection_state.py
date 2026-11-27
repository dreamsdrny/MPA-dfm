"""Lifecycle reconciliation: no stale missing placeholders or false full-board pass."""
from copy import deepcopy

from src.domain.inspection_state import reconcile_inspections
from src.domain.verdict import finalize_verdict


def fixture():
    return dict(items=[dict(item="最小阻焊桥", field="solder_mask_bridge", status="info", design=None, source="待确认"),
                       dict(item="Gerber 阻焊桥", field="gerber_mask_bridge", status="fail", design=.05, source="Gerber", layer="mask_top")],
                gerber_mask_bridge_checks=[dict(layer="mask_top", checked=True), dict(layer="mask_bottom", checked=True)],
                geometry_executed=True, geometry_complete=False,
                coverage=dict(total=1, ratio=0, missing_items=["阻焊桥"], checked_items=[]),
                release_blockers=["外层阻焊桥缺测量或铜厚对应能力，需 Gerber/板厂评估", "关键输入缺失：阻焊桥"])


def test_scanned_bridge_removes_only_missing_and_keeps_fail():
    result = reconcile_inspections(fixture())
    assert not any(row.get("field") == "solder_mask_bridge" for row in result["items"])
    assert any(row["status"] == "fail" for row in result["items"])
    state = next(row for row in result["inspection_states"] if row["check_key"] == "solder_mask_bridge")
    assert state["phase"] == "partial" and state["verdict"] == "fail"
    assert not result["coverage"]["missing_items"]
    assert not any("缺测量或铜厚" in text for text in result["release_blockers"])
    finalize_verdict(result, dict(geometry_complete=False))
    assert result["verdict_code"] == "fail" and not result["releasable"]


def test_partial_success_never_becomes_full_pass_and_is_idempotent():
    original = fixture()
    original["items"] = original["items"][:1]
    result = reconcile_inspections(original)
    state = result["inspection_states"][0]
    assert state["phase"] == "partial" and state["verdict"] == "review"
    count = len(result["items"])
    reconcile_inspections(result)
    assert len(result["items"]) == count
    finalize_verdict(result, dict(geometry_complete=False))
    assert result["verdict_code"] == "review"


def test_missing_bottom_scope_stays_missing():
    result = fixture()
    result["gerber_mask_bridge_checks"] = result["gerber_mask_bridge_checks"][:1]
    reconcile_inspections(result)
    assert result["coverage"]["missing_items"] == ["阻焊桥"]
    assert not any(row.get("field") == "solder_mask_bridge" for row in result["items"])
    assert any(s["phase"] == "unavailable" for s in next(r for r in result["inspection_states"] if r["check_key"] == "solder_mask_bridge")["scopes"])


def test_manual_confirmations_override_each_layer_not_other_layers():
    from src.application.brd_extraction import enrich_design
    payload = dict(status="extracted", directory="out", components=[], warnings=[], updates=dict(stackup=[
        dict(index=1, name="TOP", copper_um=35), dict(index=2, name="BOTTOM", copper_um=35)]))
    design = dict(copper_um=None, _copper_confirmations={"TOP": dict(copper_um=70, source="supplier", confirmed_at="2026-11-27")})
    original = deepcopy(design)
    enriched = enrich_design(design, payload)
    assert design == original
    assert enriched["stackup"][0]["copper_um"] == 70
    assert enriched["stackup"][0]["design_copper_um"] == 35
    assert enriched["stackup"][1]["copper_um"] == 35

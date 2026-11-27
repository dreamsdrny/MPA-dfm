"""External checklist-driven algorithms: explicit policy vs review; no silent limits."""
from shapely.geometry import box
import pytest

from src.domain.smt_manufacturing import scan_assembly, validate_rules
from src.domain.combined_verdict import combined_verdict
from src.domain.smt_rules import summarize_smt


def data():
    return dict(_source="fixture",layer_count=2,layer_names=["TOP","BOTTOM"],
        components=[dict(refdes="U1",side="TOP",smd=True,boundary="body",x_mm=0,y_mm=0,outline=[[-1,-1],[1,-1],[1,1],[-1,1]],expected_pin_count=2)],
        pads=[dict(refdes="U1",pin="1",side="TOP",confirmed=True,net="GND",outline=[[-.5,-.5],[.5,-.5],[.5,.5],[-.5,.5]])],
        native_holes=[dict(x_mm=0,y_mm=0,drill_mm=.2,start_layer="TOP",end_layer="BOTTOM")],
        native_pins=[dict(refdes="U1",pin="1",x_mm=0,y_mm=0)],complete=dict(native_pins=True))


def test_via_in_pad_default_review_not_electrical_failure():
    r=scan_assembly(data(),{})
    row=next(f for f in r["findings"] if f["field"]=="via_in_pad")
    assert row["status"]=="review"
    assert any(c["check_key"]=="toe_length" and c["phase"]=="unavailable" for c in r["coverage"])


def test_explicit_forbidden_via_and_pin_count_fail_affect_combined():
    d=data();d["assembly_rules"]=dict(source="supplier confirmation",via_in_pad_policy="forbidden")
    r=scan_assembly(d,{})
    assert any(f["status"]=="fail" and f["field"]=="via_in_pad" for f in r["findings"])
    assert any(f["status"]=="fail" and f["field"]=="pin_count" for f in r["findings"])
    smt=dict(items=[dict(status="pass",severity="high")],assembly_findings=r["findings"],assembly_coverage=r["coverage"])
    summarize_smt(smt)
    assert smt["verdict"]=="不符合所选能力"
    assert combined_verdict(dict(verdict_code="pass"),smt)=="存在不符合项"


def test_filled_capped_exception_and_opposite_layer_do_not_fail():
    d=data();d["assembly_rules"]=dict(source="supplier",via_in_pad_policy="filled_capped_only")
    d["native_holes"][0]["filled_capped"]=True
    assert not any(f["field"]=="via_in_pad" for f in scan_assembly(d,{})["findings"])
    d["native_holes"][0].update(start_layer="BOTTOM",end_layer="BOTTOM",filled_capped=False)
    assert not any(f["field"]=="via_in_pad" for f in scan_assembly(d,{})["findings"])


def test_paste_spacing_uses_explicit_limit_and_non_smd_is_review():
    d=data();d["assembly_rules"]=dict(source="supplier",paste_spacing_mm=.2)
    films={"paste_top":dict(geometry=box(0,0,.5,.5).union(box(.6,0,1.1,.5)))}
    r=scan_assembly(d,films)
    assert any(f["field"]=="paste_spacing" and f["status"]=="fail" for f in r["findings"])
    assert all(f["status"]=="review" for f in r["findings"] if f["field"]=="paste_outside_smd")


def test_testpoint_under_foam_and_silkscreen_clearance():
    d=data();d["test_pads"]=[dict(refdes="TP1",side="TOP",confirmed=True,outline=[[-.1,-.1],[.1,-.1],[.1,.1],[-.1,.1]])]
    d["foam_regions"]=[dict(id="FOAM1",side="TOP",confirmed=True,outline=[[-1,-1],[1,-1],[1,1],[-1,1]])]
    d["assembly_rules"]=dict(source="engineering",test_to_silk_mm=.1)
    r=scan_assembly(d,{"silkscreen_top":dict(geometry=box(0,0,.3,.3))})
    assert any(f["field"]=="test_under_body" and f.get("other_refdes")=="FOAM1" for f in r["findings"])
    assert any(f["field"]=="test_to_silk" and f["status"]=="fail" for f in r["findings"])


def test_transport_checks_minimum_not_wrong_maximum_description():
    d=data();d["board_outline"]=[[0,0],[87.3,0],[87.3,34.3],[0,34.3]]
    d["assembly_rules"]=dict(source="equipment",transport_axis="X",board_width_min_mm=50,board_width_max_mm=300)
    r=scan_assembly(d,{})
    f=next(f for f in r["findings"] if f["field"]=="board_transport")
    assert f["actual"]==34.3 and f["status"]=="fail"


def test_rules_must_have_confirmed_source_and_finite_values():
    with pytest.raises(ValueError,match="来源"):
        validate_rules(dict(paste_spacing_mm=.169))
    with pytest.raises(ValueError,match="有限"):
        validate_rules(dict(source="x",paste_spacing_mm=float('nan')))


def test_assembly_rule_template_empty_values_and_exclusive_publish(tmp_path):
    import json
    from src.application.reports import write_assembly_rule_template
    path=tmp_path/"rules.json"
    write_assembly_rule_template(path)
    rules=json.loads(path.read_text(encoding="utf-8"))["assembly_rules"]
    assert rules["paste_spacing_mm"] is None
    assert validate_rules(rules)["source"]==""
    with pytest.raises(FileExistsError):write_assembly_rule_template(path)


def test_rule_path_is_used_in_real_smt_evidence_workflow(tmp_path):
    import json
    from pathlib import Path
    from src.application.smt_workflow import analyze_smt_workflow,evidence_template
    from src.smt_engine import load_smt_profile
    board=tmp_path/"board.brd";board.write_bytes(b"fixture")
    evidence=evidence_template(board)
    evidence.update(data());evidence["coordinate_alignment_confirmed"]=True
    e=tmp_path/"objects.json";e.write_text(json.dumps(evidence))
    rules=tmp_path/"rules.json";rules.write_text(json.dumps(dict(schema_version=1,assembly_rules=dict(source="supplier",via_in_pad_policy="forbidden"))))
    doc=load_smt_profile(Path(__file__).resolve().parents[1]/"config/suppliers/smt/smt_capability_lijing.yaml")
    r=analyze_smt_workflow(doc,"automotive",{},design=dict(_input_brd=str(board)),evidence_path=str(e),assembly_rules_path=str(rules))
    assert any(f["field"]=="via_in_pad" and f["status"]=="fail" for f in r["assembly_findings"])
    assert r["assembly_rule_fingerprints"]
    assert any(s["check_key"]=="via_in_pad" for s in r["inspection_states"])

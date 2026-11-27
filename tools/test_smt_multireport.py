"""R11 unit/boundary/process contracts, including deliberately missing evidence."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from openpyxl import load_workbook
from shapely.geometry import box

from src.domain.smt_manufacturing import CHECKS, scan_assembly, validate_rules
from src.domain.smt_multireport import CHECKS as NEW_CHECKS


def obj(**attrs):
    return dict(outline=list(box(0,0,1,1).exterior.coords),confirmed=True,side="TOP",**attrs)


def base(**rules):
    return dict(_source="fixture",components=[],pads=[],assembly_rules=dict(source="factory confirmed",**rules),complete={})


def findings(data, key, films=None):
    return [f for f in scan_assembly(data,films or {})["findings"] if f["field"]==key]


def test_registry_missing_evidence_never_passes_and_no_mutation():
    d=base();old=deepcopy(d);r=scan_assembly(d,{})
    assert set(NEW_CHECKS)<=set(CHECKS)
    assert all(c["verdict"]=="review" and not c["full_board_complete"] for c in r["coverage"])
    assert d==old


def test_lead_projection_toe_min_heel_max_and_width_delta():
    d=base(toe_min_mm=.2,heel_max_mm=.3,lead_width_excess_max_mm=.05)
    d["components"]=[dict(refdes="U1",side="TOP",physical_leads=[obj(pin="1",toe_direction=[1,0],no_solder=False)])]
    d["pads"]=[dict(obj(refdes="U1",pin="1"),outline=list(box(-.5,.1,1.1,.9).exterior.coords))]
    rows=findings(d,"lead_geometry")
    assert {r["measurement"]:r["actual"] for r in rows}==pytest.approx(dict(toe_min_mm=.1,heel_max_mm=.5,lead_width_excess_max_mm=.2))
    assert all(r["status"]=="fail" for r in rows)
    d["components"][0]["physical_leads"][0]["no_solder"]=True
    assert not findings(d,"lead_geometry")


def test_area_difference_is_ratio_not_heat_metric():
    d=base(pad_area_imbalance_max=.4)
    d["components"]=[dict(refdes="C1",side="TOP",smd=True,thermal_pair_confirmed=True)]
    d["pads"]=[obj(refdes="C1",pin="1"),dict(obj(refdes="C1",pin="2"),outline=list(box(2,0,4,1).exterior.coords))]
    r=findings(d,"pad_size_balance")[0]
    assert r["actual"]==.5 and r["unit"]=="ratio" and r["status"]=="review"
    d["components"][0]["thermal_pair_confirmed"]=False
    assert not findings(d,"pad_size_balance")


def test_height_requires_actual_source_and_side():
    d=base(component_height_top_max_mm=10,component_height_bottom_max_mm=5)
    d["components"]=[dict(obj(refdes="J1"),height_mm=8,height_source="BOM confirmed"),dict(obj(refdes="J2"),side="BOTTOM",height_mm=8,height_source="BRD COMP_HEIGHT")]
    r=findings(d,"side_height")
    assert len(r)==1 and r[0]["refdes"]=="J2" and r[0]["status"]=="review"
    d["components"][1]["height_source"]="datasheet"
    assert findings(d,"side_height")[0]["status"]=="fail"


def test_conveyor_uses_explicit_segment_not_board_bounding_box():
    d=base(conveyor_clearance_top_mm=2)
    d["components"]=[obj(refdes="R1",boundary="body")]
    d["board_outline"]=list(box(-10,-10,10,10).exterior.coords)
    assert not findings(d,"conveyor_clearance")
    d["conveyed_edges"]=[dict(points=[[-1,-5],[-1,5]],assembly_sides=["TOP"],confirmed=True,source="panel drawing")]
    row=findings(d,"conveyor_clearance")[0]
    assert row["actual"]==1 and row["status"]=="fail"
    d["components"][0]["boundary"]="place_bound"
    assert findings(d,"conveyor_clearance")[0]["status"]=="review"


def test_full_shield_projection_not_certified_as_wall():
    d=base(component_shield_mm=.5);d["components"]=[obj(refdes="R1",boundary="body")]
    d["shields"]=[obj(id="SH1")]
    assert findings(d,"component_shield")[0]["status"]=="review"
    d["shields"][0]["boundary"]="skirt"
    assert findings(d,"component_shield")[0]["status"]=="fail"


def test_test_mark_copper_edge_not_center_and_wrong_side_ignored():
    d=base(test_to_mark_mm=4);d["test_pads"]=[obj(refdes="TP1")]
    d["marks"]=[dict(id="FM1",side="TOP",x_mm=3,y_mm=.5,diameter_mm=1,global_confirmed=True)]
    assert findings(d,"test_mark")[0]["actual"]==pytest.approx(1.5)
    d["marks"][0]["side"]="BOTTOM"
    assert not findings(d,"test_mark")


def test_via_and_test_clearances_use_net_and_real_copper_pad():
    d=base(via_to_pad_same_net_mm=.1,via_to_pad_different_net_mm=.2,test_to_pad_same_net_mm=.1,test_to_pad_different_net_mm=.2)
    d["components"]=[dict(refdes="R1",side="TOP",smd=True)]
    d["pads"]=[dict(obj(refdes="R1",pin="1",net="B"),outline=list(box(1.15,0,2.15,1).exterior.coords))]
    d["via_pads"]=[obj(id="V1",net="A")];d["test_pads"]=[obj(refdes="TP1",pin="1",net="A")]
    assert findings(d,"via_pad_clearance")[0]["actual"]==.15
    assert findings(d,"test_pad_clearance")[0]["net_relation"]=="different"
    d["pads"][0]["net"]="A"
    assert not findings(d,"via_pad_clearance") and not findings(d,"test_pad_clearance")
    d["pads"][0]["net"]=""
    assert not findings(d,"via_pad_clearance")


def test_exposed_copper_is_mask_intersection_review_only():
    d=base(exposed_copper_spacing_mm=.2)
    copper=box(0,0,1,1).union(box(1.1,0,2.1,1))
    films=dict(copper_top=dict(geometry=copper),mask_top=dict(geometry=box(-1,-1,3,2)))
    r=findings(d,"exposed_copper",films)[0]
    assert r["status"]=="review" and r["actual"]==.1
    films["mask_top"]["geometry"]=box(0,0,1,1)
    assert not findings(d,"exposed_copper",films)


def test_trace_width_uses_trace_normal_not_pad_long_side():
    d=base(trace_pad_width_excess_mm=0)
    d["components"]=[dict(refdes="R1",smd=True,side="TOP")]
    d["pads"]=[dict(obj(refdes="R1",pin="1",net="A"),outline=list(box(0,0,2,.5).exterior.coords))]
    d["native_traces"]=[dict(kind="LINE",layer="ETCH/TOP",net="A",width_mm=.8,endpoints_mm=[[-1,.25],[3,.25]],points=[[-1,.25],[3,.25]])]
    assert findings(d,"trace_pad_width")[0]["actual"]==.3
    d["native_traces"][0]["kind"]="ARC"
    assert not findings(d,"trace_pad_width")


def test_model_bound_to_mpn_and_explicit_cad_code_not_name_guess():
    d=base();d["components"]=[obj(refdes="C1",manufacturer_part_number="MPN-A",cad_package_code="0402",cad_pin1="2",part_model=dict(confirmed=True,source="datasheet",mpn="MPN-A",package_code="0805",pin1="1"))]
    assert len(findings(d,"part_model"))==2
    d["components"][0]["part_model"]["mpn"]="wrong"
    with pytest.raises(ValueError,match="MPN"):scan_assembly(d,{})


def test_laser_hole_span_and_thermal_via_opposite_policies():
    d=base(laser_no_via=True);d["layer_names"]=["TOP","INNER","BOTTOM"]
    d["laser_regions"]=[obj(id="QR1",source="drawing")];d["thermal_pads"]=[obj(refdes="U1",source="factory",via_policy="required_open")]
    d["native_holes"]=[dict(kind="VIA",x_mm=.5,y_mm=.5,drill_mm=.35,plated=True,start_layer="TOP",end_layer="BOTTOM",filled_capped=False)]
    assert findings(d,"laser_via")[0]["status"]=="fail"
    assert not findings(d,"thermal_via_policy")
    d["thermal_pads"][0]["via_policy"]="forbidden"
    assert findings(d,"thermal_via_policy")[0]["status"]=="fail"
    d["thermal_pads"][0]["via_policy"]="required_open";d["native_holes"]=[]
    assert findings(d,"thermal_via_policy")[0]["status"]=="review"
    d["complete"]["native_holes"]=True
    assert findings(d,"thermal_via_policy")[0]["status"]=="fail"


def test_thermal_npth_not_evidence_of_exhaust_via():
    d=base();d["layer_names"]=["TOP","BOTTOM"];d["complete"]["native_holes"]=True
    d["thermal_pads"]=[obj(refdes="U1",source="factory",via_policy="required_open")]
    d["native_holes"]=[dict(kind="PIN",x_mm=.5,y_mm=.5,plated=False,drill_mm=.4,start_layer="TOP",end_layer="BOTTOM")]
    assert findings(d,"thermal_via_policy")[0]["status"]=="review"


def test_revision_incomplete_not_claimed_wrong():
    d=base(expected_board_text=["BOARD-REV-A"])
    assert not findings(d,"silkscreen_revision")
    d["complete"]["silkscreen_text_records"]=True;d["silkscreen_text_records"]=[dict(text="BOARD-REV-B")]
    assert findings(d,"silkscreen_revision")[0]["expected_text"]=="BOARD-REV-A"


def test_pad_spacing_distinguishes_same_component_and_dnp():
    d=base(assembly_pad_spacing_mm=.254)
    d["components"]=[dict(refdes="R1",smd=True,side="TOP"),dict(refdes="R2",smd=True,side="TOP")]
    d["pads"]=[obj(refdes="R1",pin="1"),dict(obj(refdes="R2",pin="1"),outline=list(box(1.1,0,2.1,1).exterior.coords))]
    assert findings(d,"assembly_pad_spacing")[0]["actual"]==.1
    d["dnp_components"]=[dict(refdes="R2")]
    assert not findings(d,"assembly_pad_spacing")


def test_exposed_to_covered_excludes_connected_pad_trace():
    d=base(exposed_to_covered_mm=.2)
    copper=box(0,0,2,1)
    films=dict(copper_top=dict(geometry=copper),mask_top=dict(geometry=box(0,0,1,1)))
    assert not findings(d,"exposed_to_covered",films)
    films["copper_top"]["geometry"]=copper.union(box(0,1.1,1,2.1))
    assert findings(d,"exposed_to_covered",films)[0]["status"]=="review"


@pytest.mark.parametrize("rules",[dict(toe_min_mm=.4,toe_max_mm=.3),dict(heel_min_mm=.4,heel_max_mm=.3),dict(pad_area_imbalance_max=80),dict(expected_board_text="ABC"),dict(laser_no_via="yes")])
def test_invalid_rules_fail_closed(rules):
    with pytest.raises(ValueError):validate_rules(dict(source="eng",**rules))


@pytest.mark.parametrize("source",[None,"",{},True])
def test_null_or_nonstring_source_cannot_authorize_rules(source):
    with pytest.raises(ValueError):validate_rules(dict(source=source,assembly_pad_spacing_mm=.25))


def test_budget_never_full_pass(monkeypatch):
    import src.domain.smt_manufacturing as m
    monkeypatch.setattr(m,"MAX_CANDIDATES",1)
    d=base(conveyor_clearance_top_mm=2);d["components"]=[obj(refdes="R1",boundary="body")]
    d["conveyed_edges"]=[dict(points=[[-1,-5],[-1,5]],assembly_sides=["TOP"],confirmed=True,source="drawing")]*3
    r=scan_assembly(d,{})
    assert r["errors"] and all(not c["full_board_complete"] for c in r["coverage"])


def test_rule_template_and_workflow_report_images(tmp_path):
    from src.application.reports import write_assembly_rule_template
    from src.application.smt_workflow import evidence_template, analyze_smt_workflow
    from src.smt_engine import load_smt_profile, export_smt_report
    rules=tmp_path/"rules.json";write_assembly_rule_template(rules)
    assert json.loads(rules.read_text())["assembly_rules"]==json.loads((Path(__file__).resolve().parents[1]/"templates/assembly_rules.json").read_text())["assembly_rules"]
    board=tmp_path/"board.brd";board.write_bytes(b"fixture")
    d=evidence_template(board);d.update(base(conveyor_clearance_top_mm=2));d["coordinate_alignment_confirmed"]=True
    d["components"]=[obj(refdes="R1",boundary="body")];d["conveyed_edges"]=[dict(points=[[-1,-5],[-1,5]],assembly_sides=["TOP"],confirmed=True,source="drawing")]
    e=tmp_path/"objects.json";e.write_text(json.dumps(d))
    profile=load_smt_profile(Path(__file__).resolve().parents[1]/"config/suppliers/smt/smt_capability_lijing.yaml")
    r=analyze_smt_workflow(profile,"automotive",{},design=dict(_input_brd=str(board)),evidence_path=str(e))
    assert any(f["field"]=="conveyor_clearance" for f in r["assembly_findings"])
    assert len({s["check_key"] for s in r["inspection_states"]})==len(r["inspection_states"])
    report=tmp_path/"report.xlsx";export_smt_report(r,report)
    wb=load_workbook(report);assert len(wb["Issue summary"]._images)>0;wb.close()

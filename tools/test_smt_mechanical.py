"""302 report lessons: explicit boundaries/units, heel vs toe and thermal metric."""
from copy import deepcopy

import pytest
from shapely.geometry import box

from src.domain.smt_manufacturing import scan_assembly, validate_rules


def base():
    return dict(_source="confirmed fixture",assembly_rules=dict(source="engineering confirmation"),components=[],pads=[],complete={})


def pad(ref,pin,side,x0,y0,x1,y1):
    return dict(refdes=ref,pin=pin,side=side,confirmed=True,outline=list(box(x0,y0,x1,y1).exterior.coords))


def test_pth_opposite_smd_compares_solder_face_pads_not_projected_body():
    d=base();d["assembly_rules"]["pth_to_opposite_smd_mm"]=3.048
    d["components"]=[dict(refdes="D3",side="TOP",pth=True),dict(refdes="C307",side="BOTTOM",smd=True),dict(refdes="C1",side="TOP",smd=True)]
    d["pads"]=[pad("D3","1","BOTTOM",0,0,1,1),pad("C307","1","BOTTOM",3,0,4,1),pad("C1","1","TOP",.1,0,1.1,1)]
    r=scan_assembly(d,{})
    rows=[f for f in r["findings"] if f["field"]=="pth_to_opposite_smd"]
    assert len(rows)==1 and rows[0]["other_refdes"]=="C307"
    assert rows[0]["actual"]==2 and rows[0]["side"]=="BOTTOM" and rows[0]["status"]=="fail"


def test_mounting_hole_wall_distance_and_place_bound_review():
    d=base();d["assembly_rules"]["mounting_clearance_mm"]=4.064
    d["mounting_holes"]=[dict(id="M1",mounting_confirmed=True,x_mm=0,y_mm=0,diameter_mm=2,assembly_sides=["BOTTOM"])]
    d["components"]=[dict(refdes="R242",side="BOTTOM",boundary="body",outline=list(box(2,0,3,1).exterior.coords)),
                     dict(refdes="R1",side="TOP",boundary="body",outline=list(box(0,0,.2,.2).exterior.coords))]
    result=scan_assembly(d,{})
    row=next(f for f in result["findings"] if f["field"]=="mounting_clearance")
    assert row["actual"]==1 and row["status"]=="fail" and row["other_refdes"]=="R242"
    d["components"][0]["boundary"]="place_bound"
    row=next(f for f in scan_assembly(d,{})["findings"] if f["field"]=="mounting_clearance")
    assert row["status"]=="review"
    d["mounting_holes"][0]["mounting_confirmed"]=False
    assert not any(f["field"]=="mounting_clearance" for f in scan_assembly(d,{})["findings"])


def test_heel_measurement_negative_axis_distinct_from_toe():
    d=base();d["assembly_rules"].update(heel_min_mm=.0762,toe_max_mm=.5)
    d["components"]=[dict(refdes="U8",side="BOTTOM",physical_leads=[dict(pin="1",confirmed=True,no_solder=False,
        outline=list(box(0,0,1,.5).exterior.coords),toe_direction=[1,0])])]
    d["pads"]=[pad("U8","1","BOTTOM",0,0,1.1,.5)]
    rows=scan_assembly(d,{})["findings"]
    heel=next(f for f in rows if f["field"]=="heel_length")
    assert heel["actual"]==0 and heel["status"]=="fail"
    assert not any(f["field"]=="toe_length" for f in rows)
    d["pads"][0]=pad("U8","1","BOTTOM",-.1,0,1.1,.5)
    assert not any(f["field"]=="heel_length" for f in scan_assembly(d,{})["findings"])


def test_thermal_ratio_uses_outside_pad_not_always_full_pad_overlap():
    d=base();d["assembly_rules"].update(thermal_halo_mm=.2,thermal_copper_ratio_max=.5)
    d["components"]=[dict(refdes="C266",smd=True,side="BOTTOM")]
    d["pads"]=[pad("C266","1","BOTTOM",0,0,1,1)]
    only_pad={"copper_bottom":dict(geometry=box(0,0,1,1))}
    assert not any(f["field"]=="pad_copper_thermal" for f in scan_assembly(d,only_pad)["findings"])
    large_copper={"copper_bottom":dict(geometry=box(-1,-1,2,2))}
    row=next(f for f in scan_assembly(d,large_copper)["findings"] if f["field"]=="pad_copper_thermal")
    assert row["actual"]==pytest.approx(1) and row["status"]=="review" and row["unit"]=="ratio"
    assert row["measurement_basis"]=="outside_pad_halo"


def test_panel_rib_measurement_requires_basis_and_confirmed_source():
    d=base();d["assembly_rules"]["panel_rib_min_mm"]=4
    d["panel_support_ribs"]=[dict(id="RIB1",confirmed=True,measured_mm=2,dimension_basis="length",source="panel drawing")]
    row=next(f for f in scan_assembly(d,{})["findings"] if f["field"]=="panel_support_rib")
    assert row["status"]=="fail" and row["dimension_basis"]=="length"
    d["panel_support_ribs"][0].pop("dimension_basis")
    assert not any(f["field"]=="panel_support_rib" for f in scan_assembly(d,{})["findings"])


def test_rigid_flex_equipment_missing_is_not_automatically_passed():
    d=base();d.update(board_type="rigid_flex",board_type_confirmed=True)
    c=next(c for c in scan_assembly(d,{})["coverage"] if c["check_key"]=="rigid_flex_separation")
    assert c["phase"]=="unavailable" and c["verdict"]=="review"
    d["engineering_review"]=dict(rigid_flex_separation=dict(status="fail",source="factory confirms no equipment"))
    assert any(f["status"]=="fail" and f["field"]=="rigid_flex_separation" for f in scan_assembly(d,{})["findings"])


def test_ratio_not_percentage_and_no_source_rejected():
    with pytest.raises(ValueError,match="比例"):
        validate_rules(dict(source="eng",thermal_copper_ratio_max=80))
    with pytest.raises(ValueError,match="来源"):
        validate_rules(dict(mounting_clearance_mm=4.064))
    d=base();old=deepcopy(d);scan_assembly(d,{})
    assert d==old


def test_confirmed_rigid_or_valid_flex_equipment_satisfies_only_review_scope():
    d=base();d.update(board_type="rigid",board_type_confirmed=True)
    state=next(c for c in scan_assembly(d,{})["coverage"] if c["check_key"]=="rigid_flex_separation")
    assert state["verdict"]=="within_scope" and not state["full_board_complete"]
    d.update(board_type="rigid_flex",engineering_review=dict(rigid_flex_separation=dict(status="pass",source="factory report",equipment_confirmed=True,method="laser")))
    state=next(c for c in scan_assembly(d,{})["coverage"] if c["check_key"]=="rigid_flex_separation")
    assert state["verdict"]=="within_scope"


def test_no_solder_lead_and_unknown_mounting_sides_not_hard_failure():
    d=base();d["assembly_rules"].update(heel_min_mm=.1,mounting_clearance_mm=4)
    d["components"]=[dict(refdes="U1",side="TOP",physical_leads=[dict(pin="1",confirmed=True,no_solder=True,
        outline=list(box(0,0,1,1).exterior.coords),toe_direction=[1,0])])]
    d["pads"]=[pad("U1","1","TOP",0,0,1,1)]
    d["mounting_holes"]=[dict(id="M1",mounting_confirmed=True,x_mm=0,y_mm=0,diameter_mm=2)]
    assert not any(f["field"] in ("heel_length","mounting_clearance") for f in scan_assembly(d,{})["findings"])


def test_new_checks_enter_workflow_report_and_snapshot(tmp_path):
    import json
    from pathlib import Path
    from openpyxl import load_workbook
    from src.application.smt_workflow import evidence_template,analyze_smt_workflow
    from src.smt_engine import load_smt_profile,export_smt_report
    board=tmp_path/"board.brd";board.write_bytes(b"test")
    d=evidence_template(board);d.update(base());d["coordinate_alignment_confirmed"]=True
    d["assembly_rules"].update(panel_rib_min_mm=4)
    d["panel_support_ribs"]=[dict(id="RIB1",confirmed=True,measured_mm=2,dimension_basis="length",source="factory drawing",outline=list(box(0,0,2,.8).exterior.coords))]
    e=tmp_path/"objects.json";e.write_text(json.dumps(d))
    doc=load_smt_profile(Path(__file__).resolve().parents[1]/"config/suppliers/smt/smt_capability_lijing.yaml")
    result=analyze_smt_workflow(doc,"automotive",{},design=dict(_input_brd=str(board)),evidence_path=str(e))
    assert any(f["field"]=="panel_support_rib" and f["status"]=="fail" for f in result["assembly_findings"])
    assert result["picture_geometry"]["panel_support_ribs"]
    report=tmp_path/"report.xlsx";export_smt_report(result,report)
    wb=load_workbook(report)
    assert "装配补充检查" in wb.sheetnames
    assert len(wb["Issue summary"]._images)>0
    wb.close()


def test_two_terminal_thermal_imbalance_requires_explicit_pair():
    d=base();d["assembly_rules"].update(thermal_halo_mm=.2,thermal_copper_ratio_max=1,thermal_imbalance_max=.5)
    d["components"]=[dict(refdes="C266",smd=True,side="BOTTOM",thermal_pair_confirmed=True)]
    d["pads"]=[pad("C266","1","BOTTOM",0,0,1,1),pad("C266","2","BOTTOM",3,0,4,1)]
    films=dict(copper_bottom=dict(geometry=box(-1,-1,2,2).union(box(3,0,4,1))))
    row=next(f for f in scan_assembly(d,films)["findings"] if "不平衡" in f["reason"])
    assert row["actual"]==pytest.approx(1) and row["status"]=="review"
    d["components"][0]["thermal_pair_confirmed"]=False
    assert not any("不平衡" in f["reason"] for f in scan_assembly(d,films)["findings"])

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
from shapely.geometry import box

from src.application.smt_supplements import inspect_supplements
from src.application.smt_workflow import evidence_template,analyze_smt_workflow
from src.adapters.smt_evidence import sha256_file
from src.adapters.step_reader import parse_step
from tools.test_manufacturing_coordination import odb_files,write_zip


STEP="""ISO-10303-21;
HEADER;
FILE_SCHEMA(('AP214'));
ENDSEC;
DATA;
#1=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.));
#2=(GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNIT_ASSIGNED_CONTEXT((#1))REPRESENTATION_CONTEXT('',''));
#3=PRODUCT('U1','name','',());
#4=CARTESIAN_POINT('',(0.,0.,1000.));
#5=MANIFOLD_SOLID_BREP('BODY',#6);
ENDSEC;
END-ISO-10303-21;
"""


def fixture(tmp_path):
    brd=tmp_path/"board.brd";brd.write_bytes(b"board fixture")
    data=evidence_template(brd);data["coordinate_alignment_confirmed"]=True
    data["components"]=[dict(refdes="U1",side="TOP",smd=True,x_mm=1,y_mm=2,rotation=0,category="qfn")]
    data["native_pins"]=[dict(refdes="U1",pin="1",x_mm=1,y_mm=2,net="GND")]
    data["pads"]=[dict(refdes="U1",pin="1",side="TOP",confirmed=True,outline=list(box(.5,1.5,1.5,2.5).exterior.coords))]
    data["layer_names"]=["TOP","BOTTOM"]
    odb=tmp_path/"odb.zip";odb.write_bytes(b"mock")
    shape=box(.5,1.5,1.5,2.5)
    film=dict(role="copper",side="TOP",copper_index=1,geometry_wkb=shape.wkb_hex,source="odb/top/features",warnings=[],status="parsed",
              records=[dict(refdes="U1",pin="1",geometry_wkb=shape.wkb_hex)])
    layout=dict(selected_step="pcb",steps=["pcb"],component_complete=True,copper_layer_count=2,warnings=[],films=[film],
                component_records=[dict(refdes="U1",side="TOP",x_mm=1,y_mm=2,rotation=0,pins=[dict(pin="1",x_mm=1,y_mm=2,net="GND")])])
    return dict(_input_brd=str(brd)),data,odb,layout


def test_odb_identity_only_without_alignment_never_uses_geometry(tmp_path):
    design,data,odb,layout=fixture(tmp_path);old=deepcopy(data)
    with patch("src.application.smt_supplements.parse_odb",return_value=layout):
        r=inspect_supplements(design,data,odb_path=str(odb))
    assert not r["films"] and r["data"]==old
    assert next(s for s in r["coverage"] if s["check_key"]=="odb_brd_geometry")["verdict"]=="review"


def test_odb_exact_pin_pad_matching_unlocks_only_selected_films(tmp_path):
    design,data,odb,layout=fixture(tmp_path)
    with patch("src.application.smt_supplements.parse_odb",return_value=layout):
        r=inspect_supplements(design,data,odb_path=str(odb),odb_confirmed=True)
    assert r["films"]["copper_top"]["geometry"].area==1
    assert r["summary"]["odb"]["fid_pads_compared"]==1
    assert all(not s["full_board_complete"] for s in r["coverage"])


@pytest.mark.parametrize("change",["side","origin","pin","net","pad","duplicate","missing","layer_count","missing_fid","missing_pin"])
def test_odb_conflicts_never_replace_brd_or_unlock_films(tmp_path,change):
    design,data,odb,layout=fixture(tmp_path)
    if change=="side":layout["component_records"][0]["side"]="BOTTOM"
    if change=="origin":layout["component_records"][0]["x_mm"]+=1
    if change=="pin":layout["component_records"][0]["pins"][0]["x_mm"]+=1
    if change=="net":layout["component_records"][0]["pins"][0]["net"]="wrong"
    if change=="pad":layout["films"][0]["records"][0]["geometry_wkb"]=box(0,0,2,2).wkb_hex
    if change=="duplicate":layout["component_records"]*=2
    if change=="missing":layout["component_records"]=[]
    if change=="layer_count":layout["copper_layer_count"]=4
    if change=="missing_fid":layout["films"][0]["records"]=[]
    if change=="missing_pin":layout["component_records"][0]["pins"]=[]
    old=deepcopy(data)
    with patch("src.application.smt_supplements.parse_odb",return_value=layout):
        r=inspect_supplements(design,data,odb_path=str(odb),odb_confirmed=True)
    assert not r["films"] and r["data"]==old


def test_step_units_context_no_point_cloud_height_and_truncation(tmp_path):
    p=tmp_path/"body.stp";p.write_text(STEP)
    r=parse_step(p)
    assert r["unit_to_mm"]==1 and r["height_mm"] is None and r["mechanical_bodies"] is None
    assert r["products"]==["U1"]
    p.write_text(STEP.replace("GLOBAL_UNIT_ASSIGNED_CONTEXT((#1))",""))
    assert parse_step(p)["unit_to_mm"] is None
    p.write_text(STEP.replace("END-ISO-10303-21;",""))
    with pytest.raises(ValueError,match="完整结束"):parse_step(p)


def test_confirmed_step_export_supplements_body_not_leads_or_existing_conflict(tmp_path):
    design,data,odb,layout=fixture(tmp_path)
    p=tmp_path/"body.stp";p.write_text(STEP)
    mapping=tmp_path/"mapping.json"
    body=dict(refdes="U1",side="TOP",confirmed=True,source="CAD checked export",step_product="U1",outline=list(box(0,0,2,3).exterior.coords),height_mm=2)
    mapping.write_text(json.dumps(dict(schema_version=1,unit="mm",coordinate_system="brd",alignment_confirmed=True,
                                      brd_sha256=sha256_file(design["_input_brd"]),step_sha256=sha256_file(p),components=[body])))
    r=inspect_supplements(design,data,step_path=str(p),model_path=str(mapping))
    c=r["data"]["components"][0]
    assert c["boundary"]=="body" and c["height_mm"]==2 and c["model_body_confirmed"]
    assert "physical_leads" not in c and not c.get("package_dimensions_confirmed")
    data["components"][0].update(outline=list(box(10,10,12,12).exterior.coords),boundary="body")
    r=inspect_supplements(design,data,step_path=str(p),model_path=str(mapping))
    assert r["data"]["components"][0]["outline"]==data["components"][0]["outline"]
    assert any(f["status"]=="fail" for f in r["findings"])
    p.write_text(STEP+" ")
    with pytest.raises(ValueError,match="哈希"):inspect_supplements(design,data,step_path=str(p),model_path=str(mapping))


def test_real_odb_parser_inventory_enters_workflow_report_and_export_snapshot(tmp_path):
    from src.smt_engine import load_smt_profile
    from src.adapters.workbench_report import export_report
    design,data,odb,_=fixture(tmp_path)
    write_zip(odb,odb_files())
    evidence=tmp_path/"objects.json";evidence.write_text(json.dumps(data))
    p=tmp_path/"body.stp";p.write_text(STEP)
    doc=load_smt_profile(Path(__file__).resolve().parents[1]/"config/suppliers/smt/smt_capability_lijing.yaml")
    r=analyze_smt_workflow(doc,"automotive",{},design=design,evidence_path=str(evidence),odb_path=str(odb),step_path=str(p))
    assert r["supplement_analysis"]["odb"]["components"]==3 and not r["supplement_analysis"]["odb"]["films_used"]
    assert len(r["inspection_states"])==len({s["check_key"] for s in r["inspection_states"]})
    export_report("smt",tmp_path/"report.xlsx",smt=r)
    from openpyxl import load_workbook
    wb=load_workbook(tmp_path/"report.xlsx");assert "ODB_STP协同核对" in wb.sheetnames;wb.close()
    p.write_text(STEP+" ")
    with pytest.raises(ValueError,match="已变化"):export_report("smt",tmp_path/"stale.xlsx",smt=r)
    assert not (tmp_path/"stale.xlsx").exists()


def test_multiple_steps_stays_review_no_automatic_panel_mix(tmp_path):
    design,data,odb,_=fixture(tmp_path);write_zip(odb,odb_files(two_steps=True))
    r=inspect_supplements(design,data,odb_path=str(odb),odb_confirmed=True)
    assert r["summary"]["odb"]["selected_step"] is None and not r["films"]


def test_gerber_odb_conflict_rejected_before_auto_sources(tmp_path):
    from src.smt_engine import load_smt_profile
    design,data,odb,_=fixture(tmp_path)
    doc=load_smt_profile(Path(__file__).resolve().parents[1]/"config/suppliers/smt/smt_capability_lijing.yaml")
    with pytest.raises(ValueError,match="ODB"):
        analyze_smt_workflow(doc,"automotive",{},design=design,auto_sources=True,odb_path=str(odb),gerber_dir=str(tmp_path))


def test_real_fid_parser_aligned_films_feed_smt_mask_detection(tmp_path):
    from src.smt_engine import load_smt_profile
    design,data,odb,_=fixture(tmp_path)
    files={
        "job/matrix/matrix":b"STEP {\nNAME=pcb\n}\nLAYER {\nNAME=comp_+_top\nTYPE=COMPONENT\nSIDE=TOP\n}\nLAYER {\nNAME=top\nTYPE=SIGNAL\nCONTEXT=BOARD\n}\nLAYER {\nNAME=sm_top\nTYPE=SOLDER_MASK\nSIDE=TOP\n}\nLAYER {\nNAME=bottom\nTYPE=SIGNAL\nCONTEXT=BOARD\n}\n",
        "job/misc/info":b"ODB_VERSION=8.1\n",
        "job/steps/pcb/eda/data":b"LYR top\nNET GND\nSNT TOP T 0 0\nFID C 0 0\nPKG QFN\n",
        "job/steps/pcb/layers/comp_+_top/components":b"UNITS=MM\nCMP 0 1 2 0 N U1 X\nTOP 0 1 2 0 N 0 0 1\n",
        "job/steps/pcb/layers/top/features":b"UNITS=MM\n$0 rect1000x1000\nP 1 2 0 P 0 0\n",
        "job/steps/pcb/layers/sm_top/features":b"UNITS=MM\n$0 rect500x500\nP 1 2 0 P 0 0\n",
        "job/steps/pcb/layers/bottom/features":b"UNITS=MM\n",
    }
    write_zip(odb,files)
    data["pads"][0]["mask_policy"]="open"
    evidence=tmp_path/"objects.json";evidence.write_text(json.dumps(data))
    profile=load_smt_profile(Path(__file__).resolve().parents[1]/"config/suppliers/smt/smt_capability_lijing.yaml")
    r=analyze_smt_workflow(profile,"automotive",{},design=design,evidence_path=str(evidence),odb_path=str(odb),odb_confirmed=True)
    assert r["supplement_analysis"]["odb"]["spatial_gate_passed"]
    assert "mask_top" in r["supplement_analysis"]["odb"]["films_used"]
    assert any(f["field"]=="smd_mask_opening" and f["status"]=="fail" for f in r["assembly_findings"])
    assert r["picture_odb_layers"]


def test_step_body_can_check_height_without_certifying_lead_dimensions(tmp_path):
    from src.smt_engine import load_smt_profile
    design,data,odb,_=fixture(tmp_path)
    p=tmp_path/"body.stp";p.write_text(STEP)
    mapping=tmp_path/"mapping.json"
    mapping.write_text(json.dumps(dict(schema_version=1,unit="mm",coordinate_system="brd",alignment_confirmed=True,
        brd_sha256=sha256_file(design["_input_brd"]),step_sha256=sha256_file(p),components=[dict(refdes="U1",side="TOP",confirmed=True,
        source="engineering CAD export",step_product="U1",outline=list(box(0,0,2,3).exterior.coords),height_mm=2)])))
    data["assembly_rules"]=dict(source="factory confirmed",component_height_top_max_mm=1)
    e=tmp_path/"objects.json";e.write_text(json.dumps(data))
    profile=load_smt_profile(Path(__file__).resolve().parents[1]/"config/suppliers/smt/smt_capability_lijing.yaml")
    r=analyze_smt_workflow(profile,"automotive",{},design=design,evidence_path=str(e),step_path=str(p),model_path=str(mapping))
    assert any(f["field"]=="side_height" and f["status"]=="fail" for f in r["assembly_findings"])
    assert r["picture_geometry"]["components"][0]["boundary"]=="body"
    assert not r["picture_geometry"]["components"][0].get("package_dimensions_confirmed")


def test_model_template_is_hash_bound_but_unconfirmed_and_empty(tmp_path):
    from src.application.reports import write_step_model_template
    design,_,_,_=fixture(tmp_path)
    step=tmp_path/"body.stp";step.write_text(STEP)
    target=tmp_path/"model.json";write_step_model_template(design["_input_brd"],step,target)
    d=json.loads(target.read_text(encoding="utf-8"))
    assert d["step_sha256"]==sha256_file(step) and d["brd_sha256"]==sha256_file(design["_input_brd"])
    assert not d["alignment_confirmed"] and not d["components"]
    assert d["available_step_products"]==["U1"]


def test_application_service_passes_supplement_arguments_and_isolates_inputs(tmp_path):
    from src.application.smt_analysis import analyze_smt_workflow as service
    from src.smt_engine import load_smt_profile
    design,data,odb,_=fixture(tmp_path)
    write_zip(odb,odb_files())
    evidence=tmp_path/"objects.json";evidence.write_text(json.dumps(data))
    doc=load_smt_profile(Path(__file__).resolve().parents[1]/"config/suppliers/smt/smt_capability_lijing.yaml")
    old=deepcopy(design)
    r=service(doc,"automotive",{},design=design,evidence_path=str(evidence),odb_path=str(odb),odb_step="pcb")
    assert r["supplement_analysis"]["odb"]["selected_step"]=="pcb" and design==old


def test_step_conflicting_length_context_does_not_pick_first_unit(tmp_path):
    p=tmp_path/"units.stp"
    p.write_text(STEP.replace("#3=PRODUCT", "#6=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT($,.METRE.));\n#7=(GLOBAL_UNIT_ASSIGNED_CONTEXT((#6))REPRESENTATION_CONTEXT('',''));\n#3=PRODUCT"))
    r=parse_step(p)
    assert r["unit_to_mm"] is None and r["unit_context_count"]==2

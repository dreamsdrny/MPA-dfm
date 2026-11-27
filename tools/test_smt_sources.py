"""PCB -> BOM -> object evidence integration, conflict/DNP/side boundaries."""
import csv
from pathlib import Path
from unittest.mock import patch

from src.application.smt_sources import build_smt_sources
from src.brd_input import content_sha256


def inputs(tmp_path):
    board = tmp_path / "board.brd"
    board.write_bytes(b"fixture")
    folder = tmp_path / "native"
    folder.mkdir()
    native = dict(status="extracted", directory=str(folder), source_brd_sha256=content_sha256(board), unit="mm", component_count=2,
        components=[dict(refdes="R1", side="TOP", rotation=90, x_mm=5, y_mm=5, footprint="0402"),
                    dict(refdes="R2", side="BOTTOM", rotation=0, x_mm=10, y_mm=10, footprint="0402")],
        objects=dict(properties=[], pins=[], pads=[], bounds=[], holes=[], board_outline=[[0,0],[20,0],[20,20],[0,20]], vcut_lines=[], complete={}))
    design = dict(_input_brd=str(board), _native_payload=native)
    return design


def bom(path, rows):
    headers = ["refdes", "footprint", "side", "dnp", "category", "height_mm", "body_length_mm", "body_width_mm", "body_confirmed", "body_origin_centered"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)


def test_bom_body_geometry_exact_match_and_dnp(tmp_path):
    design = inputs(tmp_path)
    path = tmp_path / "bom.csv"
    bom(path, [dict(refdes="R1", footprint="0402", side="TOP", dnp="NO", category="chip_0402", height_mm=.5,
                   body_length_mm=1, body_width_mm=.5, body_confirmed="YES", body_origin_centered="YES"),
               dict(refdes="R2", footprint="0402", side="BOTTOM", dnp="YES")])
    with patch("src.application.pcb_workflow.verify_analysis_sources"):
        output = build_smt_sources(design, bom_path=str(path), output_dir=str(tmp_path / "out"))
    data = output["data"]
    assert len(data["components"]) == 1
    row = data["components"][0]
    assert row["boundary"] == "body" and row["height_mm"] == .5
    assert row["category"] == "chip_0402"
    assert data["dnp_components"][0]["refdes"] == "R2"
    assert data["complete"]["components_top"]


def test_conflicting_footprint_cannot_create_confirmed_body(tmp_path):
    design = inputs(tmp_path)
    path = tmp_path / "bom.csv"
    bom(path, [dict(refdes="R1", footprint="0603", side="TOP", dnp="NO", category="chip_0603", body_length_mm=1,
                   body_width_mm=.5, body_confirmed="YES", body_origin_centered="YES"), dict(refdes="R2", dnp="NO")])
    output = build_smt_sources(design, bom_path=str(path))
    assert not output["data"]["components"][0].get("outline")
    assert not output["data"]["complete"]["components_top"]
    assert any("封装" in error for error in output["data"]["source_association"]["errors"])


def test_unknown_dnp_and_body_origin_are_not_guessed(tmp_path):
    design = inputs(tmp_path)
    path = tmp_path / "bom.csv"
    bom(path, [dict(refdes="R1", footprint="0402", category="chip_0402", body_length_mm=1,
                   body_width_mm=.5, body_confirmed="YES"), dict(refdes="R2")])
    output = build_smt_sources(design, bom_path=str(path))
    assert not output["data"]["components"][0].get("outline")
    assert output["data"]["components"][0]["dnp"] is None
    assert not output["data"]["complete"]["components"]


def test_auto_smt_workflow_uses_generated_evidence_and_saves_associations(tmp_path):
    from src.application.smt_workflow import analyze_smt_workflow
    from src.smt_engine import load_smt_profile
    design = inputs(tmp_path)
    path = tmp_path / "bom.csv"
    bom(path, [dict(refdes="R1", footprint="0402", side="TOP", dnp="NO", category="chip_0402", height_mm=.5,
                   body_length_mm=1, body_width_mm=.5, body_confirmed="YES", body_origin_centered="YES"),
               dict(refdes="R2", footprint="0402", side="BOTTOM", dnp="NO", category="chip_0402", height_mm=.5,
                   body_length_mm=1, body_width_mm=.5, body_confirmed="YES", body_origin_centered="YES")])
    doc = load_smt_profile(Path(__file__).resolve().parents[1] / "config/suppliers/smt/smt_capability_lijing.yaml")
    result = analyze_smt_workflow(doc, "automotive", {}, design=design, bom_path=str(path), auto_sources=True)
    assert len(result["source_association"]["rows"]) == 2
    assert result["generated_evidence"]
    assert result["assembly_completeness"]["TOP"]["components"]
    assert result["linked_source_fingerprints"]


def test_declared_testpoint_without_pad_geometry_is_not_complete(tmp_path):
    design = inputs(tmp_path)
    design["_native_payload"]["objects"]["complete"]["pads"] = True
    path = tmp_path / "bom.csv"
    path.write_text("refdes,footprint,side,dnp,test_point\nR1,0402,TOP,NO,YES\nR2,0402,BOTTOM,NO,NO\n")
    output = build_smt_sources(design, bom_path=str(path))
    assert not output["data"]["complete"]["test_pads"]
    assert any("test_pads" in error for error in output["data"]["source_association"]["errors"])


def test_explicit_mounting_hole_is_exactly_linked_not_placement_origin(tmp_path):
    design=inputs(tmp_path)
    native=design["_native_payload"]
    native["objects"]["holes"]=[dict(refdes="R1",x_mm=6,y_mm=7,drill_mm=2,plated=False,start_layer="TOP",end_layer="BOTTOM")]
    path=tmp_path/"mounting.csv"
    path.write_text('refdes,footprint,side,dnp,mounting_hole,mounting_sides\nR1,0402,TOP,NO,YES,"TOP;BOTTOM"\nR2,0402,BOTTOM,NO,NO,\n')
    result=build_smt_sources(design,bom_path=str(path))
    hole=result["data"]["mounting_holes"][0]
    assert (hole["x_mm"],hole["y_mm"])==(6,7)  # placement was 5,5
    assert hole["assembly_sides"]==["TOP","BOTTOM"]
    assert "R1" in result["data"]["non_assembly_refdes"]


def test_smd_pth_conflict_never_enters_confirmed_geometry(tmp_path):
    design=inputs(tmp_path)
    path=tmp_path/"conflict.csv"
    path.write_text("refdes,footprint,side,dnp,smd,pth\nR1,0402,TOP,NO,YES,YES\nR2,0402,BOTTOM,NO,YES,NO\n")
    result=build_smt_sources(design,bom_path=str(path))
    row=result["data"]["components"][0]
    assert row["smd"] is None and row["pth"] is None
    assert not result["data"]["complete"]["components_top"]
    assert any("SMD/PTH" in error for error in result["data"]["source_association"]["errors"])


def test_large_native_via_outline_is_preserved_without_indent_inflation(tmp_path):
    import json
    from shapely.geometry import Point
    design=inputs(tmp_path)
    native=design["_native_payload"]
    native["objects"]["pads"].append(dict(refdes="",pin="",side="TOP",object_kind="via",confirmed=True,net="GND",
        outline=list(Point(10,10).buffer(.3,quad_segs=128).exterior.coords)))
    r=build_smt_sources(design,output_dir=str(tmp_path))
    text=Path(r["path"]).read_text(encoding="utf-8")
    assert "\n" not in text
    data=json.loads(text)
    assert len(data["via_pads"])==1 and len(data["via_pads"][0]["outline"])==513

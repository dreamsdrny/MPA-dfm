"""Selected BOM is actually analyzed even with auto source mode disabled."""
from pathlib import Path

from src.application.smt_analysis import analyze_smt_workflow
from src.smt_engine import load_smt_profile


def test_bom_is_used_in_parameter_mode_and_conflicts_affect_verdict(tmp_path):
    path=tmp_path / "bom.csv"
    path.write_text("refdes,footprint,side,dnp,part_number\nR1,0603,TOP,NO,P1\nR2,0402,TOP,NO,P1\nR3,0402,TOP,YES,P2\n")
    design=dict(_native_payload=dict(components=[dict(refdes="R1",footprint="0402",side="TOP",x_mm=0,y_mm=0),
        dict(refdes="R2",footprint="0402",side="TOP",x_mm=1,y_mm=1)]))
    doc=load_smt_profile(Path(__file__).resolve().parents[1] / "config/suppliers/smt/smt_capability_lijing.yaml")
    result=analyze_smt_workflow(doc,"automotive",{},design=design,bom_path=str(path),auto_sources=False)
    assert result["bom_analysis"]["total"]==3
    assert any(row["rule"]=="同料号多封装" for row in result["bom_findings"])
    assert any(row["refdes"]=="R1" and row["status"]=="fail" for row in result["bom_findings"])
    assert result["verdict"]=="不符合所选能力"
    assert result["linked_source_fingerprints"][0]["path"]==str(path.resolve())


def test_selected_bom_is_used_with_manual_object_json(tmp_path):
    import json
    from src.application.smt_workflow import evidence_template
    path=tmp_path / "bom.csv"
    path.write_text("refdes,side,dnp\nR3,TOP,NO\n")
    board=tmp_path / "board.brd"
    board.write_bytes(b"fixture")
    data=evidence_template(board)
    data.update(coordinate_alignment_confirmed=True,components=[dict(refdes="R1",side="TOP",category="chip_0402",boundary="body",outline=[[0,0],[1,0],[1,1],[0,1]])])
    evidence=tmp_path / "objects.json"
    evidence.write_text(json.dumps(data))
    doc=load_smt_profile(Path(__file__).resolve().parents[1] / "config/suppliers/smt/smt_capability_lijing.yaml")
    result=analyze_smt_workflow(doc,"automotive",{},design=dict(_input_brd=str(board)),evidence_path=str(evidence),bom_path=str(path),auto_sources=False)
    assert result["bom_analysis"]["total"]==1
    assert any(row["refdes"]=="R3" for row in result["bom_findings"])
    assert any(row["refdes"]=="R1" for row in result["bom_findings"])


def test_bom_mutation_after_analysis_is_rejected_on_export(tmp_path):
    import pytest
    from src.adapters.workbench_report import export_report
    path=tmp_path / "bom.csv"
    path.write_text("refdes,dnp\nR1,NO\n")
    doc=load_smt_profile(Path(__file__).resolve().parents[1] / "config/suppliers/smt/smt_capability_lijing.yaml")
    result=analyze_smt_workflow(doc,"automotive",{},design=dict(_components=dict(refdes=["R1"])),bom_path=str(path))
    path.write_text("refdes,dnp\nR2,NO\n")
    with pytest.raises(ValueError,match="已变化"):
        export_report("smt",tmp_path / "stale.xlsx",smt=result)
    assert not (tmp_path / "stale.xlsx").exists()


def test_same_ipn_multiple_cad_packages_without_bom_footprint_column(tmp_path):
    path=tmp_path/"bom.csv";path.write_text("refdes,part_number,dnp\nR1,P1,NO\nR2,P1,NO\n")
    design=dict(_native_payload=dict(components=[dict(refdes="R1",footprint="0402",side="TOP"),dict(refdes="R2",footprint="0805",side="TOP")]))
    doc=load_smt_profile(Path(__file__).resolve().parents[1]/"config/suppliers/smt/smt_capability_lijing.yaml")
    r=analyze_smt_workflow(doc,"automotive",{},design=design,bom_path=str(path))
    assert any(f["rule"]=="CAD 同料号多封装" and f["status"]=="fail" for f in r["bom_findings"])

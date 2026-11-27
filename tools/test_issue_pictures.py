"""Embedded image bytes and anchors survive formatting/save/reload and missing sources."""
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook

from src.adapters.issue_pictures import IssuePictureContext, add_issue_summary, pcb_entries
from src.adapters.pcb_report import build_dfm_workbook
from src.adapters.workbench_report import build_combined_workbook
from src.excel_layout import format_workbook


def pcb_fixture(tmp_path):
    path=tmp_path / "board.gts"
    path.write_text("%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,1*%\nD10*\nX0Y0D03*\nX1200000Y0D03*\nM02*\n")
    return dict(items=[], issues=[dict(code="MASK-01", category="Gerber", rule="阻焊桥", description="桥宽不足", suggestion="扩大间距",
        count=1, severity="高", status="打开", layer="mask_top", x=0.6,y=0)],
        problem_locations=[dict(item="阻焊桥",x_mm=.6,y_mm=0,layer="mask_top",source=str(path),brd_alignment=False)],
        gerber_folder=str(tmp_path),component_coordinates=[], risk={},info_stats={},issue_summary={})


def test_pcb_summary_has_embedded_picture_and_real_source(tmp_path):
    pcb=pcb_fixture(tmp_path)
    before=deepcopy(pcb)
    wb=build_dfm_workbook(pcb)
    sheet=wb["Issue summary"]
    assert sheet["L4"].value=="问题图片"
    assert len(sheet._images)==1
    assert str(tmp_path / "board.gts") in sheet["M5"].value
    output=tmp_path / "pictures.xlsx"
    format_workbook(wb).save(output)
    wb.close()
    loaded=load_workbook(output)
    assert len(loaded["Issue summary"]._images)==1
    assert loaded["Issue summary"].row_dimensions[5].height>=165
    loaded.close()
    with ZipFile(output) as archive:
        assert any(n.startswith("xl/media/") and n.endswith(".png") for n in archive.namelist())
    assert pcb==before


def test_picture_anchor_moves_with_continuation_rows(tmp_path):
    pcb=pcb_fixture(tmp_path)
    wb=Workbook();wb.remove(wb.active)
    entries=pcb_entries(pcb)
    entries=[dict(entries[0],description="x"*5000),dict(entries[0],code="MASK-02")]
    sheet=add_issue_summary(wb,entries,pcb=pcb)
    format_workbook(wb)
    second_row=next(r[0].row for r in sheet.iter_rows() if r[0].value==2)
    assert second_row>3
    assert sheet._images[1].anchor==f"D{second_row}"
    path=tmp_path / "continued.xlsx"
    wb.save(path);wb.close()
    loaded=load_workbook(path)
    assert loaded["Issue summary"]._images[1].anchor._from.row+1==second_row
    format_workbook(loaded).save(tmp_path / "roundtrip.xlsx")
    loaded.close()


def test_missing_or_unaligned_geometry_never_gets_fake_image(tmp_path):
    context=IssuePictureContext()
    assert context.render(dict(scope="PCB",layer="mask_top",x_mm=1,y_mm=2))[0] is None
    assert context.render(dict(scope="PCB",layer="mask_top"))[0] is None
    smt=dict(picture_gerber_dir=str(tmp_path),picture_gerber_alignment=False,
             picture_geometry=dict(alignment_confirmed=False,components=[]))
    pcb_fixture(tmp_path)
    assert IssuePictureContext(smt=smt).render(dict(scope="SMT",layer="TOP",x_mm=.6,y_mm=0,coordinate_system="brd"))[0] is None


def test_combined_summary_contains_smt_object_crop(tmp_path):
    from src.smt_engine import analyze_smt,load_smt_profile
    doc=load_smt_profile(Path(__file__).resolve().parents[1] / "config/suppliers/smt/smt_capability_lijing.yaml")
    smt=analyze_smt(doc,"automotive",{})
    smt["object_findings"]=[dict(field="chip_0402_spacing",label="器件间距",status="fail",side="TOP",refdes="R1",other_refdes="R2",
        x_mm=1,y_mm=1,reason="间距不足",severity="high")]
    smt["picture_geometry"]=dict(unit="mm",alignment_confirmed=True,source="verified objects",components=[
        dict(refdes="R1",side="TOP",boundary="body",outline=[[0,0],[1,0],[1,2],[0,2]]),
        dict(refdes="R2",side="TOP",boundary="place_bound",outline=[[1.1,0],[2,0],[2,2],[1.1,2]])])
    wb=build_combined_workbook(pcb_fixture(tmp_path),smt)
    assert "Issue summary" in wb.sheetnames
    assert "PCB_Issue summary" in wb.sheetnames
    assert len(wb["Issue summary"]._images)>=2
    wb.save(tmp_path / "combined.xlsx")
    wb.close()


def test_pin_issue_crop_not_dominated_by_large_component_body():
    from PIL import Image
    smt=dict(picture_geometry=dict(unit="mm",alignment_confirmed=True,components=[dict(refdes="U1",side="BOTTOM",
        outline=[[-20,-20],[20,-20],[20,20],[-20,20]])],pads=[
        dict(refdes="U1",pin="11",side="BOTTOM",outline=[[0,0],[.2,0],[.2,.2],[0,.2]]),
        dict(refdes="U1",pin="12",side="BOTTOM",outline=[[.1,.1],[.3,.1],[.3,.3],[.1,.3]])]))
    entry=dict(scope="SMT",layer="BOTTOM",side="BOTTOM",x_mm=.15,y_mm=.15,refdes="U1",other_refdes="U1",pin="11",other_pin="12")
    stream,note=IssuePictureContext(smt=smt).render(entry)
    assert stream is not None
    image=Image.open(stream).convert("RGB")
    points=[(x,y) for y in range(195) for x in range(320) if image.getpixel((x,y)) in ((255,91,88),(112,204,236))]
    assert points and max(x for x,_ in points)-min(x for x,_ in points)>30

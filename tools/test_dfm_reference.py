"""Mixed-unit external reports must retain original evidence and all rule tiers."""
from openpyxl import Workbook

from src.adapters.dfm_reference import read_dfm_reference


def test_external_mil_is_not_mm_and_no_tier_selected(tmp_path):
    wb=Workbook();s=wb.active;s.title="Issue1"
    for col,text in ((9,"测量值\nmil"),(10,"规则值\nmil"),(11,"X位置\nmil"),(12,"Y位置\nmil")):
        s.cell(28,col,text)
    for col,value in ((3,1),(4,"Toeprint distance"),(9,95.446),(10,"120, 140, 160"),(11,4388.6631),(12,6577.8402)):
        s.cell(29,col,value)
    path=tmp_path/"reference.xlsx";wb.save(path);wb.close()
    before=path.read_bytes()
    result=read_dfm_reference(path)
    row=result["details"][0]["records"][0]
    assert row["measured_mm"]==2.4243284
    assert row["x_mm"]==111.47204274
    assert row["limit_tiers_mm"]==[3.048,3.556,4.064]
    assert row["selected_limit_mm"] is None
    assert not result["macros_executed"] and not result["brd_alignment_confirmed"]
    assert path.read_bytes()==before


def test_unknown_unit_never_inferred_from_coordinates(tmp_path):
    wb=Workbook();s=wb.active;s.title="Issue2"
    s.cell(29,3,1);s.cell(29,4,"Distance");s.cell(29,9,5);s.cell(29,11,100)
    path=tmp_path/"unknown.xlsx";wb.save(path);wb.close()
    result=read_dfm_reference(path)
    assert result["warnings"]
    row=result["details"][0]["records"][0]
    assert row["x_mm"] is None and row["measured_mm"] is None


def test_generic_mil_header_does_not_convert_percent_count_or_sentinels(tmp_path):
    wb=Workbook();s=wb.active;s.title="Issue1"
    for col in (9,10,11,12):s.cell(28,col,"mil")
    for i,(rule,value,limits) in enumerate((("Toeprint covered by Surface (%)",100,"70，60，50"),("Hole in SMD",2,"-1, -2, -3"),("G. Fiducial Primary side",1,"2,3,4"),("RefDes in BOM not in CAD",None,"-1,-2,-3")),29):
        s.cell(i,3,i-28);s.cell(i,4,rule);s.cell(i,9,value);s.cell(i,10,limits)
    path=tmp_path/"stats.xlsx";wb.save(path);wb.close()
    rows=read_dfm_reference(path)["details"][0]["records"]
    assert all(r["measured_mm"] is None and all(v is None for v in r["limit_tiers_mm"]) for r in rows)
    assert rows[0]["measured_ratio"]==1 and rows[0]["limit_tiers_numeric"]==[70,60,50]
    assert rows[1]["measured_count"]==2 and rows[2]["measured_count"]==1


def test_unknown_vendor_area_difference_not_mil_or_bounded_ratio(tmp_path):
    wb=Workbook();s=wb.active;s.title="Issue1"
    for col in (9,10,11,12):s.cell(28,col,"mil")
    s.cell(29,3,1);s.cell(29,4,"Non-uniform SMD pads size");s.cell(29,9,76311.202);s.cell(29,10,"50,50,49")
    path=tmp_path/"areas.xlsx";wb.save(path);wb.close()
    result=read_dfm_reference(path);r=result["details"][0]["records"][0]
    assert r["measured_mm"] is None and r["measured_ratio"] is None and result["warnings"]
    assert r["measured_original"]==76311.202

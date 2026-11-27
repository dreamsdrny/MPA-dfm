import pytest
from openpyxl import Workbook

from src.adapters.bom_reader import read_bom


def test_unheaded_primary_secondary_after_notes_not_side_or_dnp(tmp_path):
    w=Workbook();s=w.active
    s.append(["Item","Part Number","Quantity","Reference","Part","MPN"])
    s.append([1,"P1",2,"Q3,Q4","value","M1",None,None,"主"])
    s.append([2,"P2",2,"Q3,Q4","altvalue","M2","note",None,"次"])
    p=tmp_path/"bom.xlsx";w.save(p);w.close();before=p.read_bytes()
    r=read_bom(p,strict=True)
    assert len(r["rows"])==2 and r["stats"]["alternate_records"]==2
    for row in r["rows"]:
        assert row["part_number"]=="P1" and not row["dnp_explicit"] and row["side"]==""
        assert row["alternatives"][0]["manufacturer_part_number"]=="M2"
        assert row["alternatives"][0]["sourcing_marker_column"]==9
    assert p.read_bytes()==before


def test_primary_not_first_and_unmarked_duplicates_still_rejected(tmp_path):
    w=Workbook();s=w.active;s.append(["refdes","part_number","sourcing_role"])
    s.append(["U1","A","副"]);s.append(["U1","B","主"])
    p=tmp_path/"bom.xlsx";w.save(p)
    r=read_bom(p,strict=True)
    assert r["rows"][0]["part_number"]=="B" and r["rows"][0]["alternatives"][0]["part_number"]=="A"
    s.append(["U1","C",""]);w.save(p);w.close()
    with pytest.raises(ValueError,match="重复"):read_bom(p,strict=True)


def test_alternate_without_primary_fails_not_silently_selected(tmp_path):
    p=tmp_path/"bom.csv";p.write_text("refdes,part_number,sourcing_role\nU1,A,alternate\n")
    with pytest.raises(ValueError,match="没有明确主料"):read_bom(p,strict=True)


def test_export_keeps_alternate_and_sourcing_role(tmp_path):
    from src.adapters.bom_reader import export_bom
    p=tmp_path/"in.csv";p.write_text("refdes,part_number,sourcing_role\nU1,A,primary\nU1,B,alternate\n")
    d=read_bom(p,strict=True);target=tmp_path/"out.csv";export_bom(target,d["rows"])
    d=read_bom(target,strict=True)
    assert d["rows"][0]["part_number"]=="A" and d["rows"][0]["alternatives"][0]["part_number"]=="B"

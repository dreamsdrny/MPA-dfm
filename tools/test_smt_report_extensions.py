from shapely.geometry import box
from src.domain.smt_manufacturing import scan_assembly


def test_confirmed_physical_lead_not_pad_touches_mask():
    d=dict(components=[dict(refdes="CMC4",side="TOP",physical_leads=[dict(pin="1",no_solder=False,confirmed=True,
        outline=list(box(0,0,1,1).exterior.coords))])],assembly_rules={},pads=[])
    films=dict(mask_top=dict(geometry=box(.2,0,1,1)))
    r=scan_assembly(d,films)
    f=next(f for f in r["findings"] if f["field"]=="physical_pin_mask")
    assert f["status"]=="fail" and f["unit"]=="ratio"


def test_laser_trace_and_test_distance_and_panel_review():
    d=dict(assembly_rules=dict(source="factory",laser_no_trace=True,test_to_laser_mm=6),
           laser_regions=[dict(id="QR",side="TOP",confirmed=True,source="drawing",outline=list(box(0,0,8,8).exterior.coords))],
           native_traces=[dict(layer="ETCH/TOP",points=[[-1,1],[1,1]],width_mm=.2)],
           test_pads=[dict(refdes="TP1",side="TOP",confirmed=True,outline=list(box(9,0,10,1).exterior.coords))],
           engineering_review=dict(panel_corner_support=dict(status="fail",source="panel drawing",note="corners missing")))
    r=scan_assembly(d,{})
    assert len([f for f in r["findings"] if f["field"]=="laser_trace_test_clearance"])==2
    assert next(f for f in r["findings"] if f["field"]=="panel_corner_support")["status"]=="fail"


def test_mark_silk_not_mask_and_rule_template_sync(tmp_path):
    import json
    from pathlib import Path
    from src.application.reports import write_assembly_rule_template
    d=dict(assembly_rules=dict(source="factory",mark_to_silk_mm=1),marks=[dict(id="FM",side="TOP",global_confirmed=True,diameter_mm=1,x_mm=0,y_mm=0)])
    r=scan_assembly(d,dict(silkscreen_top=dict(geometry=box(.6,0,1,1))))
    assert any(f["field"]=="mark_to_silkscreen" for f in r["findings"])
    p=tmp_path/"rules.json";write_assembly_rule_template(p)
    assert json.loads(p.read_text())["assembly_rules"]==json.loads((Path(__file__).resolve().parents[1]/"templates/assembly_rules.json").read_text())["assembly_rules"]

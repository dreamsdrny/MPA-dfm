from copy import deepcopy

import pytest
from shapely.geometry import box

from src.domain.smt_manufacturing import scan_assembly,validate_rules


def pad(ref,pin,shape,**attrs):
    return dict(refdes=ref,pin=pin,side="BOTTOM",confirmed=True,outline=list(shape.exterior.coords),**attrs)


def base(**rules):
    return dict(components=[dict(refdes="U1",side="BOTTOM",smd=True)],pads=[],complete={},assembly_rules=dict(source="eng",**rules))


def rows(d,key,films=None):
    return [f for f in scan_assembly(d,films or {})["findings"] if f["field"]==key]


def test_intra_package_overlap_detected_without_threshold_even_same_net():
    d=base();d["pads"]=[pad("U1","11",box(0,0,1,1),net="A"),pad("U1","12",box(.5,0,1.5,1),net="A")]
    old=deepcopy(d);r=rows(d,"smd_pin_spacing")[0]
    assert r["status"]=="fail" and r["actual"]==0 and r["pin"]=="11" and r["other_pin"]=="12"
    assert r["overlap_area_mm2"]==.5 and d==old
    d["dnp_components"]=[dict(refdes="U1")]
    assert not rows(d,"smd_pin_spacing")


def test_spacing_nonoverlap_requires_threshold_and_deduplicates_exact_pad():
    d=base(smd_pin_spacing_mm=.249936);d["pads"]=[pad("U1","1",box(0,0,1,1)),pad("U1","2",box(1.2,0,2.2,1))]
    d["pads"].append(deepcopy(d["pads"][0]))
    assert len(rows(d,"smd_pin_spacing"))==1
    d["assembly_rules"]["smd_pin_spacing_mm"]=None
    assert not rows(d,"smd_pin_spacing")


def test_mask_coverage_explicit_open_vs_smd_policy_and_unknown_apertures():
    d=base();d["pads"]=[pad("U1","1",box(0,0,1,1),mask_policy="open")]
    assert not rows(d,"smd_mask_opening")
    d["mask_openings"]=[pad("U1","1",box(.1,.1,.9,.9))]
    assert rows(d,"smd_mask_opening")[0]["status"]=="fail"
    d["pads"][0]["mask_policy"]="smd"
    assert not rows(d,"smd_mask_opening")
    d["pads"][0]["mask_policy"]=""
    assert rows(d,"smd_mask_opening")[0]["status"]=="review"


def test_native_mask_overlap_and_final_film_priority():
    d=base(smd_mask_bridge_mm=.1)
    d["pads"]=[pad("U1","11",box(0,0,.4,.4)),pad("U1","12",box(.5,0,.9,.4))]
    d["mask_openings"]=[pad("U1","11",box(-.1,-.1,.5,.5)),pad("U1","12",box(.4,-.1,1,.5))]
    assert rows(d,"smd_mask_bridge")[0]["measurement_basis"]=="native_padstack_opening"
    films=dict(mask_bottom=dict(geometry=box(-.01,-.01,.41,.41).union(box(.49,-.01,.91,.41))))
    row=rows(d,"smd_mask_bridge",films)[0]
    assert row["actual"]==pytest.approx(.08) and row["measurement_basis"]=="final_mask_film"


def test_mark_under_shield_recognition_stage_and_panel_scope():
    d=base(panel_mark_count_min=3,panel_mark_edge_mm=4)
    d["marks"]=[dict(id="FM1",global_confirmed=True,x_mm=.5,y_mm=.5,side="BOTTOM")]
    d["shields"]=[pad("SH1","1",box(0,0,1,1))]
    assert rows(d,"mark_shield_visibility")
    d["marks"][0]["recognition_stage"]="before_shield"
    assert not rows(d,"mark_shield_visibility")
    d.update(panel_outline=list(box(0,0,10,10).exterior.coords),panel_alignment_confirmed=True,assembly_sides=["BOTTOM"])
    d["complete"]["panel_marks"]=True
    assert rows(d,"panel_mark_layout")[0]["actual"]==0  # board Mark cannot count as panel Mark


def test_qr_frame_and_xray_are_separate_evidence_not_inferred_from_vias():
    d=base(laser_frame_min_mm=8,thermal_void_ratio_max=.4)
    d["laser_regions"]=[pad("QR1","",box(0,0,6,6),source="drawing")]
    assert rows(d,"laser_frame_size")[0]["actual"]==6
    assert not rows(d,"thermal_void_evidence")
    d["thermal_void_records"]=[dict(refdes="U1",confirmed=True,method="xray",source="customer inspection",void_ratio=.5)]
    assert rows(d,"thermal_void_evidence")[0]["status"]=="fail"
    with pytest.raises(ValueError):validate_rules(dict(source="eng",thermal_void_ratio_max=40))


def test_large_pin_count_uses_index_not_quadratic_candidate_budget():
    d=base()
    d["native_pins"]=[dict(refdes="U1",pin=str(i),x_mm=i%30,y_mm=i//30) for i in range(900)]
    r=scan_assembly(d,{})
    assert not r["errors"]
    assert next(f for f in r["findings"] if f["field"]=="pin_pitch")["actual"]==1

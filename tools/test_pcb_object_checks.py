"""Actual defect/clear/unknown/budget fixtures and ODB native object integration."""
import json

import pytest
from shapely.geometry import box, Point

from src.layout_geometry import ScanLimits
from src.pcb_object_checks import scan_object_checks
from src.odb_dfm import analyze_odb, attach_odb_result
from src.dfm_engine import analyze_design
from src.dfm_report import build_dfm_workbook
from src.workbench_report import export_report
from src.design_inputs.odb_features import parse_features, symbol_shape
from src.design_inputs.odb_connectivity import feature_links
from tools.test_manufacturing_coordination import write_zip
from src.design_inputs.archive import InputContainer
from src.design_inputs.odb import parse_odb


def film(role, shape):
    return dict(role=role, geometry=shape, path=role+".gbr", brd_alignment=False)


def pad(id, shape, net="GND", **attrs):
    ref, pin = id.split(".")
    return dict(id=id, refdes=ref, pin=pin, geometry=shape, net=net, layer="copper_top",
                mask_policy="open", smd=True, **attrs)


def test_actual_spacing_identity_and_explicit_limit():
    objects = [pad("R1.1", box(0, 0, 1, 1)), pad("R2.1", box(1.1, 0, 2.1, 1), "VCC")]
    films = {"copper_top": film("copper_top", objects[0]["geometry"].union(objects[1]["geometry"]))}
    result = scan_object_checks(films, objects, [], ScanLimits())
    check = next(c for c in result["checks"] if c["field"] == "object_pad_spacing")
    assert check["measured"] == .1 and check["refdes"] == "R1" and check["other_refdes"] == "R2"
    assert check["status"] == "warn_limit" and not check["brd_alignment"]
    hard = scan_object_checks(films, objects, [], ScanLimits(), dict(pad_to_pad_mm=.254))
    assert next(c for c in hard["checks"] if c["field"] == "object_pad_spacing")["status"] == "fail"
    objects[1]["refdes"] = "R1"
    assert not any(c["field"] == "object_pad_spacing" for c in scan_object_checks(films, objects, [], ScanLimits())["checks"])


def test_mask_missing_covered_smd_and_paste_bridge():
    objects = [pad("U1.1", box(0, 0, 1, 1)), pad("U1.2", box(1.2, 0, 2.2, 1))]
    films = {"copper_top": film("copper_top", objects[0]["geometry"].union(objects[1]["geometry"])),
             "mask_top": film("mask_top", box(-.1, -.1, .5, 1.1)),
             "paste_top": film("paste_top", box(.2, .2, 2, .8))}
    result = scan_object_checks(films, objects, [], ScanLimits())
    assert len([c for c in result["checks"] if c["field"] == "object_pad_mask_covered"]) == 2
    assert any(c["field"] == "object_paste_bridge" for c in result["checks"])
    objects[0]["mask_policy"] = "smd"
    result = scan_object_checks(films, objects, [], ScanLimits())
    assert not any(c["field"] == "object_pad_mask_covered" and c["object_id"] == "U1.1" for c in result["checks"])
    films.pop("mask_top")
    result = scan_object_checks(films, objects, [], ScanLimits())
    assert not any(c["field"] == "object_pad_mask_covered" for c in result["checks"])
    assert next(c for c in result["coverage"] if c["rule"] == "object_pad_mask")["status"] == "missing"


def test_via_different_net_and_tenting_policy():
    p = pad("R1.1", box(1.1, 0, 2.1, 1), "VCC")
    via = dict(id="V1", layer="copper_top", net="GND", geometry=box(0, 0, 1, 1), mask_policy="tented")
    films = {"copper_top": film("copper_top", box(0, 0, 1, 1).union(p["geometry"])),
             "mask_top": film("mask_top", box(1.05, -.1, 2.2, 1.1))}
    result = scan_object_checks(films, [p], [via], ScanLimits(), dict(via_to_mask_mm=.1))
    assert any(c["field"] == "object_via_pad_spacing" for c in result["checks"])
    assert any(c["field"] == "object_via_mask_spacing" and c["status"] == "fail" for c in result["checks"])
    via["net"] = "VCC"
    via["mask_policy"] = ""
    result = scan_object_checks(films, [p], [via], ScanLimits(), dict(via_to_mask_mm=.1))
    assert not any(c["field"] in ("object_via_pad_spacing", "object_via_mask_spacing") for c in result["checks"])


def test_unknown_copper_and_budget_and_invalid_limit():
    objects = [pad("R1.1", box(0, 0, 1, 1)), pad("R2.1", box(1.1, 0, 2.1, 1))]
    films = {"copper_top": film("copper_top", box(0, 0, 3, 1))}
    result = scan_object_checks(films, objects, [], ScanLimits(max_candidates=1))
    assert result["errors"] and any(c["status"] == "incomplete" for c in result["coverage"])
    with pytest.raises(ValueError, match="有限"):
        scan_object_checks(films, objects, [], ScanLimits(), dict(pad_to_pad_mm=float("nan")))
    films["copper_top"]["geometry"] = box(9, 9, 10, 10)
    result = scan_object_checks(films, objects, [], ScanLimits())
    assert not result["checks"] and result["object_counts"]["verified_pads"] == 0


def test_odb_units_native_attributes_symbol_variants_and_arc_endpoints():
    result = parse_features("U INCH\nF 1\n$0 rect10x20xc2x1 I\n@0 .smd\n@1 .pad_usage\nP 1 2 0 P 0 8 0;0,1=0\n")
    assert result["status"] == "parsed"
    assert result["records"][0]["attributes"] == {".smd": True, ".pad_usage": "0"}
    assert result["geometry"].centroid.x == pytest.approx(25.4, abs=.01)
    shape, _ = symbol_shape("rect1000x1000xc100x1", 1)
    assert shape.area == pytest.approx(.995)
    shape, _ = symbol_shape("di1000x1000", 1)
    assert shape.area == .5
    shape, _ = symbol_shape("donut_r1000x500", 1)
    assert not shape.contains(Point(0, 0))
    assert .56 < shape.area < .61
    shape, _ = symbol_shape("rect1000x1000xr100x1", 1)
    assert .997 < shape.area < 1
    metadata = parse_features("U MM\nID=12\n$0 r1000\nP 0 0 0 P 0 8 0\n")
    assert metadata["status"] == "parsed"
    with pytest.raises(ValueError, match="冲突"):
        parse_features("U INCH\nUNITS=MM\n")
    links, errors = feature_links("LYR top\nNET GND\nSNT TOP T 0 1\nFID C 0 2\n")
    assert not errors and links[("top", 2)]["pin_index"] == 1


def test_invalid_surface_preserves_next_fid_index():
    text = "U MM\nS P 0\nOB 0 0 I\nOS 1 0\nOE\nSE\n$0 r1000\nP 3 3 0 P 0 8 0\n"
    result = parse_features(text)
    assert result["status"] == "partial"
    assert result["records"][0]["index"] == 0 and result["records"][0]["geometry_wkb"] is None
    assert result["records"][1]["index"] == 1
    assert result["feature_count"] == 2
    incomplete = parse_features("U MM\nF 2\n$0 r1000\nP 0 0 0 P 0 8 0\n")
    assert incomplete["status"] == "partial" and any("图元数" in w for w in incomplete["warnings"])


def test_unix_compress_archive_and_duplicate_mapping(tmp_path):
    text = b"U INCH\n"
    # Short literal-only Unix compress stream; decoder still exercises .Z.
    bits = sum(value << (9*i) for i, value in enumerate(text))
    compressed = b"\x1f\x9d\x09" + bits.to_bytes((len(text)*9+7)//8, "little")
    path = tmp_path/"compress.zip"
    write_zip(path, {"job/features.Z": compressed})
    with InputContainer(path) as container:
        assert container.text(container.find("job/features")) == "U INCH\n"
    write_zip(path, {"job/features": text, "job/features.Z": compressed})
    with InputContainer(path) as container, pytest.raises(ValueError, match="不唯一"):
        container.find("job/features")


def test_real_hole_to_nonconnected_copper_and_unknown_net():
    pad_obj = pad("R1.1", box(.7, 0, 1.7, 1), "VCC")
    via = dict(id="V1", layer="copper_top", net="GND", geometry=box(-.3, 0, .3, 1),
               hole_geometry=box(-.2, .1, .2, .9))
    films = {"copper_top": film("copper_top", via["geometry"].union(pad_obj["geometry"]))}
    result = scan_object_checks(films, [pad_obj], [via], ScanLimits(), dict(hole_to_unconnected_copper_mm=.6))
    assert any(c["field"] == "object_hole_copper_spacing" and c["status"] == "fail" for c in result["checks"])
    pad_obj["net"] = ""
    result = scan_object_checks(films, [pad_obj], [via], ScanLimits(), dict(hole_to_unconnected_copper_mm=.6))
    assert not any(c["field"] == "object_hole_copper_spacing" for c in result["checks"])


def odb_job(path):
    files = {"job/matrix/matrix": ("STEP {\nNAME=PCB\n}\n" + "".join(
        f"LAYER {{\nROW={i}\nCONTEXT=BOARD\nTYPE={kind}\nNAME={name}\nPOLARITY=POSITIVE\n}}\n"
        for i, (kind, name) in enumerate((("COMPONENT", "comp_+_top"), ("SOLDER_PASTE", "spt"),
            ("SOLDER_MASK", "smt"), ("SIGNAL", "top"), ("SIGNAL", "bottom")), 1))).encode(),
        "job/misc/info": b"ODB_VERSION_MAJOR=7\nODB_VERSION_MINOR=0\n",
        "job/steps/pcb/eda/data": b"LYR top\nNET VCC\nSNT TOP T 0 0\nFID C 0 0\nNET GND\nSNT TOP T 1 0\nFID C 0 1\nSNT VIA\nFID C 0 2\nPKG CHIP 0.1 0 0 1 1\n",
        "job/steps/pcb/layers/comp_+_top/components": b"CMP 0 0 0 0 N R1 PART\nTOP 0 0 0 0 N 0 0 1\nCMP 0 0.045 0 0 N R2 PART\nTOP 0 .045 0 0 N 1 0 1\n",
        "job/steps/pcb/layers/top/features": b"U INCH\nF 3\n$0 rect40x40\n$1 r10\n@0 .smd\n@1 .pad_usage\nP 0 0 0 P 0 8 0;0,1=0\nP .045 0 0 P 0 8 0;0,1=0\nP .025 0 1 P 0 8 0;1=1\n",
        "job/steps/pcb/layers/bottom/features": b"U INCH\n$0 r10\nP 0 0 0 P 0 8 0\n",
        "job/steps/pcb/layers/smt/features": b"U INCH\n$0 rect50x50\nP 0 0 0 P 0 8 0\nP .045 0 0 P 0 8 0\n",
        "job/steps/pcb/layers/spt/features": b"U INCH\n$0 rect90x30\nP .0225 0 0 P 0 8 0\n"}
    write_zip(path, files)


def test_native_odb_object_checks_and_existing_report_pipeline(tmp_path):
    path = tmp_path/"board.zip"
    odb_job(path)
    result = analyze_odb(path)
    assert result["object_counts"]["verified_pads"] == 2
    assert result["object_counts"]["vias"] == 1
    assert "object_pad_spacing" in result["rule_counts"]
    assert "object_via_pad_spacing" in result["rule_counts"]
    assert "object_paste_bridge" in result["rule_counts"]
    assert result["selected_step"] == "PCB" and not result["releasable"]
    json.dumps(result, allow_nan=False)
    pcb = attach_odb_result(analyze_design({}), result, "mil")
    assert pcb["problem_locations"] and not any(p.get("command") for p in pcb["problem_locations"])
    assert "问题坐标" in build_dfm_workbook(pcb).sheetnames
    export_report("pcb", tmp_path/"report.xlsx", pcb=pcb)
    path.write_bytes(path.read_bytes()+b"changed")
    with pytest.raises(ValueError, match="已变化"):
        export_report("pcb", tmp_path/"stale.xlsx", pcb=pcb)


def test_fid_identity_mismatch_never_guesses_refdes(tmp_path):
    path = tmp_path/"job.zip"
    odb_job(path)
    import zipfile
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    key = "job/steps/pcb/layers/comp_+_top/components"
    files[key] = files[key].replace(b"TOP 0 .045 0", b"TOP 0 100 100")
    write_zip(path, files)
    result = parse_odb(path, object_checks_only=True)
    top = next(f for f in result["films"] if f["name"] == "top")
    assert not top["records"][1].get("refdes")
    assert any("坐标差" in w for w in result["warnings"])


def test_invalid_component_does_not_shift_source_component_index(tmp_path):
    path = tmp_path/"job.zip"
    odb_job(path)
    import zipfile
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    key = "job/steps/pcb/layers/comp_+_top/components"
    files[key] = files[key].replace(b"CMP 0 0 0 0 N R1 PART", b"CMP invalid")
    write_zip(path, files)
    result = parse_odb(path, object_checks_only=True)
    assert result["component_records"][0]["component_index"] == 1
    top = next(f for f in result["films"] if f["name"] == "top")
    assert top["records"][1]["refdes"] == "R2"
    assert not result["component_complete"]


def test_unsupported_fid_primitive_does_not_crash_coordinate_validation(tmp_path):
    path = tmp_path/"job.zip"
    odb_job(path)
    import zipfile
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    key = "job/steps/pcb/layers/top/features"
    files[key] = files[key].replace(b"rect40x40", b"unsupported_thermal")
    write_zip(path, files)
    result = analyze_odb(path)
    assert result["errors"] and result["object_counts"]["verified_pads"] == 0
    assert not result["releasable"]

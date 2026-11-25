"""强制几何流程、补充数据合同和新增规则的回归。"""
import json
from unittest.mock import patch

import pytest

from src.dfm_engine import analyze_design
from src.dfm_report import build_dfm_workbook
from src.gerber_analysis import analyze_gerbers, attach_gerber_result
from src.pcb_geometry_data import ensure_geometry_folder, geometry_template, load_geometry_data
from src.workbench_report import export_report


def region(points, function):
    lines = ["%FSLAX46Y46*%", "%MOMM*%", f"%TF.FileFunction,{function}*%", "G36*"]
    for i, (x, y) in enumerate([*points, points[0]]):
        lines.append(f"X{round(x*1e6)}Y{round(y*1e6)}D{'02' if i == 0 else '01'}*")
    return "\n".join([*lines, "G37*", "M02*", ""])


def rectangle(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def setup_films(folder):
    (folder/"board.gko").write_text("%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,0.01*%\nD10*\n"
        "X0Y0D02*\nX10000000Y0D01*\nX10000000Y10000000D01*\nX0Y10000000D01*\nX0Y0D01*\nM02*\n")
    for name, function in (("top.gtl", "Copper,L1,Top"), ("bottom.gbl", "Copper,L2,Bot")):
        (folder/name).write_text(region(rectangle(.2, .2, 9.8, 9.8), function))


def metadata(**attrs):
    return dict(plated=False, process="mechanical", start_layer=1, end_layer=2,
                alignment_confirmed=True, **attrs)


def test_no_gerber_always_exports_even_without_ui_checkbox(tmp_path):
    board = tmp_path/"board.brd"
    board.write_bytes(b"board")
    with patch("src.gerber_export.export_brd_gerbers", return_value=dict(directory="new", warnings=False)) as export:
        assert ensure_geometry_folder(board)["directory"] == "new"
        export.assert_called_once()
    with patch("src.gerber_export.export_brd_gerbers", side_effect=ValueError("no license")):
        with pytest.raises(ValueError, match="不会退回仅参数"):
            ensure_geometry_folder(board)


def test_existing_gerber_and_forced_export_and_stale_source(tmp_path):
    board = tmp_path/"board.brd"
    board.write_bytes(b"board")
    folder = tmp_path/"films"
    folder.mkdir()
    with patch("src.gerber_export.export_brd_gerbers", return_value=dict(directory="new", warnings=False)) as export:
        assert ensure_geometry_folder(board, folder)["directory"] == str(folder)
        export.assert_not_called()
        assert ensure_geometry_folder(board, folder, force_export=True)["directory"] == "new"
        export.assert_called_once()
    (folder/"source.json").write_text(json.dumps(dict(brd=str(board), size=1, modified_ns=1)))
    with pytest.raises(ValueError, match="过期"):
        ensure_geometry_folder(board, folder)


def test_data_version_relative_drill_path_and_safe_template(tmp_path):
    board = tmp_path/"board.brd"
    board.write_bytes(b"board")
    directory = tmp_path/"nc"
    directory.mkdir()
    (directory/"holes.drl").write_text("M48\nMETRIC\nT01C0.3\n%\nT01\nX5.0Y5.0\nM30\n")
    template = geometry_template(board, directory)
    assert template["geometry_options"]["drills"]["holes.drl"]["plated"] is None
    assert template["geometry_options"]["pad_geometry"]["alignment_confirmed"] is False
    template["geometry_options"]["drill_directory"] = "nc"
    path = tmp_path/"data.json"
    path.write_text(json.dumps(template))
    assert load_geometry_data(path, board)["drill_directory"] == str(directory)
    board.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256"):
        load_geometry_data(path, board)


def test_real_npth_edges_copper_and_tool_diameter_in_external_directory(tmp_path):
    films, nc = tmp_path/"films", tmp_path/"nc"
    films.mkdir()
    nc.mkdir()
    setup_films(films)
    (nc/"holes.drl").write_text("M48\nMETRIC\nT01C0.1\n%\nT01\nX0.25Y5.0\nM30\n")
    options = dict(drill_directory=str(nc), drills={"holes.drl": metadata()}, limits=dict(npth_to_copper_mm=.2))
    bundle = analyze_gerbers(films, layer_count=2, geometry_options=options)
    for field in ("gerber_npth_edge", "gerber_npth_copper", "gerber_drill_diameter_min"):
        assert any(row["field"] == field and row["status"] == "fail" for row in bundle["checks"])
    options["drills"]["holes.drl"]["alignment_confirmed"] = False
    bundle = analyze_gerbers(films, layer_count=2, geometry_options=options)
    assert not any(row["field"] in ("gerber_npth_edge", "gerber_npth_copper") for row in bundle["checks"])
    assert any(row["rule"] == "npth_to_edge" and row["status"] == "missing" for row in bundle["geometry_coverage"])


def test_different_net_holes_require_per_hole_nets_and_full_span(tmp_path):
    setup_films(tmp_path)
    (tmp_path/"holes.drl").write_text("M48\nMETRIC\nT01C0.3\n%\nT01\nX5.0Y5.0\nX5.5Y5.0\nM30\n")
    meta = metadata(nets={"0": "GND", "1": "VCC"})
    meta["plated"] = True
    options = dict(drills={"holes.drl": meta})
    bundle = analyze_gerbers(tmp_path, layer_count=2, geometry_options=options)
    assert any(row["field"] == "gerber_pth_spacing" and row["status"] == "fail" for row in bundle["checks"])
    meta["nets"]["1"] = "GND"
    bundle = analyze_gerbers(tmp_path, layer_count=2, geometry_options=options)
    assert not any(row["field"] == "gerber_pth_spacing" for row in bundle["checks"])


def pad_options():
    return dict(limits=dict(pad_to_mask_clearance_mm=.1), pad_geometry=dict(unit="mm", coordinate_system="gerber",
        alignment_confirmed=True, complete=True, pads=[
            dict(id="U1.1", layer=1, net="VCC", mask_required=True, outline=rectangle(2, 2, 3, 3)),
            dict(id="U1.2", layer=1, net="GND", mask_required=True, outline=rectangle(4, 2, 5, 3))]))


def test_pad_mask_and_net_contacts_and_reports(tmp_path):
    setup_films(tmp_path)
    (tmp_path/"mask.gts").write_text(region(rectangle(1.95, 1.95, 3.05, 3.05), "Soldermask,Top"))
    options = pad_options()
    bundle = analyze_gerbers(tmp_path, layer_count=2, geometry_options=options)
    assert any(row["field"] == "gerber_pad_mask_clearance" and row["status"] == "fail" for row in bundle["checks"])
    assert any(row["field"] == "gerber_pad_net_contact" and row["status"] == "warn_limit" for row in bundle["checks"])
    result = attach_gerber_result(analyze_design({}), bundle)
    assert result["geometry_executed"] and result["geometry_requirements"]
    assert not result["releasable"]
    assert "几何覆盖与缺数据" in build_dfm_workbook(result).sheetnames
    export_report("pcb", tmp_path/"result.csv", pcb=result)
    assert "几何覆盖与缺数据" in (tmp_path/"result.csv").read_text(encoding="utf-8-sig")
    options["pad_geometry"]["alignment_confirmed"] = False
    bundle = analyze_gerbers(tmp_path, layer_count=2, geometry_options=options)
    assert not any(row["field"].startswith("gerber_pad_") for row in bundle["checks"])
    assert any("逐焊盘资料" in error for error in bundle["errors"])


def test_invalid_data_never_silently_passes(tmp_path):
    setup_films(tmp_path)
    with pytest.raises(ValueError, match="有限"):
        analyze_gerbers(tmp_path, geometry_options=dict(limits=dict(npth_to_copper_mm=float("nan"))))
    options = pad_options()
    options["pad_geometry"]["pads"][0]["outline"] = rectangle(20, 20, 21, 21)
    bundle = analyze_gerbers(tmp_path, layer_count=2, geometry_options=options)
    assert any("轮廓与最终铜箔不一致" in e for e in bundle["errors"])
    assert not any(row.get("padstack") == "U1.1" for row in bundle["checks"])


def test_open_outline_never_authorizes_edge_distance(tmp_path):
    setup_films(tmp_path)
    (tmp_path/"board.gko").write_text("%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,0.01*%\nD10*\n"
        "X0Y0D02*\nX10000000Y0D01*\nM02*\n")
    bundle = analyze_gerbers(tmp_path, layer_count=2)
    assert not any(row["field"] == "gerber_copper_edge" for row in bundle["checks"])
    assert any(row["rule"] == "board_outline" and row["status"] == "missing" for row in bundle["geometry_coverage"])


def test_missing_metadata_file_and_relative_manifest_directory(tmp_path):
    setup_films(tmp_path)
    nc = tmp_path/"nc"
    nc.mkdir()
    (nc/"holes.drl").write_text("M48\nMETRIC\nT01C0.1\n%\nT01\nX0.25Y5.0\nM30\n")
    (tmp_path/"dfm_geometry.json").write_text(json.dumps(dict(drill_directory="nc", drills={
        "holes.drl": metadata(), "missing.drl": metadata()})))
    bundle = analyze_gerbers(tmp_path, layer_count=2)
    assert any(row["field"] == "gerber_drill_diameter_min" for row in bundle["checks"])
    assert any("missing.drl" in e and "不存在" in e for e in bundle["errors"])


def test_pad_budget_zero_clearance_and_invalid_net_index(tmp_path):
    setup_films(tmp_path)
    options = pad_options()
    options["scan_limits"] = dict(max_candidates=1)
    bundle = analyze_gerbers(tmp_path, layer_count=2, geometry_options=options)
    assert any("焊盘网络连通候选超过" in e for e in bundle["errors"])
    options = pad_options()
    options["limits"]["pad_to_mask_clearance_mm"] = 0
    (tmp_path/"mask.gts").write_text(region(rectangle(1, 1, 6, 6), "Soldermask,Top"))
    bundle = analyze_gerbers(tmp_path, layer_count=2, geometry_options=options)
    assert not any(row["field"] in ("gerber_pad_mask_clearance", "gerber_pad_mask_covered") for row in bundle["checks"])
    (tmp_path/"holes.drl").write_text("M48\nMETRIC\nT01C0.3\n%\nT01\nX5.0Y5.0\nM30\n")
    bundle = analyze_gerbers(tmp_path, layer_count=2, geometry_options=dict(drills={
        "holes.drl": metadata(nets={"3": "GND"})}))
    assert any("nets 孔索引超出" in e for e in bundle["errors"])

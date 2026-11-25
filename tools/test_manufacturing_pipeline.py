"""Native orchestration regression: isolation, origins, hashes, partial failures, merge."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.manufacturing_export import export_brd_manufacturing, native_drill_metadata, verify_export_manifest
from src.manufacturing_pipeline import analyze_brd_manufacturing
from src.layout_drill import read_drill
from src.workbench_report import export_report
from src.dfm_report import build_dfm_workbook
from tools.test_pcb_object_checks import odb_job
from tools.test_pcb_geometry_checks import setup_films, region, rectangle


def native_fixture(tmp_path):
    board = tmp_path/"中文 board.brd"
    board.write_bytes(b"source-board")
    exe = tmp_path/"allegro.exe"
    exe.write_bytes(b"fixture")
    for name in ("nctape.exe", "ncroute.exe", "brd2odb.exe"):
        (tmp_path/name).write_bytes(b"fixture")
    return board, exe


def fake_gerber(board, output, editor_path, timeout):
    folder = Path(output)/"films"
    folder.mkdir()
    setup_films(folder)
    for name, function in (("mask.gts", "Soldermask,Top"), ("mask.gbs", "Soldermask,Bot"),
                           ("paste.gtp", "Paste,Top"), ("paste.gbp", "Paste,Bot")):
        (folder/name).write_text(region(rectangle(.1, .1, 9.9, 9.9), function))
    return dict(directory=str(folder), files=[str(p) for p in folder.iterdir()], warnings=False, log="native artwork")


def fake_run(command, work, timeout, environment=None):
    assert Path(command[-1]).read_bytes() == b"source-board"
    assert Path(command[-1]).name == "board.brd"
    name = Path(command[0]).name
    if name == "nctape.exe":
        assert environment["NCDPATH"] == str(work)
        assert "X-OFFSET 0.000000" in (work/"nc_param.txt").read_text()
        (work/"drill-1-2.drl").write_text("M48\nMETRIC\nT02C.3\n"
            ";T02 Holesize 1. = 0.300000 Tolerance = +0/-0 PLATED MM Quantity = 1\n%\nG90\nT02\nX00500000Y00500000\nM30\n")
        (work/"ncdrill.log").write_text("FORMAT 3.5\nOUTPUT-UNITS METRIC\nENHANCED_EXCELLON YES\nMACHINE-OFFSET x:0.00000 y:0.00000\n")
    elif name == "ncroute.exe":
        (work/"route.rou").write_text("M48\nMETRIC\nT01C.3\n%\nG90\nT01\nX5.0Y5.0G85X6.0Y5.0\nM30\n")
        (work/"ncroute.log").write_text("FORMAT 3.5\nOUTPUT-UNITS METRIC\nENHANCED_EXCELLON YES\nMACHINE-OFFSET x:0.00000 y:0.00000\n")
    elif name == "brd2odb.exe":
        assert "-no_view" in command and "-sp" not in command and "-nn" not in command
        odb_job(work/"board_odb.zip")
    return 0, "native process complete"


def test_native_exports_use_isolated_copies_and_hashes(tmp_path):
    board, exe = native_fixture(tmp_path)
    with patch("src.gerber_export.export_brd_gerbers", side_effect=fake_gerber), \
            patch("src.manufacturing_export._run", side_effect=fake_run):
        manifest = export_brd_manufacturing(board, tmp_path/"out", str(exe), str(tmp_path/"brd2odb.exe"))
    assert all(s["status"] == "exported" for s in manifest["stages"].values())
    assert board.read_bytes() == b"source-board"
    verify_export_manifest(manifest)
    marker = json.loads((Path(manifest["stages"]["gerber"]["directory"])/"source.json").read_text(encoding="utf-8"))
    assert marker["brd"] == str(board) and marker["brd_sha256"] == manifest["brd_sha256"]
    metadata = native_drill_metadata(manifest, False)
    assert metadata["drill-1-2.drl"]["start_layer"] == 1
    assert metadata["drill-1-2.drl"]["process"] == "mechanical"
    assert metadata["drill-1-2.drl"]["alignment_confirmed"] is False
    hole = read_drill(manifest["stages"]["drill"]["files"][0], metadata["drill-1-2.drl"])["holes"][0]
    assert hole["diameter_mm"] == .3 and hole["geometry"].centroid.x == pytest.approx(5)
    artifact = Path(manifest["artifacts"][0]["path"])
    artifact.write_bytes(artifact.read_bytes()+b"changed")
    with pytest.raises(ValueError, match="产物内容变化"):
        verify_export_manifest(manifest)


def test_no_output_and_missing_odb_are_not_exports(tmp_path):
    board, exe = native_fixture(tmp_path)
    with patch("src.gerber_export.export_brd_gerbers", side_effect=fake_gerber), \
            patch("src.manufacturing_export.discover_odb_exporter", return_value=""), \
            patch("src.manufacturing_export._run", return_value=(0, "no output")):
        manifest = export_brd_manufacturing(board, tmp_path/"out", str(exe))
    assert manifest["stages"]["odb"]["status"] == "unavailable"
    assert manifest["stages"]["drill"]["status"] == "no_output"
    assert manifest["stages"]["route"]["status"] == "no_output"
    assert not manifest["stages"]["odb"]["files"]


def test_native_cadence_comment_mismatch_rejected(tmp_path):
    file = tmp_path/"drill.drl"
    file.write_text("M48\nMETRIC\nT02C.3\n;T02 Holesize 1. = 0.4 PLATED MM\n%\nT02\nX00500000Y00500000\nM30\n")
    with pytest.raises(ValueError, match="尺寸不一致"):
        read_drill(file, dict(number_format=[3, 5], native_cadence=True))


def test_combined_pipeline_keeps_both_coverage_drills_and_reports(tmp_path):
    board, exe = native_fixture(tmp_path)
    with patch("src.gerber_export.export_brd_gerbers", side_effect=fake_gerber), \
            patch("src.manufacturing_export._run", side_effect=fake_run):
        result = analyze_brd_manufacturing(board, dict(layer_count=2, copper_um=35),
                editor_path=str(exe), odb_exporter=str(tmp_path/"brd2odb.exe"), out_dir=str(tmp_path/"out"))
    assert result["odb_object_result"]["object_counts"]["verified_pads"] == 2
    assert any(r["rule"] == "drill_geometry" for r in result["geometry_coverage"])
    assert any(r["rule"] == "object_pad_spacing" for r in result["geometry_coverage"])
    assert result["copper_density"]
    assert any(c["field"] == "object_pad_spacing" for c in result["problem_locations"])
    assert any(r["role"] == "drill" for r in result["gerber_layers"])
    assert Path(result["gerber_preview"]).is_file() and Path(result["odb_preview"]).is_file()
    assert not result["releasable"]
    assert "原生导出与校验" in build_dfm_workbook(result).sheetnames
    export_report("pcb", tmp_path/"report.xlsx", pcb=result)
    assert "原生制造导出清单" in (tmp_path/"report.txt").read_text(encoding="utf-8")
    export_report("pcb", tmp_path/"report.csv", pcb=result)
    assert "原生导出与校验" in (tmp_path/"report.csv").read_text(encoding="utf-8-sig")
    board.write_bytes(b"new-version")
    with pytest.raises(ValueError, match="不一致"):
        export_report("pcb", tmp_path/"stale.xlsx", pcb=result)


def test_missing_odb_preserves_gerber_analysis_but_blocks_release(tmp_path):
    board, exe = native_fixture(tmp_path)
    with patch("src.gerber_export.export_brd_gerbers", side_effect=fake_gerber), \
            patch("src.manufacturing_export._run", side_effect=fake_run), \
            patch("src.manufacturing_export.discover_odb_exporter", return_value=""):
        result = analyze_brd_manufacturing(board, dict(layer_count=2, copper_um=35),
                                          editor_path=str(exe), out_dir=str(tmp_path/"out"))
    assert result["geometry_executed"] and not result["releasable"]
    assert any(r["rule"] == "native_export_odb" and r["status"] == "incomplete" for r in result["geometry_coverage"])


def test_all_sources_unavailable_never_returns_parameter_only(tmp_path):
    board, _ = native_fixture(tmp_path)
    with patch("src.gerber_export.export_brd_gerbers", side_effect=ValueError("license missing")), \
            patch("src.manufacturing_export.discover_editor", return_value=""), \
            patch("src.manufacturing_export.discover_odb_exporter", return_value=""):
        with pytest.raises(ValueError, match="未返回仅参数"):
            analyze_brd_manufacturing(board, dict(copper_um=35), out_dir=str(tmp_path/"out"))


def test_drill_still_analyzed_when_artwork_and_odb_unavailable(tmp_path):
    board, exe = native_fixture(tmp_path)
    with patch("src.gerber_export.export_brd_gerbers", side_effect=ValueError("no artwork")), \
            patch("src.manufacturing_export._run", side_effect=fake_run), \
            patch("src.manufacturing_export.discover_odb_exporter", return_value=""):
        result = analyze_brd_manufacturing(board, dict(layer_count=2, copper_um=35),
                                          editor_path=str(exe), out_dir=str(tmp_path/"out"))
    assert any(r["role"] == "drill" for r in result["gerber_layers"])
    assert not result["releasable"]
    assert result["manufacturing_export"]["stages"]["gerber"]["status"] == "unavailable"


def test_nc_wrong_native_format_is_rejected(tmp_path):
    board, exe = native_fixture(tmp_path)
    def wrong(command, work, timeout, environment=None):
        result = fake_run(command, work, timeout, environment)
        if Path(command[0]).name == "nctape.exe":
            (work/"ncdrill.log").write_text("FORMAT 2.3\nOUTPUT-UNITS ENGLISH\n")
        return result
    with patch("src.gerber_export.export_brd_gerbers", side_effect=fake_gerber), \
            patch("src.manufacturing_export._run", side_effect=wrong):
        manifest = export_brd_manufacturing(board, tmp_path/"out", str(exe), str(tmp_path/"brd2odb.exe"))
    assert manifest["stages"]["drill"]["status"] == "unavailable"
    assert "拒绝猜测" in manifest["stages"]["drill"]["error"]


def test_source_change_during_export_discards_snapshot(tmp_path):
    board, exe = native_fixture(tmp_path)
    def changed(*args):
        result = fake_gerber(*args)
        board.write_bytes(b"edited while exporting")
        return result
    with patch("src.gerber_export.export_brd_gerbers", side_effect=changed), pytest.raises(ValueError, match="源 BRD"):
        export_brd_manufacturing(board, tmp_path/"out", str(exe))


def test_timeout_and_invalid_odb_outputs_never_pass(tmp_path):
    board, exe = native_fixture(tmp_path)
    def bad(command, work, timeout, environment=None):
        if Path(command[0]).name == "nctape.exe":
            raise TimeoutError("native timeout")
        if Path(command[0]).name == "brd2odb.exe":
            (work/"fake.tgz").write_bytes(b"not odb")
            return 0, "false success"
        return fake_run(command, work, timeout, environment)
    with patch("src.gerber_export.export_brd_gerbers", side_effect=fake_gerber), \
            patch("src.manufacturing_export._run", side_effect=bad):
        manifest = export_brd_manufacturing(board, tmp_path/"out", str(exe), str(tmp_path/"brd2odb.exe"))
    assert manifest["stages"]["drill"]["status"] == "timeout"
    assert manifest["stages"]["odb"]["status"] == "unavailable"
    assert not manifest["stages"]["odb"]["files"]


def test_route_analyzed_even_without_round_drill_output(tmp_path):
    board, exe = native_fixture(tmp_path)
    def no_drill(command, work, timeout, environment=None):
        if Path(command[0]).name == "nctape.exe":
            return 0, "no circular holes"
        return fake_run(command, work, timeout, environment)
    with patch("src.gerber_export.export_brd_gerbers", side_effect=fake_gerber), \
            patch("src.manufacturing_export._run", side_effect=no_drill), \
            patch("src.manufacturing_export.discover_odb_exporter", return_value=""):
        result = analyze_brd_manufacturing(board, dict(layer_count=2, copper_um=35),
                                          editor_path=str(exe), out_dir=str(tmp_path/"out"))
    assert any(r["role"] == "drill" and r["path"].endswith(".rou") for r in result["gerber_layers"])
    assert not result["releasable"]


def test_discover_odb_environment(tmp_path):
    from src.manufacturing_export import discover_odb_exporter
    root = tmp_path/"ODB Inside"
    root.mkdir()
    exe = root/"brd2odb.exe"
    exe.write_bytes(b"vendor fixture")
    with patch.dict("os.environ", {"ALLEGRO_BRD2ODB": str(root)}), patch("shutil.which", return_value=None):
        assert discover_odb_exporter() == str(exe)
    assert discover_odb_exporter(str(root)) == str(exe)
    assert not discover_odb_exporter(str(tmp_path/"missing.exe"))

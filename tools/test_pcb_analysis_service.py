"""应用服务的输入快照、全分支版本检查与失败边界。"""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from src.brd_input import brd_signature
from src.dfm_engine import capability_fingerprint
from src.services.pcb_analysis import PCBAnalysisRequest, run_pcb_analysis


def request_for(tmp_path, **options):
    board = tmp_path / "board.brd"
    board.write_bytes(b"source-board")
    design = {"copper_um": 35, "_brd_signature": brd_signature(str(board)), "nested": {"value": 1}}
    return PCBAnalysisRequest(str(board), design, extract_native=False, **options)


def result():
    return {"items": [], "release_blockers": ["资料不完整"],
            "rule_sha256": capability_fingerprint("hongban"), "geometry_complete": False}


@pytest.mark.parametrize("native", [True, False])
def test_source_change_before_analysis_rejected(tmp_path, native):
    request = request_for(tmp_path, auto_manufacturing=native)
    from pathlib import Path
    Path(request.brd_path).write_bytes(b"changed-board-version")
    with patch("src.manufacturing_pipeline.analyze_brd_manufacturing") as engine:
        with pytest.raises(ValueError, match="已变化"):
            run_pcb_analysis(request)
        engine.assert_not_called()


def test_native_worker_cannot_mutate_callers_snapshot(tmp_path):
    request = request_for(tmp_path)
    original = deepcopy(request.design)
    def compute(board, design, **kwargs):
        design["nested"]["value"] = 2
        kwargs["progress"]("gerber")
        return result()
    stages = []
    with patch("src.manufacturing_pipeline.analyze_brd_manufacturing", side_effect=compute):
        analyzed = run_pcb_analysis(request, progress=stages.append)
    assert request.design == original
    assert stages == ["gerber"]
    assert analyzed["analysis_mode"] == "native"
    assert analyzed["verdict_code"] == "review"
    assert not analyzed["releasable"]


@pytest.mark.parametrize("native", [True, False])
def test_rule_changes_are_rejected_for_every_branch(tmp_path, native):
    request = request_for(tmp_path, auto_manufacturing=native, odb_path="external.zip")
    with patch("src.services.pcb_analysis.capability_fingerprint", side_effect=["before", "after"]), \
            patch("src.manufacturing_pipeline.analyze_brd_manufacturing", return_value=result()), \
            patch("src.services.pcb_analysis._analyze_external", return_value=result()):
        with pytest.raises(ValueError, match="能力 YAML"):
            run_pcb_analysis(request)


@pytest.mark.parametrize("native", [True, False])
def test_source_changes_during_every_branch_discard_result(tmp_path, native):
    request = request_for(tmp_path, auto_manufacturing=native)
    def compute(*args, **kwargs):
        from pathlib import Path
        Path(request.brd_path).write_bytes(b"changed-during-analysis")
        return result()
    target = ("src.manufacturing_pipeline.analyze_brd_manufacturing" if native
              else "src.services.pcb_analysis._analyze_external")
    with patch(target, side_effect=compute), pytest.raises(ValueError, match="已变化"):
        run_pcb_analysis(request)


def test_external_geometry_failure_never_runs_parameter_only(tmp_path):
    request = request_for(tmp_path, auto_manufacturing=False)
    with patch("src.pcb_geometry_data.ensure_geometry_folder", side_effect=ValueError("缺制造资料")), \
            patch("src.services.pcb_analysis.analyze_design") as parameter:
        with pytest.raises(ValueError, match="缺制造资料"):
            run_pcb_analysis(request)
        parameter.assert_not_called()


@pytest.mark.parametrize("native", [True, False])
def test_imported_stackup_change_before_analysis_is_rejected(tmp_path, native):
    from src.infrastructure.snapshots import capture_sources
    stack = tmp_path / "stackup.json"
    stack.write_text("old")
    request = request_for(tmp_path, auto_manufacturing=native,
                          stackup_sources=tuple(capture_sources([stack])))
    stack.write_text("new")
    with patch("src.manufacturing_pipeline.analyze_brd_manufacturing") as engine, \
            pytest.raises(ValueError, match="内容已变化"):
        run_pcb_analysis(request)
    engine.assert_not_called()


@pytest.mark.parametrize("native", [True, False])
def test_stackup_changes_during_analysis_discard_result(tmp_path, native):
    from src.infrastructure.snapshots import capture_sources
    stack = tmp_path / "stackup.json"
    stack.write_text("old")
    request = request_for(tmp_path, auto_manufacturing=native,
                          stackup_sources=tuple(capture_sources([stack])))
    def compute(*args, **kwargs):
        stack.write_text("new")
        return result()
    target = "src.manufacturing_pipeline.analyze_brd_manufacturing" if native else "src.services.pcb_analysis._analyze_external"
    with patch(target, side_effect=compute), pytest.raises(ValueError, match="内容已变化"):
        run_pcb_analysis(request)


def test_stackup_snapshot_is_retained_for_report_version_check(tmp_path):
    from src.infrastructure.snapshots import capture_sources
    from src.services.pcb_analysis import verify_analysis_sources
    stack = tmp_path / "stackup.json"
    stack.write_text("old")
    request = request_for(tmp_path, stackup_sources=tuple(capture_sources([stack])))
    with patch("src.manufacturing_pipeline.analyze_brd_manufacturing", return_value=result()):
        analyzed = run_pcb_analysis(request)
    assert request.stackup_sources[0] in analyzed["input_fingerprints"]
    stack.write_text("new")
    with pytest.raises(ValueError, match="资料已变化"):
        verify_analysis_sources(analyzed)


@pytest.mark.parametrize("source", ["geometry_json", "local_manifest"])
def test_external_drill_directory_change_discards_result(tmp_path, source):
    import json
    from src.pcb_geometry_data import file_sha256
    request = request_for(tmp_path, auto_manufacturing=False, auto_gerber=False)
    board = Path(request.brd_path)
    gerber, drill = tmp_path / "gerber", tmp_path / "drill"
    gerber.mkdir()
    drill.mkdir()
    (gerber / "top.gtl").write_text("film")
    hole = drill / "holes.drl"
    hole.write_text("old")
    if source == "geometry_json":
        geometry = tmp_path / "geometry.json"
        geometry.write_text(json.dumps({"schema_version": 1, "brd_sha256": file_sha256(board),
            "geometry_options": {"drill_directory": str(drill)}}))
        request = PCBAnalysisRequest(request.brd_path, request.design, auto_manufacturing=False,
                                     auto_gerber=False, gerber_dir=str(gerber), geometry_data=str(geometry))
    else:
        (gerber / "dfm_geometry.json").write_text(json.dumps({"drill_directory": "../drill"}))
        request = PCBAnalysisRequest(request.brd_path, request.design, auto_manufacturing=False,
                                     auto_gerber=False, gerber_dir=str(gerber))

    def compute(*_args):
        hole.write_text("new")
        return result()

    with patch("src.services.pcb_analysis._analyze_external", side_effect=compute), \
            pytest.raises(ValueError, match="资料在分析期间变化"):
        run_pcb_analysis(request)


def test_geometry_file_changed_while_reading_is_rejected(tmp_path):
    import json
    from src.pcb_geometry_data import file_sha256, load_geometry_data
    request = request_for(tmp_path, auto_manufacturing=False)
    geometry = tmp_path / "geometry.json"
    data = {"schema_version": 1, "brd_sha256": file_sha256(request.brd_path), "geometry_options": {}}
    geometry.write_text(json.dumps(data))
    request = PCBAnalysisRequest(request.brd_path, request.design, auto_manufacturing=False,
                                 geometry_data=str(geometry))
    def changed(path, board):
        options = load_geometry_data(path, board)
        data["geometry_options"]["note"] = "changed"
        geometry.write_text(json.dumps(data))
        return options
    with patch("src.pcb_geometry_data.load_geometry_data", side_effect=changed), \
            pytest.raises(ValueError, match="补充资料在读取期间变化"):
        run_pcb_analysis(request)


def test_auto_exported_gerber_change_during_geometry_analysis_is_rejected(tmp_path):
    from src.services.pcb_analysis import _analyze_external
    request = request_for(tmp_path, auto_manufacturing=False, auto_gerber=True)
    folder = tmp_path / "generated"
    folder.mkdir()
    film = folder / "top.gtl"
    film.write_text("before")
    def change(*_args, **_kwargs):
        film.write_text("after")
        return {}
    with patch("src.pcb_geometry_data.ensure_geometry_folder", return_value={"directory": str(folder),
               "warnings": False, "source": "test"}), \
         patch("src.services.pcb_analysis.analyze_design", return_value={}), \
         patch("src.pcb_locations.attach_locations", side_effect=lambda result, *args, **kwargs: result), \
         patch("src.gerber_analysis.analyze_gerbers", side_effect=change), \
         pytest.raises(ValueError, match="Gerber 在分析期间变化"):
        _analyze_external(request, request.design, {}, None)


def test_auto_exported_gerber_external_drill_change_is_rejected(tmp_path):
    import json
    from src.services.pcb_analysis import _analyze_external
    request = request_for(tmp_path, auto_manufacturing=False, auto_gerber=True)
    folder = tmp_path / "generated"
    folder.mkdir()
    (folder / "top.gtl").write_text("film")
    drill = tmp_path / "drills"
    drill.mkdir()
    hole = drill / "holes.drl"
    hole.write_text("before")
    (folder / "dfm_geometry.json").write_text(json.dumps({"drill_directory": "../drills"}))
    def change(*_args, **_kwargs):
        hole.write_text("after")
        return {}
    with patch("src.pcb_geometry_data.ensure_geometry_folder", return_value={"directory": str(folder),
               "warnings": False, "source": "test"}), \
         patch("src.services.pcb_analysis.analyze_design", return_value={}), \
         patch("src.pcb_locations.attach_locations", side_effect=lambda result, *args, **kwargs: result), \
         patch("src.gerber_analysis.analyze_gerbers", side_effect=change), \
         pytest.raises(ValueError, match="Gerber 在分析期间变化"):
        _analyze_external(request, request.design, {}, None)

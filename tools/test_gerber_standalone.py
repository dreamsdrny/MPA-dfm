"""Gerber-only service and real Tk workflow regressions, no BRD or CAD required."""
import tarfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.adapters.gerber_analysis import identify_layer
from src.application.gerber_analysis import (
    GerberAnalysisRequest,
    import_gerber,
    run_gerber_analysis,
)
from src.application.pcb_workflow import verify_analysis_sources
from src.application.reports import export_report

HEADER = "%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,0.1*%\nD10*\n"


def films(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for ext in ("gtl", "gbl"):
        (folder / f"board.{ext}").write_text(HEADER + "X1000000Y2000000D02*\nX4000000Y2000000D01*\nM02*\n")
    for ext in ("gts", "gbs"):
        (folder / f"board.{ext}").write_text(HEADER.replace("0.1", "0.5") + "X1500000Y1000000D03*\nM02*\n")
    (folder / "board.gto").write_text(HEADER + "X1000000Y1000000D02*\nX2000000Y1000000D01*\nM02*\n")
    (folder / "board.gko").write_text(HEADER + "X0Y0D02*\nX5000000Y0D01*\nX5000000Y5000000D01*\nX0Y5000000D01*\nX0Y0D01*\nM02*\n")


def analyze(path):
    info = import_gerber(str(path))
    return run_gerber_analysis(GerberAnalysisRequest(str(path), tuple(info["input_fingerprints"]),
                                                   design={"layer_count": 2, "copper_um": 35}))


@pytest.mark.parametrize("kind", ["folder", "zip", "tgz", "single"])
def test_import_analyze_preview_and_report_without_brd(tmp_path, kind):
    folder = tmp_path / "films"
    films(folder)
    source = folder
    if kind in ("zip", "tgz"):
        source = tmp_path / ("底片包." + kind)
        if kind == "zip":
            with zipfile.ZipFile(source, "w") as archive:
                for p in folder.iterdir():
                    archive.write(p, "nested/" + p.name)
        else:
            with tarfile.open(source, "w:gz") as archive:
                for p in folder.iterdir():
                    archive.add(p, arcname="nested/" + p.name)
    elif kind == "single":
        source = folder / "board.gto"
    with patch("src.adapters.allegro_bridge.discover_editor", side_effect=AssertionError("CAD should not be called")):
        result = analyze(source)
    assert result["analysis_mode"] == "gerber_only"
    assert result["geometry_executed"] and not result["releasable"]
    assert "input_brd_sha256" not in result
    assert len(result["gerber_layers"]) == (1 if kind == "single" else 6)
    assert result["problem_locations"]
    assert all(not row.get("command") for row in result["problem_locations"])
    assert all("evan-gerber-input-" not in row.get("source", "") for row in result["problem_locations"])
    assert "<svg" in Path(result["gerber_preview"]).read_text(encoding="utf-8")
    verify_analysis_sources(result)
    report = tmp_path / f"{kind}.xlsx"
    export_report("pcb", str(report), result, board_meta={"gerber_path": str(source)})
    from openpyxl import load_workbook
    workbook = load_workbook(report, read_only=True)
    assert workbook.sheetnames
    assert "BRD器件坐标" not in workbook.sheetnames
    workbook.close()


def test_changed_input_and_missing_import_rejected(tmp_path):
    films(tmp_path / "films")
    path = tmp_path / "films" / "board.gto"
    info = import_gerber(str(path))
    path.write_text(path.read_text() + "G04 changed*\n")
    with pytest.raises(ValueError, match="内容已变化"):
        run_gerber_analysis(GerberAnalysisRequest(str(path), tuple(info["input_fingerprints"])))
    with pytest.raises(ValueError, match="先成功导入"):
        run_gerber_analysis(GerberAnalysisRequest(str(path)))


def test_changed_source_blocks_export(tmp_path):
    films(tmp_path / "films")
    result = analyze(tmp_path / "films")
    (tmp_path / "films" / "board.gto").write_text("changed")
    with pytest.raises(ValueError, match="内容已变化"):
        export_report("pcb", str(tmp_path / "blocked.xlsx"), result)
    assert not (tmp_path / "blocked.xlsx").exists()


def test_report_cannot_modify_input_and_stackup_changes_rejected(tmp_path):
    import json

    films(tmp_path / "films")
    folder = tmp_path / "films"
    result = analyze(folder)
    with pytest.raises(ValueError, match="不能覆盖"):
        export_report("pcb", str(folder / "report.xlsx"), result)
    info = import_gerber(str(folder))
    stack = tmp_path / "stackup.json"
    stack.write_text(json.dumps({"layer_count": 2, "stackup": [{"index": 1, "copper_um": 35},
                                                            {"index": 2, "copper_um": 35}]}))
    from src.application.pcb_inputs import import_stackup
    imported = import_stackup(str(stack), "2", "35")
    stack.write_text("changed")
    request = GerberAnalysisRequest(str(folder), tuple(info["input_fingerprints"]),
                {"stackup": imported["rows"]}, stackup_sources=tuple(imported["input_fingerprints"]))
    with pytest.raises(ValueError, match="内容已变化"):
        run_gerber_analysis(request)


def test_archive_path_traversal_and_invalid_input(tmp_path):
    with pytest.raises(ValueError, match="路径不能为空"):
        import_gerber("")
    source = tmp_path / "bad.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("../escape.gtl", HEADER)
    with pytest.raises(ValueError, match="非法路径"):
        import_gerber(str(source))
    source = tmp_path / "bad.gbr"
    source.write_text("not a gerber")
    with pytest.raises(ValueError):
        import_gerber(str(source))


def test_x2_generic_names_and_additional_extensions(tmp_path):
    assert identify_layer("generic.gbr", "%TF.FileFunction,Profile,NP*%") == "outline"
    assert identify_layer("generic.gbr", "%TF.FileFunction,Legend,Bot*%") == "silkscreen_bottom"
    assert identify_layer("board-B_Paste.gbr", "") == "paste_bottom"
    assert identify_layer("board-B_SilkS.gbr", "") == "silkscreen_bottom"
    assert identify_layer("board-Edge_Cuts.gbr", "") == "outline"
    assert identify_layer("board_ETCH_L3_SIG.gbr", "") == "copper_L3"
    assert identify_layer("DEMO_BOARD_ETCH_TOP.gbr", "") == "copper_top"
    assert identify_layer("DEMO_BOARD_SILKSCREEN_TOP.gbr", "") == "silkscreen_top"
    assert identify_layer("DEMO_BOARD_SOLDERMASK_TOP.gbr", "") == "mask_top"
    assert identify_layer("PMASK_TOP.art", "") == "paste_top"
    for name in ("top.pho", "top.gerber", "top.grb", "top.gbx", "top.txt"):
        source = tmp_path / name
        source.write_text("%TF.FileFunction,Copper,L1,Top*%\n" + HEADER + "X0Y0D03*\nM02*\n")
        assert import_gerber(str(source))["layers"][0]["role"] == "copper_top"


def test_standalone_real_ui_import_run_export_and_invalidation(tmp_path):
    import tkinter as tk

    from src.ui.dfm_dialog import DFMDialog
    films(tmp_path / "films")
    root = tk.Tk()
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    with patch("src.ui.prefs.load_prefs", return_value={}), patch("src.ui.prefs.save_prefs"):
        dialog = DFMDialog(root, standalone=True)
    def wait():
        deadline = time.monotonic() + 15
        while dialog.tasks.pending and time.monotonic() < deadline:
            root.update()
            time.sleep(.005)
        root.update()
        assert not dialog.tasks.pending
        assert not errors, errors
    try:
        assert dialog._gerber_active()
        assert not dialog.advanced_frame.winfo_manager()
        assert str(dialog.run_button.cget("state")) == "disabled"
        panel = dialog.gerber_panel
        panel.load(str(tmp_path / "films"))
        wait()
        assert len(panel.layers.get_children()) == 6
        assert str(dialog.run_button.cget("state")) == "normal"
        assert str(dialog.smt.run_button.cget("state")) == "disabled"
        panel.layer_count.set("2")
        dialog._run()
        wait()
        assert panel.last_result["geometry_executed"]
        assert str(dialog.report_buttons["pcb"].cget("state")) == "normal"
        with patch("src.ui.report_dialog.choose_report", return_value=("pcb",str(tmp_path / "ui.xlsx"))), \
                patch("src.ui.gerber_panel.messagebox.showinfo"):
            dialog._export("pcb")
            wait()
        assert (tmp_path / "ui.xlsx").is_file()
        panel.copper.set("70")
        assert panel.last_result is None
        assert str(dialog.report_buttons["pcb"].cget("state")) == "disabled"
        panel.source.set(str(tmp_path / "films" / "board.gto"))
        assert panel.info is None
        assert str(dialog.run_button.cget("state")) == "disabled"
        dialog._toggle_advanced()
        assert dialog.advanced_frame.winfo_manager()
        dialog._toggle_advanced()
        assert dialog._gerber_active()
        with patch("src.ui.prefs.save_prefs"):
            dialog.language.set("English")
            dialog._apply_language()
            dialog._refresh_action_states()
            assert str(dialog.run_button.cget("text")) == "Analyze all · PCB + SMT"
            dialog.language.set("中文")
            dialog._apply_language()
            dialog._refresh_action_states()
            assert str(dialog.run_button.cget("text")) == "整体分析 · PCB + SMT"
    finally:
        dialog._finish_close()

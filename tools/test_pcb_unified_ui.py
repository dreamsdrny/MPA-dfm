"""One input/parameter/result GUI for BRD and Gerber; routing is source-bound."""
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.test_gerber_standalone import films


@pytest.fixture(scope="module")
def dialog():
    import tkinter as tk
    from src.ui.dfm_dialog import DFMDialog
    root = tk.Tk()
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))
    with patch("src.ui.prefs.load_prefs", return_value={}), patch("src.ui.prefs.save_prefs"):
        ui = DFMDialog(root, standalone=True)
        yield ui, root, errors
        ui._finish_close()


def wait(ui, root, errors):
    end = time.monotonic() + 20
    while (ui.tasks.pending or ui._loading) and time.monotonic() < end:
        root.update()
        time.sleep(.01)
    root.update()
    assert not ui.tasks.pending and not ui._loading
    assert not errors, errors


def test_one_pcb_tab_shared_parameters_and_results(dialog):
    ui, root, errors = dialog
    names = [ui.workbench.tab(tab, "text") for tab in ui.workbench.tabs()]
    assert len(names) == 3
    assert names[0] == "PCB 分析 · BRD / Gerber"
    assert ui.gerber_panel.results is ui.results
    assert ui.gerber_panel.factory is ui.params.factory
    assert ui.gerber_panel.copper is ui.params.copper_um
    assert not ui.gerber_panel.winfo_manager()  # No second visual workflow.
    assert ui.params.design_frame.winfo_manager()


def test_gerber_import_analysis_tab_switch_and_invalidation(dialog, tmp_path):
    ui, root, errors = dialog
    source = tmp_path / "films"
    films(source)
    ui._load_gerber_source(str(source))
    wait(ui, root, errors)
    ui.entries["layer_count"].set("2")
    ui.params.board_type.set("flex")
    ui.workbench.select(ui.smt)
    assert ui._gerber_active()  # Switching tabs cannot switch source semantics.
    ui._run()
    wait(ui, root, errors)
    assert ui.last_result is ui.gerber_panel.last_result
    assert ui.last_result["analysis_mode"] == "gerber_only"
    assert ui.last_result["board_type"] == "flex"
    assert ui._pcb_state == "complete"
    ui.params.copper_um.set("70")
    assert ui.last_result is None and ui.gerber_panel.last_result is None
    assert str(ui.report_buttons["pcb"].cget("state")) == "disabled"


def test_brd_and_gerber_are_kept_in_same_input_area(dialog, tmp_path):
    ui, root, errors = dialog
    board = Path(__file__).resolve().parents[1] / "demo" / "DEMO_BOARD.brd"
    source = tmp_path / "films"
    films(source)
    ui.brd_path.set(str(board))
    ui._load_from_brd()
    wait(ui, root, errors)
    ui._load_gerber_source(str(source))
    assert ui.brd_info
    assert ui.gerber_dir.get() == str(source)
    assert ui.pcb_gerber_source.get() == str(source)
    assert not ui.auto_manufacturing.get() and not ui.auto_gerber.get()
    ui.workbench.select(ui.impedance_panel)
    assert not ui._gerber_active()
    ui._clear_pcb_inputs()
    assert not ui.brd_info and not ui.gerber_panel.info
    assert not ui.brd_path.get() and not ui.gerber_dir.get()


def test_pcb_exe_diagnostic_checks_license_before_gui(tmp_path):
    from evan_dfm import main
    output = tmp_path / "blocked.json"
    with patch("src.bootstrap.prepare_startup", side_effect=ValueError("授权无效")), \
            patch("src.application.pcb_diagnostic.check_packaged_pcb") as gui:
        assert main(["--check-pcb-ui", str(output), "--gerber", "films"]) == 1
    gui.assert_not_called()
    assert json.loads(output.read_text(encoding="utf-8"))["error"] == "授权无效"

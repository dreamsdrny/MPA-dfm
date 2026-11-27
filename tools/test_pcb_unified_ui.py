"""One input/parameter/result GUI for BRD and Gerber; routing is source-bound."""
import json
import time
import threading
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
    assert len(names) == 4
    assert names[0] == "项目总览"
    assert names[1] == "PCB 分析 · BRD / Gerber"
    assert ui.gerber_panel.results is ui.results
    assert ui.gerber_panel.factory is ui.params.factory
    assert ui.gerber_panel.copper is ui.params.copper_um
    assert not ui.gerber_panel.winfo_manager()  # No second visual workflow.
    assert ui.params.design_frame.winfo_manager()


def test_import_sources_are_single_entry_and_paths_remain_available(dialog):
    ui, _root, _errors = dialog
    assert ui.source_path_var.get() == "尚未导入资料"
    assert len(ui.tools_notebook.tabs()) == 4
    assert not ui.settings_win.winfo_viewable()
    assert ui.import_gerber_button.cget("text") == "导入资料"
    # 导入菜单同时提供 PCB 文件、Gerber 目录与 ODB++（查看器 + SMT 协同）：三项、索引 0~2
    assert ui.import_menu.index("end") == 2, ui.import_menu.index("end")
    assert "import_gerber_folder_button" not in ui.__dict__
    assert "import_file_button" not in ui.__dict__
    assert sum(1 for menu, _index, _label in ui._menus if menu is ui.import_menu) == 3
    assert ui.import_menu.entrycget(2, "label").startswith("导入 ODB++")
    assert ui.report_buttons["pcb"].winfo_manager() == "pack"
    assert "preview_gerber_button" not in ui.__dict__
    assert ui.open_brd_button is ui.editor_export_button
    assert ui.menubar.index("end") == 3  # File, Tools and Help; top-level menu indexes include cascades.
    # Editable paths are grouped in the source section; Return imports them,
    # while the single toolbar menu exposes file/folder dialogs.
    assert ui.brd_row.winfo_manager() == "pack"
    assert ui.gerber_row.winfo_manager() == "pack"
    assert ui.brd_row.master is ui.source_inputs
    assert ui.menubar.entrycget(1, "label") == "工具"
    assert not any(child.winfo_class() == "TButton" for child in ui.brd_row.winfo_children())
    assert not any(child.winfo_class() == "TButton" for child in ui.gerber_row.winfo_children())
    assert sum(child.winfo_class() == "TButton" for child in ui.source_inputs.master.winfo_children()) == 1
    assert ui.clear_source_button.cget("text") == "清空资料"
    ui._show_tools(2)
    assert ui.tools_notebook.index("current") == 2
    assert ui.settings_win.winfo_viewable()
    ui._hide_settings()


def test_brd_and_gerber_paths_are_both_visible_in_linked_project(dialog, tmp_path):
    ui, _root, _errors = dialog
    brd = tmp_path / "linked.brd"
    brd.write_bytes(b"mock board")
    ui.brd_path.set(str(brd))
    ui.gerber_dir.set(str(tmp_path / "gerber-set"))
    ui._refresh_source_display()
    assert str(brd) in ui.source_path_var.get()
    assert str(tmp_path / "gerber-set") in ui.source_path_var.get()
    ui._clear_pcb_inputs()
    source = tmp_path / "independent-gerber"
    films(source)
    ui._load_gerber_source(str(source))
    wait(ui, _root, _errors)
    assert ui._gerber_active()
    ui._clear_pcb_inputs()


def test_gerber_import_analysis_tab_switch_and_invalidation(dialog, tmp_path):
    ui, root, errors = dialog
    source = tmp_path / "films"
    films(source)
    ui._load_gerber_source(str(source))
    wait(ui, root, errors)
    assert str(source) in ui.source_path_var.get()
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
    ui.pcb_gerber_source.set(str(source / "other.gtl"))
    assert not ui.gerber_panel.info
    assert str(ui.run_button.cget("state")) == "disabled"


def test_brd_and_gerber_are_kept_in_same_input_area(dialog, tmp_path):
    ui, root, errors = dialog
    board = Path(__file__).resolve().parents[1] / "demo" / "DEMO_BOARD.brd"
    source = tmp_path / "films"
    films(source)
    ui.brd_path.set(str(board))
    ui._load_from_brd()
    wait(ui, root, errors)
    ui._load_gerber_source(str(source))
    assert str(board) in ui.source_path_var.get()
    assert str(source) in ui.source_path_var.get()
    assert ui.brd_info
    assert ui.gerber_dir.get() == str(source)
    assert ui.pcb_gerber_source.get() == str(source)
    assert not ui.auto_manufacturing.get() and not ui.auto_gerber.get()
    ui.workbench.select(ui.impedance_panel)
    assert not ui._gerber_active()
    ui._clear_pcb_inputs()
    assert not ui.brd_info and not ui.gerber_panel.info
    assert not ui.brd_path.get() and not ui.gerber_dir.get()
    ui.brd_path.set(str(board))
    assert ui.auto_manufacturing.get() and ui.auto_gerber.get()
    ui.brd_path.set("")


def test_pcb_exe_diagnostic_checks_license_before_gui(tmp_path):
    from evan_dfm import main
    output = tmp_path / "blocked.json"
    with patch("src.bootstrap.prepare_startup", side_effect=ValueError("授权无效")), \
            patch("src.ui.pcb_diagnostic.check_packaged_pcb") as gui:
        assert main(["--check-pcb-ui", str(output), "--gerber", "films"]) == 1
    gui.assert_not_called()
    assert json.loads(output.read_text(encoding="utf-8"))["error"] == "授权无效"


def test_busy_gerber_cannot_dispatch_brd_or_duplicate_analysis(dialog, tmp_path):
    ui, root, errors = dialog
    ui._clear_pcb_inputs()
    source = tmp_path / "films"
    films(source)
    ui._load_gerber_source(str(source))
    wait(ui, root, errors)
    entered, release = threading.Event(), threading.Event()
    from src.application.gerber_analysis import run_gerber_analysis
    def delayed(request):
        entered.set()
        assert release.wait(10)
        return run_gerber_analysis(request)
    with patch("src.ui.gerber_panel.run_gerber_analysis", side_effect=delayed) as engine, \
            patch("src.ui.dfm_dialog.filedialog.askopenfilename") as browse:
        ui._run()
        assert entered.wait(2)
        assert ui._pcb_state == "running"
        assert str(ui.run_button.cget("state")) == "disabled"
        ui._import_pcb_file()
        ui._browse_brd()
        browse.assert_not_called()
        ui._run()
        assert engine.call_count == 1
        ui.params.copper_um.set("70")
        release.set()
        wait(ui, root, errors)
    assert ui.last_result is None
    assert ui._pcb_state == "idle"
    ui._clear_pcb_inputs()


def test_stale_gerber_report_preview_does_not_open(dialog, tmp_path):
    ui, root, errors = dialog
    source = tmp_path / "films"
    films(source)
    ui._load_gerber_source(str(source))
    wait(ui, root, errors)
    ui._run()
    wait(ui, root, errors)
    entered, release = threading.Event(), threading.Event()
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return ["preview.xlsx"]
    with patch("src.ui.gerber_panel.export_report", side_effect=delayed), \
            patch("src.ui.gerber_panel.os.startfile") as opened:
        ui.gerber_panel.export(preview=True)
        assert entered.wait(2)
        ui.params.copper_um.set("35")
        release.set()
        wait(ui, root, errors)
        opened.assert_not_called()
    ui._clear_pcb_inputs()

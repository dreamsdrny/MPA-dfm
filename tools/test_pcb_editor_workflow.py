"""Analysis automatically extracts without Editor; only explicit locate opens CAD."""
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def ui():
    import tkinter as tk

    from src.ui.dfm_dialog import DFMDialog
    root = tk.Tk()
    root.withdraw()
    with patch("src.ui.prefs.load_prefs", return_value={}), patch("src.ui.prefs.save_prefs"):
        dialog = DFMDialog(root, standalone=True)
        board = Path(__file__).resolve().parents[1] / "demo/DEMO_BOARD.brd"
        dialog.brd_path.set(str(board))
        dialog._load_from_brd()
        wait(dialog)
        yield dialog
        dialog._finish_close()


def wait(ui):
    deadline = time.monotonic() + 10
    while (ui.tasks.pending or ui._loading) and time.monotonic() < deadline:
        ui.win.update()
        time.sleep(.01)
    assert not ui.tasks.pending and not ui._loading


def payload(ui, tmp_path):
    from src.brd_input import content_sha256
    return dict(status="extracted", directory=str(tmp_path), source_brd_sha256=content_sha256(ui.brd_path.get()), unit="mils",
        components=[dict(refdes="R1", x_mm=-2.54, y_mm=0, rotation=90, side="BOTTOM", source="extracta")], warnings=[],
        updates=dict(comp_count=1, layer_count=2, layers=["TOP", "BOTTOM"], copper_um=45.974,
            stackup=[dict(index=1, name="TOP", copper_um=45.974), dict(index=2, name="BOTTOM", copper_um=45.974)]))


def test_analysis_extracts_in_worker_never_opens_editor(ui, tmp_path):
    import threading
    native = payload(ui, tmp_path)
    threads = []
    def extract(*args):
        threads.append(threading.get_ident())
        return native
    with patch("src.application.brd_extraction.prepare_brd_data", side_effect=extract) as extraction, \
            patch("src.ui.dfm_dialog.run_pcb_analysis", side_effect=ValueError("manufacturing unavailable")), \
            patch.object(ui, "_start_editor") as editor, patch("src.ui.dfm_dialog.messagebox.showerror"):
        ui._run()
        wait(ui)
        ui._refresh_ui_labels()
    editor.assert_not_called()
    extraction.assert_called_once()
    assert threads[0] != threading.get_ident()
    assert ui.params.copper_um.get() == "45.974"
    assert ui.params.layers == ["TOP", "BOTTOM"]
    assert ui.results._components[0]["x_brd"] == -100


def test_design_ui_readonly_and_no_default_copper(ui):
    names = [ui.params.sections.tab(tab, "text") for tab in ui.params.sections.tabs()]
    # 设计信息仍为只读表；叠构核对另列一页，标签不再靠“（只读）”后缀区分。
    assert names == ["分析条件", "设计信息", "叠构核对", "待补充"]
    assert ui.results.nb.tab(1, "text") == "叠构剖面"
    assert ui.params.copper_origin == "brd"
    assert ui.params.copper_entry.winfo_manager()  # Automatic value can be explicitly overridden.
    assert not ui.params._visible("layer_count")


def test_explicit_locate_is_only_editor_open(ui):
    with patch.object(ui, "_start_editor") as editor:
        ui._open_problem_point(dict(x_mm=1, y_mm=2))
        editor.assert_called_once_with(point=dict(x_mm=1, y_mm=2))


def test_trace_apply_really_launches_editor(ui):
    panel = ui.impedance_panel
    panel.set_layers(["TOP", "BOTTOM"])
    with patch("src.ui.impedance_panel.AllegroTraceSession") as session:
        panel._apply()
        session.return_value.prepare_trace.assert_called_once_with("TOP", "5", "")
        session.return_value.launch.assert_called_once()
    panel.session = None


def test_layer_copper_confirmation_keeps_design_and_applies_only_selected_layer(ui):
    panel = ui.params
    panel._refresh_stackup()
    panel.stackup_tree.selection_set(panel.stackup_tree.get_children()[0])
    with patch("tkinter.simpledialog.askfloat", return_value=70), \
            patch("tkinter.simpledialog.askstring", return_value="supplier confirmed report 2026-11-27"):
        panel._confirm_layer_copper()
    design = panel.read_design()
    assert design["stackup"][0]["copper_um"] == 70
    assert design["stackup"][0]["design_copper_um"] == 45.974
    assert design["stackup"][1]["copper_um"] == 45.974
    assert design["copper_um"] is None
    panel.stackup_tree.selection_set(panel.stackup_tree.get_children()[0])
    panel._restore_layer_copper()
    assert not panel.copper_confirmations


def test_smt_ui_passes_bom_and_gerber_sources_to_worker(ui, tmp_path):
    from src.smt_engine import analyze_smt
    panel = ui.smt
    panel.bom_path.set(str(tmp_path / "bom.csv"))
    panel.gerber_path.set(str(tmp_path / "films"))
    panel.gerber_confirmed.set(True)
    result = analyze_smt(panel.doc, panel.key.get(), {})
    with patch("src.ui.smt_panel.analyze_smt_workflow", return_value=result) as workflow:
        panel.run()
        wait(ui)
        assert workflow.call_args.kwargs["auto_sources"] is True
        assert workflow.call_args.kwargs["bom_path"] == str(tmp_path / "bom.csv")
        assert workflow.call_args.args[5] == str(tmp_path / "films")
        assert workflow.call_args.kwargs["gerber_confirmed"] is True
    panel.bom_path.set("")
    panel.gerber_path.set("")
    panel.gerber_confirmed.set(False)


def test_smt_bom_import_reads_and_validates_before_adopting(ui, tmp_path):
    panel=ui.smt
    path=tmp_path / "bom.csv"
    path.write_text("refdes,footprint,side,dnp\nR1,0402,TOP,NO\n")
    with patch("src.ui.smt_panel.filedialog.askopenfilename",return_value=str(path)):
        panel._browse_bom()
        wait(ui)
    assert panel.bom_path.get()==str(path)
    assert "1 个位号" in panel.summary.get()
    bad=tmp_path / "bad.csv"
    bad.write_text("nothing,value\na,b\n")
    with patch("src.ui.smt_panel.filedialog.askopenfilename",return_value=str(bad)), \
            patch("src.ui.smt_panel.messagebox.showerror") as error:
        panel._browse_bom()
        wait(ui)
        error.assert_called_once()
    assert panel.bom_path.get()==str(path)
    panel.bom_path.set("")

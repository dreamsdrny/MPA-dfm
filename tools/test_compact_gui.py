"""Fixed workbench, paged results, preserved full evidence and stable startup."""
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
        yield dialog
        dialog._finish_close()


def sample_result(count=420):
    return dict(items=[dict(status="fail" if i % 2 == 0 else "info", item=f"issue-{i}",
                message=f"detail-{i}", category="test", source="Gerber") for i in range(count)],
        problem_locations=[dict(item=f"issue-{i}", x_mm=i, y_mm=i + 1, layer="top") for i in range(count)],
        geometry_coverage=[], error_count=count // 2, warning_count=0,
        verdict="不通过", verdict_reasons=["完整判定依据" * 200], coverage={"ratio": .5})


def test_main_window_is_fixed_and_settings_do_not_displace_body(ui):
    import tkinter as tk
    assert not any(isinstance(w, tk.Canvas) for w in ui.content.winfo_children())
    assert ui.win.state() == "withdrawn"
    assert "preview_gerber_button" not in ui.__dict__
    assert [ui.params.sections.tab(tab, "text") for tab in ui.params.sections.tabs()] == [
        "分析条件", "设计信息", "叠构核对", "待补充"]
    assert [ui.results.nb.tab(tab, "text") for tab in ui.results.nb.tabs()][1] == "叠构剖面"
    assert ui.results.stackup_view is ui.params.stackup_view
    assert not ui.settings_win.winfo_viewable()
    ui._toggle_advanced()
    assert ui.advanced_frame.winfo_manager() == "pack"
    ui._toggle_advanced()
    assert not ui.advanced_frame.winfo_manager()


def test_paging_retains_full_result_and_location_ids(ui):
    result = sample_result()
    panel = ui.results
    panel.render_capability(result)
    assert len(panel.tree.get_children()) == 100
    assert len(panel._items) == 420
    assert len(panel.location_tree.get_children()) == 100
    assert "210 项" in panel.page_var.get()
    assert len(panel.result_var.get()) < 130
    assert len(panel.verdict_details) > 1000
    panel._change_page(1)
    assert panel.tree.get_children()[0] == "200"
    panel.tree.selection_set("200")
    with patch.object(panel, "_open_selected"):
        panel._locate_capability()
    assert panel.location_tree.selection() == ("200",)
    assert panel._location_page == 2
    panel.result_filter.set("全部")
    assert "420" in panel.page_var.get() or "421" in panel.page_var.get()
    panel._change_page(4)
    assert len(panel.tree.get_children()) <= 100
    assert len(result["items"]) == 420
    panel.clear()


def test_long_status_cannot_expand_window_and_params_do_not_auto_open(ui):
    ui.status_var.set("很长的问题说明" * 500)
    assert len(ui.status_detail_var.get()) <= 110
    ui.params.show_brd_var.set(False)
    ui.params.origins["layer_count"] = "brd"
    ui.params.reveal_brd()
    assert not ui.params.show_brd_var.get()


def test_large_analysis_keeps_bounds_at_small_and_normal_sizes(ui):
    ui.workbench.select(ui.brd_body)
    ui.win.deiconify()
    try:
        for width, height in ((800, 600), (1200, 800)):
            ui.win.geometry(f"{width}x{height}")
            ui.results.clear()
            ui.win.update_idletasks()
            ui.win.update()
            before = (ui.workbench.winfo_width(), ui.workbench.winfo_height(), ui.results.winfo_width(), ui.results.winfo_height())
            ui.results.render_capability(sample_result(5000))
            ui.win.update_idletasks()
            after = (ui.workbench.winfo_width(), ui.workbench.winfo_height(), ui.results.winfo_width(), ui.results.winfo_height())
            assert before == after
            assert after[2] > 200 and after[3] > 200
            assert len(ui.results.tree.get_children()) == 100
            assert len(ui.results.location_tree.get_children()) == 100
            assert ui.params.winfo_viewable() and ui.run_button.winfo_viewable()
        ui.results.clear()
    finally:
        ui.win.withdraw()


def test_selected_details_are_not_repeated_and_clear_with_page(ui):
    ui.results.render_capability(sample_result())
    ui.results.tree.selection_set("0")
    ui.win.update()
    assert ui.results.tree._full_text.get("1.0", "end").strip() == "detail-0"
    ui.results._change_page(1)
    assert "detail-0" not in ui.results.tree._full_text.get("1.0", "end")
    ui.results.clear()

"""叠构剖面：已知厚度等比、缺失不编造、选择联动与报告图片。"""
from pathlib import Path
from unittest.mock import patch

import pytest

import tkinter as tk

from src.domain.stackup_visual import COPPER, build_visual


def rows(count=4, copper=18, missing=()):
    return [dict(index=i, name=f"L{i}", copper_um=None if i in missing else copper * i,
                 outer=i in (1, count), source="BRD extracta") for i in range(1, count + 1)]


def test_visual_scales_known_copper_and_keeps_missing_unknown():
    visual = build_visual(rows(missing=(3,)), board_thickness_mm=1.6)
    copper = [b for b in visual["bands"] if b["kind"] == COPPER]
    assert [b["label"] for b in copper] == ["L1", "L2", "L3", "L4"]
    named = build_visual([dict(index=1, name="TOP", copper_um=35), dict(index=2, name="GND", copper_um=18)])
    assert [b["label"] for b in named["bands"] if b["kind"] == COPPER] == ["TOP", "L2 GND"]
    from src.application.pcb_inputs import layer_label
    assert layer_label(2, "L2 GND") == "L2 GND" and layer_label(2, "GND") == "L2 GND"
    assert layer_label(3, None) == "L3"
    assert [b["status"] for b in copper] == ["brd", "brd", "missing", "brd"]
    assert copper[2]["thickness_um"] is None
    assert "L3" in visual["notes"][0] and visual["missing_copper"] == ["L3"]
    assert visual["total_copper_um"] == 18 + 36 + 72
    assert visual["drills"] == []
    drilled = build_visual(rows(count=2), holes=dict(pth_span_count=3, npth_span_count=1))
    assert [(d["kind"], d["spans"]) for d in drilled["drills"]] == [("PTH", 3), ("NPTH", 1)]
    assert drilled["drills"][0]["top"] == 1 and drilled["drills"][0]["bottom"] == 2


def test_confirmed_copper_is_distinguished_and_overfull_stack_warns():
    data = rows(count=2, copper=900)
    data[0]["design_copper_um"] = 35
    data[0]["confirmed"] = True
    visual = build_visual(data, board_thickness_mm=1.6)
    assert visual["bands"][1]["status"] == "confirmed"
    assert "已超过板厚" in visual["notes"][0]


def test_invalid_thickness_never_becomes_zero():
    visual = build_visual([dict(index=1, name="TOP", copper_um="nan"), dict(index=2, name="BOT", copper_um=True)])
    assert [b["status"] for b in visual["bands"] if b["kind"] == COPPER] == ["missing", "missing"]


@pytest.fixture(scope="module")
def panel():
    import tkinter as tk
    from src.ui.dfm_params import ParamPanel
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tcl/Tk 初始化资源不可用：{exc}")
    root.withdraw()
    with patch("src.ui.prefs.load_prefs", return_value={}):
        view = ParamPanel(root, lambda: None, lambda: None, unified=True)
    yield root, view
    root.destroy()


def test_diagram_click_selects_table_and_does_not_invent_values(panel):
    root, view = panel
    from src.ui.stackup_view import StackupView
    holder = tk.Frame(root); holder.pack()
    diagram = StackupView(holder)
    diagram.pack(fill=tk.BOTH, expand=True)
    view.bind_stackup_view(diagram)
    view.set_board_available(True)
    assert [view.sections.tab(tab, "text") for tab in view.sections.tabs()][2] == "叠构核对"
    assert view.stackup_view is diagram
    assert diagram.winfo_manager() == "pack"
    view.stackup = rows(count=4, missing=(3,))
    view.entries["layer_count"].set("4")
    view._refresh_stackup()
    root.update_idletasks()
    view.stackup_view.canvas.update_idletasks()
    view.stackup_view.draw()
    assert view.stackup_view.visual["missing_copper"] == ["L3"]
    assert list(view.stackup_tree.get_children()) == ["1", "2", "3", "4"]
    assert view.stackup_tree.item("3", "values")[0].startswith("内 L3")
    assert view.stackup_tree.item("3", "values")[2] == "未获取"
    view.stackup_tree.selection_set("2")
    with patch("tkinter.simpledialog.askfloat", return_value=70), \
            patch("tkinter.simpledialog.askstring", return_value="板厂叠层表 v3"):
        view._confirm_layer_copper()
    assert view.copper_confirmations["L2"]["copper_um"] == 70
    assert view.stackup_tree.item("2", "values")[2] == "70"
    view._restore_layer_copper()
    assert "L2" not in view.copper_confirmations
    assert view.stackup_tree.item("2", "values")[2] == "36"
    band = next(b for b in view.stackup_view.visual["bands"] if b["kind"] == COPPER and b["index"] == 2)
    view.stackup_view._clicked(band)
    assert view.stackup_tree.selection() == ("2",)
    assert "36μm" in view.stackup_view.detail.get() and "BRD extracta" in view.stackup_view.detail.get()
    view.stackup_tree.selection_set("4")
    view._select_stackup_table()
    assert view.stackup_view._selected == 4
    # 未获取层不能显示成 0；图上只有斜纹与“未获取”文字
    assert "0μm" not in view.stackup_view.detail.get()


def test_stackup_picture_embeds_in_pcb_report(tmp_path):
    from openpyxl import load_workbook
    from src.adapters.stackup_picture import render_stackup_png
    from src.dfm_engine import analyze_design
    from src.application.reports import export_report
    design = dict(layer_count=4, board_thickness=1.6, stackup=rows(count=4, missing=(3,)))
    result = analyze_design(design)
    png = render_stackup_png(result["stackup"], board_thickness_mm=1.6, path=tmp_path / "stackup.png")
    assert Path(png).is_file()
    assert Path(png).stat().st_size > 4000
    export_report("pcb", tmp_path / "report.xlsx", pcb=result,
                  board_meta=dict(brd_path="", stackup_picture=png))
    wb = load_workbook(tmp_path / "report.xlsx")
    assert "叠构剖面" in wb.sheetnames
    assert len(wb["叠构剖面"]._images) == 1
    wb.close()


def test_picture_keeps_labels_clear_of_drill_symbols(tmp_path):
    from src.adapters.stackup_picture import render_stackup_png
    data = [dict(index=i, name=f"L{i}", copper_um=18, outer=i in (1, 4), source="BRD") for i in range(1, 5)]
    path = render_stackup_png(data, path=tmp_path / "drill.png", holes=dict(pth_span_count=2, npth_span_count=1))
    from PIL import Image
    with Image.open(path) as image:
        assert image.width == 1180 and image.height > 200

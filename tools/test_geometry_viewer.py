"""图形查看器：真实底片→图形模型→位图；交互合并、过期帧丢弃与不谎报。"""
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

from src.application.geometry_view import gerber_view, odb_view, result_view
from src.domain.geometry_view import build_view_model, pan_viewport, viewport, zoom_viewport
from tools.test_gerber_standalone import films

ROOT = Path(__file__).resolve().parents[1]


def test_view_model_keeps_holes_and_marks_simplification():
    ring = Polygon([(0, 0), (40, 0), (40, 30), (0, 30)]).difference(Polygon([(10, 10), (12, 10), (12, 12), (10, 12)]))
    model = build_view_model([dict(name="GTL", role="copper", geometry=MultiPolygon([ring]), objects=1)])
    layer = model["layers"][0]
    assert len(layer["polygons"]) == 1 and len(layer["polygons"][0]) == 2  # 外环 + 孔
    assert layer["simplified_mm"] > 0 and not layer["truncated"]
    assert model["bounds"] == [0, 0, 40, 30]
    assert "简化" in model["display_note"]


def test_viewport_math_pan_zoom_and_invalid_input():
    model = build_view_model([dict(name="A", role="copper", geometry=Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]))])
    area = viewport(model)
    assert area[0] < 0 and area[2] > 10
    zoomed = zoom_viewport(area, 2, anchor=(5, 5))
    assert zoomed[2] - zoomed[0] == pytest.approx((area[2] - area[0]) / 2)
    assert zoomed[0] < 5 < zoomed[2]
    assert pan_viewport(area, (1, -1))[0] == pytest.approx(area[0] + 1)
    with pytest.raises(ValueError):
        zoom_viewport(area, 0)
    assert viewport({"layers": [], "bounds": None}) is None


def test_empty_and_negative_polarity_inputs_are_not_invented():
    model = build_view_model([])
    assert model["layers"] == [] and "未读到可绘制图形" in model["notes"][0]
    huge = Point(0, 0).buffer(1, cap_style=1)
    capped = build_view_model([dict(name="X", role="copper", geometry=huge)])
    assert capped["layers"][0]["points"] > 0


def test_gerber_view_reads_real_files_and_blocks_bad_input(tmp_path):
    folder = tmp_path / "films"
    films(folder)
    result = gerber_view(folder, issues=[dict(x_mm=1, y_mm=1, item="线距不足", status="fail")])
    model = result["model"]
    assert len(model["layers"]) == 6
    assert {row["role"].split("_")[0] for row in model["layers"]} == {"copper", "mask", "silkscreen", "outline"}
    assert all(row["polygons"] or row["lines"] for row in model["layers"])
    assert model["issues"][0]["label"] == "线距不足"
    assert result["fingerprints"][0]["path"] == str(folder.resolve())
    bad = tmp_path / "not.gbr"
    bad.write_text("hello", encoding="ascii")
    with pytest.raises(ValueError):
        gerber_view(bad)
    with pytest.raises(ValueError):
        gerber_view("")


def test_odb_view_uses_same_parser_and_labels_alignment(tmp_path):
    from tools.test_pcb_object_checks import odb_job

    archive = tmp_path / "board.zip"
    odb_job(archive)
    result = odb_view(archive, aligned=False)
    model = result["model"]
    assert model["layers"] and all(row["role"].startswith("odb/") for row in model["layers"])
    assert "未确认与 BRD 同版同原点" in model["notes"][0]
    assert result["step"] == "PCB"
    aligned = odb_view(archive, aligned=True)["model"]
    assert "已通过 BRD 身份/原点对齐核对" in aligned["notes"][0]
    with pytest.raises(ValueError):
        odb_view("")


def test_result_view_uses_the_same_gerber_folder_as_the_analysis(tmp_path):
    folder = tmp_path / "set"
    films(folder)
    result = {"gerber_folder": str(folder), "problem_locations": [dict(x_mm=2, y_mm=2, item="铜到板边", layer="GTL")]}
    model = result_view(result, "gerber")["model"]
    assert model["issues"][0]["x_mm"] == 2 and model["layers"]
    with pytest.raises(ValueError, match="Gerber"):
        result_view({"gerber_folder": ""}, "gerber")


@pytest.fixture(scope="module")
def viewer_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def pump(root, seconds=1.5, until=lambda: True):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not until():
        root.update()
        time.sleep(.01)
    root.update()


def test_viewer_renders_one_bitmap_and_drops_stale_frames(viewer_root):
    from src.ui.geometry_viewer import GeometryViewer

    root = viewer_root
    view = GeometryViewer(root)
    view.pack()
    view.canvas.update_idletasks()
    model = build_view_model([dict(name="GTL", role="copper", geometry=Polygon([(0, 0), (20, 0), (20, 10), (0, 10)]))],
                             issues=[dict(x_mm=5, y_mm=5, item="问题", status="fail")])
    view.set_model(model, "测试")
    pump(root, until=lambda: view.scale_var.get() != "未载入图形")
    assert "mm/px" in view.scale_var.get() and view._photo is not None
    before = view.revision
    for _ in range(30):  # 连续缩放只应合并成少量请求，而不是排队 30 次重绘
        view._zoom(1.2)
    assert view._job is not None
    pump(root, until=lambda: view.revision - before <= 4)
    assert view.revision - before <= 4
    view._interactive = True
    view._request()
    stale = view.revision
    view._results.put((stale - 99, "ok", b"not-an-image", None, (10, 10)))
    pump(root, .3)
    assert view.scale_var.get()  # 过期帧不改状态文字
    view._destroyed(type("E", (), {"widget": view})())
    assert not view._alive


def test_viewer_reports_render_failure_instead_of_showing_old_frame(viewer_root):
    from src.ui.geometry_viewer import GeometryViewer

    root = viewer_root
    view = GeometryViewer(root)
    view.pack()
    view.canvas.update_idletasks()
    model = build_view_model([dict(name="A", role="copper", geometry=Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]))])
    view.set_model(model)
    pump(root, until=lambda: "mm/px" in view.scale_var.get())
    assert view._photo is not None
    with patch("src.adapters.geometry_raster.render_png", side_effect=OSError("boom")):
        view._schedule(immediate=True)
        pump(root, 2.5, until=lambda: "渲染失败" in view.status_var.get())
    assert "渲染失败" in view.status_var.get() and view._photo is None
    view._destroyed(type("E", (), {"widget": view})())


def test_viewer_layer_toggle_and_issue_focus(viewer_root):
    from src.ui.geometry_viewer import GeometryViewer

    root = viewer_root
    view = GeometryViewer(root)
    view.pack()
    view.canvas.update_idletasks()
    model = build_view_model([dict(name="GTL", role="copper", geometry=Polygon([(0, 0), (9, 0), (9, 9), (0, 9)])),
                              dict(name="GTS", role="mask", geometry=Polygon([(0, 0), (3, 0), (3, 3), (0, 3)]))],
                             issues=[dict(x_mm=8, y_mm=8, item="标记", status="fail")])
    view.set_model(model)
    pump(root, until=lambda: "mm/px" in view.scale_var.get())
    assert view.visible_layer_names() == ["GTL", "GTS"]
    assert view.layers.exists("GTS")
    view.layers.selection_set("GTS")
    assert view.toggle_layer("GTS") is False
    assert view.visible_layer_names() == ["GTL"]
    assert view.layers.item("GTS", "text").startswith("○")
    assert view.toggle_layer("GTS") is True
    assert view.visible_layer_names() == ["GTL", "GTS"]
    assert view.toggle_layer("不存在") is None
    view.issues.selection_set("1")
    view._issue_selected()
    assert view.area[0] < 8 < view.area[2]
    view._destroyed(type("E", (), {"widget": view})())


def test_open_viewer_returns_none_without_layers(viewer_root):
    from src.ui.geometry_viewer import open_viewer

    assert open_viewer(viewer_root, build_view_model([])) is None
    window = open_viewer(viewer_root, build_view_model(
        [dict(name="A", role="copper", geometry=Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]))]), title="X")
    assert window is not None
    window.destroy()

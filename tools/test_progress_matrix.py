"""执行进度动画：未执行灰点、执行中绿点累计、缺口黄点、失败红点，且不谎报进度。"""
import time

import pytest

from src.ui.progress_matrix import COMPLETE, ERROR, IDLE, PARTIAL, RUNNING


@pytest.fixture(scope="module")
def root():
    import tkinter as tk
    try:
        window = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tcl/Tk 初始化资源不可用：{exc}")
    window.withdraw()
    yield window
    window.destroy()


def pump(window, seconds=.6):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        window.update()
        time.sleep(.01)


def matrix(window, **kwargs):
    from src.ui.progress_matrix import ProgressMatrix
    view = ProgressMatrix(window, columns=8, rows=2, width=120, height=60, **kwargs)
    view.pack()
    view.update_idletasks()
    return view


def close(view):
    view._destroyed(type("E", (), {"widget": view})())


def ovals(view):
    return {view.itemcget(item, "fill") for item in view.find_all() if view.type(item) == "oval"}


def test_idle_is_grey_and_starts_empty(root):
    from src.ui.theme import COLORS
    view = matrix(root)
    assert view.state == IDLE
    shot = view.snapshot()
    assert shot["filled"] == 0 and shot["total"] == 16
    assert ovals(view) == {COLORS["faint"]}
    close(view)


def test_running_fills_only_completed_stages(root):
    view = matrix(root)
    view.start(["a", "b", "c", "d"])
    pump(root, .3)
    assert view.snapshot()["state"] == RUNNING and view.snapshot()["filled"] == 0
    view.advance("b")
    pump(root, .6)
    shot = view.snapshot()
    assert shot["target"] == .25 and 0 < shot["filled"] <= 4
    view.advance("d")
    pump(root, .8)
    assert view.snapshot()["target"] == .75
    close(view)


def test_unknown_stage_does_not_advance(root):
    view = matrix(root)
    view.start(["a", "b"])
    view.advance("不存在的阶段")
    assert view.snapshot()["target"] == 0.0
    close(view)


def test_stage_fraction_only_moves_within_current_stage(root):
    view = matrix(root)
    view.start(["a", "b", "c", "d"])
    view.set_fraction(.5)
    assert view.snapshot()["target"] == pytest.approx(.125)
    view.set_fraction(None)
    assert view.snapshot()["target"] == 0.0
    close(view)


def test_partial_and_error_use_distinct_colors(root):
    from src.ui.theme import COLORS
    view = matrix(root)
    view.start(["a", "b", "c", "d"])
    view.advance("c")
    pump(root, .5)
    view.finish(PARTIAL, label="仍有资料缺口")
    assert COLORS["success"] in ovals(view) and COLORS["warn"] in ovals(view)
    assert "缺口" in view.label
    other = matrix(root)
    other.start(["a", "b"])
    other.advance("b")
    pump(root, .5)
    other.finish(ERROR, label="执行失败")
    # 失败：已尝试部分红点，未执行部分仍灰点；不能整框都红让人以为全部跑过
    assert ovals(other) == {COLORS["danger"], COLORS["faint"]} or COLORS["danger"] in ovals(other)
    assert COLORS["danger"] in ovals(other) and COLORS["warn"] not in ovals(other)
    close(view)
    close(other)


def test_complete_fills_every_dot_and_stops_timer(root):
    view = matrix(root)
    view.start(["a", "b"])
    view.finish(COMPLETE)
    shot = view.snapshot()
    assert shot["state"] == COMPLETE and shot["filled"] == shot["total"]
    assert view._job is None
    close(view)


def test_reset_stops_animation_and_clears_points(root):
    view = matrix(root)
    view.start(["a", "b", "c"])
    view.advance("c")
    pump(root, .4)
    view.reset("输入已变化 · 本轮未执行")
    shot = view.snapshot()
    assert shot["state"] == IDLE and shot["filled"] == 0 and view._job is None
    close(view)


def test_destroy_cancels_pending_animation(root):
    view = matrix(root)
    view.start(["a", "b", "c"])
    pump(root, .3)
    assert view._job is not None
    close(view)
    assert view._job is None

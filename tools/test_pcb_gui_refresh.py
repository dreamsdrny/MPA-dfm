"""Periodic translation must be idempotent, not flood Tk with layout events."""
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def root():
    import tkinter as tk
    widget = tk.Tk()
    widget.withdraw()
    yield widget
    widget.destroy()


def test_repeated_localization_does_not_rewrite_unchanged_widgets(root):
    import tkinter as tk
    from tkinter import ttk
    from src.ui import i18n
    frame = ttk.Frame(root)
    source = tk.StringVar(master=root, value="执行 PCB 分析")
    label = ttk.Label(frame, textvariable=source)
    button = ttk.Button(frame, text="执行 PCB 分析")
    notebook = ttk.Notebook(frame)
    notebook.add(ttk.Frame(notebook), text="执行 PCB 分析")
    tree = ttk.Treeview(frame, columns=("status",))
    tree.heading("status", text="结果")
    try:
        with patch.object(i18n, "LANGUAGE", "en"):
            i18n.localize_widgets(frame)
            mirror = label._i18n_mirror
            with patch.object(button, "configure", wraps=button.configure) as text_write, \
                    patch.object(mirror, "set", wraps=mirror.set) as variable_write, \
                    patch.object(notebook, "tab", wraps=notebook.tab) as tab_calls, \
                    patch.object(tree, "heading", wraps=tree.heading) as heading_calls:
                i18n.localize_widgets(frame)
                text_write.assert_not_called()
                variable_write.assert_not_called()
                assert all("text" not in call.kwargs for call in tab_calls.call_args_list)
                assert all("text" not in call.kwargs for call in heading_calls.call_args_list)
            source.set("PCB 报告")
            assert mirror.get() == i18n.translate("PCB 报告")
        with patch.object(i18n, "LANGUAGE", "zh"):
            i18n.localize_widgets(frame)
            assert button.cget("text") == "执行 PCB 分析"
            assert notebook.tab(0, "text") == "执行 PCB 分析"
            assert mirror.get() == "PCB 报告"
    finally:
        frame.destroy()

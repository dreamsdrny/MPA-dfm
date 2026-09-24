"""
Layout-brd: 程序入口 (GUI)
=================================
在不改动原有代码 (main.py / gui.py / src) 的前提下，
新增独立入口，为程序窗口与可执行文件套用 Evan 图标 (evan.ico / evan.png)。

用法:
    python layout_brd.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

APP_NAME = "Layout-brd"


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def apply_icon(root) -> None:
    try:
        ico = resource_path("evan.ico")
        if os.path.exists(ico):
            try:
                root.iconbitmap(default=ico)
            except Exception:
                pass
            try:
                root.wm_iconbitmap(ico)
            except Exception:
                pass

        png = resource_path("evan.png")
        if os.path.exists(png):
            from tkinter import PhotoImage
            img = PhotoImage(file=png)
            root.iconphoto(True, img)
            root._evan_icon = img
    except Exception:
        pass


def _walk(widget):
    for child in widget.winfo_children():
        yield child
        for sub in _walk(child):
            yield sub


def remove_dfm_entry(root) -> None:
    """移除 DFM 分析入口按钮 (不改动 gui.py)"""
    try:
        from tkinter import ttk
        for w in _walk(root):
            if isinstance(w, ttk.Button):
                try:
                    label = str(w.cget("text") or "")
                except Exception:
                    continue
                if "DFM" in label:
                    w.destroy()
    except Exception:
        pass


def main():
    from gui import BRD_AIGUI

    app = BRD_AIGUI()
    try:
        app.root.title(f"{APP_NAME}: Cadence Allegro Constraint Generator")
    except Exception:
        pass
    apply_icon(app.root)
    remove_dfm_entry(app.root)
    app._open_dfm_dialog = lambda *a, **k: None
    app.run()


if __name__ == "__main__":
    main()
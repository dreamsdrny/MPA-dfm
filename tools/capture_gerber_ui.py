"""Capture only our Tk HWND and check primary toolbar layout at two window sizes."""
import ctypes
import json
from pathlib import Path
import struct
import sys
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def capture(root, destination):
    from ctypes import wintypes as w
    user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    for api, args, result in ((user.GetAncestor, [w.HWND, w.UINT], w.HWND),
                             (user.GetDC, [w.HWND], w.HDC),
                             (gdi.CreateCompatibleDC, [w.HDC], w.HDC),
                             (gdi.CreateCompatibleBitmap, [w.HDC, ctypes.c_int, ctypes.c_int], w.HANDLE),
                             (gdi.SelectObject, [w.HDC, w.HANDLE], w.HANDLE),
                             (user.PrintWindow, [w.HWND, w.HDC, w.UINT], w.BOOL),
                             (gdi.GetDIBits, [w.HDC, w.HANDLE, w.UINT, w.UINT, ctypes.c_void_p, ctypes.c_void_p, w.UINT], ctypes.c_int),
                             (gdi.DeleteObject, [w.HANDLE], w.BOOL),
                             (gdi.DeleteDC, [w.HDC], w.BOOL),
                             (user.ReleaseDC, [w.HWND, w.HDC], ctypes.c_int)):
        api.argtypes, api.restype = args, result
    hwnd = user.GetAncestor(root.winfo_id(), 2)
    dc = user.GetDC(hwnd)
    target = gdi.CreateCompatibleDC(dc)
    width, height = root.winfo_width(), root.winfo_height()
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
    old = gdi.SelectObject(target, bitmap)
    try:
        if not user.PrintWindow(hwnd, target, 3):
            raise RuntimeError("PrintWindow failed")
        gdi.SelectObject(target, old)
        header = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        info = ctypes.create_string_buffer(header)
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi.GetDIBits(dc, bitmap, 0, height, pixels, info, 0):
            raise RuntimeError("GetDIBits failed")
        destination.write_bytes(struct.pack("<2sIHHI", b"BM", 54 + len(pixels.raw), 0, 0, 54) + header + pixels.raw)
    finally:
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(target)
        user.ReleaseDC(hwnd, dc)


def main():
    from src.ui.dfm_dialog import DFMDialog
    from src.ui.responsive import FlowBar

    output = Path(__file__).resolve().parents[1] / "delivery" / "evidence" / "GERBER_20261125_v2"
    output.mkdir(parents=True, exist_ok=True)
    observations = []
    for width, height in ((1100, 720), (1366, 850)):
        root = tk.Tk()
        with patch("src.ui.prefs.load_prefs", return_value={}), patch("src.ui.prefs.save_prefs"):
            dialog = DFMDialog(root, standalone=True)
        try:
            root.geometry(f"{width}x{height}+20+20")
            root.update_idletasks()
            root.update()
            root.update_idletasks()
            capture(root, output / f"gerber_{width}x{height}.bmp")
            overflow = []
            def check(widget):
                if isinstance(widget, FlowBar) and widget.winfo_ismapped():
                    for child in widget.winfo_children():
                        if child.winfo_ismapped() and child.winfo_x() + child.winfo_width() > widget.winfo_width() + 2:
                            overflow.append(child.winfo_class())
                for child in widget.winfo_children():
                    check(child)
            check(root)
            observations.append({"size": [width, height], "gerber_default_tab": dialog._gerber_active(),
                                 "advanced_hidden": not bool(dialog.advanced_frame.winfo_manager()),
                                 "toolbar_overflow": overflow})
        finally:
            dialog._finish_close()
    (output / "ui_layout.json").write_text(json.dumps(observations, indent=2), encoding="utf-8")
    print(json.dumps(observations))


if __name__ == "__main__":
    main()

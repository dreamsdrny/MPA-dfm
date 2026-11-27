"""Explicit report scope/format selection before a standard save-file dialog."""
import tkinter as tk
from tkinter import filedialog, ttk
from src.application.reports import REPORT_FORMATS, REPORT_LABELS, default_report_name


def choose_report(parent, available, source="", preferred="dfm"):
    win = tk.Toplevel(parent)
    win.title("导出分析报告")
    win.transient(parent)
    win.resizable(False, False)
    frame = ttk.Frame(win, padding=24); frame.pack(fill=tk.BOTH, expand=True)
    ttk.Label(frame, text="选择报告内容与格式", style="Section.TLabel").pack(anchor=tk.W)
    scope = tk.StringVar(win, value=preferred if preferred in available else available[0])
    fmt = tk.StringVar(win, value=".xlsx")
    for key in available:
        ttk.Radiobutton(frame, text=REPORT_LABELS[key], variable=scope, value=key).pack(anchor=tk.W, pady=5)
    ttk.Separator(frame).pack(fill=tk.X, pady=12)
    for ext, label in REPORT_FORMATS.items():
        ttk.Radiobutton(frame, text=f"{label}  ({ext})", variable=fmt, value=ext).pack(anchor=tk.W, pady=4)
    ttk.Label(frame, text="仅保存所选文件，不自动生成附属 TXT / JSON。\nExcel / HTML 保留图片；CSV / TXT 为文本内容。",
              wraplength=430).pack(anchor=tk.W, pady=14)
    result = []
    def save():
        ext = fmt.get(); kind = scope.get()
        name, directory = default_report_name(source, kind, ext)
        options = dict(parent=win, title="保存 " + REPORT_LABELS[kind], defaultextension=ext,
                       initialfile=name, filetypes=[(REPORT_FORMATS[ext], "*" + ext)])
        if directory: options["initialdir"] = directory
        path = filedialog.asksaveasfilename(**options)
        if path:
            from pathlib import Path
            if Path(path).suffix.lower() != ext: path = str(Path(path).with_suffix(ext))
            result.extend((kind, path)); win.destroy()
    buttons = ttk.Frame(frame); buttons.pack(fill=tk.X)
    ttk.Button(buttons, text="取消", command=win.destroy).pack(side=tk.RIGHT)
    ttk.Button(buttons, text="选择保存文件…", style="Primary.TButton", command=save).pack(side=tk.RIGHT, padx=10)
    win.grab_set(); parent.wait_window(win)
    return tuple(result) if result else None

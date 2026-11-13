"""Scrollable workbench and wrapping toolbars in physical Tk pixels."""
import tkinter as tk
from tkinter import ttk

from src.ui.theme import COLORS


class FlowBar(ttk.Frame):
    """Reflow children at their requested DPI-scaled width, never clip buttons."""
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.bind("<Configure>", self._layout)
        self._job = None

    def add(self, widget):
        widget.grid(row=0, column=len(self.winfo_children())-1, sticky="w", padx=4, pady=3)
        self.after_idle(self._layout)
        return widget

    def _layout(self, _event=None):
        if not self.winfo_exists():
            return
        available = max(self.winfo_width()-20, 200)
        row = column = used = 0
        for child in self.winfo_children():
            width = child.winfo_reqwidth()+10
            if used and used+width > available:
                row += 1
                column = used = 0
            child.grid(row=row, column=column, sticky="w", padx=4, pady=3)
            used += width
            column += 1


def scroll_workbench(master):
    frame = ttk.Frame(master)
    frame.pack(fill=tk.BOTH, expand=True)
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    canvas = tk.Canvas(frame, highlightthickness=0, bg=COLORS["bg"])
    canvas.grid(row=0, column=0, sticky="nsew")
    vertical = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal = ttk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
    horizontal.grid(row=1, column=0, sticky="ew")
    canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    content = ttk.Frame(canvas)
    window = canvas.create_window(0, 0, window=content, anchor="nw")

    def resized(event=None):
        # Small screens retain a scrollable logical workspace instead of hiding controls.
        minimum = round(760 * float(master.tk.call("tk", "scaling")) / (96/72))
        canvas.itemconfigure(window, width=max(canvas.winfo_width(), minimum),
                             height=max(canvas.winfo_height(), content.winfo_reqheight()))
        canvas.configure(scrollregion=canvas.bbox("all"))
    content.bind("<Configure>", resized)
    canvas.bind("<Configure>", resized)
    def wheel(event):
        canvas.yview_scroll(-int(event.delta/120), "units")
    canvas.bind("<MouseWheel>", wheel)
    return content


def wrap_label(label, container, margin=20):
    def resized(event):
        label.configure(wraplength=max(120, event.width-margin))
    container.bind("<Configure>", resized, add="+")

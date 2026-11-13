"""通用小部件: 表单行 / 下拉 / 输入格 / 滚动区 / 表格.

这些是主窗口与 DFM 对话框共用的小积木, 只负责摆放控件, 不含业务判断。
"""
import tkinter as tk
from tkinter import ttk

from src.ui.theme import COLORS


def field_row(parent, label: str, var, browse_cmd, browse_text="浏览...", width=0):
    """一行「标签 + 输入框 + 浏览按钮」, 返回输入框控件。"""
    row = ttk.Frame(parent, style="Card.TFrame")
    row.pack(fill=tk.X, pady=(0, 10))
    ttk.Label(row, text=label, style="FieldLabel.TLabel").pack(anchor=tk.W)
    line = ttk.Frame(row, style="Card.TFrame")
    line.pack(fill=tk.X, pady=(3, 0))
    entry = ttk.Entry(line, textvariable=var, width=width or 30)
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
    btn = ttk.Button(line, text=browse_text, style="Secondary.TButton",
                     command=browse_cmd, width=7)
    btn.pack(side=tk.RIGHT, padx=(8, 0))
    return entry


def labeled_combo(parent, label, var, values, width=10, readonly=True):
    """「标签 + 下拉框」, 返回下拉框 (横向排列)。"""
    box = ttk.Frame(parent, style="Card.TFrame")
    box.pack(side=tk.LEFT, padx=(0, 14))
    ttk.Label(box, text=label, style="FieldLabel.TLabel").pack(anchor=tk.W)
    combo = ttk.Combobox(box, textvariable=var, values=values, width=width,
                         state="readonly" if readonly else "normal")
    combo.pack(anchor=tk.W, pady=(3, 0))
    return combo


def labeled_entry(parent, label, var, width=8):
    """「标签 + 输入框」, 返回输入框 (横向排列)。"""
    box = ttk.Frame(parent, style="Card.TFrame")
    box.pack(side=tk.LEFT, padx=(0, 14))
    ttk.Label(box, text=label, style="FieldLabel.TLabel").pack(anchor=tk.W)
    entry = ttk.Entry(box, textvariable=var, width=width)
    entry.pack(anchor=tk.W, pady=(3, 0))
    return entry


def combo_cell(parent, row, col, label, var, values, on_change=None):
    """网格布局的下拉单元 (带上方标签), 返回 Combobox。"""
    box = ttk.Frame(parent, style="Card.TFrame")
    box.grid(row=row, column=col, sticky="we", padx=(0, 10), pady=(0, 8))
    ttk.Label(box, text=label, style="FieldLabel.TLabel").pack(anchor=tk.W)
    combo = ttk.Combobox(box, textvariable=var, values=values,
                         state="readonly", width=12)
    combo.pack(fill=tk.X, pady=(3, 0))
    if on_change:
        combo.bind("<<ComboboxSelected>>", lambda e: on_change())
    return combo


def entry_cell(parent, row, col, label, var):
    """网格布局的输入单元 (带上方标签), 返回 Entry。"""
    box = ttk.Frame(parent, style="Card.TFrame")
    box.grid(row=row, column=col, sticky="we", padx=(0, 10), pady=(0, 8))
    ttk.Label(box, text=label, style="FieldLabel.TLabel").pack(anchor=tk.W)
    entry = ttk.Entry(box, textvariable=var)
    entry.pack(fill=tk.X, pady=(3, 0))
    return entry


def scroll_area(parent) -> tuple:
    """可滚动区域: 返回 (canvas, inner_frame)。

    滚轮仅在指针进入该区域时接管, 离开后解除, 避免抢走其他控件的滚动。
    """
    wrap = tk.Frame(parent, bg=COLORS["card"])
    wrap.pack(fill=tk.BOTH, expand=True)
    canvas = tk.Canvas(wrap, bg=COLORS["card"], highlightthickness=0, bd=0)
    vsb = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=canvas.yview)
    inner = ttk.Frame(canvas, style="Card.TFrame")
    win_id = canvas.create_window((0, 0), window=inner, anchor=tk.NW)
    inner.bind("<Configure>",
               lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>",
                lambda e: canvas.itemconfigure(win_id, width=e.width))
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def _wheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # Do not unbind the application's other scroll areas.
    canvas.bind("<MouseWheel>", _wheel)
    def bind_children(widget):
        widget.bind("<MouseWheel>", _wheel, add="+")
        for child in widget.winfo_children():
            bind_children(child)
    inner.bind("<Map>", lambda e: bind_children(inner))
    return canvas, inner


def make_tree(parent, columns, height=24):
    """带双向滚动条的只读表格。

    columns: [(列id, 表头, 宽度, 最小宽度, 对齐), ...]
    用 grid 摆放: 列宽合计大于可视宽度时仍保留双向滚动条, 不挤压内容。
    """
    frame = ttk.Frame(parent, style="Card.TFrame")
    frame.pack(fill=tk.BOTH, expand=True)
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    ids = [c[0] for c in columns]
    tree = ttk.Treeview(frame, columns=ids, show="headings", height=height,
                        selectmode="browse")
    for cid, head, width, minwidth, anchor in columns:
        tree.heading(cid, text=head)
        tree.column(cid, width=width, minwidth=minwidth, anchor=anchor, stretch=False)
    def fit(event):
        total = sum(c[2] for c in columns)
        extra = max(0, event.width-total-4)
        for cid, _head, width, _minimum, _anchor in columns:
            tree.column(cid, width=width + (extra if cid == columns[-1][0] else 0))
    tree.bind("<Configure>", fit, add="+")
    vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
    hsb = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="we")
    return tree

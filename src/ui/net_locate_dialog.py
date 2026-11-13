"""网络定位窗口 (界面二 Evan_DFM 的子窗口).

用途
----
在 Evan_DFM 里按网络名查位置: 选中一个网络, 直接给出
    坐标范围 (X/Y min~max) / 中心 / 外框长宽 / 走线总长 / 线宽 / 层 / 引脚与过孔坐标
并在画布上画出该网络的走线、引脚、过孔和包络框 (mm, 板左下为原点)。

数据口径
--------
网络名来自 .brd 归档 objects/Net.xml; 坐标来自带 NET 列的 SKILL 导出
(evan_pads.csv / evan_holes.csv / evan_traces.csv)。.brd 内嵌快照没有网络归属,
所以没有导出文件时窗口只列网络名, 不编造坐标 —— 见 src/net_locate.py。

本文件只做界面与线程调度, 解析/聚合在 src/net_locate.py。
"""
import os
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from src import net_locate
from src.ui.theme import COLORS, Card, apply_theme, fonts

# 走线/引脚配色 (按层名稳定分配)
_LAYER_COLORS = ("#2563EB", "#DC2626", "#15803D", "#B45309", "#7C3AED",
                 "#0891B2", "#DB2777", "#4B5563")

_MAX_TREE_ROWS = 3000
_MAX_ITEM_ROWS = 2000


def _fmt(value, digits=2, dash="—"):
    if value is None or value == "":
        return dash
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name or "net") or "net"


def _clear_tree(tree) -> None:
    items = tree.get_children()
    if items:
        tree.delete(*items)


class NetLocateDialog:
    """按网络名定位坐标/尺寸的子窗口 (非模态, 可一直开着对照)。"""

    def __init__(self, master, brd_path: str = "", out_dir: str = "", files=None):
        self.brd_path = brd_path or ""
        self.out_dir = out_dir or (os.path.dirname(os.path.abspath(self.brd_path))
                                   if self.brd_path else os.getcwd())
        self.files = [p for p in (files or []) if p]
        self.inv = None
        self.nets = []
        self.nets_by_name = {}
        self.filtered = []
        self.selected = None
        self._alive = True
        self._queue = queue.Queue()
        self._filter_job = None
        self._canvas_job = None
        self._polling = False

        self.win = tk.Toplevel(master)
        self.win.title("网络定位 - 按网络名查坐标 / 位置 / 外框尺寸 (mm)")
        self.win.geometry("1260x820")
        self.win.minsize(1120, 700)
        self.win.configure(bg=COLORS["bg"])
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        self.win.bind("<Destroy>", self._on_destroy, add="+")
        self._fonts = fonts(self.win)
        apply_theme(self.win)
        try:
            self.win.transient(master)
        except tk.TclError:
            pass

        self.status_var = tk.StringVar(value="就绪")
        self.hint_var = tk.StringVar(value="")
        self.filter_var = tk.StringVar(value="")
        self.only_geom = tk.BooleanVar(value=False)
        self.diff_only = tk.BooleanVar(value=False)
        self.show_outline = tk.BooleanVar(value=True)
        self.fit_board = tk.BooleanVar(value=False)
        self.count_var = tk.StringVar(value="")
        self._summary_vars = {}
        self._layer_colors = {}

        self._build()
        self._start_poll()
        if self.brd_path:
            self._start_load()
        else:
            self.status_var.set("没有 BRD 文件: 请先在 Evan_DFM 里导入 .brd")

    # ------------------------------------------------------------ 组装
    def _build(self):
        self._build_header()

        ttk.Label(self.win, textvariable=self.status_var, style="Status.TLabel",
                  anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)

        body = ttk.Frame(self.win, style="TFrame", padding=(16, 12, 16, 10))
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=0, minsize=580)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = Card(body, "网络清单", "网络名来自 .brd；坐标需要带 NET 的 SKILL 导出")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right = Card(body, "选中网络", "坐标 / 位置 / 长宽 (mm) · 下方为图形预览")
        right.grid(row=0, column=1, sticky="nsew")

        self._build_filter(left.content)
        self._build_net_list(left.content)
        self._build_summary(right.content)
        self._build_items(right.content)
        self._build_canvas(right.content)

    def _build_header(self):
        hdr = tk.Frame(self.win, bg=COLORS["header"], height=62)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)

        box = tk.Frame(hdr, bg=COLORS["header"])
        box.pack(side=tk.LEFT, padx=20, pady=9)
        tk.Label(box, text="网络定位", bg=COLORS["header"],
                 fg=COLORS["header_text"], font=self._fonts["title"]).pack(anchor=tk.W)
        tk.Label(box, text="按网络名查坐标 / 位置 / 外框长宽 (mm) · 数据来自 .brd 网络名 + SKILL 导出",
                 bg=COLORS["header"], fg=COLORS["header_muted"],
                 font=self._fonts["subtitle"]).pack(anchor=tk.W, pady=(2, 0))

        btns = tk.Frame(hdr, bg=COLORS["header"])
        btns.pack(side=tk.RIGHT, padx=20)
        ttk.Button(btns, text="导入坐标文件", style="Secondary.TButton",
                   command=self._add_files).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="生成 SKILL 脚本", style="Secondary.TButton",
                   command=self._gen_skill).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="刷新", style="Secondary.TButton",
                   command=self._start_load).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="关闭", style="Secondary.TButton",
                   command=self._close).pack(side=tk.LEFT)

    def _build_filter(self, parent):
        box = ttk.Frame(parent, style="Card.TFrame")
        box.pack(fill=tk.X, pady=(0, 8))

        line1 = ttk.Frame(box, style="Card.TFrame")
        line1.pack(fill=tk.X)
        ttk.Label(line1, text="网络名筛选", style="FieldLabel.TLabel").pack(side=tk.LEFT)
        entry = ttk.Entry(line1, textvariable=self.filter_var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        entry.bind("<KeyRelease>", self._on_filter_key)
        entry.bind("<Return>", lambda e: self._apply_filter())

        line2 = ttk.Frame(box, style="Card.TFrame")
        line2.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(line2, text="MIPI/MIPN", style="Ghost.TButton",
                   command=lambda: self._quick_filter("MIP")).pack(side=tk.LEFT)
        ttk.Checkbutton(line2, text="只看差分对", variable=self.diff_only,
                        style="Card.TCheckbutton",
                        command=self._apply_filter).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(line2, text="只看有坐标", variable=self.only_geom,
                        style="Card.TCheckbutton",
                        command=self._apply_filter).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(line2, text="重置", style="Ghost.TButton",
                   command=self._reset_filter).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(line2, text="数据来源", style="Ghost.TButton",
                   command=self._show_sources).pack(side=tk.RIGHT)
        ttk.Label(box, text="支持 * ? 通配, 如 MIPI* / *D0*；网络名逐个匹配, 不区分大小写",
                  style="CardHint.TLabel", wraplength=520, justify=tk.LEFT).pack(
            anchor=tk.W, pady=(4, 0))

    def _build_net_list(self, parent):
        head = ttk.Frame(parent, style="Card.TFrame")
        head.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(head, textvariable=self.count_var, style="CardHint.TLabel").pack(side=tk.LEFT)
        ttk.Label(head, textvariable=self.hint_var, style="CardHint.TLabel",
                  wraplength=520, justify=tk.LEFT).pack(side=tk.LEFT, padx=(8, 0))

        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill=tk.BOTH, expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        cols = (
            ("name", "网络名", 222, 150, "w"),
            ("class", "分类", 72, 56, "center"),
            ("pins", "引脚", 46, 40, "center"),
            ("vias", "过孔", 46, 40, "center"),
            ("traces", "走线", 46, 40, "center"),
            ("length", "长度mm", 62, 52, "e"),
        )
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in cols],
                                 show="headings", height=12, selectmode="browse")
        for cid, head_text, width, minwidth, anchor in cols:
            self.tree.heading(cid, text=head_text)
            self.tree.column(cid, width=width, minwidth=minwidth, anchor=anchor)
        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.tree.tag_configure("nogeom", foreground=COLORS["faint"])
        self.tree.tag_configure("diff", foreground=COLORS["primary_dark"])
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda e: self._select_preview_tab())

    def _build_summary(self, parent):
        grid = ttk.Frame(parent, style="Card.TFrame")
        grid.pack(fill=tk.X, pady=(0, 4))
        fields = (
            ("name", "网络"), ("class", "分类/阻抗"), ("pair", "差分对"),
            ("layers", "层"), ("range_x", "X 范围"), ("range_y", "Y 范围"),
            ("size", "外框 宽×高"), ("center", "中心"), ("length", "走线总长"),
            ("width", "线宽"), ("counts", "引脚/孔/线"), ("source", "来源"),
        )
        for idx, (key, label) in enumerate(fields):
            row, col = divmod(idx, 3)
            cell = ttk.Frame(grid, style="Card.TFrame")
            cell.grid(row=row, column=col, sticky="we", padx=(0, 10), pady=(0, 3))
            ttk.Label(cell, text=label + ":", style="FieldLabel.TLabel").pack(side=tk.LEFT)
            var = tk.StringVar(value="—")
            ttk.Label(cell, textvariable=var, style="Value.TLabel", anchor=tk.W).pack(
                side=tk.LEFT, padx=(5, 0))
            self._summary_vars[key] = var
        for col in range(3):
            grid.columnconfigure(col, weight=1)

    def _build_items(self, parent):
        bar = ttk.Frame(parent, style="Card.TFrame")
        bar.pack(fill=tk.X, pady=(2, 4))
        ttk.Label(bar, text="逐项坐标", style="CardTitle.TLabel").pack(side=tk.LEFT)
        ttk.Button(bar, text="导出当前网络 CSV", style="Ghost.TButton",
                   command=self._export_csv).pack(side=tk.RIGHT)

        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill=tk.X)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        cols = (
            ("kind", "类型", 58, 48, "center"),
            ("layer", "层/面", 58, 46, "center"),
            ("x", "X(mm)", 68, 54, "e"),
            ("y", "Y(mm)", 68, 54, "e"),
            ("w", "宽(mm)", 58, 46, "e"),
            ("h", "高(mm)", 58, 46, "e"),
            ("length", "长(mm)", 62, 50, "e"),
            ("ref", "位号/引脚", 112, 70, "w"),
            ("pad", "焊盘栈", 74, 56, "w"),
        )
        self.items_tree = ttk.Treeview(frame, columns=[c[0] for c in cols],
                                       show="headings", height=5, selectmode="browse")
        for cid, head_text, width, minwidth, anchor in cols:
            self.items_tree.heading(cid, text=head_text)
            self.items_tree.column(cid, width=width, minwidth=minwidth, anchor=anchor)
        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.items_tree.yview)
        hsb = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.items_tree.xview)
        self.items_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.items_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="we")

    def _build_canvas(self, parent):
        bar = ttk.Frame(parent, style="Card.TFrame")
        bar.pack(fill=tk.X, pady=(6, 4))
        ttk.Label(bar, text="图形预览", style="CardTitle.TLabel").pack(side=tk.LEFT)
        ttk.Checkbutton(bar, text="显示板框", variable=self.show_outline,
                        style="Card.TCheckbutton",
                        command=self._redraw_canvas).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Checkbutton(bar, text="整板视野", variable=self.fit_board,
                        style="Card.TCheckbutton",
                        command=self._redraw_canvas).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(bar, text="单位 mm, Y 向上", style="CardHint.TLabel").pack(
            side=tk.RIGHT, pady=(2, 0))
        self.canvas = tk.Canvas(parent, bg="#FFFFFF", highlightthickness=1,
                                highlightbackground=COLORS["border"], bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

    # ------------------------------------------------------------ 读取
    def _start_load(self):
        if not self.brd_path:
            return
        self.status_var.set("正在读取 .brd 网络与坐标 ...")
        self.hint_var.set("")
        _clear_tree(self.tree)
        _clear_tree(self.items_tree)
        self.count_var.set("")
        threading.Thread(target=self._load_worker,
                         args=(self.brd_path, tuple(self.files)), daemon=True).start()

    def _load_worker(self, brd_path, files):
        try:
            inv = net_locate.load_net_inventory(brd_path, files=files)
            self._queue.put(("ok", inv))
        except Exception as exc:  # noqa: BLE001  解析失败要在界面上说清楚
            self._queue.put(("err", exc))

    def _start_poll(self):
        if not self._alive or self._polling:
            return
        self._polling = True
        try:
            self.win.after(120, self._poll)
        except tk.TclError:
            self._polling = False

    def _poll(self):
        if not self._alive:
            return
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "ok":
                    self._loaded(payload)
                else:
                    self._failed(payload)
        except queue.Empty:
            pass
        except Exception:  # noqa: BLE001  窗口已关闭
            return
        self._polling = False
        self._start_poll()

    def _loaded(self, inv):
        self.inv = inv
        self.nets = inv.get("nets") or []
        self.nets_by_name = {n["name"]: n for n in self.nets}
        self._apply_filter()

        geo = inv.get("geometry_net_count") or 0
        sources = inv.get("sources") or []
        text = f"网络 {len(self.nets)} 个 · 有坐标 {geo} 个 · 坐标文件 {len(sources)} 个"
        if not inv.get("has_geometry"):
            text += " · 缺坐标: 需要带 NET 的 SKILL 导出"
        self.status_var.set(text)
        warnings = inv.get("warnings") or []
        if warnings:
            self.hint_var.set("提示: " + warnings[0])
        else:
            self.hint_var.set("")

        first = next((n for n in self.nets if n.get("has_geometry")), None)
        if first is None:
            first = self.nets[0] if self.nets else None
        if first:
            self.tree.selection_set(first["name"])
            self.tree.focus(first["name"])
            self.tree.see(first["name"])

    def _failed(self, exc):
        self.status_var.set("读取失败")
        self.hint_var.set(str(exc))
        messagebox.showerror("网络定位", f"读取网络/坐标失败:\n{exc}", parent=self.win)

    # ------------------------------------------------------------ 筛选
    def _on_filter_key(self, _event=None):
        if self._filter_job is not None:
            try:
                self.win.after_cancel(self._filter_job)
            except tk.TclError:
                pass
        self._filter_job = self.win.after(220, self._apply_filter)

    def _quick_filter(self, text):
        self.filter_var.set(text)
        self.only_geom.set(False)
        self.diff_only.set(False)
        self._apply_filter()

    def _reset_filter(self):
        self.filter_var.set("")
        self.only_geom.set(False)
        self.diff_only.set(False)
        self._apply_filter()

    def _apply_filter(self):
        self._filter_job = None
        if not self.inv:
            return
        selected = self.selected["name"] if self.selected else ""
        self.filtered = net_locate.find_nets(
            self.inv, self.filter_var.get(), only_geometry=self.only_geom.get(),
            diff_only=self.diff_only.get())
        _clear_tree(self.tree)
        shown = self.filtered[:_MAX_TREE_ROWS]
        for net in shown:
            self._insert_net(net)
        suffix = ""
        if len(self.filtered) > len(shown):
            suffix = f" (只显示前 {_MAX_TREE_ROWS} 个)"
        self.count_var.set(f"显示 {len(shown)} / 共 {len(self.nets)} 个网络{suffix}")

        keep_name = selected if selected in {n["name"] for n in self.filtered} else ""
        if not keep_name and self.filtered:
            keep_name = self.filtered[0]["name"]
        if keep_name and keep_name in self.nets_by_name:
            try:
                self.tree.selection_set(keep_name)
                self.tree.focus(keep_name)
                self.tree.see(keep_name)
            except tk.TclError:
                pass
        elif not self.filtered:
            self._clear_detail()

    def _insert_net(self, net):
        tags = []
        if not net.get("has_geometry"):
            tags.append("nogeom")
        if net.get("pair"):
            tags.append("diff")
        self.tree.insert("", tk.END, iid=net["name"], tags=tuple(tags), values=(
            net["name"], net.get("class") or "—",
            net.get("pins", 0), net.get("vias", 0), net.get("traces", 0),
            _fmt(net.get("trace_length_mm"), 2),
        ))

    # ------------------------------------------------------------ 选中
    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        self.selected = self.nets_by_name.get(sel[0])
        self._render_detail()

    def _select_preview_tab(self):
        if self.selected:
            self._render_detail()

    def _clear_detail(self):
        self.selected = None
        for var in self._summary_vars.values():
            var.set("—")
        _clear_tree(self.items_tree)
        self.canvas.delete("all")

    def _render_detail(self):
        net = self.selected
        if not net:
            self._clear_detail()
            return
        bbox = net.get("bbox") or {}
        center = net.get("center") or {}
        imp = net.get("impedance_ohm")
        class_text = net.get("class") or "—"
        if imp:
            class_text += f" · {imp:g}Ω"
        self._summary_vars["name"].set(net["name"])
        self._summary_vars["class"].set(class_text)
        self._summary_vars["pair"].set(net.get("pair") or "—")
        self._summary_vars["layers"].set(" / ".join(net.get("layers") or []) or "—")
        self._summary_vars["range_x"].set(
            f"{_fmt(bbox.get('x_min'))} ~ {_fmt(bbox.get('x_max'))}" if bbox else "—")
        self._summary_vars["range_y"].set(
            f"{_fmt(bbox.get('y_min'))} ~ {_fmt(bbox.get('y_max'))}" if bbox else "—")
        self._summary_vars["size"].set(
            f"{_fmt(bbox.get('w_mm'))} × {_fmt(bbox.get('h_mm'))}" if bbox else "—")
        self._summary_vars["center"].set(
            f"({_fmt(center.get('x'))}, {_fmt(center.get('y'))})" if center else "—")
        self._summary_vars["length"].set(_fmt(net.get("trace_length_mm")))
        if net.get("width_min_mm") is not None:
            self._summary_vars["width"].set(
                f"{_fmt(net['width_min_mm'], 3)} ~ {_fmt(net['width_max_mm'], 3)}")
        else:
            self._summary_vars["width"].set("—")
        self._summary_vars["counts"].set(
            f"{net.get('pins', 0)} / {net.get('vias', 0)} / {net.get('traces', 0)}")
        sources = (self.inv or {}).get("sources") or []
        self._summary_vars["source"].set(f"{len(sources)} 个文件")

        _clear_tree(self.items_tree)
        items = net.get("items") or []
        for item in items[:_MAX_ITEM_ROWS]:
            kind = item.get("kind", "")
            if kind == "TRACE":
                self.items_tree.insert("", tk.END, values=(
                    "走线", item.get("layer", "—"),
                    _fmt(item.get("x1_mm")), _fmt(item.get("y1_mm")),
                    _fmt(item.get("width_mm"), 3), "",
                    _fmt(item.get("length_mm")), "", ""))
            else:
                ref = item.get("refdes", "")
                if item.get("pin"):
                    ref = f"{ref}.{item['pin']}" if ref else str(item["pin"])
                self.items_tree.insert("", tk.END, values=(
                    "引脚" if kind == "PIN" else "过孔",
                    item.get("layer") or item.get("side") or "—",
                    _fmt(item.get("x_mm")), _fmt(item.get("y_mm")),
                    _fmt(item.get("w_mm")), _fmt(item.get("h_mm")), "",
                    ref, item.get("padstack", "")))
        if len(items) > _MAX_ITEM_ROWS:
            self.items_tree.insert("", tk.END, values=(
                "…", "", "", "", "", "", "",
                f"共 {len(items)} 项, 只显示前 {_MAX_ITEM_ROWS} 项", ""))

        self._redraw_canvas()

    # ------------------------------------------------------------ 画布
    def _on_canvas_resize(self, _event=None):
        if self._canvas_job is not None:
            try:
                self.win.after_cancel(self._canvas_job)
            except tk.TclError:
                pass
        self._canvas_job = self.win.after(120, self._redraw_canvas)

    def _layer_color(self, layer: str) -> str:
        if layer not in self._layer_colors:
            self._layer_colors[layer] = _LAYER_COLORS[
                len(self._layer_colors) % len(_LAYER_COLORS)]
        return self._layer_colors[layer]

    def _redraw_canvas(self):
        self._canvas_job = None
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            return
        canvas.delete("all")
        net = self.selected
        if not net:
            canvas.create_text(16, 16, anchor=tk.NW, text="选中一个网络后在这里看图形",
                               fill=COLORS["muted"], font=self._fonts["ui"])
            return
        bbox = net.get("bbox")
        if not bbox:
            canvas.create_text(16, 16, anchor=tk.NW,
                               text=f"网络 {net['name']} 没有带 NET 的坐标数据\n"
                                    "需要 SKILL 导出 evan_pads.csv / evan_holes.csv / "
                                    "evan_traces.csv",
                               fill=COLORS["warn"], font=self._fonts["ui"], width=520)
            return

        width = max(canvas.winfo_width(), 200)
        height = max(canvas.winfo_height(), 160)
        margin = 34

        board = (self.inv or {}).get("board") or {}
        outline = (board.get("outline") or []) if self.show_outline.get() else []
        wx0, wy0, wx1, wy1 = bbox["x_min"], bbox["y_min"], bbox["x_max"], bbox["y_max"]
        # 默认只框住网络 (小网络才看得见); 勾「整板视野」才缩到整板
        if self.fit_board.get() and outline:
            xs = [p[0] for line in outline for p in line]
            ys = [p[1] for line in outline for p in line]
            if xs and ys:
                wx0, wx1 = min(wx0, min(xs)), max(wx1, max(xs))
                wy0, wy1 = min(wy0, min(ys)), max(wy1, max(ys))
        # 单点网络 (宽或高为 0) 也要有可看的窗口
        span_x = max(wx1 - wx0, 0.2)
        span_y = max(wy1 - wy0, 0.2)
        pad_x = span_x * 0.08
        pad_y = span_y * 0.08
        wx0, wx1 = wx0 - pad_x, wx1 + pad_x
        wy0, wy1 = wy0 - pad_y, wy1 + pad_y
        span_x = wx1 - wx0
        span_y = wy1 - wy0
        scale = min((width - 2 * margin) / span_x, (height - 2 * margin) / span_y)
        if scale <= 0:
            return

        def sx(x):
            return margin + (x - wx0) * scale

        def sy(y):
            return height - margin - (y - wy0) * scale

        if outline:
            for line in outline:
                pts = []
                for x, y in line:
                    pts.extend((sx(x), sy(y)))
                if len(pts) >= 4:
                    canvas.create_line(*pts, fill="#CBD5E1", width=1)

        for item in net.get("items") or []:
            kind = item.get("kind")
            if kind == "TRACE":
                x1, y1 = item.get("x1_mm"), item.get("y1_mm")
                x2, y2 = item.get("x2_mm"), item.get("y2_mm")
                if None in (x1, y1, x2, y2):
                    continue
                thick = max(1, (item.get("width_mm") or 0.1) * scale)
                canvas.create_line(sx(x1), sy(y1), sx(x2), sy(y2),
                                   fill=self._layer_color(item.get("layer") or "—"),
                                   width=thick, capstyle="round")
            elif kind in ("PIN", "VIA"):
                x, y = item.get("x_mm"), item.get("y_mm")
                if x is None or y is None:
                    continue
                if kind == "PIN":
                    w = max((item.get("w_mm") or 0.2) * scale, 3)
                    h = max((item.get("h_mm") or 0.2) * scale, 3)
                    canvas.create_rectangle(sx(x) - w / 2, sy(y) - h / 2,
                                            sx(x) + w / 2, sy(y) + h / 2,
                                            outline="#B91C1C", fill="#F87171")
                else:
                    r = 3.5
                    canvas.create_oval(sx(x) - r, sy(y) - r, sx(x) + r, sy(y) + r,
                                       outline="#15803D", fill="#BBF7D0")

        canvas.create_rectangle(sx(bbox["x_min"]), sy(bbox["y_max"]),
                                sx(bbox["x_max"]), sy(bbox["y_min"]),
                                outline=COLORS["primary"], dash=(5, 3), width=1.4)
        cx = (bbox["x_min"] + bbox["x_max"]) / 2.0
        cy = (bbox["y_min"] + bbox["y_max"]) / 2.0
        canvas.create_line(sx(cx) - 6, sy(cy), sx(cx) + 6, sy(cy), fill=COLORS["primary"])
        canvas.create_line(sx(cx), sy(cy) - 6, sx(cx), sy(cy) + 6, fill=COLORS["primary"])

        canvas.create_text(10, 8, anchor=tk.NW,
                           text=f"{net['name']}  外框 {_fmt(bbox['w_mm'])} × "
                                f"{_fmt(bbox['h_mm'])} mm  中心 ({_fmt(cx)}, {_fmt(cy)})",
                           fill=COLORS["text"], font=self._fonts["ui_bold"])
        canvas.create_text(10, height - 8, anchor=tk.SW,
                           text=f"X {_fmt(wx0)}~{_fmt(wx1)}  Y {_fmt(wy0)}~{_fmt(wy1)} mm"
                                f"   (1 px ≈ {1.0 / scale:.3g} mm)",
                           fill=COLORS["muted"], font=self._fonts["small"])

    # ------------------------------------------------------------ 数据来源
    def _show_sources(self):
        inv = self.inv
        if not inv:
            messagebox.showinfo("数据来源", "还没有读取到数据", parent=self.win)
            return
        lines = [f"网络名来源: {inv.get('name_source', '')}", ""]
        lines.append("坐标文件:")
        if inv.get("sources"):
            for src in inv["sources"]:
                state = f"错误: {src['error']}" if src.get("error") else \
                    f"{src.get('count', 0)} 条 / {src.get('nets', 0)} 个网络"
                lines.append(f"  · {src.get('file', '')} [{src.get('kind', '')}] {state}")
        else:
            lines.append("  (没有找到带 NET 的坐标文件)")
        if inv.get("warnings"):
            lines.append("")
            lines.append("说明 / 警告:")
            lines.extend(f"  · {w}" for w in inv["warnings"])
        board = inv.get("board") or {}
        if board.get("ok"):
            lines.append("")
            lines.append(f"板框参照: {board.get('width_mm')} × {board.get('height_mm')} mm")
        messagebox.showinfo("数据来源", "\n".join(lines), parent=self.win)

    # ------------------------------------------------------------ 动作
    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择带网络坐标的导出文件 (evan_pads / evan_holes / evan_traces)",
            filetypes=[("CSV 导出", "*.csv"), ("所有文件", "*.*")], parent=self.win)
        if not paths:
            return
        for path in paths:
            if path not in self.files:
                self.files.append(path)
        self._start_load()

    def _gen_skill(self):
        from src.allegro_skill import write_bundle

        target = os.path.join(self.out_dir, "allegro_bridge")
        try:
            paths = write_bundle(target)
        except OSError as exc:
            messagebox.showerror("生成失败", f"写脚本失败: {exc}", parent=self.win)
            return
        il = paths["il"].replace("\\", "/")
        out = os.path.abspath(target).replace("\\", "/")
        cmds = f'skill load("{il}")\nevan_pick_all("{out}")'
        try:
            self.win.clipboard_clear()
            self.win.clipboard_append(cmds)
        except tk.TclError:
            pass
        messagebox.showinfo(
            "SKILL 脚本已生成",
            "脚本目录:\n" + target + "\n\n"
            "在 PCB Editor 命令窗口执行 (命令已复制到剪贴板):\n"
            f'  skill load("{il}")\n'
            f'  evan_pick_all("{out}")\n\n'
            "跑完回到本窗口点「刷新」: 导出的 NET 列会带出每个引脚/过孔/走线的网络,\n"
            "才能按网络名定位坐标。",
            parent=self.win)

    def _export_csv(self):
        net = self.selected
        if not net:
            messagebox.showinfo("提示", "请先选中一个网络", parent=self.win)
            return
        path = filedialog.asksaveasfilename(
            title="导出网络坐标",
            defaultextension=".csv",
            initialfile=f"网络_{_safe_filename(net['name'])}.csv",
            filetypes=[("CSV", "*.csv"), ("所有文件", "*.*")], parent=self.win)
        if not path:
            return
        try:
            net_locate.export_net_csv(net, path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc), parent=self.win)
            return
        messagebox.showinfo("导出完成", f"已导出:\n{path}", parent=self.win)

    # ------------------------------------------------------------ 生命周期
    def _on_destroy(self, event=None):
        if event is not None and event.widget is not self.win:
            return
        self._alive = False

    def _close(self):
        self._alive = False
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def open_net_locate(master, brd_path: str = "", out_dir: str = "", files=None):
    """从 Evan_DFM 打开网络定位窗口; 窗口保持非模态, 便于和主界面来回对照。"""
    old = getattr(master, "_net_locate_dialog", None)
    if old is not None and getattr(old, "_alive", False):
        same = os.path.abspath(old.brd_path or "") == os.path.abspath(brd_path or "")
        if same:
            try:
                old.win.deiconify()
                old.win.lift()
                old.win.focus_force()
            except tk.TclError:
                pass
            return old
        old._close()
    dialog = NetLocateDialog(master, brd_path=brd_path, out_dir=out_dir, files=files)
    try:
        master._net_locate_dialog = dialog
    except Exception:  # noqa: BLE001
        pass
    return dialog

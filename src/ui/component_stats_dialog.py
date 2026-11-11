# -*- coding: utf-8 -*-
"""器件统计窗口 (Evan_DFM 子窗口)。

按位号前缀 (R/C/L/D/Q/U/IC/LED/J/SW/F/X/Y/T/B/BT…) 统计器件数量，并给出
每个位号的封装、面别、坐标、引脚数、来源；支持按类别/位号筛选和导出 Excel。

数据口径见 `src/component_stats.py`：位号优先取 `.brd` 的 `objects/Part.xml`，
缺失时退回 `evan_components.csv` / `evan_pads.csv` / Placement 导出；没有数据
就如实提示，不拿丝印文字猜器件。
"""
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from src import component_stats
from src.ui.theme import COLORS, Card, apply_theme, fonts

_MAX_ROWS = 4000
_ALL = "__all__"


def _fmt(value, digits=2, dash="—"):
    if value is None or value == "":
        return dash
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _clear(tree):
    items = tree.get_children()
    if items:
        tree.delete(*items)


def _source_text(comp):
    names = []
    if comp.get("in_partxml"):
        names.append("Part.xml")
    for src in comp.get("sources") or []:
        short = {"元件坐标导出": "坐标", "焊盘导出": "焊盘",
                 "Placement": "Placement"}.get(src, src)
        if short not in names:
            names.append(short)
    return " + ".join(names) or "—"


class ComponentStatsDialog:
    """器件分类统计子窗口 (非模态，可和主界面来回对照)。"""

    def __init__(self, master, brd_path: str = "", out_dir: str = "", files=None):
        self.brd_path = brd_path or ""
        self.out_dir = out_dir or (os.path.dirname(os.path.abspath(self.brd_path))
                                   if self.brd_path else os.getcwd())
        self.files = [p for p in (files or []) if p]
        self.inv = None
        self.category = ""
        self.rows = []
        self._alive = True
        self._queue = queue.Queue()
        self._polling = False
        self._search_job = None

        self.win = tk.Toplevel(master)
        self.win.title("器件统计 - 按位号前缀分类 / 数量 / 封装 / 坐标 (mm)")
        self.win.geometry("1260x800")
        self.win.minsize(1100, 680)
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
        self.count_var = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="")
        self.row_info_var = tk.StringVar(value="")
        self.kpi = {k: tk.StringVar(value="—") for k in
                    ("total", "cats", "top", "bottom", "unknown", "coords", "pins")}

        self._build()
        self._start_poll()
        if self.brd_path or self.files:
            self._start_load()
        else:
            self.status_var.set("没有 BRD 文件: 先在 Evan_DFM 导入 .brd，或点「导入文件」")

    # ------------------------------------------------------------ 组装
    def _build(self):
        self._build_header()
        ttk.Label(self.win, textvariable=self.status_var, style="Status.TLabel",
                  anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)

        body = ttk.Frame(self.win, style="TFrame", padding=(16, 10, 16, 8))
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=0, minsize=440)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(1, weight=1)

        self._build_kpi(body)
        left = Card(body, "分类统计", "点一行筛选右侧明细")
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        right = Card(body, "器件明细", "封装/坐标来自导出文件 · 空值如实显示")
        right.grid(row=1, column=1, sticky="nsew")
        self._build_categories(left.content)
        self._build_details(right.content)

    def _build_header(self):
        hdr = tk.Frame(self.win, bg=COLORS["header"], height=62)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        box = tk.Frame(hdr, bg=COLORS["header"])
        box.pack(side=tk.LEFT, padx=20, pady=9)
        tk.Label(box, text="器件统计", bg=COLORS["header"],
                 fg=COLORS["header_text"], font=self._fonts["title"]).pack(anchor=tk.W)
        tk.Label(box, text="按位号前缀分类 · 数量 / 封装 / 面别 / 坐标 / 引脚",
                 bg=COLORS["header"], fg=COLORS["header_muted"],
                 font=self._fonts["subtitle"]).pack(anchor=tk.W, pady=(2, 0))

        btns = tk.Frame(hdr, bg=COLORS["header"])
        btns.pack(side=tk.RIGHT, padx=20)
        ttk.Button(btns, text="导入文件", style="Secondary.TButton",
                   command=self._add_files).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="生成 SKILL 脚本", style="Secondary.TButton",
                   command=self._gen_skill).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="导出 Excel", style="Secondary.TButton",
                   command=self._export_excel).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="刷新", style="Secondary.TButton",
                   command=self._start_load).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="关闭", style="Secondary.TButton",
                   command=self._close).pack(side=tk.LEFT)

    def _build_kpi(self, parent):
        card = Card(parent, "总览", "位号去重 · 分类按前缀 · 引脚只统计有焊盘导出的器件")
        card.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        grid = card.content
        cells = (
            ("total", "器件总数"), ("cats", "类别"), ("top", "TOP"),
            ("bottom", "BOTTOM"), ("unknown", "面别未知"),
            ("coords", "有坐标"), ("pins", "引脚总数"),
        )
        for idx, (key, label) in enumerate(cells):
            cell = ttk.Frame(grid, style="Card.TFrame")
            cell.grid(row=0, column=idx, sticky="we", padx=(0, 12))
            ttk.Label(cell, text=label, style="CardHint.TLabel").pack(anchor=tk.W)
            ttk.Label(cell, textvariable=self.kpi[key], style="KPI.TLabel").pack(
                anchor=tk.W, pady=(1, 0))
            grid.columnconfigure(idx, weight=1)
        ttk.Button(grid, text="数据说明", style="Ghost.TButton",
                   command=self._show_sources).grid(row=0, column=len(cells),
                                                    sticky="e", padx=(2, 0))

    def _build_categories(self, parent):
        line = ttk.Frame(parent, style="Card.TFrame")
        line.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(line, text="筛选", style="FieldLabel.TLabel").pack(side=tk.LEFT)
        entry = ttk.Entry(line, textvariable=self.search_var, width=18)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 4))
        entry.bind("<KeyRelease>", self._on_search_key)
        entry.bind("<Return>", lambda e: self._apply_filter())
        ttk.Button(line, text="重置", style="Ghost.TButton",
                   command=self._reset_filter).pack(side=tk.RIGHT)

        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill=tk.BOTH, expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        cols = (("cat", "类别", 104, 80, "w"), ("count", "数量", 48, 40, "center"),
                ("share", "占比", 48, 42, "e"), ("examples", "位号示例", 160, 100, "w"))
        self.cat_tree = ttk.Treeview(frame, columns=[c[0] for c in cols],
                                     show="headings", height=14, selectmode="browse")
        for cid, text, width, minwidth, anchor in cols:
            self.cat_tree.heading(cid, text=text)
            self.cat_tree.column(cid, width=width, minwidth=minwidth, anchor=anchor)
        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.cat_tree.yview)
        self.cat_tree.configure(yscrollcommand=vsb.set)
        self.cat_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.cat_tree.bind("<<TreeviewSelect>>", self._on_category)
        self.cat_tree.tag_configure("all", foreground=COLORS["primary_dark"])
        self.cat_tree.tag_configure("unclassified", foreground=COLORS["warn"])

        ttk.Label(parent, text="支持 * ? 通配，如 R* / U*；类别按位号前缀归类，"
                              "未知前缀单独列为「未分类」",
                  style="CardHint.TLabel", wraplength=400, justify=tk.LEFT).pack(
            anchor=tk.W, pady=(5, 0))

    def _build_details(self, parent):
        bar = ttk.Frame(parent, style="Card.TFrame")
        bar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(bar, textvariable=self.count_var, style="CardHint.TLabel").pack(side=tk.LEFT)
        ttk.Label(bar, textvariable=self.hint_var, style="CardHint.TLabel",
                  wraplength=520, justify=tk.LEFT).pack(side=tk.LEFT, padx=(8, 0))

        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill=tk.BOTH, expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        cols = (
            ("refdes", "位号", 72, 56, "w"),
            ("category", "类别", 108, 76, "w"),
            ("package", "封装", 116, 70, "w"),
            ("side", "面别", 50, 42, "center"),
            ("x", "X mm", 66, 50, "e"),
            ("y", "Y mm", 66, 50, "e"),
            ("pins", "引脚", 46, 38, "center"),
            ("sources", "来源", 136, 90, "w"),
        )
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in cols],
                                 show="headings", height=15, selectmode="browse")
        for cid, text, width, minwidth, anchor in cols:
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=width, minwidth=minwidth, anchor=anchor)
        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.tree.tag_configure("est", foreground=COLORS["warn"])
        self.tree.tag_configure("nocoord", foreground=COLORS["faint"])
        self.tree.tag_configure("unclassified", foreground=COLORS["warn"])
        self.tree.bind("<<TreeviewSelect>>", self._on_row)

        ttk.Label(parent, textvariable=self.row_info_var, style="CardHint.TLabel",
                  wraplength=760, justify=tk.LEFT).pack(anchor=tk.W, pady=(4, 0))

    # ------------------------------------------------------------ 读取
    def _start_load(self):
        if not self._alive:
            return
        if not self.brd_path and not self.files:
            self.status_var.set("没有数据来源: 请先导入 .brd 或点「导入文件」")
            return
        self.status_var.set("正在读取器件清单 ...")
        self.hint_var.set("")
        self.inv = None
        _clear(self.cat_tree)
        _clear(self.tree)
        self.rows = []
        for var in self.kpi.values():
            var.set("—")
        threading.Thread(target=self._load_worker,
                         args=(self.brd_path, tuple(self.files)), daemon=True).start()

    def _load_worker(self, brd_path, files):
        try:
            inv = component_stats.load_component_inventory(brd_path, files=files)
            self._queue.put(("ok", inv))
        except Exception as exc:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001
            return
        self._polling = False
        self._start_poll()

    def _loaded(self, inv):
        self.inv = inv
        st = inv.get("stats") or {}
        self.kpi["total"].set(str(st.get("total", 0)))
        self.kpi["cats"].set(f"{st.get('category_count', 0)} 类")
        self.kpi["top"].set(str((st.get("sides") or {}).get("TOP", 0)))
        self.kpi["bottom"].set(str((st.get("sides") or {}).get("BOTTOM", 0)))
        self.kpi["unknown"].set(str((st.get("sides") or {}).get("未知", 0)))
        self.kpi["coords"].set(str(st.get("with_coords", 0)))
        self.kpi["pins"].set(str(st.get("total_pins", 0)))

        if inv.get("ok"):
            text = (f"器件 {st.get('total', 0)} 个 · {st.get('category_count', 0)} 类 · "
                    f"有坐标 {st.get('with_coords', 0)} 个"
                    f"（推算 {st.get('estimated_coords', 0)}）· "
                    f"有封装 {st.get('with_package', 0)} 个")
            if not inv.get("has_partxml"):
                text += " · 无 Part.xml，清单来自导出"
            self.status_var.set(text)
        else:
            self.status_var.set(inv.get("error") or "没有器件数据")
            self.row_info_var.set(inv.get("error") or "")
        warnings = inv.get("warnings") or []
        self.hint_var.set(("提示: " + warnings[0]) if warnings else "")

        self._fill_categories()
        self.category = ""
        self._apply_filter()
        self._select_category_row(_ALL)

    def _failed(self, exc):
        self.status_var.set("读取失败")
        self.hint_var.set(str(exc))
        messagebox.showerror("器件统计", f"读取器件清单失败:\n{exc}", parent=self.win)

    # ------------------------------------------------------------ 分类
    def _fill_categories(self):
        _clear(self.cat_tree)
        st = (self.inv or {}).get("stats") or {}
        total = st.get("total", 0)
        if not total:
            return
        self.cat_tree.insert("", tk.END, iid=_ALL, tags=("all",),
                             values=("全部", total, "100.0", ""))
        for item in st.get("categories") or []:
            tags = ("unclassified",) if item["name"].startswith("未分类") else ()
            self.cat_tree.insert("", tk.END, iid=item["name"], tags=tags, values=(
                item["name"], item["count"], f"{item['share']:.1f}",
                "、".join(item["examples"][:6]) + ("…" if len(item["examples"]) > 6 else ""),
            ))

    def _select_category_row(self, iid):
        try:
            self.cat_tree.selection_set(iid)
            self.cat_tree.focus(iid)
            self.cat_tree.see(iid)
        except tk.TclError:
            pass

    def _on_category(self, _event=None):
        sel = self.cat_tree.selection()
        if not sel:
            return
        iid = sel[0]
        self.category = "" if iid == _ALL else iid
        self._apply_filter()

    # ------------------------------------------------------------ 明细
    def _on_search_key(self, _event=None):
        if self._search_job is not None:
            try:
                self.win.after_cancel(self._search_job)
            except tk.TclError:
                pass
        self._search_job = self.win.after(200, self._apply_filter)

    def _reset_filter(self):
        self.search_var.set("")
        self.category = ""
        self._select_category_row(_ALL)
        self._apply_filter()

    def _apply_filter(self):
        self._search_job = None
        if not self.inv:
            return
        self.rows = component_stats.find_components(
            self.inv, query=self.search_var.get(), category=self.category)
        _clear(self.tree)
        shown = self.rows[:_MAX_ROWS]
        for c in shown:
            tags = []
            if c.get("estimated_coord"):
                tags.append("est")
            elif not c.get("has_coords"):
                tags.append("nocoord")
            if c["category"].startswith("未分类"):
                tags.append("unclassified")
            self.tree.insert("", tk.END, iid=c["refdes"], tags=tuple(tags), values=(
                c["refdes"], c["category"], c.get("package") or "—", c.get("side") or "未知",
                _fmt(c.get("x_mm")), _fmt(c.get("y_mm")),
                c.get("pin_count") if c.get("pin_count") is not None else "—",
                _source_text(c),
            ))
        total = (self.inv.get("stats") or {}).get("total", 0)
        suffix = f"（只显示前 {_MAX_ROWS} 个）" if len(self.rows) > len(shown) else ""
        cat_text = self.category or "全部"
        q = self.search_var.get().strip()
        self.count_var.set(f"{cat_text} · 显示 {len(shown)} / {total} 个{suffix}"
                           + (f" · 搜索「{q}」" if q else ""))
        if not self.inv.get("ok"):
            self.row_info_var.set(self.inv.get("error") or "")
        else:
            self.row_info_var.set("")
        if shown:
            first = shown[0]["refdes"]
            try:
                self.tree.selection_set(first)
                self.tree.focus(first)
                self.tree.see(first)
            except tk.TclError:
                pass
        elif self.inv.get("ok"):
            self.row_info_var.set("没有匹配的器件；清空搜索或点「重置」看全部。")

    def _on_row(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            self.row_info_var.set("")
            return
        comp = next((c for c in self.rows if c["refdes"] == sel[0]), None)
        if not comp:
            return
        parts = [comp["refdes"], comp["category"], comp.get("package") or "封装未知",
                 comp.get("side") or "面别未知",
                 f"X {_fmt(comp.get('x_mm'))} / Y {_fmt(comp.get('y_mm'))}",
                 f"{comp.get('pin_count')} 脚" if comp.get("pin_count") is not None else "引脚未知"]
        if comp.get("estimated_coord"):
            parts.append("坐标=焊盘中心推算")
        if comp.get("coord_source"):
            parts.append("坐标来源: " + comp["coord_source"])
        if comp.get("padstacks"):
            parts.append("焊盘栈: " + "、".join(comp["padstacks"][:4]))
        parts.append("来源: " + _source_text(comp))
        self.row_info_var.set(" · ".join(parts))

    # ------------------------------------------------------------ 动作
    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择器件导出文件 (evan_components / evan_pads / place_txt)",
            filetypes=[("CSV / 文本", "*.csv *.txt"), ("所有文件", "*.*")],
            parent=self.win)
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
            "跑完回到本窗口点「刷新」:\n"
            "  • evan_components.csv 提供位号 / X / Y / 面别\n"
            "  • evan_pads.csv 提供每个器件的引脚数\n"
            "  • 封装名可由 Placement 导出 (place_txt.txt) 补充",
            parent=self.win)

    def _export_excel(self):
        inv = self.inv
        if not inv or not inv.get("ok"):
            messagebox.showinfo("提示", "还没有可导出的器件数据", parent=self.win)
            return
        try:
            os.makedirs(self.out_dir, exist_ok=True)
        except OSError:
            pass
        path = filedialog.asksaveasfilename(
            title="导出器件统计",
            defaultextension=".xlsx",
            initialdir=self.out_dir if os.path.isdir(self.out_dir) else None,
            initialfile="器件统计.xlsx",
            filetypes=[("Excel 工作簿", "*.xlsx"), ("CSV", "*.csv"),
                       ("所有文件", "*.*")], parent=self.win)
        if not path:
            return
        try:
            if path.lower().endswith(".csv"):
                out = component_stats.export_component_csv(inv, path)
                sheets = ""
            else:
                out = component_stats.export_component_excel(inv, path)
                sheets = "\n页签: 汇总 / 分类 / 明细"
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc), parent=self.win)
            return
        messagebox.showinfo("导出完成", f"已导出:\n{out}{sheets}", parent=self.win)

    def _show_sources(self):
        inv = self.inv
        if not inv:
            messagebox.showinfo("数据说明", "还没有读取到数据", parent=self.win)
            return
        st = inv.get("stats") or {}
        lines = [f"BRD: {self.brd_path or '(未指定)'}",
                 f"Part.xml: {'有' if inv.get('has_partxml') else '没有'}",
                 "", "数据来源:"]
        if inv.get("sources"):
            for src in inv["sources"]:
                lines.append(f"  · {src.get('file', '')} [{src.get('kind', '')}] "
                             f"{src.get('count', 0)} 条")
        else:
            lines.append("  (无)")
        lines += ["", f"器件 {st.get('total', 0)} 个 / {st.get('category_count', 0)} 类；"
                      f"有坐标 {st.get('with_coords', 0)} 个"
                      f"（焊盘推算 {st.get('estimated_coords', 0)} 个）；"
                      f"引脚 {st.get('total_pins', 0)} 个"
                      f"（{st.get('pins_known', 0)} 个器件有引脚数据）"]
        if st.get("export_only"):
            lines.append(f"只在导出文件、不在 Part.xml: {len(st['export_only'])} 个 ("
                         + "、".join(st["export_only"][:10]) + ")")
        if st.get("part_only"):
            lines.append(f"只在 Part.xml、导出文件里没有: {len(st['part_only'])} 个 ("
                         + "、".join(st["part_only"][:10]) + ")")
        if inv.get("warnings"):
            lines += ["", "说明 / 警告:"]
            lines.extend(f"  · {w}" for w in inv["warnings"])
        lines += ["", "口径: 器件数按位号去重；类别按位号前缀；"
                      "Part.xml 只有位号，Value/MPN 需 BOM/ERP 数据。"]
        messagebox.showinfo("数据说明", "\n".join(lines), parent=self.win)

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


def open_component_stats(master, brd_path: str = "", out_dir: str = "", files=None):
    """打开器件统计窗口；同一 BRD 复用，换 BRD 则关旧开新。"""
    old = getattr(master, "_component_stats_dialog", None)
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
    dialog = ComponentStatsDialog(master, brd_path=brd_path, out_dir=out_dir, files=files)
    try:
        master._component_stats_dialog = dialog
    except Exception:  # noqa: BLE001
        pass
    return dialog

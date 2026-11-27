"""DFM 对话框右栏: 分析结果表格.

一个页签: 制程能力比对  结果 / 来源 / 类别 / 详情 (PASS / WARN / FAIL / INFO 彩色)

职责只有"把结果画出来", 判定逻辑在 src/dfm_engine.py。
"""
import tkinter as tk
from tkinter import ttk

from src.ui.theme import COLORS
from src.ui.widgets import make_tree
from src.ui.i18n import translate
from src.ui.responsive import FlowBar
from src.ui.stackup_view import StackupView


class ResultPanel(ttk.Frame):
    def __init__(self, master, on_locate=None, on_open_point=None, on_point_selected=None, gerber_mode=False):
        super().__init__(master, style="Card.TFrame")
        self.gerber_mode = gerber_mode
        self._on_locate = on_locate
        self._render_id = None
        self._generation = 0
        self._locations = []
        self._items = []
        self._components = []
        self._component_ids = []
        self._component_render_id = None
        self._on_open_point = on_open_point
        self._on_point_selected = on_point_selected
        self._rows = []
        self._page = 0
        self._location_page = 0
        self.page_size = 100
        self.bind("<Destroy>", self._destroyed, add="+")

        self.result_var = tk.StringVar(value="尚未执行分析")
        summary=ttk.Label(self,textvariable=self.result_var,style="Card.TLabel",justify=tk.LEFT)
        summary.pack(fill=tk.X,pady=(0,8))
        self.verdict_details = ""
        ttk.Button(self, text="判定依据", command=self._show_verdict_details).pack(anchor=tk.E, pady=(0, 4))

        if on_locate is not None:
            bar = ttk.Frame(self, style="Card.TFrame")
            bar.pack(fill=tk.X, pady=(0, 6))
            ttk.Button(bar, text="坐标定位", style="Secondary.TButton",
                       command=on_locate).pack(side=tk.LEFT)
            ttk.Label(bar, text="  不符合项 -> 数量 + 元件/孔位坐标",
                      style="CardHint.TLabel").pack(side=tk.LEFT, pady=(2, 0))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True)

        tab1 = ttk.Frame(self.nb, style="Card.TFrame", padding=(4, 8, 4, 4))
        self.nb.add(tab1, text="制程能力比对")
        stackup_tab = ttk.Frame(self.nb, style="Card.TFrame", padding=(6, 6))
        self.nb.add(stackup_tab, text="叠构剖面")
        self.stackup_view = StackupView(stackup_tab)
        self.stackup_view.pack(fill=tk.BOTH, expand=True)
        filters = ttk.Frame(tab1, style="Card.TFrame")
        filters.pack(fill=tk.X, pady=(0, 6))
        self.result_filter = tk.StringVar(value="不符合 / 待复核")
        box = ttk.Combobox(filters, textvariable=self.result_filter, values=("不符合 / 待复核", "全部", "通过", "信息 / 缺数据"),
                           state="readonly", width=18)
        box.pack(side=tk.LEFT)
        self.page_var = tk.StringVar(value="尚无结果")
        ttk.Label(filters, textvariable=self.page_var, style="CardHint.TLabel").pack(side=tk.LEFT, padx=8)
        ttk.Button(filters, text="下一页", command=lambda: self._change_page(1), width=6).pack(side=tk.RIGHT)
        ttk.Button(filters, text="上一页", command=lambda: self._change_page(-1), width=6).pack(side=tk.RIGHT, padx=4)
        self.result_filter.trace_add("write", self._filter_changed)

        self.tree = make_tree(tab1, (
            ("status", "结果", 66, 66, "center"),
            ("source", "来源", 52, 52, "center"),
            ("category", "类别", 86, 86, "center"),
            ("message", "详情", 360, 180, "w"),
        ), height=10, compact_detail=True)
        self.tree.tag_configure("pass", foreground=COLORS["success"])
        self.tree.tag_configure("warn_limit", foreground=COLORS["warn"])
        self.tree.tag_configure("fail", foreground=COLORS["danger"])
        self.tree.tag_configure("info", foreground=COLORS["muted"])
        self.tree.bind("<Double-1>", self._locate_capability)
        tab2 = ttk.Frame(self.nb, style="Card.TFrame", padding=6)
        self.nb.add(tab2, text="问题 X/Y 坐标")
        bar = FlowBar(tab2, style="Card.TFrame")
        bar.pack(fill=tk.X, pady=(0, 6))
        self.open_point_button = ttk.Button(bar, text="Gerber 问题预览" if gerber_mode else "打开 BRD 定位", style="Primary.TButton",
                                            command=self._open_selected, state=tk.DISABLED)
        bar.add(self.open_point_button)
        bar.add(ttk.Button(bar,text="上一个点",command=lambda:self._step(-1)))
        bar.add(ttk.Button(bar,text="下一个点",command=lambda:self._step(1)))
        self.copy_location_button = ttk.Button(bar,text="复制问题坐标" if gerber_mode else "复制坐标命令",command=self._copy_location)
        bar.add(self.copy_location_button)
        self.location_hint = ttk.Label(tab2, text="坐标为 Gerber 原点的毫米值；双击打开底片与编号问题预览。" if gerber_mode else "双击有坐标的问题点，在 Allegro 打开 BRD 并缩放到该点。",
                   style="CardHint.TLabel")
        self.location_hint.pack(anchor=tk.W, pady=(0, 6))
        paging = ttk.Frame(tab2, style="Card.TFrame")
        paging.pack(fill=tk.X, pady=(0, 4))
        self.location_page_var = tk.StringVar(value="尚无位置")
        ttk.Label(paging, textvariable=self.location_page_var, style="CardHint.TLabel").pack(side=tk.LEFT)
        ttk.Button(paging, text="下一页", command=lambda: self._change_location_page(1), width=6).pack(side=tk.RIGHT)
        ttk.Button(paging, text="上一页", command=lambda: self._change_location_page(-1), width=6).pack(side=tk.RIGHT, padx=4)
        self.location_tree = make_tree(tab2, (
            ("item", "检查项", 160, 100, "w"),
            ("x", "X(BRD)", 95, 70, "w"), ("y", "Y(BRD)", 95, 70, "w"),
            ("unit", "坐标单位", 70, 60, "w"), ("net", "网络/焊盘", 130, 80, "w"),
            ("layer", "丝印/对象层", 210, 120, "w"),
            ("note", "定位状态/说明", 350, 150, "w"),
        ), height=10, compact_detail=True)
        self.location_tree.bind("<Double-1>", self._open_selected)
        self.location_tree.bind("<<TreeviewSelect>>", self._selection_changed)
        if gerber_mode:
            self.location_tree.heading("x", text="X(mm)")
            self.location_tree.heading("y", text="Y(mm)")
        tab3 = ttk.Frame(self.nb, style="Card.TFrame", padding=6)
        self.nb.add(tab3, text="BRD 器件 X/Y")
        bar = FlowBar(tab3, style="Card.TFrame")
        bar.pack(fill=tk.X, pady=(0, 6))
        bar.add(ttk.Label(bar,text="位号筛选：",style="Card.TLabel"))
        self.component_filter = tk.StringVar()
        bar.add(ttk.Entry(bar,textvariable=self.component_filter,width=16))
        self.open_component_button = ttk.Button(bar, text="定位器件", command=self._open_component,
                                                state=tk.DISABLED)
        bar.add(self.open_component_button)
        bar.add(ttk.Button(bar,text="复制 X/Y",command=self._copy_component))
        self.component_var = tk.StringVar(value="尚未提取器件坐标；点「打开 BRD」自动提取")
        ttk.Label(tab3, textvariable=self.component_var, style="CardHint.TLabel",
                  wraplength=650).pack(fill=tk.X, pady=(0, 6))
        self.component_tree = make_tree(tab3, (
            ("refdes", "位号", 85, 60, "w"),
            ("x_mm", "X(mm)", 95, 65, "w"), ("y_mm", "Y(mm)", 95, 65, "w"),
            ("x_brd", "X(BRD)", 95, 65, "w"), ("y_brd", "Y(BRD)", 95, 65, "w"),
            ("unit", "BRD单位", 75, 60, "w"), ("side", "面", 80, 50, "w"),
            ("rotation", "旋转", 70, 50, "w"), ("source", "来源", 230, 100, "w"),
        ))
        self.component_tree.bind("<Double-1>", self._open_component)
        self.component_tree.bind("<<TreeviewSelect>>", self._component_selected)
        self.component_filter.trace_add("write", lambda *_: self._render_components())
        geometry_tab = ttk.Frame(self.nb, style="Card.TFrame", padding=6)
        self.nb.add(geometry_tab, text="几何覆盖与缺数据")
        self.geometry_var = tk.StringVar(value="尚未执行实际几何检查")
        ttk.Label(geometry_tab, textvariable=self.geometry_var, style="CardHint.TLabel",
                  wraplength=650).pack(fill=tk.X, pady=(0, 6))
        self.geometry_tree = make_tree(geometry_tab, (
            ("rule", "规则", 200, 120, "w"), ("layer", "图层", 110, 70, "w"),
            ("status", "覆盖状态", 100, 70, "w"), ("need", "所需资料 / 能力", 380, 180, "w"),
            ("note", "范围 / 未完成原因", 380, 180, "w")))
        if gerber_mode:
            self.nb.hide(tab3)

    def set_input_mode(self, gerber_mode):
        if self.gerber_mode == gerber_mode:
            return
        self.gerber_mode = gerber_mode
        self.open_point_button.configure(text="Gerber 问题预览" if gerber_mode else "打开 BRD 定位")
        self.copy_location_button.configure(text="复制问题坐标" if gerber_mode else "复制坐标命令")
        for key, label in (("x", "X"), ("y", "Y")):
            self.location_tree.heading(key, text=label + ("(mm)" if gerber_mode else "(BRD)"))
        self.location_hint.configure(text="Gerber 原点毫米坐标；双击打开问题预览。" if gerber_mode else
                                     "BRD 定位仅使用已验证对齐的坐标；未对齐时查看 Gerber 预览。")
        if gerber_mode:
            self.nb.hide(2)
        else:
            self.nb.add(self.nb.tabs()[2])

    def show_components(self, coordinates, note=""):
        self._components = list(coordinates)
        self._component_note = note
        self._render_components()
        self.nb.tab(2, text=f"BRD 器件 X/Y ({len(self._components)})")

    def _render_components(self):
        if self._component_render_id is not None:
            self.after_cancel(self._component_render_id)
            self._component_render_id = None
        self.component_tree.delete(*self.component_tree.get_children())
        self.open_component_button.configure(state=tk.DISABLED)
        query = self.component_filter.get().strip().upper()
        self._component_ids = [i for i, c in enumerate(self._components) if query in c.get("refdes", "").upper()]
        self.component_var.set(f"器件原点 {len(self._components)} 个，当前显示 {len(self._component_ids)} 个；"
                               + (getattr(self, "_component_note", "") or "双击器件可在 Allegro 定位"))

        def batch(start=0):
            self._component_render_id = None
            for index in self._component_ids[start:start + 100]:
                c = self._components[index]
                display = dict(c, side=c.get("side") or "未获取",
                               rotation=c.get("rotation") if c.get("rotation") not in (None, "") else "未获取")
                self.component_tree.insert("", tk.END, iid=str(index), values=tuple(display.get(k, "") for k in
                    ("refdes", "x_mm", "y_mm", "x_brd", "y_brd", "brd_unit", "side", "rotation", "source")))
            if start + 100 < len(self._component_ids):
                self._component_render_id = self.after(1, lambda: batch(start + 100))
        batch()

    def selected_component(self):
        selected = self.component_tree.selection()
        if not selected:
            return None
        index = int(selected[0])
        return self._components[index] if index < len(self._components) else None

    def _component_selected(self, _event=None):
        component = self.selected_component()
        self.open_component_button.configure(state=tk.NORMAL if component else tk.DISABLED)
        if component and self._on_point_selected:
            self._on_point_selected(component)

    def _open_component(self, _event=None):
        component = self.selected_component()
        if component and self._on_open_point:
            self._on_open_point(component)

    def _copy_component(self):
        component = self.selected_component()
        if component:
            text = component.get("command") or f"X(mm)={component['x_mm']} Y(mm)={component['y_mm']}"
            self.clipboard_clear()
            self.clipboard_append(text)
            self.component_var.set(component["refdes"] + " 已复制：" + text)

    def selected_point(self):
        selected = self.location_tree.selection()
        if not selected:
            return None
        index = int(selected[0])
        return self._locations[index] if index < len(self._locations) else None

    def refresh_colors(self):
        for status, key in (("pass", "success"), ("fail", "danger"), ("warn_limit", "warn"), ("info", "muted")):
            self.tree.tag_configure(status, foreground=COLORS[key])

    def _selection_changed(self, _event=None):
        point = self.selected_point()
        has_xy = point is not None and point.get("x_mm") is not None and point.get("y_mm") is not None
        self.open_point_button.configure(state=tk.NORMAL if has_xy else tk.DISABLED)
        if has_xy and self._on_point_selected:
            self._on_point_selected(point)

    def _open_selected(self, _event=None):
        point = self.selected_point()
        if point and self._on_open_point:
            self._on_open_point(point)

    def _step(self, direction):
        ids = [str(i) for i, point in enumerate(self._locations)
               if point.get("x_mm") is not None]
        if not ids:
            return
        selected = self.location_tree.selection()
        current = ids.index(selected[0]) if selected and selected[0] in ids else (-1 if direction > 0 else 0)
        target = ids[(current + direction) % len(ids)]
        page = int(target) // self.page_size
        if page != self._location_page:
            self._location_page = page
            self._render_location_page()
        self.location_tree.selection_set(target)
        self.location_tree.see(target)

    def _locate_capability(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        try:
            index = int(selected[0])
        except ValueError:
            return
        if index >= len(self._items):
            return
        item = self._items[index]
        if item.get("status") not in ("fail", "warn_limit"):
            return
        matches = [str(i) for i, row in enumerate(self._locations)
                    if row.get("item") == item.get("item")]
        if matches:
            self._location_page = int(matches[0]) // self.page_size
            self._render_location_page()
            self.nb.select(1)
            self.location_tree.selection_set(matches[0])
            self.location_tree.see(matches[0])
            point = self.selected_point()
            if point.get("x_mm") is not None:
                self._open_selected()
            else:
                self.result_var.set(point.get("note", "该项需要对象级坐标"))

    def _copy_location(self, _event=None):
        selected = self.location_tree.selection()
        if not selected:
            return
        row = self.selected_point()
        if row is None:
            return
        text = (f"X(mm)={row.get('x_mm')} Y(mm)={row.get('y_mm')} Layer={row.get('layer', '')}"
                if self.gerber_mode else row.get("command") or row.get("note", ""))
        self.clipboard_clear()
        self.clipboard_append(text)
        self.result_var.set("已复制：" + text)

    # ------------------------------------------------------------ 渲染
    def _destroyed(self, event):
        if event.widget is self:
            if self._render_id is not None:
                self.after_cancel(self._render_id)
                self._render_id = None
            self._generation += 1
            if self._component_render_id is not None:
                self.after_cancel(self._component_render_id)
                self._component_render_id = None

    def clear(self, clear_components=True):
        self._generation += 1
        if self._render_id is not None:
            self.after_cancel(self._render_id)
            self._render_id = None
        self.tree.delete(*self.tree.get_children())
        self.location_tree.delete(*self.location_tree.get_children())
        self.geometry_tree.delete(*self.geometry_tree.get_children())
        for tree in (self.tree, self.location_tree, self.geometry_tree):
            tree._clear_detail()
        self.geometry_var.set("尚未执行实际几何检查")
        self._locations = []
        self._items = []
        self._rows = []
        self.verdict_details = ""
        self.page_var.set("尚无结果")
        self.location_page_var.set("尚无位置")
        self.open_point_button.configure(state=tk.DISABLED)
        if clear_components:
            self.show_components([], "未获取器件坐标；执行 PCB 分析后后台提取，不打开 Editor")

    def render_capability(self, result: dict) -> None:
        """制程能力比对表: 逐项结果 + 覆盖缺口。"""
        self.clear(clear_components=False)
        self.show_components(result.get("component_coordinates", []))
        generation = self._generation
        geometry_rows = result.get("geometry_coverage", [])
        details = list(result.get("verdict_reasons") or []) + list(result.get("gerber_errors") or [])
        self.verdict_details = "\n\n".join(dict.fromkeys(details)) or "没有额外判定依据；请核对逐项结果和覆盖缺口。"
        self.result_var.set(f"{result.get('verdict', '需复核')} · 不符合 {result.get('error_count', 0)} · 待复核 {result.get('warning_count', 0)}\n"
                            f"参数覆盖 {(result.get('coverage') or {}).get('ratio', 0):.0%} · 位置 {len(result.get('problem_locations', []))} · 覆盖缺口见页签")
        if result.get("geometry_executed"):
            summary = result.get("geometry_coverage_summary", {})
            self.geometry_var.set(f"实际几何已执行 · 已测量 {summary.get('measured', 0)} / "
                                  f"部分覆盖 {summary.get('partial', 0)} / 缺数据 {summary.get('missing', 0)} / "
                                  f"未完成 {summary.get('incomplete', 0)}；不等于完整 DFM 放行")
        rows = []

        icon = {"pass": "PASS", "warn_limit": "WARN", "fail": "FAIL", "info": "INFO"}
        if result.get("inspection_states"):
            geometry_rows = [dict(rule=row["check_key"], layer=" / ".join(scope.get("scope", "") for scope in row.get("scopes", [])),
                                 status=row["phase_label"] + " / " + row["verdict_label"], required_data="见逐项证据；部分不升级为全板通过",
                                 note="；".join(row.get("sources", []))) for row in result["inspection_states"]]
        for it in result["items"]:
            msg = it["message"]
            if it.get("note"):
                msg += f"  [{it['note']}]"
            if it.get("source_detail"):
                msg += f"  (证据出处: {it['source_detail']})"
            rows.append(((icon[it["status"]], it.get("source", "手工"),
                          it["category"], msg), it["status"]))
            from src.ui import i18n
            if i18n.LANGUAGE == "en":
                english = {"pass":"Within capability", "warn_limit":"Near limit / review",
                           "fail":"Outside capability", "info":"Information / missing data"}[it["status"]]
                msg = f"{it.get('field') or translate(it.get('item',''))}: {it.get('design')} {it.get('unit','')} / " \
                      f"capability {it.get('capability')} {it.get('unit','')}; {english}. " + translate(it.get("note", ""))
                rows[-1] = ((icon[it["status"]], translate(it.get("source", "手工")), translate(it["category"]), msg), it["status"])

        coverage = result.get("coverage") or {}
        if coverage.get("missing_items"):
            rows.append(((
                "INFO", "—", "覆盖率",
                f"缺数据未参与判定: {'、'.join(coverage['missing_items'])}"), "info"))
        # Verdict reasons belong to coverage/details, not repeated issue rows.

        assumed = result.get("assumed_fields") or []
        brd_count = result.get("brd_item_count") or 0
        if assumed:
            rows.append(((
                "INFO", "—", "未确认",
                f"表单里的示例值未确认, 已跳过判定: {'、'.join(assumed[:12])}"
                + (" 等" if len(assumed) > 12 else "")), "info"))
        if self.gerber_mode:
            rows.append((("INFO", "Gerber", "数据来源", "直接读取 Gerber / NC Drill 几何；无 BRD 数据，不生成 Allegro 跳转命令。"), "info"))
        else:
            rows.append(((
                "INFO", "BRD", "数据来源",
                f"本次判定中有 {brd_count} 项用到了 .brd 里读到的值; 其余为人工确认值"
                if brd_count else "本次判定全部使用人工值, 未用到 BRD 数据"), "info"))
        self._locations = result.get("problem_locations", [])
        self._items = result["items"]
        position_checks = result.get("position_checks", [])
        if position_checks:
            failures = sum(p.get("status") == "fail" for p in position_checks)
            rows.append((("FAIL" if failures else "WARN", "Allegro", "丝印位置",
                          f"位置检查：不符合 {failures} / 待复核 {len(position_checks)-failures}；详见问题 X/Y 坐标"),
                         "fail" if failures else "warn_limit"))
        position_meta = result.get("position_geometry", {})
        if position_meta.get("silkscreen_objects"):
            rows.append((("INFO", "Allegro", "丝印几何",
                         f"已读丝印 {position_meta['silkscreen_objects']} 个 / 板框 {position_meta.get('outline_objects',0)} 段；"
                         "文字、圆弧包围框和焊盘近距为候选复核，未解析真实阻焊开窗"), "info"))
        # Parsing/scan gaps already have authoritative geometry coverage rows.
        if result.get("gerber_layers"):
            rows.append((("INFO", "Gerber", "图形来源",
                          f"已读取 {len(result['gerber_layers'])} 个 Gerber 图层；问题预览可查看真实图形"), "info"))
        if result.get("gerber_export_warnings"):
            rows.append((("INFO", "Cadence", "导出日志", "Artwork 导出有警告，请查看 Gerber 目录内 artwork_export.log"), "info"))

        self._rows = rows
        self._page = 0
        self._location_page = 0
        self._render_result_page()
        self._render_location_page()
        def batch(start=0):
            self._render_id = None
            if generation != self._generation:
                return
            for row in geometry_rows[start:start + 100]:
                self.geometry_tree.insert("", tk.END, values=tuple(row.get(k, "") for k in
                    ("rule", "layer", "status", "required_data", "note")))
            if start + 100 < len(geometry_rows):
                self._render_id = self.after(1, lambda: batch(start + 100))
        batch()

    def _filtered_rows(self):
        allowed = {"不符合 / 待复核": {"fail", "warn_limit"}, "通过": {"pass"}, "信息 / 缺数据": {"info"}}.get(self.result_filter.get())
        return [(index, values, tag) for index, (values, tag) in enumerate(self._rows) if allowed is None or tag in allowed]

    def _show_verdict_details(self):
        dialog = tk.Toplevel(self)
        dialog.withdraw()
        dialog.title("判定依据 / 数据缺口")
        dialog.geometry("700x450")
        text = tk.Text(dialog, wrap="word", bg=COLORS["card"], fg=COLORS["text"], relief="flat", padx=12, pady=12)
        scroll = ttk.Scrollbar(dialog, command=text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        text.configure(yscrollcommand=scroll.set)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert("1.0", self.verdict_details or "尚未执行分析")
        text.configure(state=tk.DISABLED)
        dialog.update_idletasks()
        dialog.deiconify()

    def _filter_changed(self, *_):
        self._page = 0
        self._render_result_page()

    def _change_page(self, delta):
        self._page = max(0, min(self._page + delta, max(0, (len(self._filtered_rows()) - 1) // self.page_size)))
        self._render_result_page()

    def _render_result_page(self):
        self.tree.delete(*self.tree.get_children())
        self.tree._clear_detail()
        rows = self._filtered_rows()
        start = self._page * self.page_size
        for index, values, tag in rows[start:start + self.page_size]:
            self.tree.insert("", tk.END, iid=str(index), values=tuple(translate(v) for v in values), tags=(tag,))
        pages = max(1, (len(rows) + self.page_size - 1) // self.page_size)
        self.page_var.set(f"{len(rows)} 项 · {self._page + 1}/{pages} 页")

    def _change_location_page(self, delta):
        self._location_page = max(0, min(self._location_page + delta, max(0, (len(self._locations) - 1) // self.page_size)))
        self._render_location_page()

    def _render_location_page(self):
        self.location_tree.delete(*self.location_tree.get_children())
        self.location_tree._clear_detail()
        self.open_point_button.configure(state=tk.DISABLED)
        start = self._location_page * self.page_size
        for index in range(start, min(start + self.page_size, len(self._locations))):
            point = self._locations[index]
            self.location_tree.insert("", tk.END, iid=str(index), values=(translate(point["item"]),
                point.get("x_mm" if self.gerber_mode else "x_brd", ""),
                point.get("y_mm" if self.gerber_mode else "y_brd", ""), "mm" if self.gerber_mode else point.get("brd_unit", ""),
                point.get("net") or point.get("padstack", ""), point.get("layer", ""),
                translate(point.get("location_status", "") + "；" + point.get("note", ""))))
        pages = max(1, (len(self._locations) + self.page_size - 1) // self.page_size)
        self.location_page_var.set(f"{len(self._locations)} 个位置 · {self._location_page + 1}/{pages} 页")

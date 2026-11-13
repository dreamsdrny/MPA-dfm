"""DFM 对话框右栏: 分析结果表格.

一个页签: 制程能力比对  结果 / 来源 / 类别 / 详情 (PASS / WARN / FAIL / INFO 彩色)

职责只有"把结果画出来", 判定逻辑在 src/dfm_engine.py。
"""
import tkinter as tk
from tkinter import ttk

from src.ui.theme import COLORS
from src.ui.widgets import make_tree
from src.ui.i18n import translate


class ResultPanel(ttk.Frame):
    def __init__(self, master, on_locate=None, on_open_point=None, on_point_selected=None):
        super().__init__(master, style="Card.TFrame")
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
        self.bind("<Destroy>", self._destroyed, add="+")

        self.result_var = tk.StringVar(value="尚未执行分析")
        ttk.Label(self, textvariable=self.result_var, style="Card.TLabel",
                  wraplength=640, justify=tk.LEFT).pack(fill=tk.X, pady=(0, 8))

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

        self.tree = make_tree(tab1, (
            ("status", "结果", 66, 66, "center"),
            ("source", "来源", 52, 52, "center"),
            ("category", "类别", 86, 86, "center"),
            ("message", "详情", 500, 500, "w"),
        ))
        self.tree.tag_configure("pass", foreground=COLORS["success"])
        self.tree.tag_configure("warn_limit", foreground=COLORS["warn"])
        self.tree.tag_configure("fail", foreground=COLORS["danger"])
        self.tree.tag_configure("info", foreground=COLORS["muted"])
        self.tree.bind("<Double-1>", self._locate_capability)
        tab2 = ttk.Frame(self.nb, style="Card.TFrame", padding=6)
        self.nb.add(tab2, text="问题 X/Y 坐标")
        bar = ttk.Frame(tab2, style="Card.TFrame")
        bar.pack(fill=tk.X, pady=(0, 6))
        self.open_point_button = ttk.Button(bar, text="打开 BRD 定位", style="Primary.TButton",
                                            command=self._open_selected, state=tk.DISABLED)
        self.open_point_button.pack(side=tk.LEFT)
        ttk.Button(bar, text="上一个点", command=lambda: self._step(-1)).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="下一个点", command=lambda: self._step(1)).pack(side=tk.LEFT)
        ttk.Button(bar, text="复制坐标命令", command=self._copy_location).pack(side=tk.LEFT, padx=4)
        ttk.Label(tab2, text="双击有坐标的问题点，在 Allegro 打开 BRD 并缩放到该点。",
                  style="CardHint.TLabel").pack(anchor=tk.W, pady=(0, 6))
        self.location_tree = make_tree(tab2, (
            ("item", "检查项", 160, 100, "w"),
            ("x", "X(BRD)", 95, 70, "w"), ("y", "Y(BRD)", 95, 70, "w"),
            ("unit", "坐标单位", 70, 60, "w"), ("net", "网络/焊盘", 130, 80, "w"),
            ("layer", "丝印/对象层", 210, 120, "w"),
            ("note", "定位状态/说明", 350, 150, "w"),
        ))
        self.location_tree.bind("<Double-1>", self._open_selected)
        self.location_tree.bind("<<TreeviewSelect>>", self._selection_changed)
        tab3 = ttk.Frame(self.nb, style="Card.TFrame", padding=6)
        self.nb.add(tab3, text="BRD 器件 X/Y")
        bar = ttk.Frame(tab3, style="Card.TFrame")
        bar.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(bar, text="位号筛选：", style="Card.TLabel").pack(side=tk.LEFT)
        self.component_filter = tk.StringVar()
        ttk.Entry(bar, textvariable=self.component_filter, width=16).pack(side=tk.LEFT)
        self.open_component_button = ttk.Button(bar, text="定位器件", command=self._open_component,
                                                state=tk.DISABLED)
        self.open_component_button.pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="复制 X/Y", command=self._copy_component).pack(side=tk.LEFT)
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
                self.component_tree.insert("", tk.END, iid=str(index), values=tuple(c.get(k, "") for k in
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
               if point.get("x_mm") is not None and self.location_tree.exists(str(i))]
        if not ids:
            return
        selected = self.location_tree.selection()
        current = ids.index(selected[0]) if selected and selected[0] in ids else (-1 if direction > 0 else 0)
        target = ids[(current + direction) % len(ids)]
        self.location_tree.selection_set(target)
        self.location_tree.see(target)

    def _locate_capability(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        index = self.tree.index(selected[0])
        if index >= len(self._items):
            return
        item = self._items[index]
        if item.get("status") not in ("fail", "warn_limit"):
            return
        matches = [str(i) for i, row in enumerate(self._locations)
                   if row.get("item") == item.get("item") and self.location_tree.exists(str(i))]
        if matches:
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
        text = row.get("command") or row.get("note", "")
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
        self._locations = []
        self._items = []
        self.open_point_button.configure(state=tk.DISABLED)
        if clear_components:
            self.show_components([], "尚未提取器件坐标；点「打开 BRD」自动提取")

    def render_capability(self, result: dict) -> None:
        """制程能力比对表: 逐项结果 + 覆盖缺口。"""
        self.clear(clear_components=False)
        self.show_components(result.get("component_coordinates", []))
        generation = self._generation
        rows = []

        icon = {"pass": "PASS", "warn_limit": "WARN", "fail": "FAIL", "info": "INFO"}
        for it in result["items"]:
            msg = it["message"]
            if it.get("note"):
                msg += f"  [{it['note']}]"
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

        assumed = result.get("assumed_fields") or []
        brd_count = result.get("brd_item_count") or 0
        if assumed:
            rows.append(((
                "INFO", "—", "未确认",
                f"表单里的示例值未确认, 已跳过判定: {'、'.join(assumed[:12])}"
                + (" 等" if len(assumed) > 12 else "")), "info"))
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
        for error in result.get("gerber_errors", []):
            rows.append((("INFO", "Gerber", "缺失/解析", error), "info"))
        if result.get("gerber_layers"):
            rows.append((("INFO", "Gerber", "图形来源",
                          f"已读取 {len(result['gerber_layers'])} 个 Gerber 图层；问题预览可查看真实图形"), "info"))
        if result.get("gerber_export_warnings"):
            rows.append((("INFO", "Cadence", "导出日志", "Artwork 导出有警告，请查看 Gerber 目录内 artwork_export.log"), "info"))

        def batch(start=0):
            self._render_id = None
            if generation != self._generation:
                return
            for values, tag in rows[start:start + 100]:
                self.tree.insert("", tk.END, values=tuple(translate(v) for v in values), tags=(tag,))
            for index in range(start, min(start + 100, len(self._locations))):
                point = self._locations[index]
                self.location_tree.insert("", tk.END, iid=str(index), values=(
                    translate(point["item"]), point.get("x_brd", ""), point.get("y_brd", ""),
                    point.get("brd_unit", ""), point.get("net") or point.get("padstack", ""),
                    point.get("layer", ""),
                    translate(point.get("location_status", "") + "；" + point.get("note", ""))))
            if start + 100 < max(len(rows), len(self._locations)):
                self._render_id = self.after(1, lambda: batch(start + 100))
        batch()

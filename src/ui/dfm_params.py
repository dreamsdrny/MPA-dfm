"""DFM 对话框左栏: 设计参数表单.

职责:
  - 画 BRD 行 (浏览 / 带入) 与板厂、板型、能力等级、铜厚等选项
  - 画出设计参数 (按类别分带, 可滚动), 每行都标出取值来源
  - 从 .brd 自动带入能读到的值; 这些行默认**隐藏**(不占界面), 判定时照样使用,
    需要核对/覆盖时勾选「显示 BRD 带入项」即可展开
  - 只把"人工才能给"的项摆在界面上: 板框、阻焊、丝印、拼板、覆盖膜等
  - 收集当前表单值 -> analyze_design() 需要的 design 字典;
    只有「BRD 读到的」和「人工确认过的」值参与判定, 示例值不算
不含分析逻辑, 也不直接弹窗。

来源标签:
    BRD   绿色, 从 .brd 约束对象/钻孔表/焊盘栈读出来的真实值
    手工  蓝色, 用户自己填或确认过的值
    示例  橙色, 表单预填值, 用户没动过 -> 不参与判定
    待填  灰色, BRD 也没有、用户也没填
"""
import tkinter as tk
import math
from tkinter import ttk, filedialog, messagebox

from src.application.pcb_inputs import (BRD_COMPONENT_CAP, CATEGORY_HINTS, DFM_FIELDS,
                            FIELD_META, brd_design_values, brd_extra_values, layer_label,
                            surface_finish_choices)
from src.ui.theme import COLORS
from src.ui.widgets import combo_cell, entry_cell, scroll_area
from src.ui.responsive import FlowBar

# 来源标签的显示文本与颜色
_ORIGIN_TAG = {
    "brd": ("BRD", COLORS["success"]),
    "manual": ("手工", COLORS["primary"]),
    "default": ("示例", COLORS["warn"]),
    "empty": ("待填", COLORS["faint"]),
}


class ParamPanel(ttk.Frame):
    def __init__(self, master, on_browse, on_load, font_bold=None, unified=False):
        super().__init__(master, style="Card.TFrame")
        self._on_browse = on_browse
        self._on_load = on_load
        self.via_process_unknown = []
        self._font_bold = font_bold or ("TkDefaultFont", 9, "bold")
        self._font_tag = ("TkDefaultFont", 8)
        self._mute = False          # 程序写值时抑制"用户编辑"判定
        self._board_available = False
        self.unified = unified
        self.stackup_sources = []

        # ---- 表单变量 ----
        self.brd_path = tk.StringVar(value="")
        self.board_name = tk.StringVar(value="")
        self.factory = tk.StringVar(value="hongban")
        self.board_type = tk.StringVar(value="rigid")
        self.level = tk.StringVar(value="standard")
        self.copper_um = tk.StringVar(value="")
        self.copper_origin = "empty"
        self._info = {}
        self._automatic_update = False
        self.copper_confirmations = {}
        self.copper_um.trace_add("write", self._copper_edited)
        self.surface_finish = tk.StringVar(value="")
        self.polarity_var = tk.BooleanVar(value=False)
        self.panelized_var = tk.BooleanVar(value=False)
        self._confirmed_flags = set()
        self.polarity_var.trace_add("write", lambda *_: self._confirmed_flags.add("polarity_marked"))
        self.panelized_var.trace_add("write", lambda *_: self._confirmed_flags.add("panelized"))
        self.brd_status_var = tk.StringVar(value="")
        self.count_var = tk.StringVar(value="")

        self.entries = {}
        self.origins = {}
        self.sources = {}
        self.via_kind_unknown = []
        self.components = {}
        self.holes = {}
        self.layers = []
        self.stackup = []
        self.pad_metrics = {}
        self.silkscreen = {}
        self.silkscreen_to_outline = {}
        self.pads = {}
        self.test_points = {}
        self.silk_to_component = {}
        self.tags = {}
        self.surface_combo = None
        self.level_combo = None
        self._build()
        self._refresh_counter()
        self.set_board_available(False)

    # ------------------------------------------------------------ 构建
    def _build(self):
        if not self.unified:
            ttk.Label(self, text="BRD 文件", style="FieldLabel.TLabel").pack(anchor=tk.W)
            line = FlowBar(self, style="Card.TFrame")
            line.pack(fill=tk.X, pady=(3, 0))
            line.add(ttk.Entry(line,textvariable=self.brd_path,width=18))
            line.add(ttk.Button(line,text="浏览",style="Secondary.TButton",command=self._on_browse))
            line.add(ttk.Button(line,text="从 BRD 读取",style="Secondary.TButton",command=self._on_load))
        # 导入状态常驻顶部；成功导入后才展开设计参数，空白板不能分析。
        self.brd_status_label = ttk.Label(self, textvariable=self.brd_status_var,
                                          style="CardHint.TLabel",
                                          wraplength=340, justify=tk.LEFT)
        self.brd_status_label.pack(anchor=tk.W, pady=(6, 0))
        self._brd_status_packed = True
        if self.unified:
            # The unified workbench displays source status once in its source panel.
            self.brd_status_label.pack_forget()
            self._brd_status_packed = False

        self.empty_hint = ttk.Label(self, style="CardHint.TLabel", wraplength=330, justify=tk.LEFT,
            text="请在上方导入 BRD 或 Gerber 文件 / 目录 / 压缩包。\n"
                 "两类资料共用能力设置和分析结果；Gerber 可以不依赖 BRD 单独分析。")
        self.design_frame = ttk.Frame(self, style="Card.TFrame")
        self.sections = ttk.Notebook(self.design_frame)
        self.sections.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        basic = ttk.Frame(self.sections, style="Card.TFrame", padding=8)
        stack = ttk.Frame(self.sections, style="Card.TFrame", padding=8)
        manual = ttk.Frame(self.sections, style="Card.TFrame", padding=8)
        self.sections.add(basic, text="分析条件")
        self.sections.add(stack, text="设计信息")
        self.sections.add(manual, text="待补充")
        section_stack = ttk.Frame(self.sections, style="Card.TFrame", padding=(8, 6))
        self.sections.insert(2, section_stack, text="叠构核对")
        opts = ttk.Frame(basic, style="Card.TFrame")
        opts.pack(fill=tk.X)
        for col in (0, 1):
            opts.columnconfigure(col, weight=1)
        combo_cell(opts, 0, 0, "板厂", self.factory, ["hongban", "sihui"],
                   self.sync_levels)
        combo_cell(opts, 0, 1, "板类型", self.board_type, ["rigid", "flex"],
                   self.sync_levels)
        self.level_combo = combo_cell(opts, 1, 0, "能力等级", self.level,
                                      ["standard", "automotive", "extreme"], None)
        ttk.Label(opts, text="点击执行后后台提取 BRD 坐标/叠构，不打开 PCB Editor。",
                  style="CardHint.TLabel", wraplength=280).grid(row=2, column=0, columnspan=2, sticky="ew", pady=8)

        self.supplement_options = ttk.Frame(manual, style="Card.TFrame")
        self.supplement_options.pack(fill=tk.X, pady=(0, 8))
        self.copper_label = ttk.Label(self.supplement_options, text="外层成品铜厚 μm（未获取时补充）")
        self.copper_entry = ttk.Entry(self.supplement_options, textvariable=self.copper_um, width=12)
        self.copper_label.pack(anchor="w")
        self.copper_entry.pack(fill=tk.X)
        surface_host = ttk.Frame(self.supplement_options, style="Card.TFrame")
        surface_host.pack(fill=tk.X)
        self.surface_combo = combo_cell(surface_host, 0, 0, "表面处理", self.surface_finish,
                                        surface_finish_choices(), None)

        flags = ttk.Frame(manual, style="Card.TFrame")
        flags.pack(fill=tk.X, pady=(8, 0))
        ttk.Checkbutton(flags, text="极性已标注", variable=self.polarity_var,
                        style="Card.TCheckbutton").pack(side=tk.LEFT)
        ttk.Checkbutton(flags, text="已拼板", variable=self.panelized_var,
                        style="Card.TCheckbutton").pack(side=tk.LEFT, padx=(14, 0))
        ttk.Label(manual, style="CardHint.TLabel", wraplength=300, justify=tk.LEFT,
                  text="仅补充未获取资料；未填写不使用默认值。极性/拼板按实际资料确认。"
                       ).pack(fill=tk.X, pady=(4, 0))
        self.sync_levels()
        self.stackup_button = ttk.Button(manual, text="导入板厂确认叠构 JSON",
                                         command=self._import_stackup)
        self.stackup_button.pack(fill=tk.X, pady=(0, 6))
        self.design_summary = tk.StringVar(value="未获取设计数据")
        ttk.Label(stack, textvariable=self.design_summary, style="CardHint.TLabel", wraplength=300).pack(fill=tk.X)
        self.design_tree = ttk.Treeview(stack, columns=("field", "value", "source"), show="headings", height=8)
        for key, title, width in (("field", "设计信息", 100), ("value", "值", 75), ("source", "来源 / 类型", 140)):
            self.design_tree.heading(key, text=title)
            self.design_tree.column(key, width=width, minwidth=50)
        self.design_tree.pack(fill=tk.BOTH, expand=True, pady=6)
        design_scroll = ttk.Scrollbar(stack, orient=tk.VERTICAL, command=self.design_tree.yview)
        design_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.design_tree.configure(yscrollcommand=design_scroll.set)
        ttk.Label(stack, style="CardHint.TLabel", wraplength=300,
                    text="铜厚优先读取 BRD Cross-section；设计叠构值不等于实测成品铜厚。内层缺失不套用外层。"
                    ).pack(fill=tk.X)
        # 叠构单独占一页：剖面图在右侧结果区显示，这里保留逐层核对与铜厚确认。
        self.stackup_var = tk.StringVar(value="逐层叠构待补充")
        ttk.Label(section_stack, textvariable=self.stackup_var, style="CardHint.TLabel", wraplength=330).pack(
            fill=tk.X, pady=(0, 4))
        # 剖面控件由结果区创建（那里才有完整宽度），参数区继续拥有叠构数据与铜厚确认。
        self.stackup_view = None
        table_host = ttk.Frame(section_stack, style="Card.TFrame")
        table_host.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        # 左栏窄：表只留核对必需列；成品确认值与采用列同源不重复，检查范围由外/内层前缀表达。
        self.stackup_tree = ttk.Treeview(table_host, columns=("layer", "design", "copper", "source"),
                                        show="headings", height=5)
        for key, label, width in (("layer", "铜层", 96), ("design", "BRD设计 μm", 76),
                                 ("copper", "采用 μm", 68), ("source", "来源 / 确认依据", 152)):
            self.stackup_tree.heading(key, text=label)
            self.stackup_tree.column(key, width=width, minwidth=45)
        self.stackup_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.stackup_tree.bind("<<TreeviewSelect>>", self._select_stackup_table)
        scrollbar = ttk.Scrollbar(table_host, orient=tk.VERTICAL, command=self.stackup_tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.stackup_tree.configure(yscrollcommand=scrollbar.set)
        buttons = ttk.Frame(section_stack, style="Card.TFrame")
        buttons.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(buttons, text="确认 / 覆盖所选层成品铜厚", command=self._confirm_layer_copper).pack(side=tk.LEFT)
        ttk.Button(buttons, text="恢复所选层 BRD 设计铜厚", command=self._restore_layer_copper).pack(side=tk.LEFT, padx=6)
        ttk.Label(section_stack, style="CardHint.TLabel", wraplength=330, justify=tk.LEFT,
                  text="剖面图见右侧「叠构剖面」页；介质/阻焊厚度未给时只画示意带，数值仍为未获取，不参与判定。"
                  ).pack(fill=tk.X, pady=(4, 0))

        head = ttk.Frame(manual, style="Card.TFrame")
        head.pack(fill=tk.X)
        ttk.Label(head, textvariable=self.count_var, style="CardHint.TLabel",
                  wraplength=300, justify=tk.LEFT).pack(side=tk.LEFT)
        self.show_brd_var = tk.BooleanVar(value=False)
        self.show_brd_btn = ttk.Button(head, text="查看自动读取数据", command=lambda: self.sections.select(1))
        self._show_brd_shown = False
        self._brd_toggled = False   # 用户手动点过开关后, 不再自动展开
        self.confirm_btn = ttk.Button(head, text="确认全部示例值", style="Ghost.TButton",
                                      width=13, command=self.confirm_all)
        # 只有表单里存在预填值(示例)时才显示, 平时不占地方
        self._confirm_shown = False

        _, inner = scroll_area(manual)
        self._host = ttk.Frame(inner, style="Card.TFrame")
        self._host.grid(row=0, column=0, sticky="nsew")
        inner.columnconfigure(0, weight=1)

        for _cat, key, _label, default in DFM_FIELDS:
            var = tk.StringVar(value=default)
            var.trace_add("write", lambda *_a, k=key: self._on_edit(k))
            self.entries[key] = var
            self.origins[key] = "default" if default else "empty"
        self._render_fields()

    def set_board_available(self, available):
        self._board_available = bool(available)
        self.stackup_button.configure(state=tk.NORMAL if available else tk.DISABLED)
        if available or self.unified:
            self.empty_hint.pack_forget()
            if not self.design_frame.winfo_manager():
                self.design_frame.pack(fill=tk.BOTH, expand=True)
            self._refresh_stackup()
        else:
            self.design_frame.pack_forget()
            if not self.empty_hint.winfo_manager():
                self.empty_hint.pack(fill=tk.X, pady=(12, 0))

    def _copper_edited(self, *_):
        if not self._automatic_update:
            self.copper_origin = "manual" if self.copper_um.get().strip() else "empty"
            if not self.stackup_sources:
                for row in self.stackup:
                    if row["index"] in (1, len(self.stackup)):
                        row.setdefault("design_copper_um", row.get("copper_um"))
                        row.pop("copper_um", None)

    def _refresh_design_info(self):
        if not hasattr(self, "design_tree"):
            return
        self.design_tree.delete(*self.design_tree.get_children())
        names = [row.get("name") if isinstance(row, dict) else str(row) for row in self.layers]
        summary = [self.board_name.get() or "板名未获取", "层名：" + (" / ".join(n for n in names if n) or "未获取")]
        native = self._info.get("native_extraction", {})
        summary.append("器件坐标：" + (str(len(native.get("components", []))) if native else "未获取（执行分析后后台提取）"))
        self.design_summary.set("\n".join(summary))
        objects = native.get("objects") or {}
        if objects:
            self.design_tree.insert("", "end", values=("原生对象", " / ".join(f"{name}:{len(objects.get(name, []))}" for name in ("pins", "pads", "vias", "holes", "traces")),
                                                       "extracta；完整性见状态"))
        self.design_tree.insert("", "end", values=("设计单位", self._info.get("units") or native.get("unit") or "未获取", "BRD"))
        self.design_tree.insert("", "end", values=("外层铜厚 μm", self.copper_um.get() or "未获取 / 按层查看",
                                                  "BRD 叠构设计值" if self.copper_origin == "brd" else "人工确认" if self.copper_origin == "manual" else "未获取"))
        for _cat, key, label, _default in DFM_FIELDS:
            value = self.entries[key].get().strip()
            origin = self.origins.get(key)
            source = (self.sources.get(key) or {}).get("source", "")
            if origin == "brd":
                kind = "设计约束（非实测）" if "CSet" in source or "约束" in source else "自动读取"
            elif origin == "manual":
                kind = "人工确认"
            else:
                kind = "未获取"
            self.design_tree.insert("", "end", values=(label, value if origin in ("brd", "manual") and value else "未获取", kind))
        self.copper_label.configure(text="外层成品铜厚 μm（可覆盖；逐层确认优先）")

    def apply_native(self, payload):
        """Worker results are applied on Tk thread without overwriting manual data."""
        self._info["native_extraction"] = payload
        if not self.stackup_sources:
            self.stackup = []
            if self.copper_origin == "brd":
                self._automatic_update = True
                self.copper_um.set("")
                self._automatic_update = False
                self.copper_origin = "empty"
        if payload.get("unit"):
            self._info["units"] = payload["unit"]
        for key, value in payload.get("updates", {}).items():
            if key in self.entries and self.origins.get(key) != "manual":
                self._set(key, value)
                self.set_origin(key, "brd", {"source": "BRD extracta " + key, "confidence": "high"})
            elif key == "layers":
                self.layers = list(value)
            elif key == "stackup" and not self.stackup_sources:
                self.stackup = [dict(row) for row in value]
            elif key == "copper_um" and self.copper_origin != "manual" and not self.stackup_sources:
                self._automatic_update = True
                self.copper_um.set("" if value is None else f"{value:g}")
                self._automatic_update = False
                self.copper_origin = "brd" if value is not None else "empty"
        if "comp_count" in payload.get("updates", {}):
            refs = [row["refdes"] for row in payload["components"]]
            self.components = dict(ok=True, count=len(refs), refdes=refs, source="BRD extracta SYMBOL")
        if self.copper_origin == "manual" and not self.stackup_sources:
            for row in self.stackup:
                if row["index"] in (1, len(self.stackup)):
                    row.setdefault("design_copper_um", row.get("copper_um"))
                    row.pop("copper_um", None)
        self._render_fields()
        self._refresh_counter()
        self._refresh_stackup()

    def _confirm_layer_copper(self):
        from tkinter import simpledialog
        from datetime import datetime
        selection = self.stackup_tree.selection()
        if not selection:
            messagebox.showinfo("铜厚确认", "请先在叠构表中选择铜层", parent=self)
            return
        # 铜层来自派生行的 iid，不从显示文字反解；“外/内”前缀因此可以安全变化。
        row = next((r for r in getattr(self, "_stackup_rows", []) if str(r["index"]) == selection[0]), None)
        if row is None:
            messagebox.showinfo("铜厚确认", "请先执行后台提取或导入叠构", parent=self)
            return
        value = simpledialog.askfloat("成品铜厚", f"{row['name']}：板厂确认成品铜厚 μm\nBRD设计值：{row.get('design_copper_um', row.get('copper_um'))}", parent=self, minvalue=.001)
        if value is None:
            return
        if not math.isfinite(value):
            messagebox.showerror("铜厚确认", "铜厚必须为有限正数", parent=self)
            return
        source = simpledialog.askstring("确认依据", "填写板厂/工程师及确认文件或备注（必填）", parent=self)
        if not source or not source.strip():
            return
        self.copper_confirmations[row["name"]] = dict(copper_um=value, source=source.strip(), confirmed_at=datetime.now().isoformat(timespec="seconds"))
        self._refresh_stackup()
        self._automatic_update = True
        self.copper_um.set(self.copper_um.get())
        self._automatic_update = False

    def _restore_layer_copper(self):
        selection = self.stackup_tree.selection()
        if selection:
            row = next((r for r in getattr(self, "_stackup_rows", []) if str(r["index"]) == selection[0]), None)
            if row:
                self.copper_confirmations.pop(row["name"], None)
                self._automatic_update = True
                self.copper_um.set(self.copper_um.get())
                self._automatic_update = False
                self._refresh_stackup()

    def show_measurements(self, result):
        """Measured object summaries stay separate from design-rule inputs."""
        self._refresh_design_info()
        for row in result.get("gerber_copper_layer_checks", []):
            self.design_tree.insert("", "end", values=(row["layer"] + " 几何扫描",
                "已扫描" if row.get("checked") else "未获取/缺铜厚", "实际铜图形；覆盖见结果"))
        for row in result.get("gerber_mask_bridge_checks", []):
            limit = row.get("capability")
            self.design_tree.insert("", "end", values=(row["layer"] + " 阻焊桥能力",
                f"≥{limit:g} mm" if limit is not None else "未获取", "按该面铜厚选档"))
            self.design_tree.insert("", "end", values=(row["layer"] + " 阻焊开窗扫描",
                str(row.get("opening_count", "未获取")) if row.get("checked") else "未获取/未判定", "实际 Gerber；非全板合格证明"))
        self.design_tree.insert("", "end", values=("提取日志", (result.get("native_extraction") or {}).get("directory", "未获取"), "后台 extracta"))

    def _refresh_stackup(self):
        from src.application.pcb_inputs import copper_layers
        try:
            rows = copper_layers(dict(stackup=self.stackup, layers=self.layers,
                layer_count=self.entries["layer_count"].get().strip()), self.copper_um.get().strip() or None)
            for row in rows:
                confirmed = self.copper_confirmations.get(row["name"])
                if confirmed:
                    row.update(design_copper_um=row.get("design_copper_um", row.get("copper_um")),
                               copper_um=confirmed["copper_um"], confirmed=True,
                               source=confirmed["source"] + " / " + confirmed["confirmed_at"] + "（成品已确认）")
        except (ValueError, TypeError):
            self.stackup_var.set("逐层叠构/铜厚输入无效，请核对")
            self.stackup_tree.delete(*self.stackup_tree.get_children())
            self._stackup_signature = None
            self._stackup_rows = []
            return
        self._stackup_rows = rows
        missing = sum(row["copper_um"] is None for row in rows)
        self.stackup_var.set(f"共 {len(rows)} 铜层 · 缺铜厚 {missing} 层 · 阻焊仅首尾外层"
                            if rows else "尚无逐层叠构：请导入板厂确认的铜层与铜厚")
        preview_rows = [(("外 " if row["outer"] else "内 ") + layer_label(row["index"], row["name"]),
                "未获取" if row.get("design_copper_um", row.get("copper_um")) is None else f"{row.get('design_copper_um', row.get('copper_um')):g}",
                "未获取" if row["copper_um"] is None else f"{row['copper_um']:g}",
                row["source"]) for row in rows]
        # 剖面还依赖板厚/表面处理/孔跨度；只看逐层表会漏掉这些变化。
        thickness = self.entries["board_thickness"].get().strip() or None
        signature = (preview_rows, thickness, self.surface_finish.get(),
                     tuple(sorted((str(k), str(v)) for k, v in (self.holes or {}).items()
                                  if k.endswith("span_count"))))
        if signature == getattr(self, "_stackup_signature", None):
            return
        self._stackup_signature = signature
        if self.stackup_view is not None:
            self.stackup_view.set_data(rows, board_thickness_mm=thickness,
                                       surface_finish=self.surface_finish.get(), holes=self.holes)
        for status, color in (("brd", COLORS["primary"]), ("confirmed", "#D9822B"), ("missing", COLORS["faint"])):
            self.stackup_tree.tag_configure(status, foreground=color)
        # 确认/覆盖后重建行会丢选择；保留原选择，用户才能连续核对多层。
        selected = self.stackup_tree.selection()
        self.stackup_tree.delete(*self.stackup_tree.get_children())
        for row, values in zip(rows, preview_rows):
            status = "confirmed" if row["name"] in self.copper_confirmations else ("brd" if row["copper_um"] is not None else "missing")
            self.stackup_tree.insert("", tk.END, values=values, tags=(status,), iid=str(row["index"]))
        if selected and self.stackup_tree.exists(selected[0]):
            self.stackup_tree.selection_set(selected[0])
            self.stackup_tree.see(selected[0])
        if self.stackup_view is not None:
            self.stackup_view.refresh()

    def _select_stackup_layer(self, band):
        item = str(band["index"])
        if self.stackup_tree.exists(item):
            self.stackup_tree.selection_set(item); self.stackup_tree.see(item)

    def _select_stackup_table(self, _event=None):
        if self.stackup_tree.selection() and self.stackup_view is not None:
            self.stackup_view.select(int(self.stackup_tree.selection()[0]))

    def bind_stackup_view(self, view):
        """结果区注册剖面控件后重建当前叠构，避免两页显示不一致。"""
        self.stackup_view = view
        view.on_select = self._select_stackup_layer
        self._stackup_signature = None
        self._refresh_stackup()

    def _set_brd_status(self, text: str) -> None:
        """导入引导/读取问题提示；就绪状态由工作台顶部常驻显示。"""
        self.brd_status_var.set(text or "")
        if self.unified:
            return
        if text and not self._brd_status_packed:
            self.brd_status_label.pack(fill=tk.X, pady=(0, 4))
            self._brd_status_packed = True
        elif not text and self._brd_status_packed:
            self.brd_status_label.pack_forget()
            self._brd_status_packed = False

    # ------------------------------------------------------------ 参数行渲染
    def _visible(self, key: str) -> bool:
        """BRD 读到的行默认不显示 (判定仍用它), 勾选后展开。"""
        return self.origins.get(key) != "brd"

    def _render_fields(self) -> None:
        """按当前来源重画参数行; 变量保存在 self.entries 里, 重画不丢值。"""
        for child in self._host.winfo_children():
            child.destroy()
        self.tags.clear()
        current = None
        row = 0
        for cat, key, label, _default in DFM_FIELDS:
            if not self._visible(key):
                continue
            if cat != current:
                current = cat
                band = tk.Frame(self._host, bg=COLORS["row_alt"])
                band.grid(row=row, column=0, columnspan=3, sticky="we", pady=(10, 3))
                text = cat if not CATEGORY_HINTS.get(cat) else \
                    f"{cat}　·　{CATEGORY_HINTS[cat]}"
                tk.Label(band, text="  " + text, bg=COLORS["row_alt"],
                         fg=COLORS["primary_dark"],
                         font=self._font_bold, anchor=tk.W).pack(fill=tk.X, ipady=3)
                row += 1
            ttk.Label(self._host, text=label, style="Card.TLabel",wraplength=210).grid(
                row=row, column=0, sticky=tk.W, pady=1)
            ttk.Entry(self._host, textvariable=self.entries[key], width=9).grid(
                row=row, column=1, sticky=tk.E, padx=(8, 4), pady=1)
            tag = tk.Label(self._host, text="", font=self._font_tag, anchor=tk.W,
                           bg=COLORS["card"], width=4)
            tag.grid(row=row, column=2, sticky=tk.W, pady=1)
            self.tags[key] = tag
            self._paint_tag(key)
            row += 1
        self._host.columnconfigure(0, weight=1)

    def _toggle_show_brd(self) -> None:
        self._brd_toggled = True
        self._render_fields()
        self._refresh_counter()

    def reveal_brd(self) -> None:
        """执行分析后自动展开 BRD 带入项。

        判定本来就用了这些 BRD 值, 但默认藏在表单里; 分析完直接把
        「显示 BRD 带入项」按钮点亮并展开, 让人一眼看到这次判定引用了什么。
        用户手动关掉过开关的话尊重用户选择, 不再自动打开。
        """
        brd = sum(1 for origin in self.origins.values() if origin == "brd")
        if not brd:
            return
        if not self._show_brd_shown:
            self.show_brd_btn.pack(side=tk.RIGHT, padx=(6, 0))
            self._show_brd_shown = True
        if not self.unified and not self.show_brd_var.get() and not self._brd_toggled:
            self.show_brd_var.set(True)
            self._render_fields()
        self._refresh_counter()

    # ------------------------------------------------------------ 来源标签
    def _paint_tag(self, key: str) -> None:
        tag = self.tags.get(key)
        if tag is None:
            return
        origin = self.origins.get(key, "empty")
        text = _ORIGIN_TAG.get(origin, _ORIGIN_TAG["empty"])[0]
        color = COLORS[{"brd":"success", "manual":"primary", "default":"warn", "empty":"faint"}.get(origin,"faint")]
        tag.configure(text=text, fg=color)

    def _on_edit(self, key: str) -> None:
        if self._mute:
            return
        # 用户自己改过 -> 视为人工确认值, 参与判定
        if self.origins.get(key) != "manual":
            self.origins[key] = "manual"
            self.sources.pop(key, None)
            self._paint_tag(key)
            self._refresh_counter()

    def _set(self, key: str, value) -> None:
        var = self.entries.get(key)
        if var is None:
            return
        self._mute = True
        try:
            if isinstance(value, float):
                var.set("%g" % value)
            else:
                var.set(str(value))
        finally:
            self._mute = False

    def set_origin(self, key: str, origin: str, source: dict = None) -> None:
        self.origins[key] = origin
        if source:
            self.sources[key] = source
        else:
            self.sources.pop(key, None)
        self._paint_tag(key)

    def _refresh_counter(self) -> None:
        brd = sum(1 for k, o in self.origins.items() if o == "brd")
        manual = sum(1 for k, o in self.origins.items() if o == "manual")
        default = sum(1 for k, o in self.origins.items()
                      if o == "default" and self.entries[k].get().strip())
        empty = sum(1 for k, o in self.origins.items() if o == "empty")
        parts = [f"自动读取 {brd} 项（设计信息只读）",
                 f"手工确认 {manual} 项", f"待填 {empty} 项"]
        if default:
            parts.append(f"示例未确认 {default} 项")
        self.count_var.set(
            " · ".join(parts) + "\n待填与未确认示例值不参与判定。")
        if default and not self._confirm_shown:
            self.confirm_btn.pack(side=tk.RIGHT)
            self._confirm_shown = True
        elif not default and self._confirm_shown:
            self.confirm_btn.pack_forget()
            self._confirm_shown = False
        self._refresh_design_info()

    def confirm_all(self) -> None:
        for key, origin in list(self.origins.items()):
            if origin == "default" and self.entries[key].get().strip():
                self.origins[key] = "manual"
                self._paint_tag(key)
        self._refresh_counter()

    # ------------------------------------------------------------ 行为
    def sync_levels(self):
        """板厂/板型变化时, 能力等级下拉只保留该组合支持的值。"""
        from src.application.pcb_inputs import list_levels

        levels = list_levels(self.board_type.get(), factory=self.factory.get()) or ["standard"]
        if self.level_combo is not None:
            self.level_combo.configure(values=levels)
        if self.level.get() not in levels:
            self.level.set(levels[0])

    def read_design(self) -> dict:
        """把表单读成 design 字典; 有非法数字时抛 ValueError(中文说明)。

        只提交 brd / manual 来源的字段; 示例值与空值都不提交, 让引擎如实
        报"缺数据"而不是拿预填值算 PASS。
        """
        design = {}
        for key, var in self.entries.items():
            origin = self.origins.get(key, "manual")
            txt = var.get().strip()
            if not txt:
                continue
            if origin not in ("brd", "manual"):
                continue
            try:
                val = float(txt)
            except ValueError:
                raise ValueError(f"参数 {key} 不是有效数字: {txt}") from None
            if not math.isfinite(val):
                raise ValueError(f"参数 {key} 必须为有限数值")
            if key in ("layer_count", "comp_count") and val == int(val):
                val = int(val)
            design[key] = val

        if "board_size_x" in design and "board_size_y" in design:
            design["board_size"] = [design.pop("board_size_x"), design.pop("board_size_y")]
        if "panel_size_x" in design and "panel_size_y" in design:
            design["panel_size"] = [design.pop("panel_size_x"), design.pop("panel_size_y")]

        # 铜厚/表面处理/阈值类选项不来自 BRD, 但属于"看板选项", 一律带上
        from src.application.pcb_inputs import normalize_copper_um
        design["copper_um"] = normalize_copper_um(self.copper_um.get()) if self.copper_um.get().strip() else None
        design["layers"] = list(self.layers)
        design["stackup"] = [dict(row) for row in self.stackup]
        design["_copper_confirmations"] = dict(self.copper_confirmations)
        for row in design["stackup"]:
            confirmed = self.copper_confirmations.get(row["name"])
            if confirmed:
                row.update(design_copper_um=row.get("design_copper_um", row.get("copper_um")), copper_um=confirmed["copper_um"],
                           source=confirmed["source"], copper_confirmation=confirmed, thickness_basis="confirmed_finished")
        if self.copper_confirmations and design["stackup"]:
            outer = [design["stackup"][0].get("copper_um"), design["stackup"][-1].get("copper_um")]
            design["copper_um"] = outer[0] if outer[0] == outer[1] else None
        if self.surface_finish.get().strip():
            design["surface_finish"] = self.surface_finish.get().strip()
        if "polarity_marked" in self._confirmed_flags:
            design["polarity_marked"] = self.polarity_var.get()
        if "panelized" in self._confirmed_flags:
            design["panelized"] = self.panelized_var.get()
        design["board_name"] = self.board_name.get().strip()

        design["_sources"] = dict(self.sources)
        if self.copper_origin == "brd":
            design["_sources"]["copper_um"] = {"source": "BRD extracta LAYER_THICKNESS（叠构设计值，非实测）", "confidence": "high"}
        design["_manual_fields"] = [key for key, origin in self.origins.items() if origin == "manual"]
        if self.copper_origin == "manual":
            design["_manual_fields"].append("copper_um")
        design["_stackup_manual"] = bool(self.stackup_sources)
        design["_data_origin"] = "BRD+手工" if self.sources else "手工输入"
        design["_assumed"] = [
            label for _cat, key, label, _d in DFM_FIELDS
            if self.origins.get(key) == "default" and self.entries[key].get().strip()
        ]
        design["_missing"] = [
            label for _cat, key, label, _d in DFM_FIELDS
            if not self.entries[key].get().strip()
        ]
        # 判不出通孔/盲埋孔的焊盘栈, 交给判定层单列"待确认", 不能硬算机械孔
        design["_via_kind_unknown"] = self.via_kind_unknown
        design["_components"] = self.components
        design["_pad_metrics"] = self.pad_metrics
        design["_silkscreen"] = self.silkscreen
        design["_silkscreen_to_outline"] = self.silkscreen_to_outline
        design["_pads"] = self.pads
        design["_test_points"] = self.test_points
        design["_silk_to_component"] = self.silk_to_component
        design["_holes"] = self.holes
        design["_via_process_unknown"] = getattr(self, "via_process_unknown", [])
        return design

    # ------------------------------------------------------------ BRD 带入
    def reset_design(self):
        """Do not carry measurements from the previous board into a new input."""
        self.sources.clear()
        self.copper_confirmations.clear()
        self.stackup_sources = []
        self.polarity_var.set(False)
        self.panelized_var.set(False)
        self._confirmed_flags.clear()
        self._info = {}
        self._automatic_update = True
        self.copper_um.set("")
        self._automatic_update = False
        self.copper_origin = "empty"
        self.layers = []
        self.stackup = []
        self.via_kind_unknown = []
        self.via_process_unknown = []
        for key in ("components", "holes", "pad_metrics", "silkscreen", "silkscreen_to_outline",
                    "pads", "test_points", "silk_to_component"):
            setattr(self, key, {})
        for _cat, key, _label, _default in DFM_FIELDS:
            self._set(key, "")
            self.origins[key] = "empty"
        self.board_name.set("")
        self.surface_finish.set("")
        self._render_fields()
        self._refresh_counter()
        self._refresh_stackup()

    def apply_brd(self, info: dict) -> None:
        """把 BRD 里真实存在的字段填进参数表; 填不了的明确说明原因。"""
        self.via_process_unknown = list(info.get("via_process_unknown") or [])
        self._info = dict(info)
        self.via_kind_unknown = list(info.get("via_kind_unknown") or [])
        self.components = info.get("components") or {}
        self.holes = info.get("holes") or {}
        self.layers = list(info.get("layers") or [])
        stack = info.get("stackup") or []
        if not self.stackup_sources:
            self.stackup = stack.get("layers", []) if isinstance(stack, dict) else stack
        self.pad_metrics = info.get("pad_metrics") or {}
        self.silkscreen = info.get("silkscreen") or {}
        self.silkscreen_to_outline = info.get("silkscreen_to_outline") or {}
        self.pads = info.get("pads") or {}
        self.test_points = info.get("test_points") or {}
        self.silk_to_component = info.get("silk_to_component") or {}
        if info.get("error"):
            self._set_brd_status(f"读取 BRD 失败: {info['error']}")
            return

        if info.get("_legacy"):
            info = dict(info)
            comps = info.get("components") or []
            if 0 < len(comps) < BRD_COMPONENT_CAP:
                info["part_count"] = len(comps)

        name = (info.get("design_name") or info.get("board_name") or "").strip()
        if name:
            self.board_name.set(name)

        values, sources = brd_design_values(info)
        for key, val in values.items():
            if self.origins.get(key) == "manual":
                continue
            self._set(key, val)
            self.set_origin(key, "brd", sources.get(key))

        extra = brd_extra_values(info)
        if extra.get("surface_finish"):
            self.surface_finish.set(extra["surface_finish"])
        self._refresh_stackup()

        # BRD 没有的数值项: 清掉示例值, 标"待填", 避免和真实数据混在一起
        cleared = 0
        for _cat, key, _label, _default in DFM_FIELDS:
            if key in values:
                continue
            if self.entries.get(key) is None:
                continue
            if not FIELD_META.get(key, {}).get("brd") and not self.entries[key].get().strip():
                continue
            if FIELD_META.get(key, {}).get("brd"):
                self._set(key, "")
                self.set_origin(key, "empty")
                cleared += 1

        self._render_fields()
        self._refresh_counter()
        self._refresh_design_info()

        # 读到的项已在参数表里 (隐藏行) 与状态栏计数里体现, 不再铺一段来源清单;
        # 只有"读得不完整"这种真问题才留一行提示。
        if info.get("_legacy"):
            self._set_brd_status("旧格式 BRD: 只能读网络/层等基本信息")
        else:
            self._set_brd_status("")

    def _import_stackup(self):
        if not self._board_available:
            messagebox.showwarning("PCB 资料未就绪", "请先成功导入 BRD 或 Gerber，再补充逐层叠构", parent=self)
            return
        path = filedialog.askopenfilename(parent=self, title="导入逐铜层叠构",
                                          filetypes=[("叠构 JSON", "*.json")])
        if not path:
            return
        try:
            from src.application.pcb_inputs import import_stackup
            current_count = self.entries["layer_count"].get().strip()
            imported = import_stackup(path, current_count, self.copper_um.get())
            # 缺失外层铜厚仍让 read_design() 使用当前手工值，而非固化导入时的值。
            self.stackup = imported["rows"]
            self.stackup_sources = imported["input_fingerprints"]
            self._set("layer_count", imported["count"])
            self.set_origin("layer_count", "manual")
            self._refresh_counter()
            # 触发对话框现有输入变化监听，丢弃旧报告。
            self.copper_um.set(self.copper_um.get())
            self._refresh_stackup()
            self._set_brd_status(f"已导入 {imported['count']} 铜层叠构；缺铜厚/实测值的层将标待确认")
        except (ValueError, TypeError, OSError) as exc:
            messagebox.showwarning("叠构导入失败", str(exc), parent=self)

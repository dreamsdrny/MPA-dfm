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
from tkinter import ttk

from src.dfm_inputs import (BRD_COMPONENT_CAP, CATEGORY_HINTS, DFM_FIELDS,
                            FIELD_META, brd_design_values, brd_extra_values,
                            surface_finish_choices)
from src.ui.theme import COLORS
from src.ui.widgets import combo_cell, entry_cell, scroll_area

# 来源标签的显示文本与颜色
_ORIGIN_TAG = {
    "brd": ("BRD", COLORS["success"]),
    "manual": ("手工", COLORS["primary"]),
    "default": ("示例", COLORS["warn"]),
    "empty": ("待填", COLORS["faint"]),
}


class ParamPanel(ttk.Frame):
    def __init__(self, master, on_browse, on_load, font_bold=None):
        super().__init__(master, style="Card.TFrame")
        self._on_browse = on_browse
        self._on_load = on_load
        self._font_bold = font_bold or ("TkDefaultFont", 9, "bold")
        self._font_tag = ("TkDefaultFont", 8)
        self._mute = False          # 程序写值时抑制"用户编辑"判定

        # ---- 表单变量 ----
        self.brd_path = tk.StringVar(value="")
        self.board_name = tk.StringVar(value="")
        self.factory = tk.StringVar(value="hongban")
        self.board_type = tk.StringVar(value="rigid")
        self.level = tk.StringVar(value="standard")
        self.copper_oz = tk.StringVar(value="1.0")
        self.surface_finish = tk.StringVar(value="")
        self.polarity_var = tk.BooleanVar(value=True)
        self.panelized_var = tk.BooleanVar(value=True)
        self.brd_status_var = tk.StringVar(value="")
        self.count_var = tk.StringVar(value="")

        self.entries = {}
        self.origins = {}
        self.sources = {}
        self.via_kind_unknown = []
        self.components = {}
        self.holes = {}
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

    # ------------------------------------------------------------ 构建
    def _build(self):
        ttk.Label(self, text="BRD 文件", style="FieldLabel.TLabel").pack(anchor=tk.W)
        line = ttk.Frame(self, style="Card.TFrame")
        line.pack(fill=tk.X, pady=(3, 0))
        ttk.Entry(line, textvariable=self.brd_path).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(line, text="浏览", style="Secondary.TButton", width=5,
                   command=self._on_browse).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(line, text="从 BRD 读取", style="Secondary.TButton", width=11,
                   command=self._on_load).pack(side=tk.LEFT, padx=(6, 0))
        # BRD 读取结果提示: 正常读入时不显示任何来源清单, 只留真问题(读失败/旧格式)
        self.brd_status_label = ttk.Label(self, textvariable=self.brd_status_var,
                                          style="CardHint.TLabel",
                                          wraplength=340, justify=tk.LEFT)
        self.brd_status_label.pack(anchor=tk.W, pady=(6, 0))
        self._brd_status_packed = True

        opts = ttk.Frame(self, style="Card.TFrame")
        opts.pack(fill=tk.X, pady=(10, 0))
        for col in (0, 1):
            opts.columnconfigure(col, weight=1)
        combo_cell(opts, 0, 0, "板厂", self.factory, ["hongban", "sihui"],
                   self.sync_levels)
        combo_cell(opts, 0, 1, "板类型", self.board_type, ["rigid", "flex"],
                   self.sync_levels)
        self.level_combo = combo_cell(opts, 1, 0, "能力等级", self.level,
                                      ["standard", "automotive", "extreme"], None)
        entry_cell(opts, 1, 1, "铜厚 (oz)", self.copper_oz)
        self.surface_combo = combo_cell(opts, 2, 0, "表面处理", self.surface_finish,
                                        surface_finish_choices(), None)
        entry_cell(opts, 2, 1, "单板名", self.board_name)

        flags = ttk.Frame(opts, style="Card.TFrame")
        flags.grid(row=3, column=0, columnspan=2, sticky="we", pady=(8, 0))
        ttk.Checkbutton(flags, text="极性已标注", variable=self.polarity_var,
                        style="Card.TCheckbutton").pack(side=tk.LEFT)
        ttk.Checkbutton(flags, text="已拼板", variable=self.panelized_var,
                        style="Card.TCheckbutton").pack(side=tk.LEFT, padx=(14, 0))
        ttk.Label(opts, style="CardHint.TLabel", wraplength=330, justify=tk.LEFT,
                  text="极性标识 / 拼板：BRD 里读不到，请按实际板子勾选；"
                       "没勾选会作为高等级问题写进结果").grid(
            row=4, column=0, columnspan=2, sticky="we", pady=(4, 0))
        self.sync_levels()

        ttk.Separator(self, orient=tk.HORIZONTAL, style="Card.TSeparator").pack(
            fill=tk.X, pady=(10, 4))

        head = ttk.Frame(self, style="Card.TFrame")
        head.pack(fill=tk.X)
        ttk.Label(head, textvariable=self.count_var, style="CardHint.TLabel",
                  wraplength=300, justify=tk.LEFT).pack(side=tk.LEFT)
        self.show_brd_var = tk.BooleanVar(value=False)
        self.show_brd_btn = ttk.Checkbutton(
            head, text="显示 BRD 带入项", variable=self.show_brd_var,
            style="Card.TCheckbutton", command=self._toggle_show_brd)
        self._show_brd_shown = False
        self._brd_toggled = False   # 用户手动点过开关后, 不再自动展开
        self.confirm_btn = ttk.Button(head, text="确认全部示例值", style="Ghost.TButton",
                                      width=13, command=self.confirm_all)
        # 只有表单里存在预填值(示例)时才显示, 平时不占地方
        self._confirm_shown = False

        _, inner = scroll_area(self)
        self._host = ttk.Frame(inner, style="Card.TFrame")
        self._host.grid(row=0, column=0, sticky="nsew")
        inner.columnconfigure(0, weight=1)

        for _cat, key, _label, default in DFM_FIELDS:
            var = tk.StringVar(value=default)
            var.trace_add("write", lambda *_a, k=key: self._on_edit(k))
            self.entries[key] = var
            self.origins[key] = "default" if default else "empty"
        self._render_fields()

    def _set_brd_status(self, text: str) -> None:
        """BRD 读取结果提示: 正常读入时**不显示**任何来源清单, 只留问题提示。"""
        self.brd_status_var.set(text or "")
        if text and not self._brd_status_packed:
            self.brd_status_label.pack(fill=tk.X, pady=(0, 4))
            self._brd_status_packed = True
        elif not text and self._brd_status_packed:
            self.brd_status_label.pack_forget()
            self._brd_status_packed = False

    # ------------------------------------------------------------ 参数行渲染
    def _visible(self, key: str) -> bool:
        """BRD 读到的行默认不显示 (判定仍用它), 勾选后展开。"""
        if self.show_brd_var.get():
            return True
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
                band = tk.Frame(self._host, bg="#F1F5FB")
                band.grid(row=row, column=0, columnspan=3, sticky="we", pady=(10, 3))
                text = cat if not CATEGORY_HINTS.get(cat) else \
                    f"{cat}　·　{CATEGORY_HINTS[cat]}"
                tk.Label(band, text="  " + text, bg="#F1F5FB",
                         fg=COLORS["primary_dark"],
                         font=self._font_bold, anchor=tk.W).pack(fill=tk.X, ipady=3)
                row += 1
            ttk.Label(self._host, text=label, style="Card.TLabel").grid(
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
        if not self.show_brd_var.get() and not self._brd_toggled:
            self.show_brd_var.set(True)
            self._render_fields()
        self._refresh_counter()

    # ------------------------------------------------------------ 来源标签
    def _paint_tag(self, key: str) -> None:
        tag = self.tags.get(key)
        if tag is None:
            return
        origin = self.origins.get(key, "empty")
        text, color = _ORIGIN_TAG.get(origin, _ORIGIN_TAG["empty"])
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
        if brd and not self._show_brd_shown:
            self.show_brd_btn.pack(side=tk.RIGHT, padx=(6, 0))
            self._show_brd_shown = True
        elif not brd and self._show_brd_shown:
            self.show_brd_btn.pack_forget()
            self._show_brd_shown = False
        self.show_brd_btn.configure(text=f"显示 BRD 带入项 ({brd})" if brd
                                    else "显示 BRD 带入项")
        manual = sum(1 for k, o in self.origins.items() if o == "manual")
        default = sum(1 for k, o in self.origins.items()
                      if o == "default" and self.entries[k].get().strip())
        empty = sum(1 for k, o in self.origins.items() if o == "empty")
        parts = [f"BRD {brd} 项" + ("(已隐藏)" if brd and not self.show_brd_var.get()
                                     else ""),
                 f"手工确认 {manual} 项", f"待填 {empty} 项"]
        if default:
            parts.append(f"示例未确认 {default} 项")
        self.count_var.set(
            "取值来源: " + " · ".join(parts) + "\n"
            "BRD 读到的项已自动带入判定、默认不铺在界面上; 只有待填项需要你补。\n"
            "待填与示例值不参与判定 (不拿假数据算 PASS)。")
        if default and not self._confirm_shown:
            self.confirm_btn.pack(side=tk.RIGHT)
            self._confirm_shown = True
        elif not default and self._confirm_shown:
            self.confirm_btn.pack_forget()
            self._confirm_shown = False

    def confirm_all(self) -> None:
        for key, origin in list(self.origins.items()):
            if origin == "default" and self.entries[key].get().strip():
                self.origins[key] = "manual"
                self._paint_tag(key)
        self._refresh_counter()

    # ------------------------------------------------------------ 行为
    def sync_levels(self):
        """板厂/板型变化时, 能力等级下拉只保留该组合支持的值。"""
        from src.dfm_engine import list_levels

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
            if key in ("layer_count", "comp_count") and val == int(val):
                val = int(val)
            design[key] = val

        if "board_size_x" in design and "board_size_y" in design:
            design["board_size"] = [design.pop("board_size_x"), design.pop("board_size_y")]
        if "panel_size_x" in design and "panel_size_y" in design:
            design["panel_size"] = [design.pop("panel_size_x"), design.pop("panel_size_y")]

        # 铜厚/表面处理/阈值类选项不来自 BRD, 但属于"看板选项", 一律带上
        try:
            design["copper_oz"] = float(self.copper_oz.get() or 1.0)
        except ValueError:
            design["copper_oz"] = 1.0
        if self.surface_finish.get().strip():
            design["surface_finish"] = self.surface_finish.get().strip()
        design["polarity_marked"] = self.polarity_var.get()
        design["panelized"] = self.panelized_var.get()
        design["board_name"] = self.board_name.get().strip()

        design["_sources"] = dict(self.sources)
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
        return design

    # ------------------------------------------------------------ BRD 带入
    def reset_design(self):
        """Do not carry measurements from the previous board into a new input."""
        self.sources.clear()
        for _cat, key, _label, _default in DFM_FIELDS:
            self._set(key, "")
            self.origins[key] = "empty"
        self.board_name.set("")
        self.surface_finish.set("")
        # apply_brd() will render once after the worker finishes loading.

    def apply_brd(self, info: dict) -> None:
        """把 BRD 里真实存在的字段填进参数表; 填不了的明确说明原因。"""
        self.via_kind_unknown = list(info.get("via_kind_unknown") or [])
        self.components = info.get("components") or {}
        self.holes = info.get("holes") or {}
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
            self._set(key, val)
            self.set_origin(key, "brd", sources.get(key))

        extra = brd_extra_values(info)
        if extra.get("surface_finish"):
            self.surface_finish.set(extra["surface_finish"])

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

        # 读到的项已在参数表里 (隐藏行) 与状态栏计数里体现, 不再铺一段来源清单;
        # 只有"读得不完整"这种真问题才留一行提示。
        if info.get("_legacy"):
            self._set_brd_status("旧格式 BRD: 只能读网络/层等基本信息")
        else:
            self._set_brd_status("")

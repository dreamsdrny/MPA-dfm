"""行业DFM工具式叠构剖面：Canvas 比例绘制、图例、层选择与来源详情。

铜层厚度按已知值等比绘制；介质/阻焊厚度未提供时只画等分示意带，数值仍标为未获取，
绝不把示意高度当成实测厚度。缺失铜层用灰底加斜纹，避免与 BRD 设计值混淆。
"""
import tkinter as tk
from tkinter import ttk

from src.application.pcb_inputs import STACKUP_KINDS, stackup_visual

COPPER, FINISH, MASK = STACKUP_KINDS["copper"], STACKUP_KINDS["finish"], STACKUP_KINDS["mask"]
from src.ui.theme import COLORS


class StackupView(ttk.Frame):
    def __init__(self, master, on_select=None):
        super().__init__(master, style="Card.TFrame", padding=(6, 4))
        self.on_select = on_select
        self.visual = stackup_visual([])
        self._selected = None
        self._scale = 1.0
        header = ttk.Frame(self, style="Card.TFrame")
        header.pack(fill=tk.X)
        self.summary = tk.StringVar()
        ttk.Label(header, textvariable=self.summary, style="CardTitle.TLabel").pack(side=tk.LEFT)
        zoom = ttk.Frame(header, style="Card.TFrame")
        zoom.pack(side=tk.RIGHT)
        ttk.Button(zoom, text="－", width=3, command=lambda: self._zoom(-1)).pack(side=tk.LEFT)
        ttk.Button(zoom, text="＋", width=3, command=lambda: self._zoom(1)).pack(side=tk.LEFT)
        self.canvas = tk.Canvas(self, width=330, height=430, highlightthickness=1, bg=COLORS["card"],
                                highlightbackground=COLORS["border"])
        self.canvas.pack(fill=tk.BOTH, expand=True, pady=(4, 2))
        self.canvas.bind("<Configure>", lambda _event: self.draw())
        self.detail = tk.StringVar(value="点击铜层查看来源；灰色为未获取")
        ttk.Label(self, textvariable=self.detail, style="CardHint.TLabel", wraplength=330,
                  justify=tk.LEFT).pack(fill=tk.X)

    def set_data(self, rows, **kwargs):
        self.visual = stackup_visual(rows, **kwargs)
        copper = {band["index"] for band in self.visual["bands"] if band["kind"] == COPPER}
        if self._selected not in copper:
            self._selected = None
        self.summary.set(f"铜层 {self.visual['layer_count']} · 已知铜厚合计 {self.visual['total_copper_um']:g} μm"
                         if self.visual["layer_count"] else "叠构待导入")
        self.draw()

    def select(self, index):
        self._selected = index
        self.draw()

    def refresh(self):
        """重绘当前剖面；数据变化由 set_data 负责，避免这里重复解析。"""
        self.draw()

    def _zoom(self, direction):
        self._scale = max(.5, min(3.0, self._scale + direction * .25))
        self.draw()

    @staticmethod
    def _colors(band):
        if band["kind"] == COPPER:
            if band["status"] == "missing":
                return COLORS["faint"], COLORS["text"]
            if band["status"] == "confirmed":
                return "#D9822B", "#1F2937"
            return "#5C8AA6", "#FFFFFF"
        if band["kind"] == MASK:
            return "#2E7D4F", "#FFFFFF"
        if band["kind"] == FINISH:
            return "#7A5FA8", "#FFFFFF"
        return COLORS["row_alt"], COLORS["muted"]

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 260)
        height = max(canvas.winfo_height(), 260)
        bands = self.visual["bands"]
        if not bands:
            canvas.create_text(width / 2, height / 2, text="导入 BRD 或叠构 JSON 后显示剖面",
                               fill=COLORS["muted"])
            return
        known = [band["thickness_um"] for band in bands
                 if band["kind"] == COPPER and band["thickness_um"] is not None]
        tallest = max(known or [18])
        heights = []
        for band in bands:
            if band["kind"] == COPPER and band["thickness_um"] is not None:
                heights.append(max(3.0, band["thickness_um"] / tallest * 30))
            elif band["kind"] == COPPER:
                heights.append(18)
            else:
                heights.append(12)
        # 顶部说明 + 底部图例都要留位；最厚铜层不超过 46px，避免大窗口把铜层拉成不真实的厚块。
        top, legend_space = 24, 58
        copper_raw = [raw for band, raw in zip(bands, heights) if band["kind"] == COPPER]
        factor = min((height - top - legend_space) / sum(heights), 46 / max(copper_raw or [1])) * self._scale
        # 宽结果区布局：左侧层名、中间色带、右侧数值与外/内层，最右画孔跨度符号。
        label_x, left, right = 146, 154, width - 156
        value_x, edge_x, drill_base = width - 150, width - 78, width - 40
        canvas.create_text(8, 10, text="铜厚按已知值等比；介质/阻焊仅示意", anchor="w", fill=COLORS["muted"])
        header_right = (f"板厚 {self.visual['board_thickness_mm']:g} mm"
                        if self.visual["board_thickness_mm"] is not None else "板厚未获取")
        if self.visual["notes"]:
            header_right += " · " + self.visual["notes"][0]
        canvas.create_text(width - 8, 10, text=header_right, anchor="e",
                           fill=COLORS["warn"] if self.visual["board_thickness_mm"] is None or self.visual["notes"]
                           else COLORS["muted"])
        y = top
        positions = {}
        for band, raw in zip(bands, heights):
            thickness = max(7.0, raw * factor)
            fill, _text = self._colors(band)
            canvas.create_rectangle(left, y, right, y + thickness, fill=fill, outline=COLORS["border"])
            value = ""
            if band["kind"] == COPPER:
                value = (f"{band['thickness_um']:g}" if band["thickness_um"] is not None else "—")
                hit = canvas.create_rectangle(left, y, right, y + thickness, fill="", outline="", tags=("layer",))
                canvas.tag_bind(hit, "<Button-1>", lambda _event, row=band: self._clicked(row))
                if band["status"] == "missing":
                    for offset in range(int(left), int(right), 9):
                        canvas.create_line(offset, y + thickness, min(offset + thickness, right - 1), y, fill="#FFFFFF")
                if band["outer"]:
                    canvas.create_rectangle(left - 2, y - 1, right + 2, y + thickness + 1,
                                            outline=COLORS["primary"], width=1)
            canvas.create_text(label_x, y + thickness / 2, text=band["label"], anchor="e", fill=COLORS["text"])
            canvas.create_text(value_x, y + thickness / 2, text=value, anchor="w", fill=COLORS["text"])
            if band["kind"] == COPPER:
                positions[band["index"]] = (y, y + thickness)
                canvas.create_text(edge_x, y + thickness / 2, text="外层" if band["outer"] else "内层",
                                   fill=COLORS["muted"])
            if self._selected is not None and band["kind"] == COPPER and band["index"] == self._selected:
                canvas.create_rectangle(left - 3, y - 3, right + 3, y + thickness + 3,
                                        outline=COLORS["primary"], width=2)
            y += thickness + 2
        labels = []
        for offset_index, drill in enumerate(self.visual["drills"][:3]):
            x = drill_base - offset_index * 16
            color = "#C00000" if drill["kind"] == "PTH" else "#7C3AED"
            span = [positions[i] for i in range(drill["top"], drill["bottom"] + 1) if i in positions]
            if not span:
                continue
            canvas.create_line(x, span[0][0], x, span[-1][1], fill=color, width=2,
                               dash=() if drill["kind"] == "PTH" else (4, 3))
            labels.append(f"{drill['kind']} ×{drill['spans']}")
        if labels:
            canvas.create_text(drill_base + 6, height - legend_space + 14, anchor="e",
                               text="孔跨度 " + " / ".join(labels), fill="#C00000")
            canvas.create_text(drill_base + 6, height - legend_space + 30, anchor="e",
                               text="实线 PTH·虚线 NPTH，不表示逐孔位置", fill=COLORS["faint"])
        x = 6
        for label, color in (("BRD", "#5C8AA6"), ("确认", "#D9822B"), ("未获取", COLORS["faint"]),
                             ("阻焊", "#2E7D4F"), ("表面处理", "#7A5FA8"), ("孔跨度", "#C00000")):
            canvas.create_rectangle(x, height - 14, x + 9, height - 5, fill=color, outline=COLORS["border"])
            canvas.create_text(x + 12, height - 9, text=label, anchor="w", fill=COLORS["muted"])
            x += 78

    def _clicked(self, band):
        self._selected = band["index"]
        self.detail.set(f"{band['label']} · "
                        + (f"{band['thickness_um']:g}μm" if band["thickness_um"] is not None else "铜厚未获取")
                        + f" · {'外层' if band['outer'] else '内层'} · {band['source']}")
        self.draw()
        if self.on_select:
            self.on_select(band)

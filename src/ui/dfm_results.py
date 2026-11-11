"""DFM 对话框右栏: 分析结果表格.

一个页签: 制程能力比对  结果 / 来源 / 类别 / 详情 (PASS / WARN / FAIL / INFO 彩色)

职责只有"把结果画出来", 判定逻辑在 src/dfm_engine.py。
"""
import tkinter as tk
from tkinter import ttk

from src.ui.theme import COLORS
from src.ui.widgets import make_tree


class ResultPanel(ttk.Frame):
    def __init__(self, master, on_locate=None):
        super().__init__(master, style="Card.TFrame")
        self._on_locate = on_locate
        self._render_id = None
        self._generation = 0
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

    # ------------------------------------------------------------ 渲染
    def _destroyed(self, event):
        if event.widget is self:
            if self._render_id is not None:
                self.after_cancel(self._render_id)
                self._render_id = None
            self._generation += 1

    def clear(self):
        self._generation += 1
        if self._render_id is not None:
            self.after_cancel(self._render_id)
            self._render_id = None
        self.tree.delete(*self.tree.get_children())

    def render_capability(self, result: dict) -> None:
        """制程能力比对表: 逐项结果 + 覆盖缺口。"""
        self.clear()
        generation = self._generation
        rows = []

        icon = {"pass": "PASS", "warn_limit": "WARN", "fail": "FAIL", "info": "INFO"}
        for it in result["items"]:
            msg = it["message"]
            if it.get("note"):
                msg += f"  [{it['note']}]"
            rows.append(((icon[it["status"]], it.get("source", "手工"),
                          it["category"], msg), it["status"]))

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

        def batch(start=0):
            self._render_id = None
            if generation != self._generation:
                return
            for values, tag in rows[start:start + 100]:
                self.tree.insert("", tk.END, values=values, tags=(tag,))
            if start + 100 < len(rows):
                self._render_id = self.after(1, lambda: batch(start + 100))
        batch()

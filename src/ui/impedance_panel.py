"""Three-input trace search that launches licensed PCB Editor directly."""
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from src.application.cad_tools import AllegroTraceSession, trace_number
from src.ui.responsive import FlowBar, wrap_label
from src.ui.tasks import TaskRunner


class ImpedancePanel(ttk.Frame):
    def __init__(self, parent, get_board=None, get_editor=None):
        super().__init__(parent, padding=16)
        self.get_board = get_board
        self.get_editor = get_editor
        self.layer = tk.StringVar(master=self)
        self.width = tk.StringVar(master=self, value="5")
        self.spacing = tk.StringVar(master=self)
        self.status = tk.StringVar(master=self, value="导入 BRD 后选择铜层，输入线宽/线距，直接在 PCB Editor 中查找并高亮。")
        self.tasks = TaskRunner(self)
        self.session = None
        self._poll_id = None
        self._started = 0
        self._layers = ()
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text="Allegro 走线查找 / 高亮", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 16))
        self.inputs = {}
        for row, (name, label, var) in enumerate((("layer", "寻找铜层", self.layer),
                ("width", "线宽 mil（留空不限）", self.width),
                ("spacing", "线距 mil（留空不限）", self.spacing)), 1):
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=6)
            widget = ttk.Combobox(self, textvariable=var, state="readonly") if name == "layer" else ttk.Entry(self, textvariable=var)
            widget.grid(row=row, column=1, sticky="ew", pady=6)
            self.inputs[name] = widget
        bar = FlowBar(self)
        bar.grid(row=4, column=0, columnspan=2, sticky="ew", pady=12)
        self.apply_button = ttk.Button(bar, text="在 PCB Editor 查找并高亮", command=self._apply, state=tk.DISABLED)
        bar.add(self.apply_button)
        status = ttk.Label(self, textvariable=self.status)
        status.grid(row=5, column=0, columnspan=2, sticky="ew", pady=8)
        wrap_label(status, self)
        hint = ttk.Label(self, text="无需生成、导入或手工加载插件。只读打开当前 BRD，不保存或修改布线。\n"
            "线距为同层走线段到其他网络走线段的最小边到边距离；不包含焊盘、过孔或铜皮。\n"
            "匹配容差固定为 0.01 mil。此功能是几何查找，不是阻抗计算或阻抗认证。\n"
            "高亮期间保持 Allegro 的结果面板打开；在该面板清除或关闭可恢复显示。")
        hint.grid(row=6, column=0, columnspan=2, sticky="ew", pady=8)
        wrap_label(hint, self)
        self.bind("<Destroy>", self._destroyed, add="+")

    def set_layers(self, layers):
        self._layers = tuple(layers)
        self.inputs["layer"].configure(values=self._layers)
        if self.layer.get() not in self._layers:
            self.layer.set(self._layers[0] if self._layers else "")
        self.apply_button.configure(state=tk.NORMAL if self._layers else tk.DISABLED)

    def _apply(self):
        import time
        try:
            if not self.get_board or not self.get_editor:
                raise ValueError("请从 PCB 工作台导入 BRD 后使用走线查找")
            board = self.get_board()
            if self.layer.get() not in self._layers:
                raise ValueError("请选择当前 BRD 的铜层")
            trace_number(self.width.get(), "线宽")
            trace_number(self.spacing.get(), "线距")
            if self.session and self.session.running():
                raise ValueError("请先关闭上一次查找打开的 PCB Editor，再重新查找；不会关闭你正在使用的 CAD 窗口")
            editor = self.get_editor()
            if not editor:
                return
            session = AllegroTraceSession(board, editor)
            session.prepare_trace(self.layer.get(), self.width.get(), self.spacing.get())
            session.launch()
            self.session = session
            self._started = time.monotonic()
            self.status.set("正在打开当前 BRD 并执行走线查找，请完成 PCB Editor 许可选择…")
            if self._poll_id is None:
                self._poll_id = self.after(350, self._poll)
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
            messagebox.showerror("走线查找", str(exc), parent=self.winfo_toplevel())

    def _poll(self):
        import time
        self._poll_id = None
        session = self.session
        if not session:
            return
        try:
            if self.get_board and Path(self.get_board()).resolve() != Path(session.brd_path):
                self.status.set("工作台已换板；上次查找窗口仍显示旧 BRD，请关闭旧窗口后重新查找。")
                return
        except ValueError:
            self.status.set("当前 BRD 输入已变化或正在分析；上次查找结果不代表当前工作台输入。")
            return
        _, state = session.status()
        labels = {"HIGHLIGHTED": f"PCB Editor 已高亮 {session.match_count()} 段匹配走线。",
                  "NO_MATCH": "当前层没有匹配走线，不代表阻抗或 DFM 通过。",
                  "CLEARED": "PCB Editor 已清除临时高亮；关闭该查找窗口后可重新查找。",
                  "SEARCHING": "PCB Editor 正在测量当前层实际走线…",
                  "FAILED": f"原生查找失败，请检查 PCB Editor 命令日志：{session.folder}"}
        if state in labels:
            self.status.set(labels[state])
        if not session.running():
            if state not in ("CLEARED", "FAILED"):
                self.status.set(f"PCB Editor 已退出或启动失败，请检查许可及日志：{session.folder}")
            return
        if state == "WAITING" and time.monotonic() - self._started > 120:
            self.status.set(f"未收到 PCB Editor 执行回执，请检查许可/产品选择及启动脚本：{session.script}")
            return
        self._poll_id = self.after(350, self._poll)

    def _destroyed(self, event):
        if event.widget is self and self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None

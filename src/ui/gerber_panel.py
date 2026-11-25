"""Gerber-first workflow. Workers use immutable snapshots, never Tk variables."""
import os
import tkinter as tk
from copy import deepcopy
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from src.application.gerber_analysis import (
    GerberAnalysisRequest,
    import_gerber,
    run_gerber_analysis,
)
from src.application.pcb_inputs import import_stackup, list_levels, normalize_copper_um
from src.application.reports import export_report
from src.ui.dfm_results import ResultPanel
from src.ui.responsive import FlowBar, wrap_label
from src.ui.theme import COLORS, Card


class GerberPanel(ttk.Frame):
    def __init__(self, master, tasks, on_change, shared_params=None, shared_results=None):
        super().__init__(master, padding=12)
        self.tasks, self.on_change = tasks, on_change
        self.source = tk.StringVar()
        self.factory = tk.StringVar(value="hongban")
        self.board_type = tk.StringVar(value="rigid")
        self.level = tk.StringVar(value="standard")
        self.copper = tk.StringVar(value="35")
        self.layer_count = tk.StringVar()
        self.geometry_data = tk.StringVar()
        self.status = tk.StringVar(value="导入 Gerber 文件、整套目录或压缩包；无需 BRD 或 Cadence。")
        self.info = None
        self.last_result = None
        self.stackup = []
        self.stackup_sources = []
        self.revision = 0
        self.state = "idle"
        self.shared_params = shared_params
        if shared_params is not None:
            self.factory = shared_params.factory
            self.board_type = shared_params.board_type
            self.level = shared_params.level
            self.copper = shared_params.copper_um
            self.layer_count = shared_params.entries["layer_count"]
            self.results = shared_results
            layer_host = ttk.Frame(self.results.nb, padding=8)
            self.results.nb.add(layer_host, text="导入资料 / 图层清单")
            self.layers = ttk.Treeview(layer_host, columns=("name", "role", "objects", "bounds"), show="headings", height=8)
            for key, title, width in (("name", "文件", 260), ("role", "图层角色", 130),
                                       ("objects", "图形数", 80), ("bounds", "范围 mm", 200)):
                self.layers.heading(key, text=title)
                self.layers.column(key, width=width, minwidth=60)
            self.layers.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scroll = ttk.Scrollbar(layer_host, command=self.layers.yview)
            scroll.pack(side=tk.RIGHT, fill=tk.Y)
            self.layers.configure(yscrollcommand=scroll.set)
            self.stack_status = shared_params.stackup_var
            for var in (self.source, self.factory, self.board_type, self.level, self.copper,
                        self.layer_count, self.geometry_data):
                var.trace_add("write", self.invalidate)
            self.source.trace_add("write", self.source_changed)
            return
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        card = Card(self, "① Gerber 输入与能力设置", "原文件只读；建议导入同版本的铜层 / 阻焊 / 丝印 / 板框 / NC Drill 整套资料")
        card.grid(row=0, column=0, sticky="ew")
        bar = FlowBar(card.content, style="Card.TFrame")
        bar.pack(fill=tk.X)
        bar.add(ttk.Entry(bar, textvariable=self.source, width=60))
        bar.add(ttk.Button(bar, text="读取路径", command=lambda: self.load(self.source.get())))
        options = FlowBar(card.content, style="Card.TFrame")
        options.pack(fill=tk.X, pady=(8, 0))
        for label, var, values in (("板厂", self.factory, ("hongban", "sihui")),
                                   ("板型", self.board_type, ("rigid", "flex")),
                                   ("能力等级", self.level, ("standard", "automotive", "extreme"))):
            options.add(ttk.Label(options, text=label))
            box = ttk.Combobox(options, textvariable=var, values=values, state="readonly", width=12)
            options.add(box)
            if var is self.level:
                self.level_box = box
            else:
                box.bind("<<ComboboxSelected>>", lambda _: self.sync_levels())
        options.add(ttk.Label(options, text="外层铜厚 μm"))
        options.add(ttk.Entry(options, textvariable=self.copper, width=7))
        options.add(ttk.Label(options, text="实际铜层数"))
        options.add(ttk.Entry(options, textvariable=self.layer_count, width=5))
        extra = FlowBar(card.content, style="Card.TFrame")
        extra.pack(fill=tk.X, pady=(8, 0))
        extra.add(ttk.Button(extra, text="导入逐层叠构 JSON", command=self.browse_stackup))
        extra.add(ttk.Button(extra, text="选择几何配置 JSON", command=self.browse_geometry))
        extra.add(ttk.Entry(extra, textvariable=self.geometry_data, width=42))
        self.stack_status = tk.StringVar(value="内层铜厚不能由外层推算；缺资料的检查项标为待复核。")
        ttk.Label(card.content, textvariable=self.stack_status, style="CardHint.TLabel").pack(fill=tk.X, pady=6)
        label = ttk.Label(self, textvariable=self.status, foreground=COLORS["muted"])
        label.grid(row=1, column=0, sticky="ew", pady=8)
        wrap_label(label, self)
        notebook = ttk.Notebook(self)
        notebook.grid(row=2, column=0, sticky="nsew")
        self.results = ResultPanel(notebook, on_open_point=lambda _: self.preview(), gerber_mode=True)
        notebook.add(self.results, text="② 分析结果 / 问题坐标 / 覆盖缺口")
        layer_host = ttk.Frame(notebook, padding=8)
        notebook.add(layer_host, text="导入图层清单")
        self.layers = ttk.Treeview(layer_host, columns=("name", "role", "objects", "bounds"), show="headings", height=8)
        for key, title, width in (("name", "文件", 300), ("role", "图层角色", 150),
                                   ("objects", "图形数", 90), ("bounds", "范围 mm", 220)):
            self.layers.heading(key, text=title)
            self.layers.column(key, width=width, minwidth=70)
        self.layers.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(layer_host, command=self.layers.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.layers.configure(yscrollcommand=scroll.set)
        for var in (self.source, self.factory, self.board_type, self.level, self.copper, self.layer_count, self.geometry_data):
            var.trace_add("write", self.invalidate)
        self.source.trace_add("write", self.source_changed)
        self.sync_levels()

    def sync_levels(self):
        levels = list_levels(self.board_type.get(), self.factory.get())
        self.level_box.configure(values=levels)
        if self.level.get() not in levels:
            self.level.set(levels[0])

    def invalidate(self, *_):
        self.revision += 1
        self.last_result = None
        self.state = "idle"
        if hasattr(self, "results"):
            self.results.clear()
        self.on_change()

    def source_changed(self, *_):
        self.info = None
        self.stackup = []
        self.stackup_sources = []
        if self.shared_params is None:
            self.layer_count.set("")
        else:
            self.shared_params.reset_design()
        self.geometry_data.set("")
        self.layers.delete(*self.layers.get_children())
        if self.shared_params is None:
            self.stack_status.set("内层铜厚不能由外层推算；请确认实际铜层数或导入逐层叠构。")
        self.status.set("路径已变化，请点击读取路径完成导入。")
        self.on_change()

    def browse_file(self):
        path = filedialog.askopenfilename(parent=self, title="导入 Gerber 文件或压缩包", filetypes=[
            ("Gerber / 压缩包", "*.gbr *.ger *.gerber *.grb *.gbx *.pho *.art *.gtl *.gbl *.gts *.gbs *.gto *.gbo *.gko *.gm* *.g[0-9]* *.zip *.tgz *.tar *.gz"),
            ("所有文件", "*.*")])
        if path:
            self.load(path)

    def browse_folder(self):
        path = filedialog.askdirectory(parent=self, title="导入同一版本的整套 Gerber 目录")
        if path:
            self.load(path)

    def load(self, path):
        if "gerber-import" in self.tasks.pending or "gerber-analysis" in self.tasks.pending:
            return
        path = path.strip().strip('"')
        self.source.set(path)
        revision = self.revision
        self.status.set("正在后台读取 Gerber 图层…")
        def done(info):
            if revision != self.revision:
                return
            self.info = info
            if self.shared_params is not None:
                self.shared_params.board_name.set(Path(path).stem)
            self.layers.delete(*self.layers.get_children())
            for row in info["layers"]:
                self.layers.insert("", tk.END, values=(row["name"], row["role"], row["objects"],
                    ", ".join(f"{v:.3f}" for v in row["bounds_mm"])))
            unknown = sum(row["role"] == "unknown" for row in info["layers"])
            self.status.set(f"已导入 {len(info['layers'])} 个图层 · 未识别 {unknown} · 读取错误 {len(info['errors'])}；确认能力设置后执行分析。"
                            + ("\n" + "；".join(info["errors"][:3]) if info["errors"] else ""))
            self.on_change()
        self.tasks.submit("gerber-import", lambda: import_gerber(path), done,
            lambda error: self.failed(error, revision, "Gerber 导入失败"))

    @property
    def ready(self):
        return bool(self.info) and not any(key in self.tasks.pending for key in ("gerber-import", "gerber-analysis"))

    def failed(self, error, revision, title):
        if revision != self.revision:
            return
        self.state = "error"
        self.status.set(title + "：" + error)
        messagebox.showerror(title, error, parent=self)

    def browse_geometry(self):
        path = filedialog.askopenfilename(parent=self, title="Gerber 几何配置（不使用绑定 BRD 的 JSON）", filetypes=[("JSON", "*.json")])
        if path:
            self.geometry_data.set(path)

    def browse_stackup(self):
        path = filedialog.askopenfilename(parent=self, title="导入板厂确认的逐层叠构", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            data = import_stackup(path, self.layer_count.get().strip(), self.copper.get())
        except (ValueError, OSError) as exc:
            messagebox.showwarning("叠构导入失败", str(exc), parent=self)
            return
        self.stackup = data["rows"]
        self.stackup_sources = data["input_fingerprints"]
        self.layer_count.set(str(data["count"]))
        self.stack_status.set(f"已导入 {data['count']} 铜层叠构：{os.path.basename(path)}；未给铜厚的内层不会套用外层值。")

    def run(self):
        if not self.ready:
            return
        try:
            design = {"copper_um": normalize_copper_um(self.copper.get()), "stackup": deepcopy(self.stackup)}
            if self.layer_count.get().strip():
                value = float(self.layer_count.get())
                if not value.is_integer() or value < 1:
                    raise ValueError("实际铜层数必须为正整数")
                design["layer_count"] = int(value)
            if self.shared_params is not None:
                design = self.shared_params.read_design()
                self.stackup_sources = self.shared_params.stackup_sources
            request = GerberAnalysisRequest(self.source.get().strip().strip('"'),
                tuple(self.info["input_fingerprints"]), design, self.factory.get(), self.board_type.get(),
                self.level.get(), self.geometry_data.get().strip(), tuple(self.stackup_sources))
        except ValueError as exc:
            messagebox.showwarning("参数错误", str(exc), parent=self)
            return
        self.invalidate()
        revision = self.revision
        self.state = "running"
        def done(result):
            if revision != self.revision:
                self.status.set("分析期间输入已变化，旧结果已丢弃，请重新分析。")
                return
            self.last_result = result
            self.state = "complete"
            self.results.render_capability(result)
            self.status.set(f"Gerber 分析完成 · {len(result['gerber_layers'])} 个图层 · 不符合 {result['error_count']} · "
                            f"待复核 {result['warning_count']} · {result.get('verdict', '')}；覆盖缺口请查看结果页签。")
            self.results.result_var.set(self.status.get())
            self.on_change()
        self.tasks.submit("gerber-analysis", lambda: run_gerber_analysis(request), done,
            lambda error: self.failed(error, revision, "Gerber 分析失败"))
        self.status.set("正在后台执行 Gerber / NC Drill 几何检查…")

    def preview(self):
        path = (self.last_result or {}).get("gerber_preview")
        if path and Path(path).is_file():
            import webbrowser
            webbrowser.open(Path(path).as_uri())

    def export(self, ext=".xlsx", hide_low_priority=False, preview=False):
        if not self.last_result:
            return
        from tempfile import mkdtemp
        result = deepcopy(self.last_result)
        source = Path(result["input_gerber"])
        if preview:
            path = str(Path(mkdtemp(prefix="evan-gerber-report-")) / "Gerber报告预览.xlsx")
        else:
            path = filedialog.asksaveasfilename(parent=self, title="导出 Gerber 分析报告", defaultextension=ext,
                initialfile=source.stem + "_Gerber报告" + ext,
                filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv")])
        if not path:
            return
        target = Path(path).resolve()
        # Never publish reports into the input snapshot or overwrite its files.
        for record in result["input_fingerprints"]:
            input_path = Path(record["path"])
            if target == input_path or (input_path.is_dir() and target.is_relative_to(input_path)):
                messagebox.showwarning("报告保存位置", "报告不能覆盖输入文件或保存到被分析的 Gerber 目录内，请选择其他目录。", parent=self)
                return
        meta = {"board_name": source.stem, "gerber_path": str(source), "rule_set": "独立 Gerber 几何检查",
                "hide_low_priority": hide_low_priority}
        def done(paths):
            if preview:
                os.startfile(path)
            else:
                messagebox.showinfo("导出完成", "\n".join(paths), parent=self)
        self.tasks.submit("report-preview" if preview else "report-export",
            lambda: export_report("pcb", path, result, board_meta=meta), done,
            lambda error: messagebox.showerror("报告导出失败", error, parent=self))

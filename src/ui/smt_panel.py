"""SMT workbench tab; backend profiles define the quantitative input form."""
import tkinter as tk
from copy import deepcopy
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from src.application.smt_analysis import (
    analyze_smt_workflow,
    design_facts,
    load_smt_profile,
)
from src.application.smt_analysis import STATUSES, analyze_smt, number
from src.ui.i18n import translate
from src.ui.paths import resource_dir
from src.ui.widgets import scroll_area
from src.ui.responsive import FlowBar


class SMTPanel(ttk.Frame):
    def __init__(self, master, get_design, tasks, on_export, on_locate=None, get_gerber=None,
                  get_block_reason=None, on_result_changed=None, get_pcb_result=None, on_stage=None):
        super().__init__(master, padding=14)
        # on_stage 只接收字符串并转投队列；后台线程不触碰任何 Tk 控件。
        self.on_stage = on_stage
        self.get_design = get_design
        self.tasks = tasks
        self.on_export = on_export
        self.on_locate = on_locate
        self.get_gerber = get_gerber or (lambda: "")
        self.get_block_reason = get_block_reason or (lambda: "")
        self.on_result_changed = on_result_changed
        self.get_pcb_result = get_pcb_result or (lambda: None)
        self.bom_path = tk.StringVar(master=self)
        self.gerber_path = tk.StringVar(master=self)
        self.auto_sources = tk.BooleanVar(master=self, value=True)
        self.gerber_confirmed = tk.BooleanVar(master=self, value=False)
        self.assembly_rules_path=tk.StringVar(master=self)
        self.odb_path=tk.StringVar(master=self)
        self.odb_step=tk.StringVar(master=self)
        self.odb_confirmed=tk.BooleanVar(master=self,value=False)
        self.step_path=tk.StringVar(master=self)
        self.model_path=tk.StringVar(master=self)
        self._revision = 0
        self._render_id = None
        self.bind("<Destroy>", self._destroyed, add="+")
        self.doc = None
        self.baseline = None
        self.last_result = None
        self._evidence_signature = None
        self.fields = {}
        self.key = tk.StringVar()
        # 产线下拉显示业务名称，内部仍用稳定 key；测试或脚本改 key 时界面同步。
        self.combo_label = tk.StringVar()
        self._profile_keys = {}
        self.evidence_path = tk.StringVar()
        self.evidence_path.trace_add("write", lambda *_: self.invalidate())
        self.meta = tk.StringVar(value="选择 SMT 供应商与产线后执行能力比较")
        self.summary = tk.StringVar(value="设计数据共用 PCB 页签；工艺实测值在本页明确输入")
        bar = FlowBar(self)
        bar.pack(fill=tk.X)
        bar.add(ttk.Label(bar, text="SMT 供应商", style="Section.TLabel"))
        self.supplier = tk.StringVar(master=self, value="立景 / Lijing")
        self.supplier_combo = ttk.Combobox(bar, textvariable=self.supplier, state="readonly", width=18,
            values=("立景 / Lijing", "同兴达 / TXD", "外部配置"))
        bar.add(self.supplier_combo)
        self.supplier_combo.bind("<<ComboboxSelected>>", lambda _: self._select_supplier())
        self.import_button = ttk.Button(bar, text="选择外部能力文件…", command=self.import_yaml)
        bar.add(self.import_button)
        self.combo = ttk.Combobox(bar, textvariable=self.combo_label, state="readonly", width=24)
        bar.add(self.combo)
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self._select_profile())
        self.run_button = ttk.Button(bar, text="仅分析 SMT", command=self.run)
        bar.add(self.run_button)
        bar.add(ttk.Button(bar, text="补充资料 / 模型…", command=self._toggle_sources))
        self.sources_frame = ttk.Frame(self, padding=(10, 8))
        options = FlowBar(self.sources_frame)
        options.pack(fill=tk.X, pady=(6, 0))
        self.compare_baseline = tk.BooleanVar(value=True)
        self.compare_baseline.trace_add("write", lambda *_: self.invalidate())
        options.add(ttk.Checkbutton(options, text="同时检查公司内部 SMT 基线", variable=self.compare_baseline))
        options.add(ttk.Button(options, text="选择对象资料 JSON", command=self._browse_evidence))
        options.add(ttk.Button(options, text="生成对象资料模板", command=self._save_template))
        ttk.Label(self.sources_frame, textvariable=self.evidence_path, wraplength=1050).pack(anchor=tk.W)
        source_bar = FlowBar(self.sources_frame)
        source_bar.pack(fill=tk.X, pady=4)
        source_bar.add(ttk.Checkbutton(source_bar, text="自动关联 PCB 导出 / Gerber / BOM", variable=self.auto_sources))
        self.bom_button = ttk.Button(source_bar, text="导入 BOM 表", command=self._browse_bom)
        source_bar.add(self.bom_button)
        source_bar.add(ttk.Button(source_bar, text="清除 BOM", command=lambda: self.bom_path.set("")))
        source_bar.add(ttk.Button(source_bar, text="导入 SMT Gerber 目录", command=self._browse_gerber))
        source_bar.add(ttk.Checkbutton(source_bar, text="已确认外部 Gerber 与 BRD 同版同原点", variable=self.gerber_confirmed))
        rule_bar=FlowBar(self.sources_frame)
        rule_bar.pack(fill=tk.X,pady=3)
        rule_bar.add(ttk.Button(rule_bar,text="导入装配补充规则 JSON",command=self._browse_assembly_rules))
        rule_bar.add(ttk.Button(rule_bar,text="生成装配补充规则模板",command=self._assembly_rule_template))
        rule_bar.add(ttk.Label(rule_bar,textvariable=self.assembly_rules_path,wraplength=650))
        supplement_bar=FlowBar(self.sources_frame);supplement_bar.pack(fill=tk.X,pady=3)
        for label, command in (("ODB++归档",self._browse_odb),("ODB++目录",self._browse_odb_dir)):
            supplement_bar.add(ttk.Button(supplement_bar,text=label,command=command))
        supplement_bar.add(ttk.Label(supplement_bar,text="step"))
        supplement_bar.add(ttk.Entry(supplement_bar,textvariable=self.odb_step,width=12))
        supplement_bar.add(ttk.Checkbutton(supplement_bar,text="已确认ODB与BRD同版同原点",variable=self.odb_confirmed))
        for label, command in (("STP模型",self._browse_step),("确认模型映射JSON",self._browse_model),
                               ("模型映射模板",self._model_template),("清除ODB/STP",self._clear_supplements)):
            supplement_bar.add(ttk.Button(supplement_bar,text=label,command=command))
        self.supplement_paths=tk.StringVar(master=self)
        ttk.Label(self.sources_frame,textvariable=self.supplement_paths,wraplength=1050).pack(anchor=tk.W)
        ttk.Label(self.sources_frame,text="BRD始终为设计主资料；ODB先核对身份/原点。STP缺CAD内核时需确认机械模型导出。",wraplength=1050).pack(anchor=tk.W)
        self.source_var = tk.StringVar(master=self)
        ttk.Label(self.sources_frame, textvariable=self.source_var, wraplength=1000).pack(fill=tk.X)
        for variable in (self.bom_path, self.gerber_path, self.auto_sources, self.gerber_confirmed,self.assembly_rules_path,
                         self.odb_path,self.odb_step,self.odb_confirmed,self.step_path,self.model_path):
            variable.trace_add("write", lambda *_: self._sources_changed())
        ttk.Label(self, textvariable=self.meta, wraplength=1050).pack(anchor=tk.W, pady=8)
        ttk.Label(self, textvariable=self.summary, wraplength=1050).pack(anchor=tk.W, pady=(0, 8))
        split = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        split.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(split, width=390)
        right = ttk.Frame(split)
        split.add(left, weight=1)
        split.add(right, weight=3)
        tabs = ttk.Notebook(right)
        tabs.pack(fill=tk.BOTH, expand=True)
        quantitative = ttk.Frame(tabs)
        baseline_tab = ttk.Frame(tabs)
        review_tab = ttk.Frame(tabs)
        source_tab = ttk.Frame(tabs)
        assembly_tab=ttk.Frame(tabs)
        tabs.add(quantitative, text="供应商能力检测")
        tabs.add(baseline_tab, text="内部基线对比 / 检测")
        tabs.add(review_tab, text="工艺条件（人工评审）")
        tabs.add(source_tab, text="资料关联 / 分面完整性")
        tabs.add(assembly_tab,text="装配补充检查 / 缺口")
        self.assembly_tree=self._table(assembly_tab,("label","phase","verdict","checked","missing","required"),
            ("检查项","阶段","判定","测量数","未取得数","所需资料/规则"))
        self.source_tree = self._table(source_tab, ("refdes", "side", "status", "boundary", "category", "height", "issues"),
            ("位号", "面别", "关联状态", "轮廓依据", "封装类别", "高度 mm", "冲突/缺失"))
        right = quantitative
        self.baseline_tree = self._table(baseline_tab,
            ("status", "label", "actual", "baseline", "supplier", "match", "unit"),
            ("内部检测状态", "检查项", "输入值", "内部基线", "供应商能力", "能力覆盖基线", "单位"))
        self.baseline_tree.bind("<<TreeviewSelect>>", self._show_baseline)
        self.review_tree = self._table(review_tab, ("id", "category", "item", "value", "status"),
            ("编号", "分类", "工艺条目", "供应商条件", "状态"))
        self.review_detail = tk.Text(review_tab, height=9, wrap=tk.WORD, state=tk.DISABLED)
        self.review_detail.pack(fill=tk.X, pady=(8, 0))
        self.review_tree.bind("<<TreeviewSelect>>", self._show_requirement)
        ttk.Label(left, text="输入值（空白时引用已确认的 PCB 页签字段）").pack(anchor=tk.W)
        _, self.form = scroll_area(left)
        # Create both trees before traced inputs can invalidate the panel.
        columns = ("status", "label", "actual", "limit", "unit", "margin", "source")
        self.tree = ttk.Treeview(right, columns=columns, show="headings")
        for col, label, width in zip(columns, ["状态", "检查项", "输入", "能力", "单位", "裕量", "来源"],
                                     [90, 190, 75, 95, 55, 75, 180]):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, minwidth=45)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sy = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.tree.yview)
        sy.grid(row=0, column=1, sticky="ns")
        sx = ttk.Scrollbar(right, orient=tk.HORIZONTAL, command=self.tree.xview)
        sx.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        self.detail = tk.Text(right, height=7, wrap=tk.WORD, state=tk.DISABLED)
        self.detail.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)
        ttk.Label(right, text="对象问题：双击定位到 Allegro").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.findings = ttk.Treeview(right, columns=("status", "rule", "ref", "other", "side", "x", "y", "actual", "source"), show="headings", height=7)
        for col, title, width in zip(self.findings["columns"], ("状态", "规则", "位号", "相邻位号", "面", "X mm", "Y mm", "实测", "来源"), (70, 115, 85, 85, 65, 75, 75, 70, 160)):
            self.findings.heading(col, text=title)
            self.findings.column(col, width=width, minwidth=45)
        self.findings.grid(row=4, column=0, sticky="nsew")
        self.findings.bind("<Double-1>", self._locate_finding)
        for status, color in [("pass", "#1E7B34"), ("fail", "#C00000"),
                              ("missing", "#777777"), ("unconfigured", "#BF8F00"),
                              ("review", "#BF8F00"), ("not_applicable", "#777777")]:
            self.tree.tag_configure(status, foreground=color)
            self.findings.tag_configure(status, foreground=color)
            self.baseline_tree.tag_configure(status, foreground=color)
        self.availability_var = tk.StringVar()
        ttk.Label(self, textvariable=self.availability_var, wraplength=1050).pack(anchor=tk.W, before=bar)
        self.refresh_availability()

    @staticmethod
    def _table(parent, columns, labels):
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        for col, label in zip(columns, labels):
            tree.heading(col, text=label)
            tree.column(col, width=160, minwidth=60)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return tree

    def load_builtin(self):
        if "smt-profile" in self.tasks.pending:
            return
        try:
            root = Path(resource_dir()) / "config"
            self.baseline = load_smt_profile(root / "internal/smt/smt_design_rules.yaml")
            filename = self._builtin_profile()
            if filename is None:
                self.summary.set("供应商未识别：请选择「立景 / Lijing」「同兴达 / TXD」或导入外部能力文件")
                return
            self._apply_profile(load_smt_profile(root / "suppliers/smt" / filename))
        except (OSError, ValueError) as exc:
            self.summary.set(f"内置 SMT 配置加载失败：{exc}；可手动导入 YAML")

    def _builtin_profile(self):
        """按厂商标识选择内置文件；拼错/乱码不能悄悄退回另一家能力。"""
        value = self.supplier.get().upper()
        if "TXD" in value or "同兴达" in self.supplier.get():
            return "smt_capability_txd.yaml"
        if "LIJING" in value or "立景" in self.supplier.get():
            return "smt_capability_lijing.yaml"
        return None

    def _select_supplier(self):
        if self._builtin_profile() is None: self.import_yaml()
        else: self.load_builtin()

    def _toggle_sources(self):
        if self.sources_frame.winfo_manager(): self.sources_frame.pack_forget()
        else: self.sources_frame.pack(fill=tk.X, after=self.import_button.master)

    def _save_template(self):
        from src.application.reports import write_smt_template
        try:
            design = self.get_design()
            path = filedialog.asksaveasfilename(parent=self.winfo_toplevel(), title="保存 SMT 对象资料模板",
                defaultextension=".json", initialfile="smt_objects.json", filetypes=[("JSON", "*.json")])
            if path:
                write_smt_template(design["_input_brd"], path)
                self.summary.set("模板已生成；请填写真实对象，确认坐标与完整性后再导入，不能把模板示例当板级证据")
        except (OSError, ValueError) as exc:
            messagebox.showerror("SMT 模板", str(exc), parent=self.winfo_toplevel())

    def refresh_availability(self):
        reason = self.get_block_reason()
        busy = any(key in self.tasks.pending for key in ("smt-analysis", "smt-profile", "smt-bom-import", "pcb-analysis", "gerber-analysis"))
        self.run_button.configure(state=tk.NORMAL if not reason and self.doc and not busy else tk.DISABLED)
        self.availability_var.set(reason or ("请导入 SMT 厂商 YAML，确认工艺输入后分析" if not self.doc
                                            else "BRD 已导入 · SMT 能力配置已加载"))

    def _result_changed(self):
        self.refresh_availability()
        if self.on_result_changed:
            self.on_result_changed()

    def _browse_evidence(self):
        path = filedialog.askopenfilename(parent=self.winfo_toplevel(), title="选择 SMT 对象资料",
                                          filetypes=[("SMT 对象资料", "*.json")])
        if path:
            self.evidence_path.set(path)

    def _browse_bom(self):
        path = filedialog.askopenfilename(parent=self.winfo_toplevel(), title="导入同版 BOM（位号精确关联）",
            filetypes=[("BOM", "*.csv *.xlsx *.xlsm *.tsv *.txt")])
        if path:
            from src.application.smt_bom import inspect_bom
            self.bom_button.configure(state=tk.DISABLED)
            revision = self._revision
            def done(bom):
                if revision != self._revision:
                    self.summary.set("BOM 读取期间输入已变化，未采用旧导入结果")
                    return
                self.bom_path.set(path)
                self.summary.set(f"BOM 已读取 {len(bom['rows'])} 个位号、{bom['stats'].get('alternate_records',0)} 条副料；按明确主料关联，主副不当面别/DNP。")
            self.tasks.submit("smt-bom-import", lambda: inspect_bom(path), done,
                lambda error: messagebox.showerror("BOM 导入失败", error, parent=self.winfo_toplevel()),
                lambda: self.bom_button.configure(state=tk.NORMAL))

    def _browse_gerber(self):
        path = filedialog.askdirectory(parent=self.winfo_toplevel(), title="选择 SMT Gerber 目录")
        if path:
            self.gerber_confirmed.set(False)
            self.gerber_path.set(path)

    def _browse_odb(self):
        path=filedialog.askopenfilename(parent=self.winfo_toplevel(),title="ODB++归档",filetypes=[("ODB++","*.zip *.tgz *.tar *.gz")])
        if path:self.odb_confirmed.set(False);self.odb_path.set(path)

    def _browse_odb_dir(self):
        path=filedialog.askdirectory(parent=self.winfo_toplevel(),title="ODB++ Job目录")
        if path:self.odb_confirmed.set(False);self.odb_path.set(path)

    def _browse_step(self):
        path=filedialog.askopenfilename(parent=self.winfo_toplevel(),title="STP实体/装配文件",filetypes=[("STEP","*.stp *.step")])
        if path:self.step_path.set(path)

    def _browse_model(self):
        path=filedialog.askopenfilename(parent=self.winfo_toplevel(),title="当前STP/BRD确认模型导出",filetypes=[("模型映射","*.json")])
        if path:self.model_path.set(path)

    def _clear_supplements(self):
        for value in (self.odb_path,self.odb_step,self.step_path,self.model_path):value.set("")
        self.odb_confirmed.set(False)

    def _model_template(self):
        try:
            design=self.get_design()
            if not design.get("_input_brd") or not self.step_path.get():raise ValueError("请先导入BRD并选择STP，模板将绑定两者哈希")
            path=filedialog.asksaveasfilename(parent=self.winfo_toplevel(),title="保存当前BRD/STP模型映射模板",defaultextension=".json",initialfile="step_model_mapping.json")
            if not path:return
            from src.application.reports import write_step_model_template
            write_step_model_template(design["_input_brd"],self.step_path.get(),path)
            self.summary.set("空映射模板已生成；请由CAD导出真实投影与高度并确认，不以STP点云或文件名猜本体")
        except (OSError,ValueError) as exc:messagebox.showerror("STP模型模板",str(exc),parent=self.winfo_toplevel())

    def _browse_assembly_rules(self):
        path=filedialog.askopenfilename(parent=self.winfo_toplevel(),title="选择有确认来源的装配补充规则",filetypes=[("JSON","*.json")])
        if path:self.assembly_rules_path.set(path)

    def _assembly_rule_template(self):
        from src.application.reports import write_assembly_rule_template
        path=filedialog.asksaveasfilename(parent=self.winfo_toplevel(),title="保存装配补充规则模板",initialfile="assembly_rules.json",defaultextension=".json",filetypes=[("JSON","*.json")])
        if path:
            try:
                write_assembly_rule_template(path)
                self.summary.set("模板已生成；填写真实工艺阈值及确认来源，不能把外部报告的三档值直接当唯一规则")
            except (OSError,ValueError) as exc:
                messagebox.showerror("装配规则模板",str(exc),parent=self.winfo_toplevel())

    def _sources_changed(self):
        self.source_var.set("BOM：" + (self.bom_path.get() or "未获取") + "\nGerber：" + (self.gerber_path.get() or "优先本次 PCB 分析导出资料"))
        self.invalidate()

    def check_source_changes(self):
        if self.get_block_reason():
            if self.last_result:
                self.invalidate()
            self.refresh_availability()
            return
        if not self.last_result:
            return
        if self.last_result.get("linked_source_fingerprints"):
            # Lightweight change detector; full hashes are checked before exporting.
            signatures = self.last_result.get("_linked_stat_signature", [])
            try:
                current = [(path, Path(path).stat().st_size, Path(path).stat().st_mtime_ns) for path, _, _ in signatures]
            except OSError:
                current = None
            if current != signatures:
                self.invalidate()
                self.summary.set("PCB/BOM/制造资料已变化，请重新执行 SMT 分析")
                return
        from src.application.smt_analysis import verify_profile_sources
        try:
            verify_profile_sources(self.last_result)
        except (OSError, ValueError):
            self.invalidate()
            self.summary.set("SMT 能力配置已变化，请重新导入并分析")
            return
        if self._evidence_signature is None:
            return
        from src.application.smt_analysis import source_signature
        try:
            snapshot = self.last_result.get("evidence_fingerprints", {})
            current = source_signature(snapshot.get("evidence_path") or self.evidence_path.get(),
                self.last_result.get("input_brd", ""), snapshot.get("gerber_dir", ""))
        except (OSError, ValueError):
            current = None
        if current != self._evidence_signature:
            self.invalidate()
            self.summary.set("SMT 输入文件已变化，请重新确认资料并分析")
            return
        # Content hashing happens in the background export service, not this
        # Tk callback. A large Gerber set must not freeze the UI.

    def _locate_finding(self, _event=None):
        if self.on_locate and self.last_result and self.findings.selection():
            item = self.last_result.get("displayed_findings",self.last_result.get("object_findings",[]))[int(self.findings.selection()[0])]
            if item.get("x_mm") is None or item.get("y_mm") is None:
                messagebox.showinfo("SMT 问题定位", "该项没有已确认的对象坐标", parent=self.winfo_toplevel())
            else:
                self.on_locate(item)

    def invalidate(self, clear_inputs=False):
        self._revision += 1
        if self._render_id is not None:
            self.after_cancel(self._render_id)
            self._render_id = None
        self.last_result = None
        self._evidence_signature = None
        self.tree.delete(*self.tree.get_children())
        self.findings.delete(*self.findings.get_children())
        self.baseline_tree.delete(*self.baseline_tree.get_children())
        self.source_tree.delete(*self.source_tree.get_children())
        self.assembly_tree.delete(*self.assembly_tree.get_children())
        self._detail("")
        self.summary.set("输入已变化，请重新执行 SMT 分析")
        self.supplement_paths.set("ODB++："+self.odb_path.get()+"  STP："+self.step_path.get()+"  映射："+self.model_path.get())
        if clear_inputs:
            self.evidence_path.set("")
            self.bom_path.set("")
            self.gerber_path.set("")
            self.gerber_confirmed.set(False)
            self.assembly_rules_path.set("")
            self._clear_supplements()
            for value, _unit in self.fields.values():
                value.set("")
        self._result_changed()

    def _destroyed(self, event):
        if event.widget is self and self._render_id is not None:
            self.after_cancel(self._render_id)
            self._render_id = None

    def import_yaml(self):
        path = filedialog.askopenfilename(parent=self.winfo_toplevel(), title="选择 SMT 厂商能力 YAML",
                                           filetypes=[("YAML", "*.yaml *.yml")])
        if not path:
            return
        self.import_button.configure(state=tk.DISABLED)
        self.invalidate()
        revision = self._revision

        def complete(doc):
            if revision == self._revision:
                self.supplier.set("外部配置")
                self._apply_profile(doc)
            else:
                self.summary.set("配置读取期间输入已变化，请重新导入 YAML")

        self.tasks.submit("smt-profile", lambda: load_smt_profile(path), complete,
                          lambda error: messagebox.showerror("SMT 配置无效", error, parent=self.winfo_toplevel()),
                          lambda: self.import_button.configure(state=tk.NORMAL))

    def _apply_profile(self, doc):
        self.doc = doc
        self._profile_keys = {str(profile.get("label") or key): key for key, profile in doc["profiles"].items()}
        labels = list(self._profile_keys)
        self.combo.configure(values=labels)
        self.combo_label.set(labels[0])
        self.key.set(self._profile_keys[labels[0]])
        self.meta.set(f"厂商：{doc['meta']['factory']} | 版本：{doc['meta']['revision']}\n{doc['path']}")
        self.review_tree.delete(*self.review_tree.get_children())
        from src.application.smt_analysis import manual_requirements
        self._requirements = manual_requirements(doc, self.key.get())
        for idx, row in enumerate(self._requirements):
            self.review_tree.insert("", tk.END, iid=str(idx), values=(row["id"], row.get("category", ""),
                row.get("item", ""), row.get("supplier_value", ""), "待人工评审"))
        self._render_fields()
        self._result_changed()

    def _select_profile(self):
        key = self._profile_keys.get(self.combo_label.get())
        if key is None:
            self.summary.set("产线名称不在当前能力文件中，请重新选择")
            return
        self.key.set(key)
        self._render_fields()

    def _render_fields(self):
        self.invalidate()
        for label, key in self._profile_keys.items():
            if key == self.key.get():
                self.combo_label.set(label)
        self.review_tree.delete(*self.review_tree.get_children())
        from src.application.smt_analysis import manual_requirements
        self._requirements = manual_requirements(self.doc, self.key.get())
        for idx, row in enumerate(self._requirements):
            self.review_tree.insert("",tk.END,iid=str(idx),values=(row["id"],row.get("category") or "",row.get("item") or row.get("process") or "未填写",row.get("supplier_value") or "未填写","待人工评审"))
        for child in self.form.winfo_children():
            child.destroy()
        self.fields.clear()
        rules = list(self.doc["profiles"][self.key.get()]["rules"])
        if self.baseline:
            rules += self.baseline["profiles"][next(iter(self.baseline["profiles"]))]["rules"]
        for rule in rules:
            key = (rule["scope"], rule["field"])
            if key in self.fields:
                continue
            value = tk.StringVar()
            value.trace_add("write", lambda *_a: self.invalidate())
            self.fields[key] = (value, rule["unit"])
            row = len(self.fields) - 1
            label = rule["label"] + f" ({rule['unit']})"
            prefix = "工艺实测" if rule["scope"] == "process" else "设计/已确认"
            ttk.Label(self.form, text=f"{label}\n{prefix} · {rule['field']}",
                      wraplength=270).grid(row=row, column=0, sticky="w", pady=7)
            ttk.Entry(self.form, textvariable=value, width=12).grid(row=row, column=1, padx=8)

    def run(self):
        if any(k in self.tasks.pending for k in ("smt-analysis","smt-profile","smt-bom-import","pcb-analysis","gerber-analysis")):
            return
        reason = self.get_block_reason()
        if reason:
            self.refresh_availability()
            messagebox.showwarning("BRD 未就绪", reason, parent=self.winfo_toplevel())
            return
        if not self.doc:
            messagebox.showinfo("SMT 分析", "请先导入 SMT YAML", parent=self.winfo_toplevel())
            return
        try:
            design = self.get_design()
            evidence_path = self.evidence_path.get().strip()
            gerber_dir = self.get_gerber()
            if self.gerber_path.get():
                gerber_dir = self.gerber_path.get()
            if self.odb_path.get():
                if self.gerber_path.get():raise ValueError("请清除手工Gerber路径，或清除ODB++；不混用两套最终层")
                gerber_dir=""
            overrides = {}
            for (scope, field), (value, unit) in self.fields.items():
                txt = value.get().strip()
                if txt:
                    overrides[(scope, field)] = {"value": number(txt, field), "unit": unit,
                                              "source": "人工确认工艺实测" if scope == "process" else "人工确认设计输入"}
            facts = design_facts(design, self.fields, overrides)
        except (OSError, ValueError, TypeError) as exc:
            self.invalidate()
            messagebox.showerror("SMT 分析失败", str(exc), parent=self.winfo_toplevel())
            return
        self.invalidate()
        revision = self._revision
        doc, key = deepcopy(self.doc), self.key.get()
        baseline = deepcopy(self.baseline) if self.compare_baseline.get() else None
        pcb = deepcopy(self.get_pcb_result())
        auto_sources, bom_path, gerber_confirmed = self.auto_sources.get(), self.bom_path.get(), self.gerber_confirmed.get()
        assembly_rules_path=self.assembly_rules_path.get()
        supplement_args=dict(odb_path=self.odb_path.get(),odb_step=self.odb_step.get(),odb_confirmed=self.odb_confirmed.get(),
                             step_path=self.step_path.get(),model_path=self.model_path.get())
        from src.ui.paths import default_output_dir
        output = str(Path(default_output_dir()) / "SMT_Sources")
        Path(output).mkdir(parents=True, exist_ok=True)
        self.run_button.configure(state=tk.DISABLED)
        self.summary.set("正在后台关联 PCB/Gerber/BOM 并执行 SMT 分析…" if auto_sources and not evidence_path else "正在后台执行 SMT 分析…")

        def compute():
            return analyze_smt_workflow(doc, key, facts, design, evidence_path, gerber_dir, baseline, analyzer=analyze_smt,
                pcb_result=pcb, bom_path=bom_path, auto_sources=auto_sources, gerber_confirmed=gerber_confirmed,
                output_dir=output, assembly_rules_path=assembly_rules_path,
                progress=(lambda name: self.on_stage("smt_" + name)) if self.on_stage else None,
                **supplement_args)

        def complete(result):
            if self.on_stage:
                self.on_stage("smt_done")
            if revision == self._revision and not self.get_block_reason():
                self.last_result = result
                result["_linked_stat_signature"] = [(row["path"], Path(row["path"]).stat().st_size, Path(row["path"]).stat().st_mtime_ns)
                                                     for row in result.get("linked_source_fingerprints", [])]
                self._evidence_signature = result.get("_source_signature")
                self._render_result(result, revision)
            else:
                self.summary.set("SMT 分析期间输入已变化，已丢弃过期结果，请重新分析")
            self._result_changed()

        if self.on_stage:
            self.on_stage("smt_start")
        self.tasks.submit("smt-analysis", compute, complete,
                          lambda error: messagebox.showerror("SMT 分析失败", error, parent=self.winfo_toplevel()),
                          self._result_changed)

    def capture_analysis(self):
        """Capture every Tk input before overall analysis starts; worker receives no widgets."""
        if not self.doc: raise ValueError("请先选择 SMT 供应商能力")
        overrides = {}
        for (scope, field), (value, unit) in self.fields.items():
            if value.get().strip():
                overrides[(scope, field)] = dict(value=number(value.get(),field),unit=unit,source="人工确认输入")
        if self.odb_path.get() and self.gerber_path.get(): raise ValueError("SMT最终层请选择ODB++或Gerber之一")
        from src.ui.paths import default_output_dir
        return dict(doc=deepcopy(self.doc), key=self.key.get(), fields=tuple(self.fields), overrides=overrides,
            baseline=deepcopy(self.baseline) if self.compare_baseline.get() else None,
            evidence_path=self.evidence_path.get(), gerber_dir="" if self.odb_path.get() else (self.gerber_path.get() or self.get_gerber()),
            auto_sources=self.auto_sources.get(),bom_path=self.bom_path.get(),gerber_confirmed=self.gerber_confirmed.get(),
            assembly_rules_path=self.assembly_rules_path.get(),odb_path=self.odb_path.get(),odb_step=self.odb_step.get(),
            odb_confirmed=self.odb_confirmed.get(),step_path=self.step_path.get(),model_path=self.model_path.get(),
            output_dir=str(Path(default_output_dir()) / "SMT_Sources"))

    def accept_result(self, result):
        self.last_result=result
        result["_linked_stat_signature"]=[(row["path"],Path(row["path"]).stat().st_size,Path(row["path"]).stat().st_mtime_ns)
            for row in result.get("linked_source_fingerprints",[])]
        self._evidence_signature=result.get("_source_signature")
        self._render_result(result,self._revision)
        self._result_changed()

    def _render_result(self, result, revision):
        self.tree.delete(*self.tree.get_children())
        self.findings.delete(*self.findings.get_children())
        self.baseline_tree.delete(*self.baseline_tree.get_children())
        self.source_tree.delete(*self.source_tree.get_children())
        self.assembly_tree.delete(*self.assembly_tree.get_children())
        for row in result.get("assembly_coverage",[]):
            from src.application.smt_analysis import INSPECTION_LABELS,INSPECTION_VERDICTS
            self.assembly_tree.insert("","end",values=(row["label"],INSPECTION_LABELS.get(row["phase"],row["phase"]),
                INSPECTION_VERDICTS.get(row["verdict"],row["verdict"]),row["checked"],row["missing"],row["required_data"]))
        for row in result.get("source_association", {}).get("rows", []):
            self.source_tree.insert("", "end", values=(row.get("refdes"), row.get("side"), row.get("status"),
                row.get("boundary"), row.get("category"), row.get("height_mm", "未获取"), "；".join(row.get("issues", []))))
        for row in result.get("bom_analysis", {}).get("rows", []):
            self.source_tree.insert("", "end", values=(row["refdes"], row.get("side"), "BOM " + row["status"],
                row.get("footprint"), row.get("part_number"), "", "；".join(row.get("issues", []))))
        for side, complete in result.get("assembly_completeness", {}).items():
            self.source_tree.insert("", "end", values=("完整性", side, "需复核" if not all(complete.values()) else "已取得",
                "", "", "", str(complete)))
        base_items = {row["field"]: row for row in result.get("baseline_result", {}).get("items", [])}
        for idx, row in enumerate(result.get("baseline_comparison", [])):
            item = base_items.get(row["field"], {})
            status = item.get("status", "missing")
            self.baseline_tree.insert("", tk.END, iid=str(idx), tags=(status,), values=(STATUSES[status], row["label"],
                item.get("actual"), row["baseline_limit"], row["supplier_limit"], STATUSES[row["status"]], row["unit"]))
        result["displayed_findings"]=result.get("object_findings",[])+result.get("assembly_findings",[])
        for idx, item in enumerate(result["displayed_findings"]):
            self.findings.insert("", tk.END, iid=str(idx), tags=(item.get("status", "missing"),), values=(
                STATUSES.get(item.get("status"), item.get("status")), item.get("id", ""), item.get("refdes", ""),
                item.get("other_refdes", ""), item.get("side", ""), item.get("x_mm", ""), item.get("y_mm", ""),
                item.get("actual", ""), item.get("source", "")))
        # Insert bounded batches instead of freezing Tk on large supplier files.
        def batch(start=0):
            self._render_id = None
            if not self.winfo_exists() or revision != self._revision:
                return
            for idx in range(start, min(start + 100, len(result["items"]))):
                it = result["items"][idx]
                fmt = lambda v: "—" if v is None else f"{v:.6g}"
                self.tree.insert("", tk.END, iid=str(idx), tags=(it["status"],), values=(
                    translate(STATUSES[it["status"]]), translate(it["label"]), fmt(it["actual"]), it["requirement"],
                    it["unit"], fmt(it["margin"]), translate(it["source"] or it["reason"])))
            if start + 100 < len(result["items"]):
                self._render_id = self.after(1, lambda: batch(start + 100))
        batch()
        r = result
        self.summary.set(f"{r['profile_label']} | {r['verdict']} | 风险：{r['risk']} | 判定覆盖 {r['coverage']:.0%}\n"
                          + r.get("inspection_mode", "") + f" · 非定量待评审 {r.get('manual_review_count', 0)} 项\n"
                          + (f"内部基线：{r['baseline_verdict']}\n" if r.get("baseline_verdict") else "")
                           + r["conclusion_note"])
        if r.get("generated_evidence"):
            self.source_var.set("BOM：" + (self.bom_path.get() or "未获取") + "\n自动对象资料：" + r["generated_evidence"])
        if r.get("bom_analysis"):
            b = r["bom_analysis"]
            self.summary.set(self.summary.get() + f"\nBOM {b['total']} 位号／匹配 {b['matched']}／DNP {b['dnp']}／核对问题 {len(b['findings'])}")

    def _show_baseline(self, _event=None):
        if self.last_result and self.baseline_tree.selection():
            row = self.last_result["baseline_comparison"][int(self.baseline_tree.selection()[0])]
            self._detail(f"{row['label']}\n能力对比：{STATUSES[row['status']]} · {row['reason']}\n"
                         "供应商检测与内部基线检测分别判定；满足供应商最低能力不等于满足内部设计要求。")

    def _show_requirement(self, _event=None):
        if not self.review_tree.selection():
            return
        row = self._requirements[int(self.review_tree.selection()[0])]
        text = "\n".join(f"{label}：{row.get(key, '')}" for key, label in (
            ("item", "条目"), ("supplier_value", "供应商条件"), ("equipment", "设备"),
            ("inspection", "检测方法"), ("design_guidance", "设计建议"), ("reference", "参考"), ("reason", "评审口径")))
        self.review_detail.configure(state=tk.NORMAL)
        self.review_detail.delete("1.0", tk.END)
        self.review_detail.insert("1.0", text)
        self.review_detail.configure(state=tk.DISABLED)

    def _detail(self, text):
        self.detail.configure(state=tk.NORMAL)
        self.detail.delete("1.0", tk.END)
        self.detail.insert("1.0", translate(text))
        self.detail.configure(state=tk.DISABLED)

    def show_detail(self, _event=None):
        if self.last_result and self.tree.selection():
            it = self.last_result["items"][int(self.tree.selection()[0])]
            self._detail(f"{it['id']} · {it['label']}\n依据：{it['reason']}\n来源：{it['source'] or '未提供'}\n"
                         f"建议：{it['advice']}\n备注：{it['note']}")

    def export(self):
        self.on_export()

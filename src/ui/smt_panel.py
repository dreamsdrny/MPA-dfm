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


class SMTPanel(ttk.Frame):
    def __init__(self, master, get_design, tasks, on_export, on_locate=None, get_gerber=None,
                 get_block_reason=None, on_result_changed=None):
        super().__init__(master, padding=14)
        self.get_design = get_design
        self.tasks = tasks
        self.on_export = on_export
        self.on_locate = on_locate
        self.get_gerber = get_gerber or (lambda: "")
        self.get_block_reason = get_block_reason or (lambda: "")
        self.on_result_changed = on_result_changed
        self._revision = 0
        self._render_id = None
        self.bind("<Destroy>", self._destroyed, add="+")
        self.doc = None
        self.baseline = None
        self.last_result = None
        self._evidence_signature = None
        self.fields = {}
        self.key = tk.StringVar()
        self.evidence_path = tk.StringVar()
        self.evidence_path.trace_add("write", lambda *_: self.invalidate())
        self.meta = tk.StringVar(value="导入 SMT 厂商 YAML，选择产线/等级后执行能力比较")
        self.summary = tk.StringVar(value="设计数据共用 PCB 页签；工艺实测值在本页明确输入")
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X)
        self.import_button = ttk.Button(bar, text="导入 SMT YAML", command=self.import_yaml)
        self.import_button.pack(side=tk.LEFT)
        ttk.Button(bar, text="内置立景能力", command=self.load_builtin).pack(side=tk.LEFT, padx=6)
        self.combo = ttk.Combobox(bar, textvariable=self.key, state="readonly", width=24)
        self.combo.pack(side=tk.LEFT, padx=10)
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self._render_fields())
        self.run_button = ttk.Button(bar, text="执行 SMT 分析", command=self.run)
        self.run_button.pack(side=tk.LEFT)
        ttk.Button(bar, text="选择对象资料 JSON", command=self._browse_evidence).pack(side=tk.LEFT, padx=8)
        options = ttk.Frame(self)
        options.pack(fill=tk.X, pady=(6, 0))
        self.compare_baseline = tk.BooleanVar(value=True)
        self.compare_baseline.trace_add("write", lambda *_: self.invalidate())
        ttk.Checkbutton(options, text="同时检查公司内部 SMT 基线", variable=self.compare_baseline).pack(side=tk.LEFT)
        ttk.Button(options, text="生成对象资料模板", command=self._save_template).pack(side=tk.LEFT, padx=8)
        ttk.Label(self, textvariable=self.evidence_path, wraplength=1050).pack(anchor=tk.W)
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
        tabs.add(quantitative, text="供应商能力检测")
        tabs.add(baseline_tab, text="内部基线对比 / 检测")
        tabs.add(review_tab, text="工艺条件（人工评审）")
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
            self._apply_profile(load_smt_profile(root / "suppliers/smt/smt_capability_lijing.yaml"))
        except (OSError, ValueError) as exc:
            self.summary.set(f"内置 SMT 配置加载失败：{exc}；可手动导入 YAML")

    def _save_template(self):
        from src.application.reports import write_smt_template
        try:
            design = self.get_design()
            path = filedialog.asksaveasfilename(parent=self.winfo_toplevel(), title="保存 SMT 对象资料模板",
                defaultextension=".json", initialfile="smt_objects.json", filetypes=[("JSON", "*.json")])
            if path:
                write_smt_template(design["_input_brd"], path)
                self.summary.set("模板已生成；请按 SMT_TESTING.md 填写真实对象，确认坐标与完整性后再导入")
        except (OSError, ValueError) as exc:
            messagebox.showerror("SMT 模板", str(exc), parent=self.winfo_toplevel())

    def refresh_availability(self):
        reason = self.get_block_reason()
        busy = "smt-analysis" in self.tasks.pending or "smt-profile" in self.tasks.pending
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

    def check_source_changes(self):
        if self.get_block_reason():
            if self.last_result:
                self.invalidate()
            self.refresh_availability()
            return
        if not self.last_result:
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
            current = source_signature(self.evidence_path.get(), self.last_result.get("input_brd", ""), self.get_gerber())
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
            item = self.last_result.get("object_findings", [])[int(self.findings.selection()[0])]
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
        self._detail("")
        self.summary.set("输入已变化，请重新执行 SMT 分析")
        if clear_inputs:
            self.evidence_path.set("")
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
                self._apply_profile(doc)
            else:
                self.summary.set("配置读取期间输入已变化，请重新导入 YAML")

        self.tasks.submit("smt-profile", lambda: load_smt_profile(path), complete,
                          lambda error: messagebox.showerror("SMT 配置无效", error, parent=self.winfo_toplevel()),
                          lambda: self.import_button.configure(state=tk.NORMAL))

    def _apply_profile(self, doc):
        self.doc = doc
        keys = list(doc["profiles"])
        self.combo.configure(values=keys)
        self.key.set(keys[0])
        self.meta.set(f"厂商：{doc['meta']['factory']} | 版本：{doc['meta']['revision']}\n{doc['path']}")
        self.review_tree.delete(*self.review_tree.get_children())
        from src.application.smt_analysis import manual_requirements
        self._requirements = manual_requirements(doc)
        for idx, row in enumerate(self._requirements):
            self.review_tree.insert("", tk.END, iid=str(idx), values=(row["id"], row.get("category", ""),
                row.get("item", ""), row.get("supplier_value", ""), "待人工评审"))
        self._render_fields()
        self._result_changed()

    def _render_fields(self):
        self.invalidate()
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
        if "smt-analysis" in self.tasks.pending or "smt-profile" in self.tasks.pending:
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
        self.run_button.configure(state=tk.DISABLED)
        self.summary.set("正在后台执行 SMT 分析…")

        def compute():
            return analyze_smt_workflow(doc, key, facts, design, evidence_path, gerber_dir, baseline, analyzer=analyze_smt)

        def complete(result):
            if revision == self._revision and not self.get_block_reason():
                self.last_result = result
                self._evidence_signature = result.get("_source_signature")
                self._render_result(result, revision)
            else:
                self.summary.set("SMT 分析期间输入已变化，已丢弃过期结果，请重新分析")
            self._result_changed()

        self.tasks.submit("smt-analysis", compute, complete,
                          lambda error: messagebox.showerror("SMT 分析失败", error, parent=self.winfo_toplevel()),
                          self._result_changed)

    def _render_result(self, result, revision):
        self.tree.delete(*self.tree.get_children())
        self.findings.delete(*self.findings.get_children())
        self.baseline_tree.delete(*self.baseline_tree.get_children())
        base_items = {row["field"]: row for row in result.get("baseline_result", {}).get("items", [])}
        for idx, row in enumerate(result.get("baseline_comparison", [])):
            item = base_items.get(row["field"], {})
            status = item.get("status", "missing")
            self.baseline_tree.insert("", tk.END, iid=str(idx), tags=(status,), values=(STATUSES[status], row["label"],
                item.get("actual"), row["baseline_limit"], row["supplier_limit"], STATUSES[row["status"]], row["unit"]))
        for idx, item in enumerate(result.get("object_findings", [])):
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

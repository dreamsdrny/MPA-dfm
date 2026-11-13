"""SMT workbench tab; backend profiles define the quantitative input form."""
from copy import deepcopy
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from src.dfm_inputs import FIELD_META
from src.smt_engine import (STATUSES, UNITS, analyze_smt,
                            load_smt_profile, number)
from src.ui.widgets import scroll_area
from src.ui.i18n import translate

DESIGN_UNITS = {key: meta.get("unit", "mm") for key, meta in FIELD_META.items()}
DESIGN_UNITS.update(chip_pad_symmetry_ratio="ratio", bga_pitch="mm",
                    bga_pad_annular="mm", mark_to_edge_mm="mm",
                    tooling_hole_count="count", panel_utilization="ratio")


class SMTPanel(ttk.Frame):
    def __init__(self, master, get_design, tasks, on_export):
        super().__init__(master, padding=14)
        self.get_design = get_design
        self.tasks = tasks
        self.on_export = on_export
        self._revision = 0
        self._render_id = None
        self.bind("<Destroy>", self._destroyed, add="+")
        self.doc = None
        self.last_result = None
        self.fields = {}
        self.key = tk.StringVar()
        self.meta = tk.StringVar(value="导入 SMT 厂商 YAML，选择产线/等级后执行能力比较")
        self.summary = tk.StringVar(value="设计数据共用 PCB 页签；工艺实测值在本页明确输入")
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X)
        self.import_button = ttk.Button(bar, text="导入 SMT YAML", command=self.import_yaml)
        self.import_button.pack(side=tk.LEFT)
        self.combo = ttk.Combobox(bar, textvariable=self.key, state="readonly", width=24)
        self.combo.pack(side=tk.LEFT, padx=10)
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self._render_fields())
        self.run_button = ttk.Button(bar, text="执行 SMT 分析", command=self.run)
        self.run_button.pack(side=tk.LEFT)
        ttk.Label(self, textvariable=self.meta, wraplength=1050).pack(anchor=tk.W, pady=8)
        ttk.Label(self, textvariable=self.summary, wraplength=1050).pack(anchor=tk.W, pady=(0, 8))
        split = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        split.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(split, width=390)
        right = ttk.Frame(split)
        split.add(left, weight=1)
        split.add(right, weight=3)
        ttk.Label(left, text="输入值（空白时引用已确认的 PCB 页签字段）").pack(anchor=tk.W)
        _, self.form = scroll_area(left)
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
        for status, color in [("pass", "#1E7B34"), ("fail", "#C00000"),
                              ("missing", "#777777"), ("unconfigured", "#BF8F00")]:
            self.tree.tag_configure(status, foreground=color)

    def invalidate(self, clear_inputs=False):
        self._revision += 1
        if self._render_id is not None:
            self.after_cancel(self._render_id)
            self._render_id = None
        self.last_result = None
        self.tree.delete(*self.tree.get_children())
        self._detail("")
        self.summary.set("输入已变化，请重新执行 SMT 分析")
        if clear_inputs:
            for value, _unit in self.fields.values():
                value.set("")

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
        self._render_fields()

    def _render_fields(self):
        self.invalidate()
        for child in self.form.winfo_children():
            child.destroy()
        self.fields.clear()
        for rule in self.doc["profiles"][self.key.get()]["rules"]:
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
        if not self.doc:
            messagebox.showinfo("SMT 分析", "请先导入 SMT YAML", parent=self.winfo_toplevel())
            return
        try:
            design = self.get_design()
            facts = {}
            for (scope, field), (value, unit) in self.fields.items():
                txt = value.get().strip()
                if txt:
                    facts[(scope, field)] = {"value": number(txt, field), "unit": unit,
                                             "source": "人工确认工艺实测" if scope == "process" else "人工确认设计输入"}
                elif scope == "design" and field in design and field in DESIGN_UNITS:
                    native = DESIGN_UNITS[field]
                    native = {"个": "count", "层": "count", "%": "percent", "": "ratio"}.get(native, native)
                    if native not in UNITS:
                        continue
                    source = (design.get("_sources", {}).get(field) or {}).get("source")
                    facts[(scope, field)] = {"value": design[field], "unit": native,
                                             "source": source or "PCB页签人工确认输入"}
        except Exception as exc:
            self.invalidate()
            messagebox.showerror("SMT 分析失败", str(exc), parent=self.winfo_toplevel())
            return
        self.invalidate()
        revision = self._revision
        doc, key = deepcopy(self.doc), self.key.get()
        self.run_button.configure(state=tk.DISABLED)
        self.summary.set("正在后台执行 SMT 分析…")

        def compute():
            result = analyze_smt(doc, key, facts)
            result["board_name"] = design.get("board_name", "")
            result["input_brd"] = design.get("_input_brd", "")
            result["analyzed_at"] = datetime.now().isoformat(timespec="seconds")
            return result

        def complete(result):
            if revision == self._revision:
                self.last_result = result
                self._render_result(result, revision)
            else:
                self.summary.set("SMT 分析期间输入已变化，已丢弃过期结果，请重新分析")

        self.tasks.submit("smt-analysis", compute, complete,
                          lambda error: messagebox.showerror("SMT 分析失败", error, parent=self.winfo_toplevel()),
                          lambda: self.run_button.configure(state=tk.NORMAL))

    def _render_result(self, result, revision):
        self.tree.delete(*self.tree.get_children())
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
                         + r["conclusion_note"])

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

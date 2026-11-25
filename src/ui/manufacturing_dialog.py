"""不依赖 BRD 的 Gerber/ODB++/BOM 协同工作台。"""
import json
import os
import tkinter as tk
from copy import deepcopy
from tkinter import filedialog, messagebox, ttk

from src.application.manufacturing import (load_manufacturing_project, require_project_sources,
                                           run_object_dfm)
from src.application.reports import export_coordination, preview_coordination
from src.application.reports import STATUS_LABELS
from src.ui.paths import default_output_dir
from src.ui.responsive import FlowBar, scroll_workbench
from src.ui.tasks import TaskRunner
from src.ui.theme import apply_app_icon
from src.ui.widgets import field_row, make_tree


class ManufacturingDialog:
    def __init__(self, master, out_dir=""):
        self.win = tk.Toplevel(master)
        self.win.title("Gerber / ODB++ ↔ BOM 制造资料协同")
        self.win.geometry("1100x780")
        self.win.minsize(760, 540)
        apply_app_icon(self.win)
        self.layout_path = tk.StringVar()
        self.bom_path = tk.StringVar()
        self.placement_path = tk.StringVar()
        self.kind = tk.StringVar(value="auto")
        self.step = tk.StringVar()
        self.alignment = tk.BooleanVar(value=False)
        self.search = tk.StringVar()
        self.status = tk.StringVar(value="选择 Gerber / ODB++ 资料及 BOM；不需要 BRD")
        self.out_dir = out_dir or default_output_dir()
        self.project = None
        self.revision = 0
        self.rows = {}
        self.content = scroll_workbench(self.win)
        inputs = ttk.LabelFrame(self.content, text="制造资料与器件信息", padding=12)
        inputs.pack(fill=tk.X, padx=10, pady=8)
        field_row(inputs, "Gerber 单文件/目录/ZIP；ODB++ 目录/ZIP/TGZ", self.layout_path,
                  self._browse_layout, browse_text="文件")
        toolbar = FlowBar(inputs)
        toolbar.pack(fill=tk.X)
        toolbar.add(ttk.Button(toolbar, text="选择制造资料目录", command=self._browse_folder))
        toolbar.add(ttk.Label(toolbar, text="格式"))
        toolbar.add(ttk.Combobox(toolbar, textvariable=self.kind, values=("auto", "gerber", "odb"),
                                   state="readonly", width=10))
        toolbar.add(ttk.Label(toolbar, text="ODB++ step"))
        self.step_combo = ttk.Combobox(toolbar, textvariable=self.step, values=(), width=20)
        toolbar.add(self.step_combo)
        field_row(inputs, "BOM (.csv / .xlsx / .xlsm)，可选", self.bom_path, self._browse_bom)
        field_row(inputs, "器件坐标 CSV（普通 Gerber 关联器件时使用），可选", self.placement_path,
                  self._browse_placement)
        ttk.Checkbutton(inputs, text="已核实坐标 CSV 与制造图层的单位、原点、旋转和镜像一致",
                        variable=self.alignment).pack(anchor=tk.W)
        ttk.Label(inputs, text="不猜 Gerber 位号或器件原点；ODB++ 此入口渲染外层铜/阻焊/锡膏/钻孔，非全层 DRC；多 step 必须选择",
                  wraplength=900).pack(anchor=tk.W, pady=5)
        actions = FlowBar(self.content, padding=8)
        actions.pack(fill=tk.X)
        self.load_button = actions.add(ttk.Button(actions, text="导入并协调 BOM", command=self.run))
        self.export_button = actions.add(ttk.Button(actions, text="导出关联报告", command=self.export))
        self.preview_button = actions.add(ttk.Button(actions, text="叠图预览所选器件", command=self.preview))
        self.dfm_button = actions.add(ttk.Button(actions, text="执行 ODB++ 对象 DFM", command=self.run_dfm))
        actions.add(ttk.Label(actions, text="筛选位号/料号/封装"))
        actions.add(ttk.Entry(actions, textvariable=self.search, width=24))
        ttk.Label(self.content, textvariable=self.status, wraplength=950).pack(fill=tk.X, padx=12)
        notebook = ttk.Notebook(self.content)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        comp_tab, layer_tab, notes_tab = [ttk.Frame(notebook) for _ in range(3)]
        for tab, label in ((comp_tab, "器件 / BOM 关联"), (layer_tab, "制造图层"), (notes_tab, "来源 / 缺失 / 冲突")):
            notebook.add(tab, text=label)
        columns = [(key, label, width, 60, "w") for key, label, width in (
            ("refdes", "位号", 80), ("status", "关联状态", 120), ("part_number", "料号", 130),
            ("mpn", "MPN", 140), ("value", "值", 100), ("package", "封装", 120),
            ("side", "面别", 70), ("dnp", "不贴", 60), ("x", "X mm", 90),
            ("y", "Y mm", 90), ("coordinate", "坐标类型", 160), ("aligned", "空间对齐", 90))]
        self.tree = make_tree(comp_tab, columns, height=12)
        self.tree.bind("<<TreeviewSelect>>", self._detail, add="+")
        self.layers = make_tree(layer_tab, [(key, label, width, 80, "w") for key, label, width in
                                          (("name", "文件/层", 220), ("role", "角色", 130),
                                           ("count", "图元数", 90), ("status", "状态", 100),
                                           ("source", "来源", 500))], height=12)
        self.notes = tk.Text(notes_tab, wrap="word", height=15, state="disabled")
        self.notes.pack(fill=tk.BOTH, expand=True)
        self.tasks = TaskRunner(self.win, self._busy)
        for var in (self.layout_path, self.bom_path, self.placement_path, self.kind, self.step, self.alignment):
            var.trace_add("write", self._changed)
        self.search.trace_add("write", lambda *_: self._render_components())
        self._busy(())

    def _changed(self, *_):
        self.revision += 1
        self.project = None
        self.rows.clear()
        self.tree.delete(*self.tree.get_children())
        self.layers.delete(*self.layers.get_children())
        self._set_notes("")
        self.status.set("输入已变化，请重新导入并协调")
        self._busy(tuple(self.tasks.pending))

    def _busy(self, pending):
        self.load_button.config(state="disabled" if pending else "normal")
        state = "normal" if not pending and self.project and self.project["status"] != "needs_step" else "disabled"
        self.export_button.config(state=state)
        self.preview_button.config(state=state)
        self.dfm_button.config(state="normal" if not pending and self.project and
                               self.project["kind"] == "odb" and self.project["status"] != "needs_step" else "disabled")

    def _browse_layout(self):
        path = filedialog.askopenfilename(parent=self.win, title="选择 Gerber 或 ODB++ 资料",
                                         filetypes=[("制造资料", "*.zip *.tgz *.tar.gz *.tar *.gbr *.art *.gtl *.gbl"),
                                                    ("所有文件", "*.*")])
        if path:
            self.step.set("")
            self.layout_path.set(path)

    def _browse_folder(self):
        path = filedialog.askdirectory(parent=self.win, title="选择 Gerber / ODB++ 目录")
        if path:
            self.step.set("")
            self.layout_path.set(path)

    def _browse_bom(self):
        path = filedialog.askopenfilename(parent=self.win, title="选择 BOM",
                                         filetypes=[("BOM", "*.csv *.xlsx *.xlsm *.tsv *.txt")])
        if path:
            self.bom_path.set(path)

    def _browse_placement(self):
        path = filedialog.askopenfilename(parent=self.win, title="选择器件坐标 CSV",
                                         filetypes=[("坐标 CSV", "*.csv")])
        if path:
            self.placement_path.set(path)

    def run(self):
        layout = self.layout_path.get().strip()
        if not layout:
            messagebox.showwarning("资料导入", "请先选择制造资料", parent=self.win)
            return
        revision = self.revision
        args = dict(layout_path=layout, bom_path=self.bom_path.get().strip(), kind=self.kind.get(),
                     placement_path=self.placement_path.get().strip(), step=self.step.get().strip() or None,
                     alignment_confirmed=self.alignment.get(), object_checks_only=True)
        self.project = None
        self.status.set("正在后台解析制造资料并关联 BOM…")

        def done(project):
            if revision != self.revision:
                self.status.set("导入期间输入变化，已丢弃过期结果，请重新导入")
                return
            self.project = project
            self.step_combo.config(values=project["layout"].get("steps", []))
            summary = (project.get("reconciliation") or {}).get("summary", {})
            self.status.set(f"{project['kind'].upper()} · {STATUS_LABELS.get(project['status'], project['status'])} · "
                            f"器件 {len(project['components'])} · 匹配 {summary.get('matched_count', 0)} · "
                            f"BOM漏项 {summary.get('missing_in_bom_count', 0)} · "
                            f"BOM多出 {summary.get('missing_in_layout_count', 0)} · 冲突 {summary.get('conflict_count', 0)}")
            self._render_components()
            self.layers.delete(*self.layers.get_children())
            for film in project["layout"].get("films", []):
                self.layers.insert("", "end", values=[film.get(key, "") for key in
                                   ("name", "role", "feature_count", "status", "source")])
            self._set_notes("\n".join(project["warnings"]) + "\n\n来源:\n" + "\n".join(
                f"{source['path']}\nSHA256: {source['sha256']}" for source in project["sources"]))

        self.tasks.submit("import", lambda: load_manufacturing_project(**args), done,
                          lambda error: self._failed(error, revision))

    def _failed(self, error, revision):
        if revision == self.revision:
            self.status.set("资料导入/报告失败")
            messagebox.showerror("制造资料协同", error, parent=self.win)

    def run_dfm(self):
        if not self.project or self.project["kind"] != "odb":
            return
        project, revision = deepcopy(self.project), self.revision
        self.status.set("正在执行 ODB++ 实际焊盘/过孔/开窗检查…")
        def work():
            return run_object_dfm(project)
        def done(result):
            if revision != self.revision:
                return
            self.project["object_dfm"] = result
            self.status.set(f"对象 DFM：{result['verdict']}；问题 {len(result['checks'])}；"
                            f"实测焊盘 {result['object_counts']['verified_pads']}；可导出关联报告查看对象明细")
            self._set_notes("\n".join(f"{r['item']} | {r['layer']} | {r['object_id']} / {r['other_object_id']} | "
                                      f"{r['measured']}{r['unit']} | X={r['x_mm']} Y={r['y_mm']}"
                                      for r in result["checks"]) + "\n\n" + "\n".join(result["errors"]))
        self.tasks.submit("object-dfm", work, done, lambda error: self._failed(error, revision))

    def _render_components(self):
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        search = self.search.get().strip().upper()
        for index, row in enumerate((self.project or {}).get("components", [])):
            if search and search not in " ".join(str(row.get(key) or "") for key in
                                                 ("refdes", "part_number", "manufacturer_part_number", "value", "package")).upper():
                continue
            key = str(index)
            self.rows[key] = row
            def coord(field):
                return "" if row.get(field) is None else f"{row[field]:.6f}"
            self.tree.insert("", "end", iid=key, values=(row["refdes"], STATUS_LABELS.get(row["status"], row["status"]),
                row.get("part_number", ""), row.get("manufacturer_part_number", ""), row.get("value", ""),
                row.get("package", ""), row.get("side", ""), row.get("dnp", ""), coord("x_mm"), coord("y_mm"),
                row.get("coordinate_kind") or "无器件原点", "已确认" if row.get("spatial_verified") else "未确认"))

    def _detail(self, _event=None):
        selected = self.tree.selection()
        if selected and selected[0] in self.rows:
            row = self.rows[selected[0]]
            self._set_notes(json.dumps(row, ensure_ascii=False, indent=2))

    def _set_notes(self, text):
        self.notes.config(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", text)
        self.notes.config(state="disabled")

    @staticmethod
    def _require_sources(project):
        require_project_sources(project)

    def export(self):
        if not self.project:
            return
        path = filedialog.asksaveasfilename(parent=self.win, title="导出制造资料关联报告",
                                            initialdir=self.out_dir, initialfile="manufacturing_bom.xlsx",
                                            defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx"), ("JSON", "*.json")])
        if not path:
            return
        project, revision = deepcopy(self.project), self.revision
        def work():
            self._require_sources(project)
            return export_coordination(project, path)
        self.tasks.submit("export", work, lambda output: self.status.set(f"关联报告已导出: {output}"),
                          lambda error: self._failed(error, revision))

    def preview(self):
        if not self.project:
            return
        project, revision = deepcopy(self.project), self.revision
        selected = self.tree.selection()
        ref = self.rows[selected[0]]["refdes"] if selected and selected[0] in self.rows else ""
        def work():
            return preview_coordination(project, ref)
        def done(path):
            if self.revision == revision:
                os.startfile(path)
        self.tasks.submit("preview", work, done, lambda error: self._failed(error, revision))


def open_manufacturing(master, out_dir=""):
    return ManufacturingDialog(master, out_dir)

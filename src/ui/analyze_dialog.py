"""BRD 分析与输出对话框: 选能力 -> 跑分析 -> 看结论 -> 出报告.

界面只做三件事, 判定逻辑全在 src/brd_analysis 里 (不在这里写业务规则):
    1. 列出 8 大类能力, 让用户勾选要跑哪些 (不选 = 全跑)
    2. 后台线程跑分析, 逐项把状态回填到表格里
    3. 把结果写成报告 (Excel/TXT/HTML/JSON/CSV) 与可生成的制造文件

线程纪律: 分析在工作线程里跑, 所有 Tk 控件操作都通过 root.after 回主线程。
"""
import os
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from src.ui.theme import COLORS, Card, fonts

# 状态 -> (显示文字, 颜色) ; 与 model.STATUS_LABELS 保持一致
_STATUS_STYLE = {
    "pass": ("通过", "#1E7B34"),
    "fail": ("不符合", "#C00000"),
    "warn": ("需关注", "#BF8F00"),
    "info": ("信息", "#2F5496"),
    "unavailable": ("数据不足", "#808080"),
    "skipped": ("未运行", "#A6A6A6"),
}


class AnalyzeDialog:
    """analyses 对话框。master 为父窗口, log 为可选的外部日志回调。"""

    def __init__(self, master, brd_path: str = "", out_dir: str = "",
                 export_files=None, log=None):
        self.master = master
        self.log = log
        self.brd_path = tk.StringVar(value=brd_path or "")
        self.out_dir = tk.StringVar(value=out_dir or "")
        self.status_var = tk.StringVar(value="就绪")
        self.bundle = None
        self._report_files = {}
        self._busy = False
        self._selected = set()          # 勾选的能力 id; 空集合 = 全跑

        from src.ui.theme import enable_dpi_awareness

        enable_dpi_awareness()
        self.top = tk.Toplevel(master)
        self.top.title("BRD 分析与输出 — DRC / 物理结构 / SI电气 / 制造输出 / 报告 / 装配 / MCAD / 数据导出")
        self.top.geometry("1180x800")
        self.top.minsize(1040, 680)
        self.top.configure(bg=COLORS["bg"])
        self._fonts = fonts(self.top)
        self.top.transient(master)
        self.top.report_callback_exception = self._on_callback_error

        self._build()
        self._load_catalog(export_files)
        self.top.protocol("WM_DELETE_WINDOW", self.close)

    # ------------------------------------------------------------------ 界面
    def _build(self):
        head = tk.Frame(self.top, bg=COLORS["header"], height=62)
        head.pack(fill=tk.X)
        head.pack_propagate(False)
        tk.Label(head, text="BRD 分析与输出", bg=COLORS["header"],
                 fg=COLORS["header_text"], font=self._fonts["title"]).pack(
            side=tk.LEFT, padx=20, pady=(10, 0))
        tk.Label(head, text="  8 大类能力 · 能算的给结论, 算不了的写清缺什么",
                 bg=COLORS["header"], fg=COLORS["header_muted"],
                 font=self._fonts["subtitle"]).pack(side=tk.LEFT, pady=(16, 0))

        bar = tk.Frame(self.top, bg=COLORS["border"])
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        inner = tk.Frame(bar, bg=COLORS["card"])
        inner.pack(fill=tk.X, pady=(1, 0))
        box = ttk.Frame(inner, style="Card.TFrame", padding=(16, 10))
        box.pack(fill=tk.X)
        self.run_btn = ttk.Button(box, text="开始分析", style="Primary.TButton",
                                  command=self._run)
        self.run_btn.pack(side=tk.LEFT)
        self.report_btn = ttk.Button(box, text="导出报告", style="Secondary.TButton",
                                     command=self._export, state=tk.DISABLED)
        self.report_btn.pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(box, text="打开输出目录", style="Secondary.TButton",
                   command=self._open_out).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(box, text="关闭", style="Secondary.TButton",
                   command=self.close).pack(side=tk.RIGHT)
        self.progress = ttk.Progressbar(box, mode="indeterminate", length=180)
        self.progress.pack(side=tk.RIGHT, padx=(0, 12))

        status = tk.Frame(self.top, bg=COLORS["status_bg"], height=32)
        status.pack(fill=tk.X, side=tk.BOTTOM)
        status.pack_propagate(False)
        tk.Label(status, textvariable=self.status_var, bg=COLORS["status_bg"],
                 fg=COLORS["muted"], font=self._fonts["small"], anchor=tk.W).pack(
            side=tk.LEFT, padx=16)

        body = ttk.Frame(self.top, style="TFrame", padding=(14, 12, 14, 10))
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=0, minsize=430)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, style="TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = ttk.Frame(body, style="TFrame")
        right.grid(row=0, column=1, sticky="nsew")

        self._build_input(left)
        self._build_detail(right)

    def _build_input(self, parent):
        card = Card(parent, "① 输入", "BRD / 导出文件 / 输出目录")
        card.pack(fill=tk.X, pady=(0, 10))
        c = card.content

        ttk.Label(c, text="Allegro BRD", style="FieldLabel.TLabel").pack(anchor=tk.W)
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill=tk.X, pady=(3, 10))
        ttk.Entry(row, textvariable=self.brd_path).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="浏览", style="Ghost.TButton",
                   command=self._browse_brd).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(c, text="输出目录", style="FieldLabel.TLabel").pack(anchor=tk.W)
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill=tk.X, pady=(3, 10))
        ttk.Entry(row, textvariable=self.out_dir).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="浏览", style="Ghost.TButton",
                   command=self._browse_out).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(c, text="Allegro 导出文件 (可选, 能显著提高结论的完整性)",
                  style="FieldLabel.TLabel").pack(anchor=tk.W)
        ttk.Label(c, text="DRC 报告 .rpt / Placement place_txt.txt / 钻孔 .drl / "
                          "网表 pstxnet.dat / IPC-356 .ipc / 另一个 .brd(版本差异)",
                  style="CardHint.TLabel", wraplength=380,
                  justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 4))
        self.export_list = tk.Listbox(c, height=6, activestyle="none",
                                      font=self._fonts["small"])
        self.export_list.pack(fill=tk.X, pady=(0, 6))
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill=tk.X)
        ttk.Button(row, text="添加文件", style="Ghost.TButton",
                   command=self._add_exports).pack(side=tk.LEFT)
        ttk.Button(row, text="清空", style="Ghost.TButton",
                   command=lambda: self.export_list.delete(0, tk.END)).pack(
            side=tk.LEFT, padx=(6, 0))

        hint = Card(parent, "② 能力选择", "双击一行 = 勾选/取消勾选; 不勾选 = 全部运行")
        hint.pack(fill=tk.BOTH, expand=True)
        hc = hint.content
        ttk.Label(hc, text="只有 8 大类的模块都加载成功, 才能跑对应能力;\n"
                           "结果表格里的状态列会显示每项的真实结论。",
                  style="CardHint.TLabel", justify=tk.LEFT).pack(anchor=tk.W)

    def _build_detail(self, parent):
        card = Card(parent, "能力与结论", "状态 / 结论 / 关键指标")
        card.pack(fill=tk.BOTH, expand=True)
        c = card.content

        tools = ttk.Frame(c, style="Card.TFrame")
        tools.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(tools, text="全选", style="Ghost.TButton",
                   command=lambda: self._select_all(True)).pack(side=tk.LEFT)
        ttk.Button(tools, text="全不选", style="Ghost.TButton",
                   command=lambda: self._select_all(False)).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(tools, text="只选不符合/数据不足", style="Ghost.TButton",
                   command=self._select_problems).pack(side=tk.LEFT, padx=(6, 0))

        cols = ("category", "name", "status", "summary")
        self.tree = ttk.Treeview(c, columns=cols, show="tree headings",
                                 selectmode="extended")
        self.tree.heading("#0", text="选")
        self.tree.column("#0", width=34, stretch=False, anchor=tk.CENTER)
        for key, label, width in (("category", "类别", 130), ("name", "能力", 150),
                                  ("status", "状态", 74), ("summary", "结论", 520)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, stretch=(key == "summary"),
                             anchor=tk.W)
        yscroll = ttk.Scrollbar(c, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", self._toggle_row)
        self.tree.tag_configure("fail", foreground="#C00000")
        self.tree.tag_configure("warn", foreground="#BF8F00")
        self.tree.tag_configure("pass", foreground="#1E7B34")
        self.tree.tag_configure("unavailable", foreground="#808080")

        self.detail = tk.Text(c, height=9, wrap=tk.WORD, relief=tk.FLAT,
                              bg="#F7F9FC", font=self._fonts["small"], padx=8, pady=6)
        self.detail.pack(fill=tk.X, pady=(8, 0))
        self.detail.configure(state=tk.DISABLED)
        self.tree.bind("<<TreeviewSelect>>", self._show_detail)

    # ------------------------------------------------------------------ 数据
    def _load_catalog(self, export_files=None):
        from src.brd_analysis import capability_rows
        from src.brd_analysis.catalog import CATALOG

        self.rows = capability_rows()
        by_category = {}
        for row in self.rows:
            by_category.setdefault(row["category_label"], []).append(row)
            self._selected.add(row["id"])
        for label, rows in by_category.items():
            for row in rows:
                self.tree.insert(
                    "", tk.END, iid=row["id"], text="☑",
                    values=(label, row["name_cn"], "待运行", row["desc"]),
                    tags=("pending",))
        self.catalog_errors = list(CATALOG.errors)
        if self.catalog_errors:
            self.status_var.set("部分类别模块未加载: " + "; ".join(self.catalog_errors))
        for path in (export_files or []):
            if path:
                self.export_list.insert(tk.END, path)

    def _toggle_row(self, event=None):
        if event is None:
            return
        row = self.tree.identify_row(event.y)
        if not row:
            # 窗口还没映射时 bbox/identify 可能取不到, 这时候不要抛异常
            return
        self._toggle_id(row)

    def _toggle_id(self, row: str):
        """勾选/取消勾选一项能力。"""
        if not row or not self.tree.exists(row):
            return
        if row in self._selected:
            self._selected.discard(row)
            self.tree.item(row, text="☐")
        else:
            self._selected.add(row)
            self.tree.item(row, text="☑")

    def _select_all(self, on: bool):
        for row in self.rows:
            if on:
                self._selected.add(row["id"])
            else:
                self._selected.discard(row["id"])
            self.tree.item(row["id"], text="☑" if on else "☐")

    def _select_problems(self):
        if not self.bundle:
            messagebox.showinfo("提示", "先跑一次分析, 才能按结论筛选")
            return
        keep = {r["id"] for r in self.bundle["results"]
                if r["status"] in ("fail", "warn", "unavailable")}
        self._select_all(False)
        for cap_id in keep:
            self._selected.add(cap_id)
            if self.tree.exists(cap_id):
                self.tree.item(cap_id, text="☑")

    def _show_detail(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        cap_id = sel[0]
        row = next((r for r in self.rows if r["id"] == cap_id), None)
        if not row:
            return
        lines = [f"{row['name_cn']}  ({row['name']} / {row['id']})",
                 f"类别: {row['category_label']}    数据来源: {row['engine_label']}",
                 f"回答的问题: {row['desc']}",
                 f"需要的数据: {'、'.join(row['needs']) or '—'}",
                 f"产出: {'、'.join(row['outputs']) or '—'}"]
        res = None
        if self.bundle:
            res = next((r for r in self.bundle["results"] if r["id"] == cap_id), None)
        if res:
            lines += ["", f"状态: {res['status_label']}    结论: {res['summary']}"]
            for m in (res.get("metrics") or [])[:12]:
                unit = f" {m['unit']}" if m.get("unit") else ""
                src = f"   ({m['source']})" if m.get("source") else ""
                lines.append(f"  · {m['label']}: {m['value']}{unit}{src}")
            for miss in res.get("missing") or []:
                lines.append(f"  ! 缺数据: {miss}")
            for how in res.get("howto") or []:
                lines.append(f"    -> {how}")
            for art in res.get("artifacts") or []:
                lines.append(f"  * 已生成: {art['path']}")
        self.detail.configure(state=tk.NORMAL)
        self.detail.delete("1.0", tk.END)
        self.detail.insert(tk.END, "\n".join(lines))
        self.detail.configure(state=tk.DISABLED)

    def _append_log(self, text, tag=None):
        if self.log:
            try:
                self.log(text, tag)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ 动作
    def _browse_brd(self):
        path = filedialog.askopenfilename(
            title="选择 Allegro BRD 文件",
            filetypes=[("BRD files", "*.brd"), ("All files", "*.*")])
        if path:
            self.brd_path.set(path)

    def _browse_out(self):
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.out_dir.set(path)

    def _add_exports(self):
        paths = filedialog.askopenfilenames(
            title="选择 Allegro 导出文件 (可多选)",
            filetypes=[("支持的文件", "*.rpt *.drl *.ipc *.csv *.txt *.dat *.brd *.nc"),
                       ("All files", "*.*")])
        for path in paths or ():
            existing = list(self.export_list.get(0, tk.END))
            if path not in existing:
                self.export_list.insert(tk.END, path)

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.run_btn.config(state=tk.DISABLED if busy else tk.NORMAL)
        if busy:
            self.progress.pack(side=tk.RIGHT, padx=(0, 12))
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def _run(self):
        brd = self.brd_path.get().strip()
        if not brd or not os.path.exists(brd):
            messagebox.showwarning("提示", "请先选择一个存在的 .brd 文件")
            return
        out_dir = self.out_dir.get().strip() or os.path.join(
            os.path.dirname(brd), "output")
        self.out_dir.set(out_dir)
        exports = [p for p in self.export_list.get(0, tk.END) if p]
        only = [r["id"] for r in self.rows if r["id"] in self._selected]
        if len(only) == len(self.rows):
            only = None                     # 全选 = 不传过滤条件
        self._set_busy(True)
        self.status_var.set("正在分析…")
        for row in self.rows:
            self.tree.item(row["id"], values=(
                self.tree.set(row["id"], "category"), row["name_cn"],
                "运行中", row["desc"]), tags=("pending",))
        threading.Thread(target=self._execute, args=(brd, out_dir, exports, only),
                         daemon=True).start()

    def _execute(self, brd, out_dir, exports, only):
        from src.brd_analysis import analyze

        try:
            bundle = analyze(
                brd, out_dir=out_dir, export_files=exports, only=only,
                log=lambda text, tag=None: self.top.after(
                    0, lambda: self._append_log(text, tag)))
            self.top.after(0, lambda: self._apply_bundle(bundle))
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            detail = traceback.format_exc()
            self.top.after(0, lambda: self._append_log(f"[ERROR] {message}", "error"))
            self.top.after(0, lambda: self._append_log(detail, "error"))
            self.top.after(0, lambda: self._fail(message))

    def _apply_bundle(self, bundle):
        self.bundle = bundle
        for res in bundle.get("results") or []:
            if not self.tree.exists(res["id"]):
                continue
            label, _color = _STATUS_STYLE.get(res["status"], (res["status"], ""))
            self.tree.item(res["id"], values=(
                self.tree.set(res["id"], "category"), res["name_cn"],
                label, res["summary"]), tags=(res["status"],))
        stats = bundle.get("stats") or {}
        self.status_var.set(
            f"完成: 通过 {stats.get('pass', 0)} / 不符合 {stats.get('fail', 0)} / "
            f"需关注 {stats.get('warn', 0)} / 信息 {stats.get('info', 0)} / "
            f"数据不足 {stats.get('unavailable', 0)}  (共 {stats.get('total', 0)} 项)")
        self.report_btn.config(state=tk.NORMAL)
        self._set_busy(False)
        self._show_detail()

    def _fail(self, message):
        self.status_var.set(f"分析失败: {message}")
        self._set_busy(False)

    def _export(self):
        if not self.bundle:
            return
        from src.brd_analysis import write_reports

        out_dir = self.out_dir.get().strip()
        board = (self.bundle.get("board") or {}).get("board_name") or "board"
        files = write_reports(self.bundle, out_dir, base_name=f"BRD分析报告_{board}")
        self._report_files = files
        lines = [f"{k.upper()}: {v}" for k, v in files.items()
                 if k != "csv_files" and v]
        if files.get("csv_files"):
            lines.append(f"CSV: {len(files['csv_files'])} 个文件")
        self._append_log("[报告] " + " | ".join(lines), "success")
        messagebox.showinfo("导出完成", "\n".join(lines))

    def _open_out(self):
        out = self.out_dir.get().strip()
        if out and os.path.exists(out):
            os.startfile(out)
        else:
            messagebox.showwarning("提示", "输出目录还不存在, 先跑一次分析")

    def _on_callback_error(self, exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        self._append_log("[ERROR] 界面回调异常: " + str(exc), "error")
        self._append_log(text, "error")

    def close(self):
        if self._busy and not messagebox.askyesno("确认", "分析仍在进行, 确定关闭吗?"):
            return
        try:
            self.top.destroy()
        except tk.TclError:
            pass


def open_analyze(master, brd_path: str = "", out_dir: str = "",
                 export_files=None, log=None):
    """打开对话框; 返回 AnalyzeDialog 实例 (调用方可保留引用)。"""
    return AnalyzeDialog(master, brd_path=brd_path, out_dir=out_dir,
                         export_files=export_files, log=log)

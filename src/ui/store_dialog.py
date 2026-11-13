"""资料库联动检查对话框: 选 store-brd / store-bom -> 自动配对 -> 逐项结论 -> 出报告.

界面只负责三件事 (业务逻辑全在 src/design_inputs/store.py):
    1. 指定 BRD 目录 / BOM 目录 / 输出目录 (默认就是程序目录下的 store-brd / store-bom)
    2. 后台线程跑联动检查, 把"配对结论"和"位号明细"回填到两张表里
    3. 导出 xlsx/txt/html/json 报告

线程纪律: 检查在工作线程里跑, 所有 Tk 控件操作都通过 top.after 回主线程。
"""
import os
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from src.ui.theme import COLORS, Card, fonts

_VERDICT_STYLE = {
    "pass": ("通过", "#15803D"),
    "warn": ("需确认", "#B45309"),
    "fail": ("不符合", "#B91C1C"),
}

_ROW_STATUS = {
    "matched": "匹配", "missing_in_bom": "BOM 漏项", "missing_in_brd": "BOM 多出",
    "conflict": "属性冲突", "unverified": "未核对",
}

_FILTERS = ("全部", "只看差异", "只看未核对")


class StoreCheckDialog:
    """BRD ↔ BOM 联动检查窗口。master 为父窗口, log 为可选的外部日志回调。"""

    def __init__(self, master, brd_dir: str = "", bom_dir: str = "",
                 out_dir: str = "", log=None):
        from src.design_inputs.store import store_paths

        self.master = master
        self.log = log
        self.bundle = None
        self._busy = False
        self._rows = []

        paths = store_paths()
        self.brd_dir = tk.StringVar(value=brd_dir or paths["brd"])
        self.bom_dir = tk.StringVar(value=bom_dir or paths["bom"])
        self.out_dir = tk.StringVar(value=out_dir or paths["out"])
        self.status_var = tk.StringVar(value="就绪")
        self.filter_var = tk.StringVar(value=_FILTERS[0])

        from src.ui.theme import enable_dpi_awareness

        enable_dpi_awareness()
        self.top = tk.Toplevel(master)
        self.top.title("BRD ↔ BOM 资料库联动检查")
        self.top.geometry("1200x820")
        self.top.minsize(1060, 700)
        self.top.configure(bg=COLORS["bg"])
        self._fonts = fonts(self.top)
        self.top.transient(master)
        self.top.report_callback_exception = self._on_callback_error

        self._build()
        self.top.protocol("WM_DELETE_WINDOW", self.close)

    # ------------------------------------------------------------------ 界面
    def _build(self):
        head = tk.Frame(self.top, bg=COLORS["header"], height=62)
        head.pack(fill=tk.X)
        head.pack_propagate(False)
        tk.Label(head, text="BRD ↔ BOM 资料库联动检查", bg=COLORS["header"],
                 fg=COLORS["header_text"], font=self._fonts["title"]).pack(
            side=tk.LEFT, padx=20, pady=(10, 0))
        tk.Label(head, text="  store-brd / store-bom 自动配对 · 逐位号双向核对 · 缺数据写清原因",
                 bg=COLORS["header"], fg=COLORS["header_muted"],
                 font=self._fonts["subtitle"]).pack(side=tk.LEFT, pady=(16, 0))

        status = tk.Frame(self.top, bg=COLORS["status_bg"], height=32)
        status.pack(fill=tk.X, side=tk.BOTTOM)
        status.pack_propagate(False)
        tk.Label(status, textvariable=self.status_var, bg=COLORS["status_bg"],
                 fg=COLORS["muted"], font=self._fonts["small"], anchor=tk.W).pack(
            side=tk.LEFT, padx=16)

        bar = tk.Frame(self.top, bg=COLORS["border"])
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        inner = tk.Frame(bar, bg=COLORS["card"])
        inner.pack(fill=tk.X, pady=(1, 0))
        box = ttk.Frame(inner, style="Card.TFrame", padding=(16, 10))
        box.pack(fill=tk.X)
        self.run_btn = ttk.Button(box, text="开始检查", style="Primary.TButton",
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
        self.progress.pack_forget()

        body = ttk.Frame(self.top, style="TFrame", padding=(14, 12, 14, 10))
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=0, minsize=430)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, style="TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = ttk.Frame(body, style="TFrame")
        right.grid(row=0, column=1, sticky="nsew")

        self._build_inputs(left)
        self._build_results(right)

    def _build_inputs(self, parent):
        card = Card(parent, "① 资料库目录", "默认就是程序目录下的两个文件夹")
        card.pack(fill=tk.X, pady=(0, 10))
        content = card.content

        for label, var, command in (
                ("BRD 目录 (.brd)", self.brd_dir, self._browse_brd_dir),
                ("BOM 目录 (.xlsx / .csv)", self.bom_dir, self._browse_bom_dir),
                ("输出目录", self.out_dir, self._browse_out)):
            ttk.Label(content, text=label, style="FieldLabel.TLabel").pack(anchor=tk.W)
            row = ttk.Frame(content, style="Card.TFrame")
            row.pack(fill=tk.X, pady=(3, 10))
            ttk.Entry(row, textvariable=var).pack(
                side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Button(row, text="浏览", style="Ghost.TButton",
                       command=command).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(content, text="BOM 支持 .xlsx / .csv, 表头 Part Reference / PCB Footprint / "
                               "IPN / MPN / Quantity 会自动识别; 位号可写在一格里用逗号分隔。",
                  style="CardHint.TLabel", wraplength=380, justify=tk.LEFT).pack(anchor=tk.W)

        hint = Card(parent, "② 配对与判定", "先把文件放对位置, 再点「开始检查」")
        hint.pack(fill=tk.BOTH, expand=True)
        ttk.Label(hint.content,
                  text=("配对: 先看 BOM 里是否有料号等于 BRD 文件名的料号"
                        "(例如 BOM 的 PCB1 行 = S-860-000000), 其次看文件名共用的数字;"
                        " 都不匹配就列为未配对, 不猜。\n\n"
                        "核对: 料号 / 版本 / BOM 位号质量 / BRD 位号来源 / 位号双向核对 /"
                        " 非电气项 / BRD 设计量。\n\n"
                        "BRD 没有 objects/Part.xml (FAB 拼板图常见) 时, 把 Allegro 的"
                        " place_txt.txt 或 SKILL 导出的 evan_components.csv 放到"
                        " store-brd 同目录, 位号就能核对。\n\n"
                        "判定只用数据能证明的结论, 缺数据写「数据不足」并给出补法。"),
                  style="CardHint.TLabel", wraplength=380, justify=tk.LEFT).pack(
            anchor=tk.W, pady=(2, 0))

    def _build_results(self, parent):
        card = Card(parent, "③ 检查结果", "选一对看逐项结论与位号明细")
        card.pack(fill=tk.BOTH, expand=True)
        content = card.content

        notebook = ttk.Notebook(content)
        notebook.pack(fill=tk.BOTH, expand=True)
        self.notebook = notebook

        pairs_tab = ttk.Frame(notebook, style="Card.TFrame", padding=(0, 6))
        notebook.add(pairs_tab, text="配对结论")
        columns = ("brd", "bom", "verdict", "refs", "reason")
        self.pair_tree = ttk.Treeview(pairs_tab, columns=columns, show="headings",
                                      selectmode="browse")
        for key, label, width, stretch in (
                ("brd", "BRD 文件", 210, False), ("bom", "BOM 文件", 210, False),
                ("verdict", "结论", 70, False), ("refs", "位号", 70, False),
                ("reason", "匹配依据", 240, True)):
            self.pair_tree.heading(key, text=label)
            self.pair_tree.column(key, width=width, stretch=stretch, anchor=tk.W)
        self.pair_tree.tag_configure("pass", foreground="#15803D")
        self.pair_tree.tag_configure("warn", foreground="#B45309")
        self.pair_tree.tag_configure("fail", foreground="#B91C1C")
        self.pair_tree.pack(fill=tk.BOTH, expand=True)
        self.pair_tree.bind("<<TreeviewSelect>>", self._show_pair)

        detail_tab = ttk.Frame(notebook, style="Card.TFrame", padding=(0, 6))
        notebook.add(detail_tab, text="位号明细")
        tools = ttk.Frame(detail_tab, style="Card.TFrame")
        tools.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(tools, text="筛选", style="FieldLabel.TLabel").pack(side=tk.LEFT)
        combo = ttk.Combobox(tools, textvariable=self.filter_var, values=_FILTERS,
                             state="readonly", width=12)
        combo.pack(side=tk.LEFT, padx=(6, 0))
        combo.bind("<<ComboboxSelected>>", lambda event: self._fill_refdes())
        self.refdes_hint = ttk.Label(tools, text="先选一对已配对的 BRD/BOM",
                                     style="CardHint.TLabel")
        self.refdes_hint.pack(side=tk.LEFT, padx=(10, 0))

        ref_columns = ("refdes", "status", "in_brd", "in_bom", "package",
                       "brd_pn", "bom_pn", "bom_mpn", "value")
        self.ref_tree = ttk.Treeview(detail_tab, columns=ref_columns,
                                     show="headings", selectmode="browse")
        for key, label, width in (
                ("refdes", "位号", 70), ("status", "状态", 80), ("in_brd", "在BRD", 55),
                ("in_bom", "在BOM", 55), ("package", "封装", 150),
                ("brd_pn", "BRD 料号", 120), ("bom_pn", "BOM 料号", 120),
                ("bom_mpn", "BOM MPN", 170), ("value", "BOM 值", 100)):
            self.ref_tree.heading(key, text=label)
            self.ref_tree.column(key, width=width, stretch=(key in ("package", "bom_mpn")),
                                 anchor=tk.W)
        for status, color in (("missing_in_bom", "#B91C1C"), ("missing_in_brd", "#B45309"),
                              ("conflict", "#B45309"), ("unverified", "#6B7280")):
            self.ref_tree.tag_configure(status, foreground=color)
        ref_scroll = ttk.Scrollbar(detail_tab, orient=tk.VERTICAL,
                                   command=self.ref_tree.yview)
        self.ref_tree.configure(yscrollcommand=ref_scroll.set)
        self.ref_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ref_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.detail = tk.Text(content, height=10, wrap=tk.WORD, relief=tk.FLAT,
                              bg="#F7F9FC", font=self._fonts["small"], padx=8, pady=6)
        self.detail.pack(fill=tk.X, pady=(8, 0))
        self.detail.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------ 浏览
    def _browse_brd_dir(self):
        path = filedialog.askdirectory(title="选择 BRD 目录")
        if path:
            self.brd_dir.set(path)

    def _browse_bom_dir(self):
        path = filedialog.askdirectory(title="选择 BOM 目录")
        if path:
            self.bom_dir.set(path)

    def _browse_out(self):
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.out_dir.set(path)

    def _open_out(self):
        out = self.out_dir.get().strip()
        if out and os.path.exists(out):
            os.startfile(out)
        else:
            messagebox.showwarning("提示", "输出目录还不存在, 先跑一次检查")

    # ------------------------------------------------------------------ 日志
    def _append_log(self, text, tag=None):
        if self.log:
            try:
                self.log(text, tag)
            except Exception:  # noqa: BLE001,S110  主窗口日志不可用不影响检查
                pass

    def _post_log(self, text, tag=None):
        try:
            self.top.after(0, lambda: self._append_log(text, tag))
        except (RuntimeError, tk.TclError):
            pass

    def _post(self, func, *args):
        """把回调排到主线程; 窗口已关闭时静默丢弃。"""
        try:
            self.top.after(0, lambda: func(*args))
        except (RuntimeError, tk.TclError):
            pass

    # ------------------------------------------------------------------ 运行
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
        if self._busy:
            return
        brd_dir = self.brd_dir.get().strip()
        bom_dir = self.bom_dir.get().strip()
        if not brd_dir or not os.path.isdir(brd_dir):
            messagebox.showwarning("提示", f"BRD 目录不存在:\n{brd_dir or '(空)'}")
            return
        if not bom_dir or not os.path.isdir(bom_dir):
            messagebox.showwarning("提示", f"BOM 目录不存在:\n{bom_dir or '(空)'}")
            return
        if not self.out_dir.get().strip():
            self.out_dir.set(os.path.join(os.path.dirname(brd_dir), "output"))
        self._set_busy(True)
        self.status_var.set("正在扫描资料库…")
        threading.Thread(target=self._execute, args=(brd_dir, bom_dir),
                         daemon=True).start()

    def _execute(self, brd_dir, bom_dir):
        from src.design_inputs.store import run_check, summary_lines

        try:
            bundle = run_check(brd_dir=brd_dir, bom_dir=bom_dir, log=self._post_log)
            for line in summary_lines(bundle):
                self._post_log(line, "success" if bundle["verdict"] == "pass" else "warn")
            self._post(self._apply_bundle, bundle)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            detail = traceback.format_exc()
            self._post(self._append_log, f"[ERROR] {message}", "error")
            self._post(self._append_log, detail, "error")
            self._post(self._fail, message)
        finally:
            self._post(self._set_busy, False)

    def _fail(self, message):
        self.status_var.set(f"检查失败: {message}")

    # ------------------------------------------------------------------ 回填
    def _apply_bundle(self, bundle):
        self.bundle = bundle
        self.pair_tree.delete(*self.pair_tree.get_children())
        for index, pair in enumerate(bundle.get("pairs") or []):
            brd, bom = pair.get("brd") or {}, pair.get("bom") or {}
            verdict = pair.get("verdict") or "warn"
            label, _color = _VERDICT_STYLE.get(verdict, ("需确认", ""))
            self.pair_tree.insert(
                "", tk.END, iid=str(index),
                values=(brd.get("name", ""), bom.get("name", ""), label,
                        (pair.get("bom") or {}).get("summary", {}).get("rows", 0),
                        "、".join(pair.get("match_reasons") or [])),
                tags=(verdict,))

        if bundle.get("unmatched_brd") or bundle.get("unmatched_bom"):
            for item in bundle.get("unmatched_brd") or []:
                self.pair_tree.insert(
                    "", tk.END,
                    values=(item.get("name", ""), "(未配对)", "未配对", "",
                            item.get("reason", "")), tags=("warn",))
            for item in bundle.get("unmatched_bom") or []:
                self.pair_tree.insert(
                    "", tk.END,
                    values=("(未配对)", item.get("name", ""), "未配对", "",
                            item.get("reason", "")), tags=("warn",))

        stats = bundle.get("stats") or {}
        self.status_var.set(
            f"完成: 配对 {stats.get('pair_count', 0)} 对, 结论 {bundle.get('verdict_label')} "
            f"(通过 {stats.get('pass', 0)} / 需确认 {stats.get('warn', 0)} / "
            f"不符合 {stats.get('fail', 0)})")
        self.report_btn.config(state=tk.NORMAL)
        for warning in bundle.get("warnings") or []:
            self._append_log(f"[注意] {warning}", "warn")
        children = self.pair_tree.get_children()
        if children:
            self.pair_tree.selection_set(children[0])
            self.pair_tree.focus(children[0])
            self._show_pair()

    def _current_pair(self):
        if not self.bundle:
            return None
        selection = self.pair_tree.selection()
        if not selection:
            return None
        try:
            index = int(selection[0])
        except ValueError:
            return None
        pairs = self.bundle.get("pairs") or []
        return pairs[index] if 0 <= index < len(pairs) else None

    def _show_pair(self, event=None):
        pair = self._current_pair()
        self._fill_refdes()
        if not pair:
            self._set_detail("这一行没有配对的 BOM/BRD, 原因见「匹配依据」。")
            return
        brd, bom = pair.get("brd") or {}, pair.get("bom") or {}
        lines = [
            f"{brd.get('name', '')}  ↔  {bom.get('name', '')}",
            (f"结论: {pair.get('verdict_label')}    匹配依据: "
             f"{'、'.join(pair.get('match_reasons') or []) or '—'}"),
            (f"料号: BRD {brd.get('part_number') or '—'} / "
             f"BOM {bom.get('part_number') or '—'}    "
             f"版本: BRD {brd.get('rev') or '—'} / BOM {bom.get('rev') or '—'}"),
            "",
        ]
        for check in pair.get("checks") or []:
            marker = {"pass": "✔", "warn": "!", "fail": "✘",
                      "unavailable": "—", "info": "·"}.get(check.get("status"), "·")
            lines.append(f"{marker} [{check.get('status_label')}] {check.get('name')}: "
                         f"{check.get('detail')}")
        reconciliation = pair.get("reconciliation") or {}
        if reconciliation.get("missing_in_bom"):
            sample = "、".join(item["refdes"] for item in reconciliation["missing_in_bom"][:15])
            lines += ["", f"BOM 漏项: {sample}"]
        if reconciliation.get("missing_in_brd"):
            sample = "、".join(item["refdes"] for item in reconciliation["missing_in_brd"][:15])
            lines += [f"BOM 多出: {sample}"]
        if reconciliation.get("conflicts"):
            sample = "、".join(f"{item['refdes']}({item['field']})"
                               for item in reconciliation["conflicts"][:15])
            lines += [f"属性冲突: {sample}"]
        if pair.get("warnings"):
            lines += ["", "提示:"] + [f"  ! {warning}" for warning in pair["warnings"]]
        self._set_detail("\n".join(lines))

    def _fill_refdes(self):
        self.ref_tree.delete(*self.ref_tree.get_children())
        pair = self._current_pair()
        rows = (pair.get("reconciliation") or {}).get("rows") or [] if pair else []
        mode = self.filter_var.get()
        shown = 0
        for row in rows:
            status = row.get("status") or ""
            if mode == "只看差异" and status not in ("missing_in_bom", "missing_in_brd",
                                                    "conflict"):
                continue
            if mode == "只看未核对" and status != "unverified":
                continue
            brd = row.get("brd") or {}
            bom = row.get("bom") or {}
            self.ref_tree.insert(
                "", tk.END,
                values=(row.get("refdes") or bom.get("refdes") or "",
                        _ROW_STATUS.get(status, status),
                        "是" if row.get("in_brd") else "",
                        "是" if row.get("in_bom") else "",
                        row.get("package") or "",
                        brd.get("part_number") or "",
                        bom.get("part_number") or "",
                        bom.get("manufacturer_part_number") or "",
                        bom.get("value") or ""),
                tags=(status,))
            shown += 1
        if pair:
            self.refdes_hint.config(text=f"显示 {shown} / {len(rows)} 个位号")
        else:
            self.refdes_hint.config(text="先选一对已配对的 BRD/BOM")

    def _set_detail(self, text):
        self.detail.configure(state=tk.NORMAL)
        self.detail.delete("1.0", tk.END)
        self.detail.insert(tk.END, text)
        self.detail.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------ 动作
    def _export(self):
        if not self.bundle:
            messagebox.showinfo("提示", "先跑一次检查, 再导出报告")
            return
        from src.store_report import write_report

        out_dir = self.out_dir.get().strip()
        try:
            files = write_report(self.bundle, out_dir)
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self._append_log("[报告] " + " | ".join(files.values()), "success")
        messagebox.showinfo("导出完成",
                            "报告已写出:\n" + "\n".join(files.values()))

    def _on_callback_error(self, exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        self._append_log("[ERROR] 界面回调异常: " + str(exc), "error")
        self._append_log(text, "error")

    def close(self):
        if self._busy and not messagebox.askyesno("确认", "检查仍在进行, 确定关闭吗?"):
            return
        try:
            self.top.destroy()
        except tk.TclError:
            pass


def open_store_check(master, brd_dir: str = "", bom_dir: str = "",
                     out_dir: str = "", log=None):
    """打开联动检查窗口; 返回 StoreCheckDialog 实例 (调用方可保留引用)。"""
    return StoreCheckDialog(master, brd_dir=brd_dir, bom_dir=bom_dir,
                            out_dir=out_dir, log=log)

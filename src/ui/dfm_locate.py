"""坐标定位窗口: 把 DFM 不符合项落成 "数量 + 坐标"。

入口在 DFM 对话框结果区的「坐标定位」按钮:
    - 自动扫描 BRD 同目录/输出目录里的坐标来源 (SKILL 导出 / Placement / NC Drill)
    - 没有来源时一键生成 Allegro SKILL 导出脚本 (src/allegro_skill.py)
    - 生成 定位清单.xlsx/.csv/.txt 与 locate.scr (Allegro 里逐个跳转)

本文件只做界面与线程调度, 解析/匹配/出表都在 src/pick.py。
"""
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from src import pick
from src.allegro_skill import write_bundle
from src.ui.theme import COLORS, Card, LogView, apply_theme, fonts

_KIND_LABEL = {"skill": "SKILL 导出", "placement": "Placement 导出",
               "excellon": "钻孔文件", "components": "元件", "holes": "孔位"}


class LocateDialog:
    """独立的定位清单窗口 (模态)。"""

    def __init__(self, master, brd_path: str = "", result: dict = None,
                 out_dir: str = "", base_name: str = "定位清单",
                 board_type: str = "rigid", level: str = "standard",
                 factory: str = "hongban"):
        self.brd_path = brd_path or ""
        self.result = result
        self.base_name = base_name
        self.board_type = board_type
        self.level = level
        self.factory = factory
        self.files = []
        self.sources = []
        self.last_bundle = None
        self._busy = False
        self._queue = queue.Queue()

        default_dir = (os.path.join(os.path.dirname(os.path.abspath(self.brd_path)), "定位清单")
                       if self.brd_path else os.path.join(os.getcwd(), "定位清单"))
        self.out_dir = out_dir or default_dir

        self.win = tk.Toplevel(master)
        self.win.title("坐标定位清单 - 元件坐标 / 孔位坐标")
        self.win.geometry("820x640")
        self.win.minsize(720, 560)
        self.win.configure(bg=COLORS["bg"])
        self._fonts = fonts(self.win)
        apply_theme(self.win)
        try:
            self.win.transient(master)
            self.win.grab_set()
        except tk.TclError:
            pass

        self.status_var = tk.StringVar(value="就绪")
        self._build()
        self._scan()
        self.win.after(180, self._focus_first)
        self.win.after(120, self._poll_queue)

    # ------------------------------------------------------------ 组装
    def _build(self):
        hdr = tk.Frame(self.win, bg=COLORS["header"], height=58)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        box = tk.Frame(hdr, bg=COLORS["header"])
        box.pack(side=tk.LEFT, padx=20, pady=9)
        tk.Label(box, text="坐标定位清单", bg=COLORS["header"],
                 fg=COLORS["header_text"], font=self._fonts["title"]).pack(anchor=tk.W)
        tk.Label(box, text="不符合项 -> 数量 + 坐标 · 元件 X/Y · 钻孔位置",
                 bg=COLORS["header"], fg=COLORS["header_muted"],
                 font=self._fonts["subtitle"]).pack(anchor=tk.W, pady=(2, 0))

        ttk.Label(self.win, textvariable=self.status_var, style="Status.TLabel",
                  anchor=tk.W).pack(fill=tk.X)

        # 底部按钮先占位, 避免窗口偏矮时被内容挤掉 (pack 按顺序分配空间)
        foot = ttk.Frame(self.win, style="TFrame", padding=(16, 0, 16, 12))
        foot.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(foot, text="生成定位清单", style="Primary.TButton",
                   command=self._run).pack(side=tk.LEFT)
        self.open_btn = ttk.Button(foot, text="打开输出目录", style="Secondary.TButton",
                                   command=self._open_dir, state=tk.DISABLED)
        self.open_btn.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(foot, text="关闭", style="Secondary.TButton",
                   command=self.win.destroy).pack(side=tk.RIGHT)

        body = ttk.Frame(self.win, style="TFrame", padding=(16, 12, 16, 8))
        body.pack(fill=tk.BOTH, expand=True)
        body.rowconfigure(0, weight=0)
        body.rowconfigure(1, weight=0)
        body.rowconfigure(2, weight=1)
        body.columnconfigure(0, weight=1)

        src_card = Card(body, "坐标来源", "SKILL 导出 / Placement 导出 / NC Drill 文件")
        src_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        bar = ttk.Frame(src_card.content, style="Card.TFrame")
        bar.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(bar, text="添加文件...", style="Secondary.TButton",
                   command=self._add_files).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bar, text="重新扫描", style="Ghost.TButton",
                   command=self._scan).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bar, text="生成 SKILL 导出脚本", style="Ghost.TButton",
                   command=self._gen_skill).pack(side=tk.LEFT)

        cols = ("file", "kind", "count")
        self.tree = ttk.Treeview(src_card.content, columns=cols, show="headings", height=4)
        for c, t, w in (("file", "文件", 420), ("kind", "类型", 130), ("count", "条数", 70)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=tk.W if c == "file" else tk.CENTER)
        self.tree.pack(fill=tk.X)

        res_card = Card(body, "定位结果", "只对能用孔位/坐标验证的项给出坐标")
        res_card.grid(row=1, column=0, sticky="nsew")
        body.rowconfigure(1, weight=1)
        self.log = LogView(res_card.content, height=12)
        self.log.pack(fill=tk.BOTH, expand=True)


    def _focus_first(self):
        try:
            self.win.lift()
            self.win.focus_force()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 来源
    def _scan(self):
        self.sources = pick.discover_sources(self.brd_path, [self.out_dir]) if self.brd_path else []
        for item in self.tree.get_children():
            self.tree.delete(item)
        seen = set()
        for s in self.sources:
            seen.add(os.path.abspath(s["path"]))
            self.tree.insert("", tk.END, values=(s["path"], _KIND_LABEL.get(s["kind"], s["kind"]), ""))
        for path in self.files:
            if os.path.abspath(path) in seen:
                continue
            self.tree.insert("", tk.END, values=(path, "手工添加", ""))
        if self.sources:
            self.status_var.set(f"发现 {len(self.sources)} 个坐标来源, 可直接生成定位清单")
            self.log.clear()
            self.log.append(f"发现坐标来源 {len(self.sources)} 个:", "success")
            for s in self.sources:
                self.log.append(f"  {os.path.basename(s['path'])}  ({_KIND_LABEL.get(s['kind'], s['kind'])})")
        else:
            self.status_var.set("没有坐标来源: 数量可知, 坐标需在 PCB Editor 导出")
            self.log.clear()
            self.log.append("没有找到坐标来源。", "warn")
            self.log.append("点「生成 SKILL 导出脚本」, 在 PCB Editor 里跑一次即可得到元件与孔位坐标;", "info")
            self.log.append("或者用 Manufacture > NC > NC Drill 导出 .drl 文件, 直接拖进来。", "info")

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择坐标来源文件",
            filetypes=[("坐标文件", "*.csv *.txt *.drl *.nc *.xln"),
                       ("所有文件", "*.*")])
        if not paths:
            return
        for p in paths:
            if p not in self.files:
                self.files.append(p)
        self._scan()

    def _gen_skill(self):
        target = os.path.join(self.out_dir, "allegro_bridge")
        try:
            paths = write_bundle(target)
        except OSError as exc:
            messagebox.showerror("生成失败", f"写脚本失败: {exc}", parent=self.win)
            return
        il = paths["il"].replace("\\", "/")
        out = os.path.abspath(target).replace("\\", "/")
        cmds = f'skill load("{il}")\nevan_pick_all("{out}")'
        try:
            self.win.clipboard_clear()
            self.win.clipboard_append(cmds)
        except tk.TclError:
            pass
        self.log.append("", None)
        self.log.append("已生成 SKILL 导出脚本 (命令已复制到剪贴板):", "success")
        for key in ("il", "scr", "readme"):
            self.log.append(f"  {paths[key]}", "info")
        self.log.append("PCB Editor 里执行:", "info")
        self.log.append(f'    skill load("{il}")', "info")
        self.log.append(f'    evan_pick_all("{out}")', "info")
        self.log.append("跑完回到本窗口点「重新扫描」, 再点「生成定位清单」。", "muted")
        self.status_var.set("SKILL 脚本已生成, 命令已复制")
        try:
            os.startfile(target)  # noqa: S606  打开脚本目录
        except OSError:
            pass

    # ------------------------------------------------------------ 执行
    def _run(self):
        if self._busy:
            return
        if not self.brd_path:
            messagebox.showwarning("提示", "没有 BRD 文件, 无法判定不符合项", parent=self.win)
            return
        if not (self.sources or self.files):
            if not messagebox.askyesno(
                    "没有坐标来源",
                    "现在没有任何坐标数据。\n\n数量可以从 .brd 得到, 但坐标必须在 PCB Editor "
                    "里导出 (SKILL / Placement / NC Drill)。\n\n要现在生成 SKILL 导出脚本吗?",
                    parent=self.win):
                return
            self._gen_skill()
            return
        self._busy = True
        self.status_var.set("正在分析并定位 ...")
        used = [os.path.basename(f) for f in self.files] or [os.path.basename(s["path"])
                                                             for s in self.sources]
        self.log.clear()
        self.log.append("开始分析 BRD 并生成定位清单 ...", "info")
        if used:
            self.log.append("使用坐标来源: " + ", ".join(used), "info")
        else:
            self.log.append("没有坐标来源: 只会给出数量, 不给坐标", "warn")
        args = dict(brd_path=self.brd_path, files=self.files or None,
                    out_dir=self.out_dir, result=self.result,
                    board_type=self.board_type, level=self.level,
                    factory=self.factory, base_name=self.base_name)
        threading.Thread(target=self._worker, args=(args,), daemon=True).start()

    def _worker(self, args):
        """后台只做计算, 结果进队列, 由主线程的 _poll_queue 取 (Tk 跨线程不安全)。"""
        try:
            self._queue.put(("done", pick.analyze_and_locate(**args)))
        except Exception as exc:  # noqa: BLE001
            self._queue.put(("error", exc))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "done":
                    self._done(payload)
                else:
                    self._failed(payload)
        except queue.Empty:
            pass
        try:
            self.win.after(120, self._poll_queue)
        except tk.TclError:
            pass

    def _failed(self, exc):
        self._busy = False
        self.status_var.set("定位失败")
        self.log.append(f"失败: {exc}", "error")
        messagebox.showerror("定位失败", str(exc), parent=self.win)

    def _done(self, bundle):
        self._busy = False
        self.last_bundle = bundle
        inv = bundle.get("inventory") or {}
        located = bundle.get("located") or []
        paths = bundle.get("paths") or {}
        self.log.append("", None)
        self.log.append(f"元件坐标 {len(inv.get('components') or [])} 条 / "
                        f"孔位 {len(inv.get('holes') or [])} 条", "success")
        for row in located:
            tag = "FAIL" if row["status"] == "fail" else "WARN"
            if row.get("count") is not None:
                self.log.append(f"[{tag}] {row['item']}: 命中 {row['count']} 个孔 ({row['note']})",
                                "error" if tag == "FAIL" else "warn")
                for c in (row.get("coords") or [])[:5]:
                    self.log.append(f"      X={c['x_mm']:.3f}  Y={c['y_mm']:.3f}  "
                                    f"钻={c['drill_mm']}  {c['padstack'] or ''}", "muted")
            else:
                self.log.append(f"[{tag}] {row['item']}: {row['note']}", "warn")
        for key in ("xlsx", "csv", "txt", "scr"):
            if paths.get(key):
                self.log.append(f"  {key}: {paths[key]}", "info")
        self.status_var.set(f"已生成: {bundle.get('out_dir')}")
        if paths.get("xlsx") or paths.get("txt"):
            self.open_btn.configure(state=tk.NORMAL)
            messagebox.showinfo("完成",
                                f"定位清单已生成:\n{bundle.get('out_dir')}",
                                parent=self.win)

    def _open_dir(self):
        target = (self.last_bundle or {}).get("out_dir") or self.out_dir
        try:
            os.startfile(target)  # noqa: S606
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc), parent=self.win)


def open_locate(master, brd_path: str = "", result: dict = None, out_dir: str = "",
                board_type: str = "rigid", level: str = "standard",
                factory: str = "hongban") -> LocateDialog:
    """从 DFM 对话框调用: 打开定位窗口并返回实例。"""
    return LocateDialog(master, brd_path=brd_path, result=result, out_dir=out_dir,
                        board_type=board_type, level=level, factory=factory)

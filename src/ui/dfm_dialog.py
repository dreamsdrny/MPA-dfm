"""Unified PCB/SMT workbench with snapshot-based background analysis and reports."""
from copy import deepcopy
from datetime import datetime
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from src.brd_design import extract_brd_design
from src.dfm_engine import analyze_design
from src.workbench_report import export_report
from src.pcb_reader import extract_brd_info
from src.ui.dfm_params import ParamPanel
from src.ui.dfm_results import ResultPanel
from src.ui.theme import COLORS, Card, apply_theme, enable_dpi_awareness, fonts
from src.ui.tasks import TaskRunner


class DFMDialog:
    """设计参数 -> 板厂制程能力比对 (支持红版/四会)。

    打开窗口即自动从 .brd 读取能读到的一切: 层数 / 器件数 / 最小线宽 / 最小线距 /
    最小颈宽 / 最小孔 / 最大孔 / 激光孔 / 孔环 / 板厚 / 孔壁间距, 表面处理一并带入。
    板框、阻焊、丝印、拼板、覆盖膜属图形层, .brd 不含, 需人工给 (标"待填")。
    判定只用「BRD 读到的值」和「人工确认过的值」; 表单预填的示例值不参与判定,
    免得拿假数据算出 PASS。
    """

    def __init__(self, master, brd_path: str = "", standalone: bool = False):
        self.brd_info = None
        self.last_result = None
        self.last_design = {}
        self._brd_queue = queue.Queue()
        self._poll_id = None
        self._alive = True
        self._load_token = 0
        self._loading = False
        self._loaded_path = ""
        self._checklist_window = None
        self._input_revision = 0

        enable_dpi_awareness()
        self.standalone = standalone
        self.win = master if standalone else tk.Toplevel(master)
        self.win.title("MPA-DFM - PCB / SMT 制造能力分析")
        self.win.geometry("1380x850")
        self.win.minsize(1080, 700)
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        self.win.bind("<Destroy>", self._on_destroy, add="+")
        self.win.configure(bg=COLORS["bg"])
        self._fonts = fonts(self.win)
        apply_theme(self.win)

        self.status_var = tk.StringVar(value="就绪：导入 BRD 后点「执行分析」")

        ttk.Label(self.win, textvariable=self.status_var, style="Status.TLabel",
                  anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)

        self._build_header()
        self._build_report_bar()
        self.tasks = TaskRunner(self.win, self._tasks_changed)
        self._build_body()

        if not brd_path and standalone:
            try:
                from src.ui.prefs import load_prefs

                brd_path = (load_prefs().get("dfm_brd") or "").strip()
            except Exception:  # noqa: BLE001
                brd_path = ""
        if brd_path:
            self.params.brd_path.set(brd_path)

        self.win.after(120, self._poll_brd_queue)
        if self.brd_path.get().strip():
            self.params._set_brd_status("正在读取 BRD ...")
            self._load_from_brd()
        else:
            self.params._set_brd_status("")

    # ------------------------------------------------------------ 组装
    def _build_header(self):
        hdr = tk.Frame(self.win, bg=COLORS["header"], height=64)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)

        title_box = tk.Frame(hdr, bg=COLORS["header"])
        title_box.pack(side=tk.LEFT, padx=22, pady=10)
        tk.Label(title_box, text="MPA-DFM", bg=COLORS["header"],
                 fg=COLORS["header_text"], font=self._fonts["title"]).pack(anchor=tk.W)
        tk.Label(title_box, text="PCB 制造能力 · SMT 工艺能力 · Checklist",
                 bg=COLORS["header"], fg=COLORS["header_muted"],
                 font=self._fonts["subtitle"]).pack(anchor=tk.W, pady=(2, 0))

        btns = tk.Frame(hdr, bg=COLORS["header"])
        btns.pack(side=tk.RIGHT, padx=22)
        self.run_button = ttk.Button(btns, text="执行 PCB 分析", style="Primary.TButton",
                                     command=self._run)
        self.run_button.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="Checklist", style="Secondary.TButton",
                   command=self._open_checklist).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(btns, text="器件统计", style="Secondary.TButton",
                   command=self._open_component_stats).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(btns, text="网络定位", style="Secondary.TButton",
                   command=self._open_net_locate).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(btns, text="关闭", style="Secondary.TButton",
                   command=self._close).pack(side=tk.LEFT)

    def _build_report_bar(self):
        bar = ttk.Frame(self.win, padding=(16, 6))
        bar.pack(fill=tk.X)
        ttk.Label(bar, text="报告导出", style="FieldLabel.TLabel").pack(side=tk.LEFT, padx=(0, 12))
        self.report_buttons = {}
        for kind, label in [("dfm", "DFM 综合报告"), ("pcb", "PCB 报告"), ("smt", "SMT 报告")]:
            button = ttk.Button(bar, text=label,
                                style="Primary.TButton" if kind == "dfm" else "Secondary.TButton",
                                command=lambda k=kind: self._export(k))
            button.pack(side=tk.LEFT, padx=(0, 8))
            self.report_buttons[kind] = button
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=130)
        self.progress.pack(side=tk.RIGHT)
        self.task_var = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.task_var).pack(side=tk.RIGHT, padx=12)

    def _tasks_changed(self, pending):
        self.run_button.configure(state=tk.DISABLED if self._loading or "pcb-analysis" in pending else tk.NORMAL)
        for button in self.report_buttons.values():
            button.configure(state=tk.DISABLED if "report-export" in pending else tk.NORMAL)
        if pending:
            self.task_var.set("后台处理中 · " + " / ".join(pending))
            self.progress.start(15)
        else:
            self.task_var.set("就绪")
            self.progress.stop()

    def _build_body(self):
        self.workbench = ttk.Notebook(self.win)
        self.workbench.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        body = ttk.Frame(self.workbench, style="TFrame", padding=(16, 14, 16, 12))
        self.workbench.add(body, text="PCB 厂 · 制造能力分析")
        body.columnconfigure(0, weight=0, minsize=384)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left_card = Card(body, "设计参数", "BRD 值自动带入并隐藏 · 只留需人工补的项")
        left_card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        right_card = Card(body, "分析结果", "")
        right_card.grid(row=0, column=1, sticky="nsew")

        self.params = ParamPanel(left_card.content, self._browse_brd, self._load_from_brd,
                                 font_bold=self._fonts["ui_bold"])
        self.params.pack(fill=tk.BOTH, expand=True)

        self.results = ResultPanel(right_card.content,
                                   on_locate=self._open_locate)
        self.results.pack(fill=tk.BOTH, expand=True)

        # 兼容旧属性名 (早期代码/测试直接访问这些变量)
        self.brd_path = self.params.brd_path
        self.brd_status_var = self.params.brd_status_var
        self._set_brd_status = self.params._set_brd_status
        self.entries = self.params.entries
        self.level_combo = self.params.level_combo
        self.tree = self.results.tree
        self.nb = self.results.nb
        self.result_var = self.results.result_var
        from src.ui.smt_panel import SMTPanel

        self.smt = SMTPanel(self.workbench, self._smt_design, self.tasks,
                            on_export=lambda: self._export("smt"))
        self.workbench.add(self.smt, text="SMT 厂 · 工艺能力分析 / YAML")
        for var in list(self.entries.values()) + [self.params.factory, self.params.level,
                    self.params.board_type, self.params.copper_oz, self.params.surface_finish,
                    self.params.board_name, self.params.polarity_var, self.params.panelized_var,
                    self.brd_path]:
            var.trace_add("write", self._invalidate_results)

    def _invalidate_results(self, *_args):
        self._input_revision += 1
        self.last_result = None
        self.last_design = {}
        self.results.clear()
        self.result_var.set("输入已变化，请重新执行 PCB 分析")
        self.smt.invalidate()

    def _smt_design(self):
        if self._loading:
            raise ValueError("BRD 正在读取，请等待完成")
        path = self.brd_path.get().strip()
        if path and path != self._loaded_path:
            raise ValueError("BRD 路径已变化，请先点击「从 BRD 读取」")
        design = self.params.read_design()
        design["_input_brd"] = path
        return design

    def _open_checklist(self):
        from src.ui.main_window import MainWindow

        if self._checklist_window and self._checklist_window.root.winfo_exists():
            self._checklist_window.root.lift()
            return
        self._checklist_window = MainWindow(self.win)
        self._checklist_window.root.title("MPA-DFM · Checklist 自动填写")
        self._checklist_window.brd_path.set(self.brd_path.get())

    # ------------------------------------------------------------ BRD 带入
    def _open_locate(self):
        """打开坐标定位窗口: 用当前参数/结果, 把不符合项落到坐标上。"""
        from src.ui.dfm_locate import open_locate

        path = self.brd_path.get().strip()
        if not path:
            messagebox.showinfo("提示", "请先选择 BRD 文件", parent=self.win)
            return
        out_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "定位清单")
        open_locate(self.win, brd_path=path, result=self.last_result, out_dir=out_dir,
                    board_type=self.params.board_type.get(),
                    level=self.params.level.get(),
                    factory=self.params.factory.get())

    def _open_component_stats(self):
        """打开「器件统计」: 按位号前缀分类，统计数量/封装/坐标/引脚。"""
        from src.ui.component_stats_dialog import open_component_stats

        path = self.brd_path.get().strip()
        if not path:
            messagebox.showinfo("提示", "请先选择 BRD 文件", parent=self.win)
            return
        if not os.path.exists(path):
            messagebox.showerror("提示", f"文件不存在:\n{path}", parent=self.win)
            return
        out_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "统计清单")
        open_component_stats(self.win, brd_path=path, out_dir=out_dir)

    def _open_net_locate(self):
        """打开「网络定位」: 按网络名查坐标 / 位置 / 外框长宽。"""
        from src.ui.net_locate_dialog import open_net_locate

        path = self.brd_path.get().strip()
        if not path:
            messagebox.showinfo("提示", "请先选择 BRD 文件", parent=self.win)
            return
        if not os.path.exists(path):
            messagebox.showerror("提示", f"文件不存在:\n{path}", parent=self.win)
            return
        out_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "定位清单")
        open_net_locate(self.win, brd_path=path, out_dir=out_dir)

    def _browse_brd(self):
        path = filedialog.askopenfilename(
            title="Select Allegro BRD File",
            filetypes=[("BRD files", "*.brd"), ("All files", "*.*")])
        if path:
            self.brd_path.set(path)
            self._load_from_brd()

    def _load_from_brd(self):
        path = self.brd_path.get().strip()
        if not path:
            messagebox.showwarning("提示", "请先选择 BRD 文件")
            return
        if not os.path.exists(path):
            self._set_brd_status(f"BRD 文件不存在: {path}")
            return
        self._set_brd_status("正在读取 BRD ...")
        self._load_token += 1
        token = self._load_token
        self._loading = True
        self._loaded_path = ""
        self.brd_info = None
        self.params.reset_design()
        self.smt.invalidate(clear_inputs=True)
        self.run_button.configure(state=tk.DISABLED)
        if self.standalone:
            try:
                from src.ui.prefs import load_prefs, save_prefs

                data = load_prefs()
                data["dfm_brd"] = path
                save_prefs(data)
            except Exception:  # noqa: BLE001  记不住不影响分析
                pass
        threading.Thread(target=self._read_brd_worker, args=(path, token), daemon=True).start()

    def _read_brd_worker(self, path, token):
        """后台线程只做解析, 结果放队列; 所有 Tk 调用都回主线程 (见 _poll_brd_queue)。

        优先解析 .brd 内的约束 XML; 老格式 (无 zip 归档) 退回 pcb_reader 的二进制正则。
        """
        try:
            info = extract_brd_design(path)
            if not info.get("ok"):
                legacy = extract_brd_info(path)
                legacy["_legacy"] = True
                legacy["_new_error"] = info.get("error")
                info = legacy
        except Exception as exc:  # noqa: BLE001
            self._brd_queue.put((token, path, "err", str(exc)))
            return
        self._brd_queue.put((token, path, "ok", info))

    def _poll_brd_queue(self):
        if not self._alive:
            return
        self._poll_id = None
        try:
            while True:
                token, path, kind, payload = self._brd_queue.get_nowait()
                if token != self._load_token:
                    continue
                self._loading = False
                self._tasks_changed(tuple(self.tasks.pending))
                if path != self.brd_path.get().strip():
                    self._set_brd_status("路径已变化，请重新读取 BRD")
                    continue
                if kind == "ok":
                    self.brd_info = payload
                    self.params.apply_brd(payload)
                    self._loaded_path = path
                else:
                    self._set_brd_status(f"读取 BRD 失败: {payload}")
        except queue.Empty:
            pass
        except Exception:  # noqa: BLE001  窗口已关闭
            return
        try:
            if self._alive:
                self._poll_id = self.win.after(120, self._poll_brd_queue)
        except Exception:  # noqa: BLE001  窗口已关闭
            pass

    def _close(self):
        self._alive = False
        self.tasks.close()
        self._cancel_poll()
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def _on_destroy(self, event=None):
        if event is not None and event.widget is not self.win:
            return
        self._alive = False
        self._cancel_poll()

    def _cancel_poll(self):
        if self._poll_id is not None:
            try:
                self.win.after_cancel(self._poll_id)
            except Exception:  # noqa: BLE001
                pass
            self._poll_id = None

    # ------------------------------------------------------------ 分析
    def _run(self):
        try:
            design = self._smt_design()
        except ValueError as exc:
            messagebox.showwarning("参数错误", str(exc))
            return

        if self.brd_info:
            # 数据来源由 ParamPanel.read_design() 按字段给出 (_sources / _assumed)
            design["_warnings"] = self.brd_info.get("warnings", [])
            design.setdefault("_data_origin", "BRD+手工")

        revision = self._input_revision
        design = deepcopy(design)
        options = dict(board_type=self.params.board_type.get(),
                       capability_level=self.params.level.get(), factory=self.params.factory.get())
        brd_path = self.brd_path.get().strip()

        def compute():
            result = analyze_design(design, **options)
            result["analyzed_at"] = datetime.now().isoformat(timespec="seconds")
            result["input_brd"] = brd_path
            return result

        def complete(result):
            if revision != self._input_revision:
                self.status_var.set("PCB 分析期间输入已变化，已丢弃过期结果，请重新分析")
                return
            self._render_pcb(result, design)

        def failed(error):
            if revision == self._input_revision:
                messagebox.showerror("PCB 分析失败", error, parent=self.win)

        if self.tasks.submit("pcb-analysis", compute, complete, failed):
            self.status_var.set("正在后台执行 PCB 分析，界面可继续操作…")

    def _render_pcb(self, result, design):
        self.last_result = result
        self.last_design = design

        self.results.render_capability(result)
        # 判定用了 BRD 带入值, 分析完直接把「显示 BRD 带入项」按钮点亮并展开
        try:
            self.params.reveal_brd()
        except Exception:  # noqa: BLE001  面板已销毁时忽略
            pass

        coverage = result.get("coverage") or {}
        verdict = "DFM PASS" if result["passed"] else "DFM FAIL"
        risk = result.get("risk", {})
        risk_txt = f" | 评分 {risk.get('score')}/100 ({risk.get('risk_level')})" if risk else ""
        name_txt = f"{design['board_name']} | " if design.get("board_name") else ""
        brd_n = result.get("brd_item_count") or 0
        manual_n = sum(1 for it in result.get("items", []) if it.get("source") == "手工")
        assumed_n = len(result.get("assumed_fields") or [])
        src_txt = f"BRD 数据 {brd_n} 项 / 人工值 {manual_n} 项"
        if assumed_n:
            src_txt += f" / 示例未确认 {assumed_n} 项(未参与)"
        status = (f"{name_txt}{result['factory_label']} {result['board_type_label']} - "
                  f"{result['capability_label']} | "
                  f"{result.get('verdict', verdict)} (错误 {result['error_count']}, "
                  f"警告 {result['warning_count']}, 覆盖 {coverage.get('ratio', 0):.0%}, "
                  f"{src_txt}, 共 {len(result.get('issues', []))} 项问题){risk_txt}")
        self.status_var.set(status)

        if not result.get("items"):
            messagebox.showinfo(
                "没有可判定的参数",
                "左侧没有已确认的数值 —— BRD 没读到, 也没手动填。\n"
                "请先「从 BRD 读取」, 或手工填入已知项 (填完即视为人工确认值)。")

        self.results.result_var.set(
            f"{name_txt}{result['factory_label']} · {result['board_type_label']} · "
            f"{result['capability_label']}　|　{result.get('verdict', verdict)}"
            f"　|　覆盖 {coverage.get('ratio', 0):.0%}"
            )

    # ------------------------------------------------------------ 导出
    def _export(self, kind="pcb"):
        if "report-export" in self.tasks.pending:
            return
        if kind in ("dfm", "pcb") and not self.last_result:
            messagebox.showwarning("报告", "请先完成当前输入的 PCB 分析", parent=self.win)
            return
        if kind in ("dfm", "smt") and not self.smt.last_result:
            messagebox.showwarning("报告", "请先完成当前输入的 SMT 分析", parent=self.win)
            return
        # Snapshot before the modal dialog: subsequent input edits cannot change this report.
        pcb = deepcopy(self.last_result)
        smt = deepcopy(self.smt.last_result)
        board_meta = {"board_name": self.last_design.get("board_name", "") or
                      (smt or {}).get("board_name", ""),
                      "brd_path": self.brd_path.get().strip(), "submitter": "MPA-DFM",
                      "rule_set": "BRD 带入 + 已确认参数"}
        labels = {"dfm": "DFM综合报告", "pcb": "PCB报告", "smt": "SMT报告"}
        path = filedialog.asksaveasfilename(
            parent=self.win, title="导出 " + labels[kind],
            defaultextension=".xlsx",
            initialfile=labels[kind] + ".xlsx",
            filetypes=[("Excel", "*.xlsx"), ("All files", "*.*")],
        )
        if not path:
            return
        def done(paths):
            messagebox.showinfo("导出完成", "\n".join(paths), parent=self.win)

        self.tasks.submit("report-export",
                          lambda: export_report(kind, path, pcb, smt, board_meta), done,
                          lambda error: messagebox.showerror("导出失败", error, parent=self.win))

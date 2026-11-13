"""Unified PCB/SMT workbench with snapshot-based background analysis and reports."""
from copy import deepcopy
from datetime import datetime
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from src.brd_design import extract_brd_design
from src.dfm_engine import analyze_design
from src.workbench_report import export_report
from src.pcb_reader import extract_brd_info
from src.ui.dfm_params import ParamPanel
from src.ui.dfm_results import ResultPanel
from src.ui.theme import (COLORS, Card, apply_app_icon, apply_theme, enable_dpi_awareness, fonts,
                          set_color_mode, refresh_widget_colors)
from src.ui import i18n
from src.ui.tasks import TaskRunner
from src.allegro_bridge import AllegroSession, discover_editor, arrange_editor, point_mm
from src.ui.responsive import scroll_workbench, FlowBar, wrap_label


class DFMDialog:
    """设计参数 -> 板厂制程能力比对 (支持红版/四会)。

    打开窗口即自动从 .brd 读取能读到的一切: 层数 / 器件数 / 最小线宽 / 最小线距 /
    最小颈宽 / 最小孔 / 最大孔 / 激光孔 / 孔环 / 板厚 / 孔壁间距, 表面处理一并带入。
    板框、阻焊、丝印、拼板、覆盖膜属图形层, .brd 不含, 需人工给 (标"待填")。
    判定只用「BRD 读到的值」和「人工确认过的值」; 表单预填的示例值不参与判定,
    免得拿假数据算出 PASS。
    """

    def __init__(self, master, brd_path: str = "", standalone: bool = False):
        from src.ui.prefs import load_prefs
        prefs = load_prefs()
        self._ui_poll_id = None
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
        self._pcb_state = "idle"
        self._editor_session = None
        self._editor_poll_id = None
        self._editor_last_ack = None
        self._editor_started = 0
        self._editor_export_pending = False
        self._editor_arranged = False
        self._editor_layout_requested = False
        self._component_coordinates = []
        self._component_brd_path = ""
        self._coordinate_load_token = 0

        enable_dpi_awareness()
        self.standalone = standalone
        self.win = master if standalone else tk.Toplevel(master)
        set_color_mode(prefs.get("ui_theme", "light"))
        i18n.LANGUAGE = prefs.get("ui_language", "zh")
        self.win.title("Evan MPA-DFM - PCB / SMT 制造能力分析")
        width = min(1380, max(800, self.win.winfo_screenwidth() - 60))
        height = min(850, max(560, self.win.winfo_screenheight() - 100))
        self.win.geometry(f"{width}x{height}")
        self.win.minsize(min(800, width), min(500, height))
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        self.win.bind("<Destroy>", self._on_destroy, add="+")
        self.win.configure(bg=COLORS["bg"])
        self._fonts = fonts(self.win)
        apply_theme(self.win)
        apply_app_icon(self.win)

        self.status_var = tk.StringVar(value="就绪：导入 BRD 后点「执行分析」")
        self.coordinate_dir = tk.StringVar()
        self.report_format = tk.StringVar(value=".xlsx")
        self.language = tk.StringVar(value="English" if i18n.LANGUAGE == "en" else "中文")
        self.color_mode = tk.StringVar(value="Dark" if prefs.get("ui_theme") == "dark" else "Light")
        self.hide_low_priority = tk.BooleanVar(value=False)
        self.output_pcb_report = tk.BooleanVar(value=False)
        self.gerber_dir = tk.StringVar()
        self.auto_gerber = tk.BooleanVar(value=True)
        self._exported_gerber = None
        self.editor_path = tk.StringVar(value=discover_editor(prefs.get("allegro_editor", "")))
        self.sync_editor = tk.BooleanVar(value=True)
        self.beside_editor = tk.BooleanVar(value=True)
        self.editor_status = tk.StringVar(value="Allegro：未连接")

        status_label = ttk.Label(self.win, textvariable=self.status_var, style="Status.TLabel", anchor=tk.W)
        status_label.pack(fill=tk.X, side=tk.BOTTOM)
        wrap_label(status_label, self.win, 32)
        self.content = scroll_workbench(self.win)

        self._build_header()
        self._build_report_bar()
        self._build_coordinate_bar()
        self.tasks = TaskRunner(self.win, self._tasks_changed)
        self._build_body()
        self.output_pcb_report.trace_add("write", lambda *_: self._tasks_changed(tuple(self.tasks.pending)))
        self._tasks_changed(())
        self._apply_language()
        self._ui_poll_id = self.win.after(600, self._refresh_ui_labels)

        if not brd_path and standalone:
            try:
                from src.ui.prefs import load_prefs

                brd_path = (load_prefs().get("dfm_brd") or "").strip()
            except Exception:  # noqa: BLE001
                brd_path = ""
        if brd_path:
            self.params.brd_path.set(brd_path)

        self._poll_id = self.win.after(120, self._poll_brd_queue)
        if self.brd_path.get().strip():
            self.params._set_brd_status("正在读取 BRD ...")
            self._load_from_brd()
        else:
            self.params._set_brd_status("")

    # ------------------------------------------------------------ 组装
    def _build_header(self):
        hdr = tk.Frame(self.content, bg=COLORS["header"])
        hdr.pack(fill=tk.X)

        title_box = tk.Frame(hdr, bg=COLORS["header"])
        title_box.pack(side=tk.LEFT, padx=22, pady=10)
        tk.Label(title_box, text="Evan · MPA-DFM", bg=COLORS["header"],
                 fg=COLORS["header_text"], font=self._fonts["title"]).pack(anchor=tk.W)
        tk.Label(title_box, text="PCB 制造能力 · SMT 工艺能力 · BRD 问题定位",
                 bg=COLORS["header"], fg=COLORS["header_muted"],
                 font=self._fonts["subtitle"]).pack(anchor=tk.W, pady=(2, 0))

        btns = FlowBar(self.content, padding=(12, 0))
        btns.pack(fill=tk.X)
        self.run_button = ttk.Button(btns, text="执行 PCB 分析", style="Primary.TButton",
                                     command=self._run)
        btns.add(self.run_button)
        self.open_brd_button = ttk.Button(btns, text="打开 BRD", style="Secondary.TButton",
                                         command=self._open_brd_editor)
        btns.add(self.open_brd_button)

        btns.add(ttk.Button(btns, text="网络定位", style="Secondary.TButton", command=self._open_net_locate))

        btns.add(ttk.Button(btns, text="关闭", style="Secondary.TButton", command=self._close))
        settings = ttk.Frame(self.content, padding=(16, 3))
        settings.pack(fill=tk.X)
        ttk.Label(settings, text="语言").pack(side=tk.LEFT)
        language = ttk.Combobox(settings, textvariable=self.language, values=("中文", "English"),
                                state="readonly", width=9)
        language.pack(side=tk.LEFT, padx=(6, 16))
        language.bind("<<ComboboxSelected>>", lambda _: self._apply_language())
        ttk.Label(settings, text="主题").pack(side=tk.LEFT)
        theme = ttk.Combobox(settings, textvariable=self.color_mode, values=("Light", "Dark"),
                             state="readonly", width=8)
        theme.pack(side=tk.LEFT, padx=6)
        theme.bind("<<ComboboxSelected>>", lambda _: self._apply_color_mode())

    def _save_ui_preferences(self):
        from src.ui.prefs import load_prefs, save_prefs
        prefs = load_prefs()
        prefs.update(ui_language="en" if self.language.get() == "English" else "zh",
                     ui_theme="dark" if self.color_mode.get() == "Dark" else "light")
        save_prefs(prefs)

    def _apply_language(self):
        i18n.LANGUAGE = "en" if self.language.get() == "English" else "zh"
        i18n.localize_widgets(self.win)
        if self.last_result:
            self.results.render_capability(self.last_result)
        if self.smt.last_result:
            self.smt._render_result(self.smt.last_result, self.smt._revision)
        self._save_ui_preferences()

    def _apply_color_mode(self):
        old = set_color_mode("dark" if self.color_mode.get() == "Dark" else "light")
        refresh_widget_colors(self.win, old)
        self.params._render_fields()
        self.results.refresh_colors()
        self.smt.detail.configure(bg=COLORS["card"], fg=COLORS["text"], insertbackground=COLORS["text"])
        self._tasks_changed(tuple(self.tasks.pending))
        i18n.localize_widgets(self.win)
        self._save_ui_preferences()

    def _refresh_ui_labels(self):
        self._ui_poll_id = None
        if not self._alive:
            return
        i18n.localize_widgets(self.win)
        if self.language.get() == "English":
            self.win.title("Evan MPA-DFM - PCB / SMT Manufacturing Analysis")
        else:
            self.win.title("Evan MPA-DFM - PCB / SMT 制造能力分析")
        self._ui_poll_id = self.win.after(600, self._refresh_ui_labels)

    def _build_report_bar(self):
        bar = ttk.Frame(self.content, padding=(16, 6))
        bar.pack(fill=tk.X)
        ttk.Label(bar, text="报告导出", style="FieldLabel.TLabel").pack(side=tk.LEFT, padx=(0, 12))
        ttk.Combobox(bar, textvariable=self.report_format, values=(".xlsx", ".csv"),
                     state="readonly", width=7).pack(side=tk.LEFT, padx=(0, 8))
        self.report_buttons = {}
        for kind, label in [("dfm", "DFM 综合报告"), ("pcb", "PCB 报告"), ("smt", "SMT 报告")]:
            button = ttk.Button(bar, text=label,
                                style="Primary.TButton" if kind == "dfm" else "Secondary.TButton",
                                command=lambda k=kind: self._export(k))
            button.pack(side=tk.LEFT, padx=(0, 8))
            self.report_buttons[kind] = button
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=130,
                                        style="Idle.Horizontal.TProgressbar")
        self.progress.pack(side=tk.RIGHT)
        self.task_var = tk.StringVar(value="就绪")
        self.task_label = ttk.Label(bar, textvariable=self.task_var, foreground=COLORS["muted"])
        self.task_label.pack(side=tk.BOTTOM, fill=tk.X)
        wrap_label(self.task_label, bar)
        self.task_dot = tk.Canvas(bar, width=16, height=16, bg=COLORS["bg"],
                                  highlightthickness=0)
        self.task_dot.pack(side=tk.RIGHT)
        self._dot = self.task_dot.create_oval(3, 3, 13, 13, fill=COLORS["faint"], outline="")
        options = ttk.Frame(self.content, padding=(16, 0))
        options.pack(fill=tk.X)
        ttk.Checkbutton(options, text="报告隐藏低优先级", variable=self.hide_low_priority).pack(side=tk.LEFT)
        ttk.Checkbutton(options, text="需要独立 PCB 报告", variable=self.output_pcb_report).pack(side=tk.LEFT, padx=12)

    def _build_coordinate_bar(self):
        gerber = ttk.Frame(self.content, padding=(16, 3))
        gerber.pack(fill=tk.X)
        ttk.Checkbutton(gerber, text="执行分析时后台导出 Gerber", variable=self.auto_gerber).pack(side=tk.LEFT)
        ttk.Entry(gerber, textvariable=self.gerber_dir, width=35).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=8)
        ttk.Button(gerber, text="选择已有 Gerber", command=self._browse_gerber).pack(side=tk.LEFT)
        ttk.Button(gerber, text="导出 Gerber", command=self._export_gerber_only).pack(side=tk.LEFT, padx=6)
        ttk.Button(gerber, text="Gerber 问题预览", command=self._preview_gerber).pack(side=tk.LEFT)
        bar = ttk.Frame(self.content, padding=(16, 2))
        bar.pack(fill=tk.X)
        ttk.Label(bar, text="问题坐标来源").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Entry(bar, textvariable=self.coordinate_dir, width=40).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(bar, text="选择导出目录", command=self._browse_coordinates).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="导入 COMP_XY", command=self._import_comp_xy).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="生成 Allegro 坐标脚本", command=self._generate_coordinates).pack(side=tk.LEFT)
        editor = ttk.Frame(self.content, padding=(16, 2))
        editor.pack(fill=tk.X)
        ttk.Button(editor, text="设置 PCB Editor", command=self._configure_editor).pack(side=tk.LEFT)
        self.editor_export_button = ttk.Button(editor, text="打开 BRD 并提取坐标",
                                               command=self._open_export_editor)
        self.editor_export_button.pack(side=tk.LEFT, padx=6)
        ttk.Checkbutton(editor, text="选点同步到 Allegro", variable=self.sync_editor).pack(side=tk.LEFT)
        ttk.Checkbutton(editor, text="并排显示", variable=self.beside_editor).pack(side=tk.LEFT, padx=6)
        editor_label = ttk.Label(editor, textvariable=self.editor_status)
        editor_label.pack(side=tk.BOTTOM, fill=tk.X)
        wrap_label(editor_label, editor)

    def _browse_coordinates(self):
        path = filedialog.askdirectory(parent=self.win, title="选择当前 BRD 的 SKILL / NC Drill 导出目录")
        if path:
            self.coordinate_dir.set(path)
            self._refresh_component_coordinates(select_tab=True)

    def _browse_gerber(self):
        path = filedialog.askdirectory(parent=self.win, title="选择同一版本的一套 Gerber 目录")
        if path:
            self.auto_gerber.set(False)
            self.gerber_dir.set(path)

    def _export_gerber_only(self):
        from src.gerber_export import export_brd_gerbers
        path = self.brd_path.get().strip()
        if not os.path.isfile(path):
            messagebox.showwarning("Gerber", "请先选择 BRD", parent=self.win)
            return
        editor = self.editor_path.get()
        output = os.path.join(os.path.dirname(os.path.abspath(path)), "Gerber_DFM")
        def done(bundle):
            self._exported_gerber = bundle
            self.auto_gerber.set(False)
            self.gerber_dir.set(bundle["directory"])
            self.status_var.set(f"Gerber 已导出：{len(bundle['files'])} 层；点击执行分析")
        self.tasks.submit("gerber-export", lambda: export_brd_gerbers(path, output, editor), done,
                          lambda error: messagebox.showerror("Gerber 导出失败", error, parent=self.win))

    def _preview_gerber(self):
        path = (self.last_result or {}).get("gerber_preview")
        if path and os.path.isfile(path):
            import webbrowser
            from pathlib import Path
            webbrowser.open(Path(path).as_uri())
        else:
            messagebox.showinfo("Gerber", "请先完成 Gerber 分析，再打开问题预览", parent=self.win)

    def _import_comp_xy(self):
        from src.pick import parse_placement_txt
        from src.pcb_locations import component_coordinates
        path = filedialog.askopenfilename(parent=self.win, title="选择 BRD 导出的 COMP_XY 坐标文件",
                                         filetypes=[("COMP_XY 坐标", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        parsed = parse_placement_txt(path)
        if not parsed.get("ok"):
            messagebox.showerror("坐标导入", parsed.get("error", "未读到坐标"), parent=self.win)
            return
        self.coordinate_dir.set(os.path.dirname(path))
        unit = (self.brd_info or {}).get("units") or parsed.get("unit")
        coordinates = component_coordinates({"components": parsed["items"]}, unit)
        self._component_coordinates = coordinates
        self._component_brd_path = os.path.normcase(os.path.abspath(self.brd_path.get().strip()))
        self.results.show_components(coordinates, "COMP_XY 器件原点；丝印需另外的图形导出")
        self.results.nb.select(2)
        self.editor_status.set(f"已导入 COMP_XY：{len(coordinates)} 个器件坐标")
        if self.brd_path.get().strip() and not self._loading and "pcb-analysis" not in self.tasks.pending:
            self._run()

    def _generate_coordinates(self):
        from src.allegro_skill import write_bundle
        folder = filedialog.askdirectory(parent=self.win, title="选择坐标脚本和 CSV 输出目录")
        if not folder:
            return
        try:
            paths = write_bundle(folder)
        except OSError as exc:
            messagebox.showerror("生成失败", str(exc), parent=self.win)
            return
        self.coordinate_dir.set(folder)
        command = 'skill load("' + paths["il"].replace("\\", "/") + '")\n'
        command += 'skill evan_pick_all("' + os.path.abspath(folder).replace("\\", "/") + '")\n'
        command += 'skill load("' + paths["geometry_il"].replace("\\", "/") + '")\n'
        command += 'skill evan_geometry_all("' + os.path.abspath(folder).replace("\\", "/") + '")'
        self.win.clipboard_clear()
        self.win.clipboard_append(command)
        messagebox.showinfo("坐标导出", "命令已复制。在 Allegro 打开同一版本 BRD，执行：\n\n" + command +
                            "\n\n导出后重新执行 PCB 分析，报告将包含问题 X/Y 和定位命令。", parent=self.win)

    def _tasks_changed(self, pending):
        self.run_button.configure(state=tk.DISABLED if self._loading or "pcb-analysis" in pending or "gerber-export" in pending else tk.NORMAL)
        for button in self.report_buttons.values():
            button.configure(state=tk.DISABLED if "report-export" in pending else tk.NORMAL)
        if not self.output_pcb_report.get():
            self.report_buttons["pcb"].configure(state=tk.DISABLED)
        if pending:
            names = {"pcb-analysis": "PCB 分析", "smt-analysis": "SMT 分析",
                     "report-export": "报告导出"}
            self.task_var.set("处理中 · " + " / ".join(names.get(p, p) for p in pending))
            self.task_label.configure(foreground=COLORS["primary"])
            self.task_dot.itemconfigure(self._dot, fill=COLORS["primary"])
            self.progress.configure(mode="indeterminate", style="Running.Horizontal.TProgressbar")
            self.progress.start(15)
        else:
            self.progress.stop()
            color = COLORS["primary"] if self._pcb_state == "complete" else (
                COLORS["danger"] if self._pcb_state == "error" else COLORS["faint"])
            self.task_var.set({"complete": "PCB 分析完成", "error": "PCB 分析失败"}.get(
                self._pcb_state, "就绪"))
            self.task_label.configure(foreground=color)
            self.task_dot.itemconfigure(self._dot, fill=color)
            self.progress.configure(mode="determinate", value=100 if self._pcb_state == "complete" else 0,
                                    style="Complete.Horizontal.TProgressbar" if self._pcb_state == "complete"
                                    else "Idle.Horizontal.TProgressbar")

    def _build_body(self):
        self.workbench = ttk.Notebook(self.content)
        self.workbench.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        body = ttk.Frame(self.workbench, style="TFrame", padding=(16, 14, 16, 12))
        self.workbench.add(body, text="PCB 厂 · 制造能力分析")
        body.columnconfigure(0, weight=1, minsize=350)
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
                                    on_locate=self._open_locate,
                                    on_open_point=self._open_problem_point,
                                    on_point_selected=self._sync_problem_point)
        self.results.pack(fill=tk.BOTH, expand=True)
        body.configure(height=600)

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
        self.smt.detail.configure(bg=COLORS["card"], fg=COLORS["text"], insertbackground=COLORS["text"])
        for var in list(self.entries.values()) + [self.params.factory, self.params.level,
                    self.params.board_type, self.params.copper_oz, self.params.surface_finish,
                    self.params.board_name, self.params.polarity_var, self.params.panelized_var,
                    self.brd_path, self.coordinate_dir, self.gerber_dir, self.auto_gerber]:
            var.trace_add("write", self._invalidate_results)

    def _invalidate_results(self, *_args):
        self._input_revision += 1
        self.last_result = None
        self.last_design = {}
        path = self.brd_path.get().strip()
        same_board = bool(self._component_brd_path) and os.path.normcase(os.path.abspath(path)) == self._component_brd_path
        self.results.clear(clear_components=not same_board)
        if not same_board:
            self._component_coordinates = []
        self.result_var.set("输入已变化，请重新执行 PCB 分析")
        self.smt.invalidate()
        self._pcb_state = "idle"
        self._tasks_changed(tuple(self.tasks.pending))

    def _smt_design(self):
        if self._loading:
            raise ValueError("BRD 正在读取，请等待完成")
        path = self.brd_path.get().strip()
        if path and path != self._loaded_path:
            raise ValueError("BRD 路径已变化，请先点击「从 BRD 读取」")
        design = self.params.read_design()
        design["_input_brd"] = path
        return design

    def _configure_editor(self):
        path = filedialog.askopenfilename(parent=self.win, title="选择 Allegro PCB Editor (allegro.exe)",
                                         filetypes=[("Windows 程序", "*.exe")])
        if path:
            self.editor_path.set(path)
            from src.ui.prefs import load_prefs, save_prefs
            prefs = load_prefs()
            prefs["allegro_editor"] = path
            save_prefs(prefs)

    def _editor_for_current_board(self):
        session = self._editor_session
        path = self.brd_path.get().strip()
        return session if (session and session.running() and
                           os.path.normcase(os.path.abspath(path)) == os.path.normcase(session.brd_path)) else None

    def _start_editor(self, point=None, export=False):
        path = self.brd_path.get().strip()
        if not os.path.isfile(path):
            messagebox.showwarning("打开 BRD", "请先选择有效的 BRD 文件", parent=self.win)
            return
        if point is not None:
            if point.get("brd_alignment") is False:
                messagebox.showinfo("坐标原点", "该 Gerber 未确认与 BRD 原点一致，请使用 Gerber 问题预览查看位置。",
                                    parent=self.win)
                return
            try:
                point_mm(point)
            except ValueError as exc:
                messagebox.showinfo("问题定位", str(exc), parent=self.win)
                return
        session = self._editor_for_current_board()
        if session and export and self._editor_export_pending:
            self.editor_status.set("Allegro：正在提取当前 BRD 坐标，请等待…")
            return
        if session and not export:
            if point is not None:
                try:
                    session.locate(point)
                except (OSError, ValueError) as exc:
                    messagebox.showerror("问题定位", str(exc), parent=self.win)
                    return
                self.editor_status.set("Allegro：正在同步问题点…")
            self._editor_layout_requested = True
            self._editor_arranged = False
            self._schedule_editor_poll()
            return
        editor = discover_editor(self.editor_path.get())
        if not editor:
            self._configure_editor()
            editor = discover_editor(self.editor_path.get())
            if not editor:
                messagebox.showwarning("PCB Editor", "未找到 PCB Editor，请设置 allegro.exe 路径", parent=self.win)
                return
        try:
            session = AllegroSession(path, editor)
            # Keep exports next to the board: next launch can rediscover them.
            output = os.path.join(os.path.dirname(os.path.abspath(path)), "定位清单", "allegro_bridge") if export else ""
            session.prepare(export_dir=output)
            if point is not None:
                session.locate(point)
            session.launch()
        except (OSError, ValueError) as exc:
            messagebox.showerror("打开 BRD 失败", str(exc), parent=self.win)
            return
        self._editor_session = session
        self._editor_started = time.monotonic()
        self._editor_last_ack = None
        self._editor_export_pending = export
        self._editor_layout_requested = True
        self._editor_arranged = False
        self.editor_status.set("Allegro：正在打开 BRD" + ("并导出坐标…" if export else "…"))
        self._schedule_editor_poll()

    def _open_brd_editor(self):
        self._open_export_editor()

    def _open_export_editor(self):
        if "pcb-analysis" in self.tasks.pending or self._loading:
            messagebox.showinfo("坐标提取", "请等待当前 PCB 读取/分析完成", parent=self.win)
            return
        self._start_editor(export=True)

    def _open_problem_point(self, point):
        self._start_editor(point=point)

    def _sync_problem_point(self, point):
        if point.get("brd_alignment") is False:
            return
        if self.sync_editor.get():
            session = self._editor_for_current_board()
            if session:
                try:
                    session.locate(point)
                    self.editor_status.set("Allegro：正在同步问题点…")
                except (OSError, ValueError) as exc:
                    self.editor_status.set("Allegro：同步失败 " + str(exc))

    def _schedule_editor_poll(self):
        if self._alive and self._editor_poll_id is None:
            self._editor_poll_id = self.win.after(350, self._poll_editor)

    def _poll_editor(self):
        self._editor_poll_id = None
        session = self._editor_session
        if not self._alive or session is None:
            return
        sequence, state = session.status()
        current_board = self._editor_for_current_board() is not None
        if not session.running():
            self.editor_status.set("Allegro：已关闭或启动失败，请检查许可/板文件")
            return
        if not current_board:
            self.editor_status.set("Allegro：BRD 已切换，请重新打开当前板")
        elif (sequence, state) != self._editor_last_ack:
            labels = {"READY": "已连接", "LOCATED": "已定位选中问题点", "BOARD_CHANGED": "Editor 已换板，请重新打开",
                      "ZOOM_FAILED": "缩放失败", "TIMER_FAILED": "同步定时器不可用"}
            if state != "WAITING":
                self.editor_status.set("Allegro：" + labels.get(state, state))
                self._editor_last_ack = (sequence, state)
        if current_board and self._editor_export_pending and state in ("READY", "LOCATED"):
            self._editor_export_pending = False
            self.coordinate_dir.set(session.export_dir)
            self._refresh_component_coordinates(auto_analyze=True, select_tab=True)
        if current_board and self.beside_editor.get() and self._editor_layout_requested and not self._editor_arranged:
            self._editor_arranged = arrange_editor(session.process.pid, self.win.winfo_id())
            if self._editor_arranged:
                self._editor_layout_requested = False
        if current_board and state == "WAITING" and time.monotonic() - self._editor_started > 30:
            self.editor_status.set("Allegro：等待连接；请完成许可选择/打开板，再查看启动脚本")
        elif current_board and session.sequence > (sequence or 0) and time.monotonic() - session.request_time > 8:
            self.editor_status.set("Allegro：定位待响应，请结束 Editor 当前交互命令")
        self._schedule_editor_poll()

    def _refresh_component_coordinates(self, auto_analyze=False, select_tab=False):
        from src.pick import build_inventory
        from src.pcb_locations import component_coordinates
        from src.net_locate import parse_traces_csv
        path = self.brd_path.get().strip()
        directory = self.coordinate_dir.get().strip()
        unit = (self.brd_info or {}).get("units", "")
        self._coordinate_load_token += 1
        token = self._coordinate_load_token

        def work():
            search = [directory] if directory else [os.path.join(os.path.dirname(os.path.abspath(path)), "定位清单")]
            inventory = build_inventory("" if directory else path, extra_dirs=search)
            coordinates = component_coordinates(inventory, unit)
            trace = parse_traces_csv(os.path.join(directory, "evan_traces.csv")) if directory else {}
            return coordinates, bool(inventory.get("holes") or trace.get("items"))

        def done(payload):
            if (token != self._coordinate_load_token or path != self.brd_path.get().strip()
                    or directory != self.coordinate_dir.get().strip()):
                return
            coordinates, has_geometry = payload
            self._component_coordinates = coordinates
            self._component_brd_path = os.path.normcase(os.path.abspath(path))
            self.results.show_components(coordinates, "" if coordinates else "未读到器件放置原点；请点「打开 BRD」提取")
            if select_tab:
                self.results.nb.select(2)
            if auto_analyze:
                self.editor_status.set(f"Allegro：已提取 {len(coordinates)} 个器件 X/Y" if coordinates else
                                       "Allegro：没有器件原点，请检查 evan_pick.log")
                if (coordinates or has_geometry) and not self._loading and "pcb-analysis" not in self.tasks.pending:
                    self._run()

        self.tasks.submit("component-coordinates-" + str(token), work, done,
                          lambda error: self.editor_status.set("器件坐标读取失败：" + error))

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
                    self._refresh_component_coordinates()
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
        if self._checklist_window and self._checklist_window.root.winfo_exists():
            if self._checklist_window.tasks.pending:
                messagebox.showinfo("正在生成报告", "请等待 Checklist 报告生成完成后再关闭。", parent=self.win)
                return
            self._checklist_window._close()
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
        self.tasks.close()
        self._cancel_poll()

    def _cancel_poll(self):
        if self._ui_poll_id is not None:
            try:
                self.win.after_cancel(self._ui_poll_id)
            except tk.TclError:
                pass
            self._ui_poll_id = None
        if self._editor_poll_id is not None:
            try:
                self.win.after_cancel(self._editor_poll_id)
            except tk.TclError:
                pass
            self._editor_poll_id = None
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
        coordinate_dir = self.coordinate_dir.get().strip()
        design_unit = (self.brd_info or {}).get("units", "")
        gerber_dir = self.gerber_dir.get().strip()
        auto_gerber = self.auto_gerber.get()
        editor_path = self.editor_path.get()

        def compute():
            from src.pcb_locations import attach_locations
            result = analyze_design(design, **options)
            result["analyzed_at"] = datetime.now().isoformat(timespec="seconds")
            result["input_brd"] = brd_path
            result = attach_locations(result, brd_path, design_unit=design_unit,
                                      extra_dirs=[coordinate_dir] if coordinate_dir else [])
            if (auto_gerber and brd_path) or gerber_dir:
                from src.gerber_export import export_brd_gerbers
                from src.gerber_analysis import analyze_gerbers, attach_gerber_result, write_preview
                folder = gerber_dir
                if auto_gerber and brd_path:
                    bundle = export_brd_gerbers(brd_path,
                        os.path.join(os.path.dirname(os.path.abspath(brd_path)), "Gerber_DFM"), editor_path)
                    folder = bundle["directory"]
                    result["gerber_export_warnings"] = bundle["warnings"]
                geometry = analyze_gerbers(folder, options["factory"], options["board_type"], options["capability_level"],
                                          copper_oz=design.get("copper_oz", 1.0))
                result = attach_gerber_result(result, geometry, design_unit)
                result["gerber_preview"] = write_preview(geometry, os.path.join(folder, "DFM_Gerber_preview.html"))
            return result

        def complete(result):
            if revision != self._input_revision:
                self._pcb_state = "idle"
                self.status_var.set("PCB 分析期间输入已变化，已丢弃过期结果，请重新分析")
                return
            self._render_pcb(result, design)
            self._pcb_state = "complete"

        def failed(error):
            if revision == self._input_revision:
                self._pcb_state = "error"
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
        if kind == "pcb" and not self.output_pcb_report.get():
            return
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
        board_meta["hide_low_priority"] = self.hide_low_priority.get()
        labels = {"dfm": "DFM综合报告", "pcb": "PCB报告", "smt": "SMT报告"}
        path = filedialog.asksaveasfilename(
            parent=self.win, title="导出 " + labels[kind],
            defaultextension=self.report_format.get(),
            initialfile=labels[kind] + self.report_format.get(),
            filetypes=[("Excel 工作簿", "*.xlsx"), ("CSV 表格 (UTF-8)", "*.csv")],
        )
        if not path:
            return
        def done(paths):
            messagebox.showinfo("导出完成", "\n".join(paths), parent=self.win)

        self.tasks.submit("report-export",
                          lambda: export_report(kind, path, pcb, smt, board_meta), done,
                          lambda error: messagebox.showerror("导出失败", error, parent=self.win))

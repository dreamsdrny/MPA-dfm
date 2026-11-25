"""Unified PCB/SMT workbench with snapshot-based background analysis and reports."""
from copy import deepcopy
import os
import queue
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from src.application.board_inputs import extract_brd_design
from src.application.pcb_analysis import PCBAnalysisRequest, import_brd, run_pcb_analysis
from src.application.reports import REPORT_LABELS, default_report_name, export_report
from src.ui.dfm_params import ParamPanel
from src.ui.dfm_results import ResultPanel
from src.ui.theme import (COLORS, Card, apply_app_icon, apply_theme, enable_dpi_awareness, fonts,
                          set_color_mode, refresh_widget_colors)
from src.ui import i18n
from src.ui.tasks import TaskRunner
from src.application.cad_tools import AllegroSession, discover_editor, arrange_editor, point_mm
from src.ui.responsive import scroll_workbench, FlowBar, wrap_label
from src.application.board_inputs import (brd_signature, canonical_brd_path,
                              require_brd_signature, validate_brd_info)


class DFMDialog:
    """设计参数 -> 板厂制程能力比对 (支持红版/四会)。

    未成功导入当前版本 BRD 时不允许 PCB/SMT 分析；路径文字不等于已导入。
    BRD 离线提取约束、层与部分快照；孔径/孔环名称推算只作为提示。
    实际制造几何由独立应用服务编排原生导出或同版本外部资料，
    不把板级约束、未知孔型或未确认工艺当成实测制造能力输入。
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
        self._manufacturing_stage_queue = queue.Queue()
        self._poll_id = None
        self._alive = True
        self._load_token = 0
        self._loading = False
        self._loaded_path = ""
        self._loaded_signature = None
        self._brd_error = ""
        self._changing_board = False
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

        self.status_var = tk.StringVar(value="就绪：导入 BRD 或 Gerber，确认能力设置后执行 PCB 分析")
        self.board_state_var = tk.StringVar(value="PCB 统一工作台 · BRD / Gerber 共用输入、参数与结果")
        self.coordinate_dir = tk.StringVar()
        self.report_format = tk.StringVar(value=".xlsx")
        self.language = tk.StringVar(value="English" if i18n.LANGUAGE == "en" else "中文")
        self.color_mode = tk.StringVar(value="Dark" if prefs.get("ui_theme") == "dark" else "Light")
        self.hide_low_priority = tk.BooleanVar(value=False)
        self.output_pcb_report = tk.BooleanVar(value=True)
        self.gerber_dir = tk.StringVar()
        self.geometry_data = tk.StringVar()
        self.odb_path = tk.StringVar()
        self.odb_step = tk.StringVar()
        self.auto_gerber = tk.BooleanVar(value=True)
        self.auto_manufacturing = tk.BooleanVar(value=True)
        from src.application.cad_tools import discover_odb_exporter
        self.odb_exporter = tk.StringVar(value=discover_odb_exporter(prefs.get("odb_exporter", "")))
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

        # Gerber-first startup must not silently restore a historical BRD and switch pages.
        if brd_path:
            self.params.brd_path.set(brd_path)

        self._poll_id = self.win.after(120, self._poll_brd_queue)
        if self.brd_path.get().strip():
            self.params._set_brd_status("正在读取 BRD ...")
            self._load_from_brd()
        else:
            self.params._set_brd_status("BRD 可单独导入并自动导出制造资料；Gerber 可直接分析，无需 Cadence。")
        self._tasks_changed(tuple(self.tasks.pending))
        # Finish initial DPI/reflow geometry before the first background action.
        self.win.update_idletasks()

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
        from src.application.version import BUILD_ID
        tk.Label(hdr, text=BUILD_ID, bg=COLORS["header"], fg=COLORS["header_muted"],
                 font=self._fonts["subtitle"]).pack(side=tk.RIGHT, padx=18)

        btns = FlowBar(self.content, padding=(12, 0))
        btns.pack(fill=tk.X)
        self.import_gerber_button = ttk.Button(btns, text="导入 PCB 资料（BRD / Gerber / 压缩包）", style="Primary.TButton",
                                               command=self._import_pcb_file)
        btns.add(self.import_gerber_button)
        self.import_gerber_folder_button = ttk.Button(btns, text="导入 Gerber 目录", command=self._import_gerber_folder)
        btns.add(self.import_gerber_folder_button)
        self.run_button = ttk.Button(btns, text="执行 PCB 分析", style="Primary.TButton",
                                      command=self._run)
        btns.add(self.run_button)
        btns.add(ttk.Button(btns, text="CAD / 导出设置", command=self._toggle_advanced))
        btns.add(ttk.Button(btns, text="关闭", style="Secondary.TButton", command=self._close))
        workflow = ttk.Frame(self.content, padding=(16, 6))
        workflow.pack(fill=tk.X)
        self.board_state_label = ttk.Label(workflow, textvariable=self.board_state_var,
                                           foreground=COLORS["warn"])
        self.board_state_label.pack(fill=tk.X)
        wrap_label(self.board_state_label, workflow)
        ttk.Label(workflow, text="① 导入 BRD / Gerber  →  ② 确认板厂能力 / 铜厚 / 叠构  →  ③ 执行 PCB 分析、预览及导出报告",
                  style="CardHint.TLabel").pack(anchor=tk.W)
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
        self.smt.check_source_changes()
        if self.last_result and not self._gerber_active():
            self.results.render_capability(self.last_result)
        if self.gerber_panel.last_result:
            self.gerber_panel.results.render_capability(self.gerber_panel.last_result)
        if self.smt.last_result:
            self.smt._render_result(self.smt.last_result, self.smt._revision)
        self._save_ui_preferences()

    def _apply_color_mode(self):
        old = set_color_mode("dark" if self.color_mode.get() == "Dark" else "light")
        refresh_widget_colors(self.win, old)
        self.params._render_fields()
        self.results.refresh_colors()
        self.gerber_panel.results.refresh_colors()
        self.smt.detail.configure(bg=COLORS["card"], fg=COLORS["text"], insertbackground=COLORS["text"])
        self._tasks_changed(tuple(self.tasks.pending))
        i18n.localize_widgets(self.win)
        self._save_ui_preferences()

    def _refresh_ui_labels(self):
        self._ui_poll_id = None
        if not self._alive:
            return
        while True:
            try:
                revision, stage = self._manufacturing_stage_queue.get_nowait()
            except queue.Empty:
                break
            if revision == self._input_revision and "pcb-analysis" in self.tasks.pending:
                labels = {"gerber": "原生导出 Gerber", "drill": "原生导出 NC Drill", "route": "原生导出 NC Route 槽孔",
                          "odb": "原生导出 ODB++", "gerber_analysis": "检查 Gerber 与钻孔几何", "odb_analysis": "检查 ODB++ 位号/网络对象"}
                self.status_var.set("BRD 一键制造分析：" + labels.get(stage, stage) + "…")
        if self._loaded_signature:
            try:
                require_brd_signature(self.brd_path.get().strip(), self._loaded_signature)
            except (ValueError, OSError) as exc:
                self._clear_imported_board(str(exc))
        i18n.localize_widgets(self.win)
        self.smt.check_source_changes()
        if self.language.get() == "English":
            self.win.title("Evan MPA-DFM - PCB / SMT Manufacturing Analysis")
        else:
            self.win.title("Evan MPA-DFM - PCB / SMT 制造能力分析")
        self._ui_poll_id = self.win.after(600, self._refresh_ui_labels)

    def _build_report_bar(self):
        bar = FlowBar(self.content, padding=(12, 4))
        bar.pack(fill=tk.X)
        bar.add(ttk.Label(bar, text="报告导出", style="FieldLabel.TLabel"))
        bar.add(ttk.Combobox(bar, textvariable=self.report_format, values=(".xlsx", ".csv"),state="readonly", width=7))
        self.report_buttons = {}
        for kind, label in [("dfm", "DFM 综合报告"), ("pcb", "PCB 报告"), ("smt", "SMT 报告")]:
            button = ttk.Button(bar, text=label,
                                style="Primary.TButton" if kind == "dfm" else "Secondary.TButton",
                                command=lambda k=kind: self._export(k))
            bar.add(button)
            self.report_buttons[kind] = button
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=130,
                                        style="Idle.Horizontal.TProgressbar")
        bar.add(self.progress)
        self.task_var = tk.StringVar(value="就绪")
        self.task_label = ttk.Label(bar, textvariable=self.task_var, foreground=COLORS["muted"])
        bar.add(self.task_label)
        wrap_label(self.task_label, bar)
        self.task_dot = tk.Canvas(bar, width=16, height=16, bg=COLORS["bg"],
                                  highlightthickness=0)
        bar.add(self.task_dot)
        self._dot = self.task_dot.create_oval(3, 3, 13, 13, fill=COLORS["faint"], outline="")
        options = ttk.Frame(self.content, padding=(16, 0))
        options.pack(fill=tk.X)
        ttk.Checkbutton(options, text="报告隐藏低优先级", variable=self.hide_low_priority).pack(side=tk.LEFT)
        self.preview_report_button = ttk.Button(options,text="Excel 报表预览",command=self._preview_report)
        self.preview_report_button.pack(side=tk.LEFT,padx=8)

    def _preview_report(self):
        """Open the exact exported workbook in Excel, not a different HTML layout."""
        if self._gerber_active():
            self.gerber_panel.export(preview=True, hide_low_priority=self.hide_low_priority.get())
            return
        import tempfile
        if not self._require_imported_board():
            return
        if not self.last_result:
            messagebox.showinfo("报告", "请先完成当前输入的 PCB 分析",parent=self.win)
            return
        self.smt.check_source_changes()
        pcb=deepcopy(self.last_result)
        smt=deepcopy(self.smt.last_result)
        kind="dfm" if smt else "pcb"
        folder=tempfile.mkdtemp(prefix="evan-report-preview-")
        stem=os.path.splitext(os.path.basename(self.brd_path.get().strip()))[0] or "board"
        path=os.path.join(folder,f"{stem}_报告预览.xlsx")
        meta=dict(board_name=self.last_design.get("board_name",""),hide_low_priority=self.hide_low_priority.get())
        revision = self._input_revision
        def done(_):
            if revision == self._input_revision:
                os.startfile(path)
            else:
                self.status_var.set("预览生成期间输入已变化，已丢弃旧预览。")
        self.tasks.submit("report-preview",lambda:export_report(kind,path,pcb,smt,meta),
            done,lambda error:messagebox.showerror("报告预览",error,parent=self.win))

    def _build_coordinate_bar(self):
        primary = FlowBar(self.content, padding=(12, 2))
        primary.pack(fill=tk.X)
        self.gerber_view_button = ttk.Button(primary, text="Gerber 问题预览", command=self._preview_gerber)
        primary.add(self.gerber_view_button)
        primary.add(ttk.Button(primary, text="Gerber/ODB++ ↔ BOM 协同", command=self._open_manufacturing))
        self.advanced_frame = ttk.Frame(self.content)
        self._advanced_visible = False
        native = FlowBar(self.advanced_frame, padding=(12, 2))
        native.pack(fill=tk.X)
        self.import_brd_button = self.import_gerber_button
        self.open_brd_button = ttk.Button(native, text="打开 BRD", command=self._open_brd_editor)
        native.add(self.open_brd_button)
        native.add(ttk.Button(native, text="网络定位", command=self._open_net_locate))
        self.trace_filter_button = ttk.Button(native, text="阻抗 / 走线原生高亮", command=self._open_trace_filter)
        native.add(self.trace_filter_button)
        native.add(ttk.Checkbutton(native, text="BRD 一键导出并分析 Gerber / NC Drill / NC Route / ODB++",
                                  variable=self.auto_manufacturing))
        native.add(ttk.Entry(native, textvariable=self.odb_exporter, width=30))
        native.add(ttk.Button(native, text="设置 ODB++ 导出器", command=self._configure_odb_exporter))
        gerber = FlowBar(self.advanced_frame, padding=(12, 2))
        gerber.pack(fill=tk.X)
        gerber.add(ttk.Checkbutton(gerber, text="每次从 BRD 重新导出 Gerber", variable=self.auto_gerber))
        gerber.add(ttk.Entry(gerber, textvariable=self.gerber_dir, width=28))
        gerber.add(ttk.Button(gerber, text="选择已有 Gerber", command=self._browse_gerber))
        gerber.add(ttk.Button(gerber, text="Gerber/ODB++ ↔ BOM 协同", command=self._open_manufacturing))
        self.preview_gerber_button = ttk.Button(gerber, text="Gerber 问题预览", command=self._preview_gerber)
        gerber.add(self.preview_gerber_button)
        gerber.add(ttk.Button(gerber, text="ODB++ 问题预览", command=lambda: self._preview_gerber("odb_preview")))
        geometry = FlowBar(self.advanced_frame, padding=(12, 2))
        geometry.pack(fill=tk.X)
        geometry.add(ttk.Label(geometry, text="实际几何检查始终执行；无已有 Gerber 时自动导出"))
        geometry.add(ttk.Entry(geometry, textvariable=self.geometry_data, width=28))
        geometry.add(ttk.Button(geometry, text="补充几何资料 JSON", command=self._browse_geometry_data))
        geometry.add(ttk.Button(geometry, text="生成补充资料模板", command=self._create_geometry_template))
        odb = FlowBar(self.advanced_frame, padding=(12, 2))
        odb.pack(fill=tk.X)
        odb.add(ttk.Label(odb, text="对象 DFM（可选，同版 ODB++）"))
        odb.add(ttk.Entry(odb, textvariable=self.odb_path, width=32))
        odb.add(ttk.Button(odb, text="选择 ODB++ 对象资料", command=self._browse_odb))
        odb.add(ttk.Label(odb, text="step"))
        odb.add(ttk.Entry(odb, textvariable=self.odb_step, width=12))
        odb.add(ttk.Label(odb, text="逐焊盘/过孔/阻焊/锡膏；未验证 BRD 对齐不跳转"))
        bar = FlowBar(self.advanced_frame, padding=(12, 2))
        bar.pack(fill=tk.X)
        bar.add(ttk.Label(bar, text="问题坐标来源"))
        bar.add(ttk.Entry(bar, textvariable=self.coordinate_dir, width=28))
        bar.add(ttk.Button(bar, text="选择导出目录", command=self._browse_coordinates))
        bar.add(ttk.Button(bar, text="导入 COMP_XY", command=self._import_comp_xy))
        bar.add(ttk.Button(bar, text="生成 Allegro 坐标脚本", command=self._generate_coordinates))
        editor = FlowBar(self.advanced_frame, padding=(12, 2))
        editor.pack(fill=tk.X)
        editor.add(ttk.Button(editor, text="设置 PCB Editor", command=self._configure_editor))
        self.editor_export_button = ttk.Button(editor, text="打开 BRD 并提取坐标",
                                               command=self._open_export_editor)
        editor.add(self.editor_export_button)
        editor.add(ttk.Checkbutton(editor, text="选点同步到 Allegro", variable=self.sync_editor))
        editor.add(ttk.Checkbutton(editor, text="并排显示", variable=self.beside_editor))
        editor_label = ttk.Label(editor, textvariable=self.editor_status)
        editor.add(editor_label)
        wrap_label(editor_label, editor)

    def _toggle_advanced(self):
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self.advanced_frame.pack(fill=tk.X, before=self.workbench)
        else:
            self.advanced_frame.pack_forget()

    def _gerber_active(self):
        # Analysis routing follows imported data, never the selected notebook tab.
        return hasattr(self, "gerber_panel") and not self.brd_path.get().strip()

    def _import_pcb_file(self):
        path = filedialog.askopenfilename(parent=self.win, title="导入 PCB 资料：BRD / Gerber / 压缩包",
            filetypes=[("PCB 资料", "*.brd *.art *.gbr *.ger *.gtl *.gbl *.gts *.gbs *.gto *.gbo *.gko *.zip *.tgz *.gz"),
                       ("所有文件", "*.*")])
        if not path:
            return
        if path.lower().endswith(".brd"):
            self.brd_path.set(path)
            self._load_from_brd()
        else:
            self._load_gerber_source(path)

    def _load_gerber_source(self, path):
        self.workbench.select(self.brd_body)
        if self.brd_path.get().strip():
            # BRD + existing Gerber uses the BRD-bound workflow. Keep both sources.
            from pathlib import Path
            if not Path(path).is_dir():
                messagebox.showinfo("BRD 配套 Gerber", "BRD 已导入。请选择同版本 Gerber 目录；"
                    "若需独立分析文件/压缩包，请先点击「清空资料」。", parent=self.win)
                return
            self.auto_manufacturing.set(False)
            self.auto_gerber.set(False)
            self.gerber_dir.set(path)
            self.status_var.set("已选择 BRD 配套 Gerber，执行 PCB 分析将使用这两份资料。")
        else:
            self.gerber_panel.load(path)

    def _clear_pcb_inputs(self):
        self.brd_path.set("")
        self.gerber_panel.source.set("")
        self.params.reset_design()
        self.results.clear()
        self.status_var.set("资料已清空，请导入 BRD 或 Gerber")

    def _import_gerber_file(self):
        self._import_pcb_file()

    def _import_gerber_folder(self):
        path = filedialog.askdirectory(parent=self.win, title="导入 Gerber 目录（单独分析或补充当前 BRD）")
        if path:
            self._load_gerber_source(path)

    def _gerber_state_changed(self):
        if hasattr(self, "smt"):
            if self._gerber_active():
                self.last_result = self.gerber_panel.last_result
                self._pcb_state = self.gerber_panel.state
                self.status_var.set(self.gerber_panel.status.get())
            self._refresh_action_states()

    def _open_manufacturing(self):
        from src.ui.manufacturing_dialog import open_manufacturing
        open_manufacturing(self.win)

    def _browse_coordinates(self):
        path = filedialog.askdirectory(parent=self.win, title="选择当前 BRD 的 SKILL / NC Drill 导出目录")
        if path:
            self.coordinate_dir.set(path)
            self._refresh_component_coordinates(select_tab=True)

    def _browse_gerber(self):
        path = filedialog.askdirectory(parent=self.win, title="选择同一版本的一套 Gerber 目录")
        if path:
            self.auto_gerber.set(False)
            self.auto_manufacturing.set(False)
            self.gerber_dir.set(path)

    def _browse_geometry_data(self):
        path = filedialog.askopenfilename(parent=self.win, title="选择当前 BRD 的 PCB 几何补充资料",
                                         filetypes=[("PCB 几何资料", "*.json")])
        if path:
            self.geometry_data.set(path)

    def _browse_odb(self):
        path = filedialog.askopenfilename(parent=self.win, title="选择与当前 BRD 同版的 ODB++",
                                          filetypes=[("ODB++", "*.tgz *.tar.gz *.tar *.zip"), ("所有文件", "*.*")])
        if path:
            self.auto_manufacturing.set(False)
            self.odb_path.set(path)

    def _configure_odb_exporter(self):
        path = filedialog.askopenfilename(parent=self.win, title="选择 ODB++ Inside 的 brd2odb.exe",
                                          filetypes=[("ODB++ Inside", "brd2odb.exe"), ("程序", "*.exe")])
        if path:
            self.odb_exporter.set(path)
            from src.ui.prefs import load_prefs, save_prefs
            prefs = load_prefs()
            prefs["odb_exporter"] = path
            save_prefs(prefs)

    def _create_geometry_template(self):
        if not self._require_imported_board():
            return
        stem = os.path.splitext(os.path.basename(self.brd_path.get().strip()))[0] or "board"
        path = filedialog.asksaveasfilename(parent=self.win, title="保存当前 BRD 的几何补充资料模板",
                                           defaultextension=".json", initialfile=f"{stem}_geometry_data.json",
                                           filetypes=[("PCB 几何资料", "*.json")])
        if not path:
            return
        from src.application.reports import write_geometry_template
        try:
            write_geometry_template(self.brd_path.get().strip(),
                                    self.coordinate_dir.get().strip() or self.gerber_dir.get().strip(), path)
        except (ValueError, OSError) as exc:
            messagebox.showerror("补充资料模板", str(exc), parent=self.win)
            return
        self.geometry_data.set(path)
        messagebox.showinfo("补充资料模板", "模板已绑定当前 BRD 版本。请用文本编辑器填写实际 NC Drill 目录、"
                            "孔型/跨度/对齐、逐焊盘资料和板厂能力；空白项不会判为通过。", parent=self.win)

    def _export_gerber_only(self):
        from src.application.cad_tools import export_brd_gerbers
        if not self._require_imported_board():
            return
        path = self.brd_path.get().strip()
        if not os.path.isfile(path):
            messagebox.showwarning("Gerber", "请先选择 BRD", parent=self.win)
            return
        editor = self.editor_path.get()
        output = os.path.join(os.path.dirname(os.path.abspath(path)), "Gerber_DFM")
        signature = self._loaded_signature
        def done(bundle):
            if self._loaded_signature != signature or self._brd_block_reason():
                self.status_var.set("BRD 已变化，已丢弃过期 Gerber 导出结果")
                return
            self._exported_gerber = bundle
            self.auto_gerber.set(False)
            self.gerber_dir.set(bundle["directory"])
            self.status_var.set(f"Gerber 已导出：{len(bundle['files'])} 层；点击执行分析")
        self.tasks.submit("gerber-export", lambda: export_brd_gerbers(path, output, editor), done,
                          lambda error: messagebox.showerror("Gerber 导出失败", error, parent=self.win))

    def _preview_gerber(self, key="gerber_preview"):
        if self._gerber_active():
            self.gerber_panel.preview()
            return
        path = (self.last_result or {}).get(key)
        if path and os.path.isfile(path):
            import webbrowser
            from pathlib import Path
            webbrowser.open(Path(path).as_uri())
        else:
            messagebox.showinfo("Gerber", "请先完成 Gerber 分析，再打开问题预览", parent=self.win)

    def _import_comp_xy(self):
        if not self._require_imported_board():
            return
        from src.application.locations import import_component_coordinates
        path = filedialog.askopenfilename(parent=self.win, title="选择 BRD 导出的 COMP_XY 坐标文件",
                                         filetypes=[("COMP_XY 坐标", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            coordinates = import_component_coordinates(path, (self.brd_info or {}).get("units", ""))
        except (ValueError, OSError) as exc:
            messagebox.showerror("坐标导入", str(exc), parent=self.win)
            return
        self.coordinate_dir.set(os.path.dirname(path))
        self._component_coordinates = coordinates
        self._component_brd_path = os.path.normcase(os.path.abspath(self.brd_path.get().strip()))
        self.results.show_components(coordinates, "COMP_XY 器件原点；丝印需另外的图形导出")
        self.results.nb.select(2)
        self.editor_status.set(f"已导入 COMP_XY：{len(coordinates)} 个器件坐标")
        if self.brd_path.get().strip() and not self._loading and "pcb-analysis" not in self.tasks.pending:
            self._run()

    def _generate_coordinates(self):
        from src.application.cad_tools import write_bundle
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

    def _brd_block_reason(self):
        path = self.brd_path.get().strip()
        if self._loading:
            return "BRD 正在读取，请等待完成"
        if not path:
            return "请先导入 BRD 文件，成功读取后才能分析"
        if self._brd_error:
            return self._brd_error
        if not self.brd_info or canonical_brd_path(path) != canonical_brd_path(self._loaded_path):
            return "BRD 尚未成功读取，请点击「从 BRD 读取」"
        try:
            require_brd_signature(path, self._loaded_signature)
        except (ValueError, OSError) as exc:
            return str(exc)
        return ""

    def _require_imported_board(self):
        reason = self._brd_block_reason()
        if not reason:
            return True
        if self._loaded_signature:
            self._clear_imported_board(reason)
        self._tasks_changed(tuple(self.tasks.pending))
        messagebox.showwarning("BRD 未就绪", reason, parent=self.win)
        return False

    def _refresh_action_states(self):
        """按钮状态统一由当前 BRD、任务和结果决定，完成回调不能强制重新启用。"""
        pending = self.tasks.pending
        reason = self._brd_block_reason()
        ready = not reason
        self.run_button.configure(state=tk.NORMAL if ready and not any(
            key in pending for key in ("pcb-analysis", "gerber-export")) else tk.DISABLED)
        gerber_active = self._gerber_active()
        importing = any(key in pending for key in ("gerber-import", "gerber-analysis"))
        self.import_gerber_button.configure(state=tk.DISABLED if importing else tk.NORMAL)
        self.import_gerber_folder_button.configure(state=tk.DISABLED if importing else tk.NORMAL)
        self.run_button.configure(text="执行 PCB 分析",
                                  state=tk.NORMAL if (self.gerber_panel.ready if gerber_active else ready and
                                      not any(key in pending for key in ("pcb-analysis", "gerber-export"))) else tk.DISABLED)
        available_reports = {
            "dfm": ready and self.last_result and self.smt.last_result,
            "pcb": ready and self.last_result and self.output_pcb_report.get(),
            "smt": ready and self.smt.last_result,
        }
        if gerber_active:
            available_reports = {"pcb": bool(self.gerber_panel.last_result), "dfm": False, "smt": False}
        for kind, button in self.report_buttons.items():
            button._flow_hidden = False
            button.configure(text={"pcb": "PCB 报告", "dfm": "DFM 综合报告", "smt": "SMT 报告"}[kind],
                             state=tk.NORMAL if available_reports[kind] and "report-export" not in pending else tk.DISABLED)
            button.grid()
        self.preview_report_button.configure(state=tk.NORMAL if ready and self.last_result and
            "report-preview" not in pending else tk.DISABLED)
        self.preview_gerber_button.configure(state=tk.NORMAL if ready and
             (self.last_result or {}).get("gerber_preview") else tk.DISABLED)
        gerber_result = self.gerber_panel.last_result if gerber_active else self.last_result
        self.gerber_view_button.configure(state=tk.NORMAL if (gerber_result or {}).get("gerber_preview") else tk.DISABLED)
        if gerber_active:
            self.preview_report_button.configure(state=tk.NORMAL if gerber_result and
                "report-preview" not in pending else tk.DISABLED)
        self.params.set_board_available(ready or (gerber_active and bool(self.gerber_panel.info)))
        self.results.set_input_mode(gerber_active)
        self.smt.refresh_availability()
        self.board_state_label.configure(foreground=COLORS["success"] if ready else COLORS["warn"])
        self.board_state_var.set((f"BRD 已导入 · {os.path.basename(self._loaded_path)} · 可执行分析"
                                  if ready else f"分析已禁用 · {reason}"))
        if gerber_active:
            self.board_state_var.set(f"Gerber 已导入 · {os.path.basename(self.gerber_panel.source.get())} · 可执行 PCB 分析"
                                     if self.gerber_panel.info else "请导入 BRD 或 Gerber · 共用下方参数和结果")
            self.board_state_label.configure(foreground=COLORS["success"] if self.gerber_panel.info else COLORS["muted"])
        i18n.localize_widgets(self.win)

    def _tasks_changed(self, pending):
        self._refresh_action_states()
        if pending or self._loading:
            names = {"pcb-analysis": "PCB 分析", "smt-analysis": "SMT 分析", "gerber-import": "Gerber 读取", "gerber-analysis": "Gerber 分析",
                      "report-export": "报告导出"}
            labels = (["读取 BRD"] if self._loading else []) + [names.get(p, p) for p in pending]
            self.task_var.set("处理中 · " + " / ".join(labels))
            self.task_label.configure(foreground=COLORS["primary"])
            self.task_dot.itemconfigure(self._dot, fill=COLORS["primary"])
            self.progress.configure(mode="indeterminate", style="Running.Horizontal.TProgressbar")
            self.progress.start(15)
        else:
            self.progress.stop()
            color = COLORS["primary"] if self._pcb_state == "complete" else (
                COLORS["danger"] if self._pcb_state == "error" else COLORS["faint"])
            self.task_var.set({"complete": "PCB 分析完成", "error": "PCB 分析失败"}.get(
                self._pcb_state, "等待导入 BRD" if self._brd_block_reason() else "就绪"))
            self.task_label.configure(foreground=color)
            self.task_dot.itemconfigure(self._dot, fill=color)
            self.progress.configure(mode="determinate", value=100 if self._pcb_state == "complete" else 0,
                                    style="Complete.Horizontal.TProgressbar" if self._pcb_state == "complete"
                                     else "Idle.Horizontal.TProgressbar")
            if self._gerber_active():
                self.task_var.set({"complete": "Gerber 分析完成", "error": "Gerber 操作失败"}.get(
                    self.gerber_panel.state, "就绪" if self.gerber_panel.info else "等待导入 Gerber"))

    def _build_body(self):
        self.workbench = ttk.Notebook(self.content)
        self.workbench.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        body = ttk.Frame(self.workbench, style="TFrame", padding=(16, 14, 16, 12))
        self.brd_body = body
        self.workbench.add(body, text="PCB 分析 · BRD / Gerber")
        body.columnconfigure(0, weight=1, minsize=350)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left_card = Card(body, "PCB 资料与能力设置", "BRD / Gerber 共用参数；缺数据不自动判为通过")
        left_card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        right_card = Card(body, "分析结果", "")
        right_card.grid(row=0, column=1, sticky="nsew")

        self.params = ParamPanel(left_card.content, self._browse_brd, self._load_from_brd,
                                  font_bold=self._fonts["ui_bold"], unified=True)
        inputs = ttk.Frame(left_card.content, style="Card.TFrame")
        inputs.pack(fill=tk.X)
        ttk.Label(inputs, text="BRD 文件（可选）", style="FieldLabel.TLabel").pack(anchor=tk.W)
        brd_row = FlowBar(inputs, style="Card.TFrame")
        brd_row.pack(fill=tk.X)
        brd_row.add(ttk.Entry(brd_row, textvariable=self.params.brd_path, width=35))
        brd_row.add(ttk.Button(brd_row, text="导入 BRD", command=self._browse_brd))
        brd_row.add(ttk.Button(brd_row, text="重新读取", command=self._load_from_brd))
        self.params.pack(fill=tk.BOTH, expand=True)

        self.results = ResultPanel(right_card.content,
                                    on_locate=self._open_locate,
                                    on_open_point=self._open_problem_point,
                                    on_point_selected=self._sync_problem_point)
        self.results.pack(fill=tk.BOTH, expand=True)
        from src.ui.gerber_panel import GerberPanel
        self.gerber_panel = GerberPanel(body, self.tasks, self._gerber_state_changed,
                                       shared_params=self.params, shared_results=self.results)
        self.geometry_data.trace_add("write", lambda *_:
                                     self.gerber_panel.geometry_data.set(self.geometry_data.get()))
        ttk.Label(inputs, text="Gerber 文件 / 目录 / 压缩包", style="FieldLabel.TLabel").pack(anchor=tk.W, pady=(8, 0))
        self.pcb_gerber_source = tk.StringVar()
        gerber_row = FlowBar(inputs, style="Card.TFrame")
        gerber_row.pack(fill=tk.X)
        gerber_row.add(ttk.Entry(gerber_row, textvariable=self.pcb_gerber_source, width=35))
        gerber_row.add(ttk.Button(gerber_row, text="读取 Gerber", command=lambda:
                                  self._load_gerber_source(self.pcb_gerber_source.get().strip())))
        gerber_row.add(ttk.Button(gerber_row, text="清空资料", command=self._clear_pcb_inputs))
        self.pcb_gerber_source.trace_add("write", self._gerber_path_edited)
        self.gerber_panel.source.trace_add("write", lambda *_:
                                         self.pcb_gerber_source.set(self.gerber_panel.source.get()))
        self.gerber_dir.trace_add("write", lambda *_:
                                 self.pcb_gerber_source.set(self.gerber_dir.get()) if self.brd_path.get().strip() else None)
        source_hint = ttk.Label(inputs, textvariable=self.gerber_panel.status, style="CardHint.TLabel", wraplength=350)
        source_hint.pack(fill=tk.X, pady=(4, 8))
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
                             on_export=lambda: self._export("smt"), on_locate=self._open_problem_point,
                             get_gerber=lambda: self.gerber_dir.get().strip(),
                             get_block_reason=self._brd_block_reason,
                             on_result_changed=self._refresh_action_states)
        self.workbench.add(self.smt, text="SMT 厂 · 工艺能力分析 / YAML")
        from src.ui.impedance_panel import ImpedancePanel
        self.impedance_panel = ImpedancePanel(self.workbench)
        self.workbench.add(self.impedance_panel, text="Allegro 阻抗 / 走线高亮")
        self.smt.detail.configure(bg=COLORS["card"], fg=COLORS["text"], insertbackground=COLORS["text"])
        for var in list(self.entries.values()) + [self.params.factory, self.params.level,
                    self.params.board_type, self.params.copper_um, self.params.surface_finish,
                    self.params.board_name, self.params.polarity_var, self.params.panelized_var,
                    self.coordinate_dir, self.gerber_dir, self.auto_gerber, self.geometry_data, self.odb_path, self.odb_step,
                    self.editor_path, self.auto_manufacturing, self.odb_exporter]:
            var.trace_add("write", self._invalidate_results)
        self.brd_path.trace_add("write", self._brd_path_changed)
        self.smt.load_builtin()
        self.workbench.bind("<<NotebookTabChanged>>", lambda _: self._tasks_changed(tuple(self.tasks.pending)))

    def _brd_path_changed(self, *_args):
        self.workbench.select(self.brd_body)
        self.gerber_panel.source.set("")
        self._clear_imported_board("")

    def _gerber_path_edited(self, *_):
        if self._gerber_active() and self.pcb_gerber_source.get().strip() != self.gerber_panel.source.get():
            self.gerber_panel.info = None
            self.gerber_panel.invalidate()
            self.status_var.set("Gerber 路径已变化，请点击读取 Gerber")

    def _clear_imported_board(self, reason):
        self._load_token += 1  # 旧读取线程的结果不能让新路径变为已导入
        self._loading = False
        self._loaded_path = ""
        self._loaded_signature = None
        self._brd_error = reason
        self.brd_info = None
        self._coordinate_load_token += 1
        self._component_brd_path = ""
        self._component_coordinates = []
        self._exported_gerber = None
        self._changing_board = True
        try:
            self.params.reset_design()
            self.coordinate_dir.set("")
            self.gerber_dir.set("")
            self.geometry_data.set("")
            self.odb_path.set("")
            self.odb_step.set("")
            self.smt.invalidate(clear_inputs=True)
        finally:
            self._changing_board = False
        self._invalidate_results()
        self._set_brd_status(reason or "请先选择 BRD 文件并点击「从 BRD 读取」")

    def _invalidate_results(self, *_args):
        if self._changing_board:
            return
        self._input_revision += 1
        self.last_result = None
        self.last_design = {}
        path = self.brd_path.get().strip()
        same_board = bool(self._component_brd_path) and os.path.normcase(os.path.abspath(path)) == self._component_brd_path
        self.results.clear(clear_components=not same_board)
        if not same_board:
            self._component_coordinates = []
        reason = self._brd_block_reason()
        self.result_var.set(reason or "输入已变化，请重新执行 PCB 分析")
        self.status_var.set(reason or "输入已变化，请重新执行 PCB 分析")
        self.smt.invalidate()
        self._pcb_state = "idle"
        if hasattr(self, "gerber_panel") and self._gerber_active():
            self.gerber_panel.invalidate()
        self._tasks_changed(tuple(self.tasks.pending))

    def _smt_design(self):
        reason = self._brd_block_reason()
        if reason:
            raise ValueError(reason)
        path = self.brd_path.get().strip()
        design = self.params.read_design()
        design["_input_brd"] = path
        design["_brd_signature"] = self._loaded_signature
        design["_brd_sha256"] = (self.brd_info or {}).get("_import_sha256")
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
        if self._gerber_active():
            self.gerber_panel.preview()
            return
        self._start_editor(point=point)

    def _sync_problem_point(self, point):
        if self._gerber_active():
            return
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
        if self._brd_block_reason():
            return
        from src.application.locations import refresh_component_coordinates
        path = self.brd_path.get().strip()
        directory = self.coordinate_dir.get().strip()
        unit = (self.brd_info or {}).get("units", "")
        self._coordinate_load_token += 1
        token = self._coordinate_load_token

        def work():
            return refresh_component_coordinates(path, directory, unit)

        def done(payload):
            if (token != self._coordinate_load_token or path != self.brd_path.get().strip()
                    or directory != self.coordinate_dir.get().strip() or self._brd_block_reason()):
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
        self._checklist_window.copper_um.set(self.params.copper_um.get())

    def _open_trace_filter(self):
        """Select the integrated native-plugin page, not a detached viewer."""
        self.workbench.select(self.impedance_panel)

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
        try:
            brd_signature(path)
        except (ValueError, OSError) as exc:
            self._clear_imported_board(str(exc))
            return
        self._clear_imported_board("")
        self._set_brd_status("正在读取 BRD ...")
        self._load_token += 1
        token = self._load_token
        self._loading = True
        self._loaded_path = ""
        self.brd_info = None
        self._tasks_changed(tuple(self.tasks.pending))
        self.status_var.set("BRD 正在读取，请等待完成")
        if self.standalone:
            try:
                from src.ui.prefs import load_prefs, save_prefs

                data = load_prefs()
                data["dfm_brd"] = path
                save_prefs(data)
            except Exception:  # noqa: BLE001  记不住不影响分析
                pass
        self.tasks.submit("brd-import", lambda: self._read_brd_worker(path, token), lambda _: None,
                          lambda error: self._brd_queue.put((token, path, "err", error)))

    def _read_brd_worker(self, path, token):
        """后台线程只做解析, 结果放队列; 所有 Tk 调用都回主线程 (见 _poll_brd_queue)。

        仅接受可解析的 BRD 约束对象；不把文本特征猜测当正式导入。
        """
        try:
            info = import_brd(path, reader=extract_brd_design)
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
                if path != self.brd_path.get().strip():
                    self._set_brd_status("路径已变化，请重新读取 BRD")
                    self._tasks_changed(tuple(self.tasks.pending))
                    continue
                if kind == "ok":
                    try:
                        validate_brd_info(payload)
                        require_brd_signature(path, payload.get("_import_signature"))
                        self.params.apply_brd(payload)
                    except (ValueError, TypeError, OSError) as exc:
                        self._clear_imported_board(f"读取 BRD 失败: {exc}")
                    else:
                        self.brd_info = payload
                        self._loaded_path = path
                        self._loaded_signature = payload["_import_signature"]
                        self._brd_error = ""
                        self.result_var.set("BRD 已导入，确认逐层叠构和板厂能力后执行分析")
                        self.status_var.set("BRD 已导入，确认逐层叠构和板厂能力后执行分析")
                        self._refresh_component_coordinates()
                else:
                    self._clear_imported_board(f"读取 BRD 失败: {payload}")
                self._tasks_changed(tuple(self.tasks.pending))
        except queue.Empty:
            pass
        except tk.TclError:
            return
        except Exception as exc:  # noqa: BLE001  读取回调出错也不能误放开分析/停止队列
            self._clear_imported_board(f"读取 BRD 失败: {exc}")
        try:
            if self._alive:
                self._poll_id = self.win.after(120, self._poll_brd_queue)
        except Exception:  # noqa: BLE001  窗口已关闭
            pass

    def _close(self):
        if self._loading or self.tasks.pending or self.impedance_panel.tasks.pending:
            messagebox.showinfo("后台任务进行中", "请等待读取、分析或报告导出完成后再关闭，避免中断输出。", parent=self.win)
            return
        if self._checklist_window and self._checklist_window.root.winfo_exists():
            if self._checklist_window.tasks.pending:
                messagebox.showinfo("正在生成报告", "请等待 Checklist 报告生成完成后再关闭。", parent=self.win)
                return
            self._checklist_window._close()
        self.impedance_panel.tasks.close()
        self._finish_close()

    def _finish_close(self):
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
        if self._gerber_active():
            self.gerber_panel.run()
            return
        if not self._require_imported_board():
            return
        if "pcb-analysis" in self.tasks.pending or "gerber-export" in self.tasks.pending:
            return
        try:
            design = self._smt_design()
        except ValueError as exc:
            messagebox.showwarning("参数错误", str(exc))
            return

        if self.brd_info:
            # 数据来源由 ParamPanel.read_design() 按字段给出 (_sources / _assumed)
            design["_warnings"] = self.brd_info.get("warnings", [])
            design.setdefault("_data_origin", "BRD+手工")
        if not design.get("_brd_sha256"):
            messagebox.showwarning("BRD 版本未验证", "缺少导入时的 BRD 内容哈希，请重新读取 BRD 后分析。", parent=self.win)
            return

        revision = self._input_revision
        design = deepcopy(design)
        options = dict(board_type=self.params.board_type.get(),
                       capability_level=self.params.level.get(), factory=self.params.factory.get())
        brd_path = self.brd_path.get().strip()
        coordinate_dir = self.coordinate_dir.get().strip()
        design_unit = (self.brd_info or {}).get("units", "")
        gerber_dir = self.gerber_dir.get().strip()
        geometry_data = self.geometry_data.get().strip()
        odb_path = self.odb_path.get().strip()
        odb_step = self.odb_step.get().strip() or None
        auto_gerber = self.auto_gerber.get()
        editor_path = self.editor_path.get()
        auto_manufacturing = self.auto_manufacturing.get()
        odb_exporter = self.odb_exporter.get().strip()

        request = PCBAnalysisRequest(
            brd_path=brd_path, design=deepcopy(design), **options, coordinate_dir=coordinate_dir,
            design_unit=design_unit, gerber_dir=gerber_dir, geometry_data=geometry_data,
            odb_path=odb_path, odb_step=odb_step, auto_gerber=auto_gerber,
            auto_manufacturing=auto_manufacturing, editor_path=editor_path, odb_exporter=odb_exporter,
            output_dir=self._pcb_output_dir())

        def compute():
            return run_pcb_analysis(request, progress=lambda stage:
                                    self._manufacturing_stage_queue.put((revision, stage)))

        def complete(result):
            if revision != self._input_revision or self._brd_block_reason():
                self._pcb_state = "idle"
                self.status_var.set("PCB 分析期间输入已变化，已丢弃过期结果，请重新分析")
                return
            self._render_pcb(result, design)
            self._pcb_state = "complete"

        def failed(error):
            if revision == self._input_revision:
                self._pcb_state = "error"
                self.status_var.set("PCB 分析失败：" + error)
                self.result_var.set("PCB 分析失败：" + error)
                from src.infrastructure.diagnostics import log_path
                messagebox.showerror("PCB 分析失败", error + "\n\n诊断日志：" + str(log_path()), parent=self.win)

        # A failed rerun must not leave an older analysis looking like its result.
        self.last_result = None
        self.last_design = {}
        self.results.clear(clear_components=False)
        self._pcb_state = "running"
        if self.tasks.submit("pcb-analysis", compute, complete, failed):
            self.status_var.set("正在后台执行 PCB 分析，界面可继续操作…")

    def _pcb_output_dir(self):
        from src.ui.paths import default_output_dir
        return os.path.join(default_output_dir(), "PCB_Manufacturing")

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
        # 唯一权威结论来自 verdict.finalize_verdict; 这里只做显示, 不再另行判定
        from src.application.reports import VERDICT_LABELS
        code = result.get("verdict_code") or (
            "fail" if not result["passed"] else "pass" if result.get("releasable") else "review")
        verdict = f"DFM {VERDICT_LABELS[code]}"
        reasons = result.get("verdict_reasons") or []
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
                   f"警告 {result['warning_count']}, 参数覆盖 {coverage.get('ratio', 0):.0%}, "
                  f"{src_txt}, 共 {len(result.get('issues', []))} 项问题){risk_txt}")
        if reasons:
            status += f" | 判定依据: {'；'.join(reasons)}"
        self.status_var.set(status)

        if not result.get("items"):
            messagebox.showinfo(
                "没有可判定的参数",
                "左侧没有已确认的数值 —— BRD 没读到, 也没手动填。\n"
                "请先「从 BRD 读取」, 或手工填入已知项 (填完即视为人工确认值)。")

        geometry_txt = ("实际几何已执行，覆盖缺口见页签" if result.get("geometry_executed")
                        else "未执行几何检查")
        self.results.result_var.set(
            f"{name_txt}{result['factory_label']} · {result['board_type_label']} · "
            f"{result['capability_label']}　|　{result.get('verdict', verdict)}"
            f"　|　参数覆盖 {coverage.get('ratio', 0):.0%}；{geometry_txt}"
            + (f"\n判定依据：{'；'.join(reasons)}" if reasons else "")
            )

    # ------------------------------------------------------------ 导出
    def _export(self, kind="pcb"):
        if self._gerber_active():
            if kind == "pcb":
                self.gerber_panel.export(self.report_format.get(), self.hide_low_priority.get())
            return
        if not self._require_imported_board():
            return
        self.smt.check_source_changes()
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
        # 文件名自动跟随 BRD 名: <板名>_PCB报告.xlsx 等; 用户仍可改名, 覆盖由对话框确认
        initialfile, initialdir = default_report_name(board_meta["brd_path"], kind, self.report_format.get())
        dialog_kwargs = dict(parent=self.win, title="导出 " + REPORT_LABELS[kind],
                             defaultextension=self.report_format.get(), initialfile=initialfile,
                             filetypes=[("Excel 工作簿", "*.xlsx"), ("CSV 表格 (UTF-8)", "*.csv")])
        if initialdir:
            dialog_kwargs["initialdir"] = initialdir
        path = filedialog.asksaveasfilename(**dialog_kwargs)
        if not path:
            return
        revision = self._input_revision
        expected_brd = board_meta["brd_path"]
        pcb_sig = (pcb or {}).get("input_brd_sha256")
        smt_sig = (smt or {}).get("input_brd_sha256")
        if kind in ("pcb", "dfm") and not pcb_sig:
            messagebox.showwarning("报告版本未验证", "PCB 结果缺少 BRD 内容哈希，请重新执行 PCB 分析。", parent=self.win)
            return
        if kind in ("smt", "dfm") and not smt_sig:
            messagebox.showwarning("报告版本未验证", "SMT 结果缺少 BRD 内容哈希，请重新执行 SMT 分析。", parent=self.win)
            return

        def generate():
            return export_report(kind, path, pcb if kind in ("pcb", "dfm") else None,
                                 smt if kind in ("smt", "dfm") else None, board_meta)

        def done(paths):
            changed = revision != self._input_revision or expected_brd != self.brd_path.get().strip()
            message = ("导出期间界面输入已变化；下列文件是开始导出时的快照，不代表当前输入：\n"
                       if changed else "") + "\n".join(paths)
            messagebox.showinfo("导出完成", message, parent=self.win)

        self.tasks.submit("report-export", generate, done,
                          lambda error: messagebox.showerror("导出失败", error, parent=self.win))

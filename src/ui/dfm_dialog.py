"""Unified PCB/SMT workbench with snapshot-based background analysis and reports."""
from copy import deepcopy
import os
import queue
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from src.application.board_inputs import extract_brd_design
from src.application.pcb_analysis import PCBAnalysisRequest, import_brd, run_pcb_analysis
from src.application.overall_analysis import combined_summary
from src.application.reports import export_report
from src.ui.dfm_params import ParamPanel
from src.ui.dfm_results import ResultPanel
from src.ui.theme import (COLORS, Card, apply_app_icon, apply_theme, enable_dpi_awareness, fonts,
                          set_color_mode, refresh_widget_colors)
from src.ui import i18n
from src.ui.tasks import TaskRunner
from src.application.cad_tools import AllegroSession, discover_editor, arrange_editor, point_mm
from src.ui.responsive import FlowBar, wrap_label
from src.ui.progress_matrix import ProgressMatrix
from src.application.board_inputs import (brd_signature, canonical_brd_path,
                              require_brd_signature, validate_brd_info)


class DFMDialog:
    """设计参数 -> 板厂制程能力比对 (支持红版/四会)。

    PCB 使用已导入 BRD 或 Gerber；SMT 仍要求当前 BRD；路径文字不等于已导入。
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
        self._overall_state = "idle"
        self._overall_error = ""
        self.progress_labels = {"pcb": "PCB 分析", "overall": "整体分析 · PCB + SMT", "gerber": "独立 Gerber 分析"}
        self.progress_step_labels = {
            "extracta": "后台 extracta 提取器件坐标与叠构（不打开 Editor）", "gerber": "原生导出 Gerber",
            "drill": "原生导出 NC Drill", "route": "原生导出 NC Route 槽孔", "odb": "原生导出 ODB++",
            "gerber_read": "读取 Gerber 图层", "gerber_analysis": "检查 Gerber 与钻孔几何",
            "odb_analysis": "检查 ODB++ 位号 / 网络对象", "verdict": "能力比对与结论",
            "smt_sources": "SMT 资料关联（PCB 导出 / BOM / Gerber）", "smt_rules": "SMT 供应商能力比较",
            "smt_geometry": "SMT 对象几何检测", "smt_assembly": "装配补充检查与缺口"}
        self._editor_session = None
        self._editor_poll_id = None
        self._editor_last_ack = None
        self._editor_started = 0
        self._editor_export_pending = False
        self._editor_analysis_request = None
        self._editor_arranged = False
        self._editor_layout_requested = False
        self._component_coordinates = []
        self._applied_extraction = None
        self._component_brd_path = ""
        self._coordinate_load_token = 0

        enable_dpi_awareness()
        self.standalone = standalone
        self.win = master if standalone else tk.Toplevel(master)
        self._show_when_ready = self.win.state() != "withdrawn"
        self.win.withdraw()
        set_color_mode(prefs.get("ui_theme", "light"))
        i18n.LANGUAGE = prefs.get("ui_language", "zh")
        self.win.title("Evan MPA-DFM - PCB / SMT 制造能力分析")
        width = min(1440, max(960, self.win.winfo_screenwidth() - 48))
        height = min(920, max(640, self.win.winfo_screenheight() - 80))
        self.win.geometry(f"{width}x{height}")
        self.win.minsize(min(960, width), min(640, height))
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        self.win.bind("<Destroy>", self._on_destroy, add="+")
        self.win.configure(bg=COLORS["bg"])
        self._fonts = fonts(self.win)
        apply_theme(self.win)
        apply_app_icon(self.win)

        self.status_var = tk.StringVar(value="就绪：导入资料 → 选择 PCB / SMT 能力 → 整体分析 → 评审报告")
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

        self.status_detail_var = tk.StringVar()
        self.status_var.trace_add("write", self._update_status_display)
        status_label = ttk.Label(self.win, textvariable=self.status_detail_var, style="Status.TLabel", anchor=tk.W)
        status_label.pack(fill=tk.X, side=tk.BOTTOM)
        self.content = ttk.Frame(self.win)
        self.content.pack(fill=tk.BOTH, expand=True)

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
        if self._show_when_ready:
            self.win.deiconify()

    def _update_status_display(self, *_):
        text = self.status_var.get().replace("\n", " ")
        shown = text if len(text) <= 110 else text[:107] + "…"
        if self.status_detail_var.get() != shown:
            self.status_detail_var.set(shown)

    # ------------------------------------------------------------ 组装
    def _build_header(self):
        hdr = tk.Frame(self.content, bg=COLORS["header"])
        hdr.pack(fill=tk.X)

        title_box = tk.Frame(hdr, bg=COLORS["header"])
        title_box.pack(side=tk.LEFT, padx=16, pady=8)
        tk.Label(title_box, text="Evan · MPA-DFM", bg=COLORS["header"],
                 fg=COLORS["header_text"], font=self._fonts["section"]).pack(anchor=tk.W)
        tk.Label(title_box,text="MANUFACTURING VERIFICATION WORKSPACE",bg=COLORS["header"],
                 fg=COLORS["header_muted"],font=self._fonts["small"]).pack(anchor=tk.W,pady=(2,0))
        from src.application.version import BUILD_ID
        tk.Label(hdr, text=BUILD_ID, bg=COLORS["header"], fg=COLORS["header_muted"],
                 font=self._fonts["subtitle"]).pack(side=tk.RIGHT, padx=18)

        menubar = tk.Menu(self.win)
        self.menubar = menubar
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="退出", command=self._close)
        menubar.add_cascade(label="文件", menu=file_menu)
        tool_menu = tk.Menu(menubar, tearoff=0)
        for label, page in (("显示与报告…", 0), ("PCB 工具…", 1),
                            ("几何资料…", 2), ("坐标定位…", 3)):
            tool_menu.add_command(label=label, command=lambda index=page: self._show_tools(index))
        menubar.add_cascade(label="工具", menu=tool_menu)
        menubar.add_command(label="帮助", command=self._show_help)
        self._menus = [(menubar, 0, "文件"), (file_menu, 0, "退出"), (menubar, 1, "工具"),
                       (tool_menu, 0, "显示与报告…"), (tool_menu, 1, "PCB 工具…"),
                       (tool_menu, 2, "几何资料…"), (tool_menu, 3, "坐标定位…"),
                       (menubar, 2, "帮助")]
        self.win.configure(menu=menubar)

        btns = ttk.Frame(self.content, padding=(12, 10))
        btns.pack(fill=tk.X)
        self.action_bar = btns
        source_actions = ttk.Menubutton(btns, text="导入资料")
        source_menu = tk.Menu(source_actions, tearoff=0)
        source_menu.add_command(label="导入 PCB 文件 / 压缩包…", command=self._import_pcb_file)
        source_menu.add_command(label="导入 Gerber 目录…", command=self._import_gerber_folder)
        source_menu.add_command(label="导入 ODB++（查看器 + SMT 协同）…", command=lambda: self._import_viewer("odb"))
        source_actions.configure(menu=source_menu)
        source_actions.pack(side=tk.LEFT, padx=(0, 12))
        self.import_menu = source_menu
        self._menus.extend(((source_menu, 0, "导入 PCB 文件 / 压缩包…"),
                            (source_menu, 1, "导入 Gerber 目录…"),
                            (source_menu, 2, "导入 ODB++（查看器 + SMT 协同）…")))
        # Keep one import control rather than parallel file and folder buttons.
        self.import_gerber_button = source_actions
        ttk.Separator(btns, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=7)
        self.run_button = ttk.Button(btns, text="整体分析 · PCB + SMT", style="Primary.TButton",
                                     command=lambda: self._run(overall=True))
        self.run_button.pack(side=tk.LEFT, padx=(0, 7))
        ttk.Separator(btns, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=7)

    def _show_help(self):
        messagebox.showinfo("MPA-DFM 使用提示",
            "导入资料 → 选择 PCB 板厂与 SMT 供应商/产线 → 整体分析 → 评审问题与覆盖缺口 → 选择格式导出分析报告。\n\n"
            "PCB 页可以单独执行 PCB 分析。SMT 页选择立景/同兴达，补充资料按钮展开 BOM/ODB/STP 等输入。\n"
            "独立 Gerber 缺少 BRD 时 SMT 未执行，不视为整体放行。",
            parent=self.win)

    def _show_tools(self, page=0):
        if not self._advanced_visible:
            self._toggle_advanced()
        self.tools_notebook.select(page)

    def _save_ui_preferences(self):
        from src.ui.prefs import load_prefs, save_prefs
        prefs = load_prefs()
        prefs.update(ui_language="en" if self.language.get() == "English" else "zh",
                     ui_theme="dark" if self.color_mode.get() == "Dark" else "light")
        save_prefs(prefs)

    def _apply_language(self):
        i18n.LANGUAGE = "en" if self.language.get() == "English" else "zh"
        i18n.localize_widgets(self.win)
        self._refresh_menu_labels()
        self.smt.check_source_changes()
        if self.last_result and not self._gerber_active():
            self.results.render_capability(self.last_result)
        if self.gerber_panel.last_result:
            self.gerber_panel.results.render_capability(self.gerber_panel.last_result)
        if self.smt.last_result:
            self.smt._render_result(self.smt.last_result, self.smt._revision)
        self._save_ui_preferences()

    def _refresh_menu_labels(self):
        for menu, index, source in getattr(self, "_menus", ()):
            if menu is None:
                continue
            try:
                menu.entryconfigure(index, label=i18n.translate(source))
            except tk.TclError:
                pass
        self.import_gerber_button.configure(text=i18n.translate("导入资料"))
        self.run_button.configure(text=i18n.translate("整体分析 · PCB + SMT"))

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
        while True:
            try:
                revision, stage = self._manufacturing_stage_queue.get_nowait()
            except queue.Empty:
                break
            if revision == self._input_revision and isinstance(stage, tuple) and stage[0] == "extracted":
                self._apply_extracted_data(stage[1])
                continue
            if revision == self._input_revision and "pcb-analysis" in self.tasks.pending:
                labels = {"gerber": "原生导出 Gerber", "drill": "原生导出 NC Drill", "route": "原生导出 NC Route 槽孔",
                          "odb": "原生导出 ODB++", "gerber_analysis": "检查 Gerber 与钻孔几何", "odb_analysis": "检查 ODB++ 位号/网络对象",
                          "extracta": "后台 extracta 获取器件坐标与铜厚（不打开 Editor）", "smt": "SMT 同轮装配与工艺核对"}
                self.status_var.set("BRD 一键制造分析：" + labels.get(stage, stage) + "…")
                self._progress_stage(stage if stage != "smt" else "smt_sources")
            elif revision == self._input_revision and isinstance(stage, str) and stage.startswith("smt_"):
                self._progress_stage(stage)
        if self._loaded_signature:
            try:
                require_brd_signature(self.brd_path.get().strip(), self._loaded_signature)
            except (ValueError, OSError) as exc:
                self._clear_imported_board(str(exc))
        self.smt.check_source_changes()
        if self.language.get() == "English":
            self.win.title("Evan MPA-DFM - PCB / SMT Manufacturing Analysis")
        else:
            self.win.title("Evan MPA-DFM - PCB / SMT 制造能力分析")
        self._ui_poll_id = self.win.after(600, self._refresh_ui_labels)

    def _build_report_bar(self):
        self.report_buttons = {}
        self.analysis_export_button = ttk.Button(self.action_bar, text="导出分析报告…", style="Secondary.TButton",
                                 command=self._export_analysis)
        self.analysis_export_button.pack(side=tk.LEFT, padx=6)
        status = ttk.Frame(self.win, padding=(12, 4))
        status.pack(side=tk.BOTTOM, fill=tk.X)
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=100,
                                        style="Idle.Horizontal.TProgressbar")
        self.progress.pack(side=tk.RIGHT)
        self.task_var = tk.StringVar(value="就绪")
        self.task_label = ttk.Label(status, textvariable=self.task_var, foreground=COLORS["muted"])
        self.task_label.pack(side=tk.LEFT)
        self.task_dot = tk.Canvas(status, width=16, height=16, bg=COLORS["bg"],
                                  highlightthickness=0)
        self.task_dot.pack(side=tk.LEFT, padx=6)
        self._dot = self.task_dot.create_oval(3, 3, 13, 13, fill=COLORS["faint"], outline="")

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
        self.settings_win = tk.Toplevel(self.win)
        self.settings_win.withdraw()
        self.settings_win.title("更多工具 / 设置")
        tool_width = min(1120, max(760, self.settings_win.winfo_screenwidth() - 100))
        tool_height = min(760, max(520, self.settings_win.winfo_screenheight() - 120))
        self.settings_win.geometry(f"{tool_width}x{tool_height}")
        self.settings_win.minsize(min(760, tool_width), min(520, tool_height))
        self.settings_win.protocol("WM_DELETE_WINDOW", self._hide_settings)
        self.advanced_frame = ttk.Frame(self.settings_win, padding=12)
        self._advanced_visible = False
        self.tools_notebook = ttk.Notebook(self.advanced_frame)
        self.tools_notebook.pack(fill=tk.BOTH, expand=True)
        appearance_tab = ttk.Frame(self.tools_notebook, padding=10)
        pcb_tab = ttk.Frame(self.tools_notebook, padding=10)
        geometry_tab = ttk.Frame(self.tools_notebook, padding=10)
        locate_tab = ttk.Frame(self.tools_notebook, padding=10)
        for page, title in ((appearance_tab, "显示 / 报告"), (pcb_tab, "PCB 工具"),
                            (geometry_tab, "几何资料"), (locate_tab, "坐标定位")):
            self.tools_notebook.add(page, text=title)

        appearance = FlowBar(appearance_tab)
        appearance.pack(fill=tk.X, pady=(0, 8))
        appearance.add(ttk.Label(appearance, text="语言"))
        language = ttk.Combobox(appearance, textvariable=self.language, values=("中文", "English"), state="readonly", width=9)
        appearance.add(language)
        language.bind("<<ComboboxSelected>>", lambda _: self._apply_language())
        appearance.add(ttk.Label(appearance, text="主题"))
        theme = ttk.Combobox(appearance, textvariable=self.color_mode, values=("Light", "Dark"), state="readonly", width=8)
        appearance.add(theme)
        theme.bind("<<ComboboxSelected>>", lambda _: self._apply_color_mode())
        reports = FlowBar(appearance_tab)
        reports.pack(fill=tk.X, pady=(0, 8))
        reports.add(ttk.Label(reports, text="报告格式"))
        reports.add(ttk.Combobox(reports, textvariable=self.report_format, values=(".xlsx", ".csv"), state="readonly", width=7))
        reports.add(ttk.Checkbutton(reports, text="报告隐藏低优先级", variable=self.hide_low_priority))
        self.preview_report_button = ttk.Button(reports, text="报告预览", command=self._preview_report)
        reports.add(self.preview_report_button)
        self.odb_preview_button = ttk.Button(reports, text="ODB++ 问题预览",
                                             command=lambda: self._preview_gerber("odb_preview"))
        reports.add(self.odb_preview_button)
        tools = FlowBar(pcb_tab)
        tools.pack(fill=tk.X, pady=(0, 8))
        tools.add(ttk.Button(tools, text="Gerber/ODB++ ↔ BOM 协同", command=self._open_manufacturing))
        native = FlowBar(pcb_tab, padding=(12, 2))
        native.pack(fill=tk.X)
        native.add(ttk.Button(native, text="网络定位", command=self._open_net_locate))
        self.trace_filter_button = ttk.Button(native, text="走线查找 / 原生高亮", command=self._open_trace_filter)
        native.add(self.trace_filter_button)
        native.add(ttk.Checkbutton(native, text="BRD 一键导出并分析 Gerber / NC Drill / NC Route / ODB++",
                                  variable=self.auto_manufacturing))
        native.add(ttk.Entry(native, textvariable=self.odb_exporter, width=30))
        native.add(ttk.Button(native, text="设置 ODB++ 导出器", command=self._configure_odb_exporter))
        gerber = FlowBar(geometry_tab, padding=(12, 2))
        gerber.pack(fill=tk.X)
        gerber.add(ttk.Checkbutton(gerber, text="每次从 BRD 重新导出 Gerber", variable=self.auto_gerber))
        geometry = FlowBar(geometry_tab, padding=(12, 2))
        geometry.pack(fill=tk.X)
        geometry.add(ttk.Label(geometry, text="实际几何检查始终执行；无已有 Gerber 时自动导出"))
        geometry.add(ttk.Entry(geometry, textvariable=self.geometry_data, width=28))
        geometry.add(ttk.Button(geometry, text="补充几何资料 JSON", command=self._browse_geometry_data))
        geometry.add(ttk.Button(geometry, text="生成补充资料模板", command=self._create_geometry_template))
        odb = FlowBar(geometry_tab, padding=(12, 2))
        odb.pack(fill=tk.X)
        odb.add(ttk.Label(odb, text="对象 DFM（可选，同版 ODB++）"))
        odb.add(ttk.Entry(odb, textvariable=self.odb_path, width=32))
        odb.add(ttk.Button(odb, text="选择 ODB++ 对象资料", command=self._browse_odb))
        odb.add(ttk.Label(odb, text="step"))
        odb.add(ttk.Entry(odb, textvariable=self.odb_step, width=12))
        odb.add(ttk.Label(odb, text="逐焊盘/过孔/阻焊/锡膏；未验证 BRD 对齐不跳转"))
        bar = FlowBar(locate_tab, padding=(12, 2))
        bar.pack(fill=tk.X)
        bar.add(ttk.Label(bar, text="问题坐标来源"))
        bar.add(ttk.Entry(bar, textvariable=self.coordinate_dir, width=28))
        bar.add(ttk.Button(bar, text="选择导出目录", command=self._browse_coordinates))
        bar.add(ttk.Button(bar, text="导入 COMP_XY", command=self._import_comp_xy))
        bar.add(ttk.Button(bar, text="生成 Allegro 坐标脚本", command=self._generate_coordinates))
        editor = FlowBar(locate_tab, padding=(12, 2))
        editor.pack(fill=tk.X)
        editor.add(ttk.Button(editor, text="设置 PCB Editor", command=self._configure_editor))
        self.editor_export_button = ttk.Button(editor, text="打开 BRD",
                                               command=self._open_export_editor)
        editor.add(self.editor_export_button)
        # Legacy attribute now points to the single BRD-open/extract action.
        self.open_brd_button = self.editor_export_button
        editor.add(ttk.Checkbutton(editor, text="已连接时选点同步到 Allegro", variable=self.sync_editor))
        editor.add(ttk.Checkbutton(editor, text="并排显示", variable=self.beside_editor))
        editor_label = ttk.Label(editor, textvariable=self.editor_status)
        editor.add(editor_label)
        wrap_label(editor_label, editor)

    def _toggle_advanced(self):
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self.advanced_frame.pack(fill=tk.BOTH, expand=True)
            self.settings_win.update_idletasks()
            self.settings_win.deiconify()
            self.settings_win.lift()
        else:
            self._hide_settings()

    def _hide_settings(self):
        self._advanced_visible = False
        self.settings_win.withdraw()
        self.advanced_frame.pack_forget()

    def _gerber_active(self):
        # Analysis routing follows imported data, never the selected notebook tab.
        return hasattr(self, "gerber_panel") and not self.brd_path.get().strip()

    def _import_pcb_file(self):
        if self._pcb_inputs_busy():
            return
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
        if self._pcb_inputs_busy():
            self.status_var.set("PCB 资料正在读取或分析，请等待完成后再导入")
            return
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
            self._refresh_source_display()
            self.status_var.set("已选择 BRD 配套 Gerber，执行 PCB 分析将使用这两份资料。")
        else:
            self.gerber_panel.load(path)

    def _clear_pcb_inputs(self):
        self.brd_path.set("")
        self.gerber_panel.source.set("")
        self.pcb_gerber_source.set("")
        self.gerber_dir.set("")
        self.params.reset_design()
        self.results.clear()
        self.status_var.set("资料已清空，请导入 BRD 或 Gerber")
        self._refresh_source_display()

    def _pcb_inputs_busy(self):
        return self._loading or self._editor_analysis_request is not None or any(key in self.tasks.pending for key in
            ("brd-import", "gerber-import", "gerber-analysis", "pcb-analysis", "gerber-export"))

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
                if self.gerber_panel.state == "running":
                    self._progress_start("gerber")
                elif self.gerber_panel.state == "complete":
                    self._progress_stage("gerber_analysis")
                    self._progress_finish("partial" if (self.gerber_panel.last_result or {}).get("geometry_coverage")
                                         else "complete")
                elif self.gerber_panel.state == "error":
                    self._progress_finish("error")
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
        """优先用内置查看器绘制；浏览器 HTML 仍供报告使用，不再承担放大绘制。"""
        kind = "odb" if key == "odb_preview" else "gerber"
        if not self._gerber_active() and (self.last_result or {}).get(key):
            self._open_viewer(kind)
            return
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
        importing = self._pcb_inputs_busy()
        self.import_gerber_button.configure(state=tk.DISABLED if importing else tk.NORMAL)
        gerber_ready = (self.gerber_panel.ready and self.pcb_gerber_source.get().strip() == self.gerber_panel.source.get()
                        if gerber_active else False)
        self.run_button.configure(text=i18n.translate("整体分析 · PCB + SMT"),
                                    state=tk.NORMAL if not importing and (gerber_ready if gerber_active else ready) else tk.DISABLED)
        analysis_busy=any(k in pending for k in ("pcb-analysis","gerber-analysis","smt-analysis","smt-profile","smt-bom-import"))
        if analysis_busy: self.run_button.configure(state=tk.DISABLED)
        self.pcb_run_button.configure(state=tk.NORMAL if not importing and not analysis_busy and (gerber_ready if gerber_active else ready) else tk.DISABLED)
        available_reports = {
            "dfm": ready and self.last_result and self.smt.last_result,
            "pcb": ready and self.last_result and self.output_pcb_report.get(),
            "smt": ready and self.smt.last_result,
        }
        if gerber_active:
            available_reports = {"pcb": bool(self.gerber_panel.last_result), "dfm": False, "smt": False}
        for kind, button in self.report_buttons.items():
            button.configure(text=i18n.translate({"pcb": "导出 PCB 报告", "dfm": "综合报告", "smt": "SMT 报告"}[kind]),
                             state=tk.NORMAL if available_reports[kind] and "report-export" not in pending else tk.DISABLED)
            if kind != "pcb":
                button._flow_hidden = False
        self.analysis_export_button.configure(state=tk.NORMAL if (self.last_result or self.smt.last_result) and not analysis_busy and "report-export" not in pending else tk.DISABLED)
        for kind, state in getattr(self, "viewer_models", {}).items():
            # 查看按钮只在资料可读时放开；读取中/无本轮结果都保持禁用，避免点了没反应。
            ready = bool(state["loaded"]) or bool(self.last_result)
            for button in state["buttons"]:
                if button.winfo_exists():
                    button.configure(state=tk.NORMAL if ready and not state["pending"]
                                     and "viewer-" + kind not in pending
                                     and "viewer-live-" + kind not in pending else tk.DISABLED)
        self.preview_report_button.configure(state=tk.NORMAL if (gerber_active and self.gerber_panel.last_result or
            ready and self.last_result) and "report-preview" not in pending else tk.DISABLED)
        gerber_result = self.gerber_panel.last_result if gerber_active else self.last_result
        self.gerber_view_button.configure(state=tk.NORMAL if (gerber_result or {}).get("gerber_preview") else tk.DISABLED)
        self._refresh_action_labels()
        if gerber_active:
            self.preview_report_button.configure(state=tk.NORMAL if gerber_result and
                "report-preview" not in pending else tk.DISABLED)
        self.odb_preview_button.configure(state=tk.NORMAL if (self.last_result or {}).get("odb_preview")
                                          else tk.DISABLED)
        self.params.set_board_available(ready or (gerber_active and bool(self.gerber_panel.info)))
        self.results.set_input_mode(gerber_active)
        self.smt.refresh_availability()
        self.workflow_state_label.configure(foreground=COLORS["success"] if ready else COLORS["warn"])
        self.board_state_var.set((f"BRD 已导入 · {os.path.basename(self._loaded_path)} · 可执行分析"
                                  if ready else f"分析已禁用 · {reason}"))
        if gerber_active:
            self.board_state_var.set(f"Gerber 已导入 · {os.path.basename(self.gerber_panel.source.get())} · 可执行 PCB 分析"
                                     if self.gerber_panel.info else "请导入 BRD 或 Gerber · 共用下方参数和结果")
            self.workflow_state_label.configure(foreground=COLORS["success"] if self.gerber_panel.info else COLORS["muted"])
        self._refresh_source_display()
        self._refresh_overview()
        text = self.board_state_var.get()
        if len(text) > 110:
            self.board_state_var.set(text[:107] + "…")
        # Language changes explicitly refresh captions; task state changes do
        # not relabel/reflow the entire widget tree.

    def _tasks_changed(self, pending):
        self._refresh_action_states()
        if pending or self._loading:
            names = {"pcb-analysis": "PCB 分析", "smt-analysis": "SMT 分析", "gerber-import": "Gerber 读取", "gerber-analysis": "Gerber 分析",
                      "report-export": "报告导出"}
            labels = (["读取 BRD"] if self._loading else []) + [names.get(p, p) for p in pending]
            self.task_var.set("处理中 · " + " / ".join(labels))
            self.task_label.configure(foreground=COLORS["primary"])
            self.task_dot.itemconfigure(self._dot, fill=COLORS["primary"])
            if not getattr(self, "_progress_running", False):
                self.progress.configure(mode="indeterminate", style="Running.Horizontal.TProgressbar")
                self.progress.start(15)  # 底部条状进度条；点阵动画只跟真实阶段，不在此启动
                self._progress_running = True
        else:
            if getattr(self, "_progress_running", False):
                self.progress.stop()
                self._progress_running = False
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
        self.overview = ttk.Frame(self.workbench, padding=22)
        self.workbench.add(self.overview,text="项目总览")
        self._build_overview()
        body = ttk.Frame(self.workbench, style="TFrame", padding=(16, 14, 16, 12))
        self.brd_body = body
        self.workbench.add(body, text="PCB 分析 · BRD / Gerber")
        body.columnconfigure(0, weight=0, minsize=390)
        body.columnconfigure(1, weight=1, minsize=300)
        body.rowconfigure(1, weight=1)
        pcb_actions=ttk.Frame(body);pcb_actions.grid(row=0,column=0,columnspan=2,sticky="ew",pady=(0,12))
        ttk.Label(pcb_actions,text="PCB · 制造分析",style="Section.TLabel").pack(side=tk.LEFT,padx=(0,20))
        self.pcb_run_button=ttk.Button(pcb_actions,text="执行 PCB 分析",command=self._run)
        self.pcb_run_button.pack(side=tk.LEFT,padx=6)
        self.report_buttons["pcb"]=ttk.Button(pcb_actions,text="导出 PCB 报告…",command=lambda:self._export("pcb"))
        self.report_buttons["pcb"].pack(side=tk.LEFT,padx=6)
        self.gerber_view_button=ttk.Button(pcb_actions,text="Gerber 预览",command=self._preview_gerber)
        self.gerber_view_button.pack(side=tk.LEFT,padx=6)

        left_card = Card(body, "分析设置", "选择资料后执行分析；缺少证据的项目会标为待复核")
        left_card.configure(width=400)
        left_card.pack_propagate(False)
        left_card.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        right_card = Card(body, "分析结果", "")
        right_card.pack_propagate(False)
        right_card.grid(row=1, column=1, sticky="nsew")

        sources = ttk.Frame(left_card.content, style="Card.TFrame")
        sources.pack(fill=tk.X, pady=(0, 8))
        source_head = ttk.Frame(sources, style="Card.TFrame")
        source_head.pack(fill=tk.X)
        ttk.Label(source_head, text="资料与状态", style="FieldLabel.TLabel").pack(side=tk.LEFT)
        self.board_state_label = ttk.Label(sources, textvariable=self.board_state_var,
                                           foreground=COLORS["warn"], wraplength=330, justify=tk.LEFT)
        self.board_state_label.pack(fill=tk.X, pady=(4, 2))
        self.workflow_state_label = self.board_state_label
        self.source_path_var = tk.StringVar(value="尚未导入资料")
        self.source_path_label = ttk.Label(sources, textvariable=self.source_path_var,
                                           style="CardHint.TLabel", wraplength=360, justify=tk.LEFT)
        self.source_path_label.pack(fill=tk.X, pady=(2, 2))
        self.clear_source_button = ttk.Button(sources, text="清空资料", command=self._clear_pcb_inputs)
        self.clear_source_button.pack(anchor=tk.E, pady=(2, 2))
        self.params = ParamPanel(left_card.content, self._browse_brd, self._load_from_brd,
                                  font_bold=self._fonts["ui_bold"], unified=True)
        self.pcb_gerber_source = tk.StringVar()
        self.source_inputs = ttk.Frame(sources, style="Card.TFrame")
        self.source_inputs.pack(fill=tk.X, pady=(5, 0))
        self.brd_row = ttk.Frame(self.source_inputs, style="Card.TFrame")
        self.brd_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(self.brd_row, text="BRD", style="FieldLabel.TLabel", width=7).pack(side=tk.LEFT)
        self.brd_path_entry = ttk.Entry(self.brd_row, textvariable=self.params.brd_path, width=18)
        self.brd_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 5))
        self.brd_path_entry.bind("<Return>", lambda _event: self._load_from_brd())
        self.gerber_row = ttk.Frame(self.source_inputs, style="Card.TFrame")
        self.gerber_row.pack(fill=tk.X)
        ttk.Label(self.gerber_row, text="Gerber", style="FieldLabel.TLabel", width=7).pack(side=tk.LEFT)
        self.gerber_path_entry = ttk.Entry(self.gerber_row, textvariable=self.pcb_gerber_source, width=18)
        self.gerber_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 5))
        self.gerber_path_entry.bind("<Return>", lambda _event: self._load_gerber_source(
            self.pcb_gerber_source.get().strip()))
        ttk.Label(sources, text="路径可粘贴后按 Enter 读取；也可从上方「导入资料」选择。",
                  style="CardHint.TLabel", wraplength=360, justify=tk.LEFT).pack(fill=tk.X, pady=(3, 0))
        self.params.pack(fill=tk.BOTH, expand=True)

        self.results = ResultPanel(right_card.content,
                                    on_open_point=self._open_problem_point,
                                    on_point_selected=self._sync_problem_point)
        self.results.pack(fill=tk.BOTH, expand=True)
        self.params.bind_stackup_view(self.results.stackup_view)
        from src.ui.gerber_panel import GerberPanel
        self.gerber_panel = GerberPanel(body, self.tasks, self._gerber_state_changed,
                                       shared_params=self.params, shared_results=self.results)
        self.geometry_data.trace_add("write", lambda *_:
                                     self.gerber_panel.geometry_data.set(self.geometry_data.get()))
        self.pcb_gerber_source.trace_add("write", self._gerber_path_edited)
        self.gerber_panel.source.trace_add("write", lambda *_:
                                          self.pcb_gerber_source.set(self.gerber_panel.source.get()))
        self.gerber_dir.trace_add("write", lambda *_:
                                 self.pcb_gerber_source.set(self.gerber_dir.get()) if self.brd_path.get().strip() else None)

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
                             on_result_changed=self._refresh_action_states, get_pcb_result=lambda: self.last_result,
                             on_stage=lambda name: self._manufacturing_stage_queue.put((self._input_revision, name)))
        self.workbench.add(self.smt, text="SMT 分析 · 供应商 / 产线")
        from src.ui.impedance_panel import ImpedancePanel
        self.impedance_panel = ImpedancePanel(self.workbench, self._trace_board, self._resolve_editor)
        self.workbench.add(self.impedance_panel, text="Allegro 走线查找 / 高亮")
        self.smt.detail.configure(bg=COLORS["card"], fg=COLORS["text"], insertbackground=COLORS["text"])
        for var in list(self.entries.values()) + [self.params.factory, self.params.level,
                    self.params.board_type, self.params.copper_um, self.params.surface_finish,
                    self.params.board_name, self.params.polarity_var, self.params.panelized_var,
                    self.coordinate_dir, self.gerber_dir, self.auto_gerber, self.geometry_data, self.odb_path, self.odb_step,
                    self.editor_path, self.auto_manufacturing, self.odb_exporter]:
            var.trace_add("write", self._invalidate_results)
        self.brd_path.trace_add("write", self._brd_path_changed)
        self.brd_path.trace_add("write", lambda *_: self._refresh_source_display())
        self.pcb_gerber_source.trace_add("write", lambda *_: self._refresh_source_display())
        self.gerber_dir.trace_add("write", lambda *_: self._refresh_source_display())
        self.gerber_panel.source.trace_add("write", lambda *_: self._refresh_source_display())
        self.smt.load_builtin()
        self.workbench.bind("<<NotebookTabChanged>>", lambda _: self._tasks_changed(tuple(self.tasks.pending)))

    def _build_overview(self):
        """总览页：状态卡 + 执行进度动画 + Gerber / ODB++ 图形查看器入口。"""
        # 总览内容比窗口高时仍可滚动到底部动作按钮，不靠“缩窗口”凑合。
        from src.ui.responsive import scroll_workbench
        page = scroll_workbench(self.overview)
        self.overview_scroll = page
        head = FlowBar(page)
        head.pack(fill=tk.X)
        head.add(ttk.Label(head, text="设计 → 制造 · 统一验证工作区", style="Section.TLabel"))
        head.add(ttk.Label(head, text="01 导入资料  /  02 选择 PCB 与 SMT 能力  /  03 整体分析  /  04 评审与报告",
                           foreground=COLORS["muted"]))
        cards = ttk.Frame(page)
        cards.pack(fill=tk.X, pady=(10, 6))
        self.overview_vars = {}
        for index, (key, title, hint) in enumerate((("pcb", "PCB 制造", "BRD / Gerber · 制造能力与几何"),
                    ("smt", "SMT 装配", "供应商条件 · 器件与装配"), ("overall", "整体结论", "缺资料不可放行 · 统一快照"))):
            cards.columnconfigure(index, weight=1)
            card = Card(cards, title, hint)
            card.grid(row=0, column=index, sticky="nsew", padx=(0, 12), pady=6)
            value = tk.StringVar(value="尚未分析")
            self.overview_vars[key] = value
            ttk.Label(card.content, textvariable=value, style="CardTitle.TLabel", wraplength=280).pack(anchor=tk.W, pady=12)
        progress_card = Card(page, "本轮执行进度", "灰点=未执行 · 绿点按真实阶段累计填满 · 黄点=仍有资料缺口 · 红点=失败")
        progress_card.pack(fill=tk.X, pady=(4, 8))
        inner = ttk.Frame(progress_card.content, style="Card.TFrame")
        inner.pack(fill=tk.X)
        self.progress_matrix = ProgressMatrix(inner, columns=18, rows=5, width=340, height=112)
        self.progress_matrix.pack(side=tk.LEFT)
        self.progress_note = tk.StringVar(value="未执行：导入资料后点击「整体分析 · PCB + SMT」")
        side = ttk.Frame(inner, style="Card.TFrame")
        side.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(16, 0))
        ttk.Label(side, textvariable=self.progress_note, style="CardHint.TLabel", wraplength=520,
                  justify=tk.LEFT).pack(anchor=tk.W)
        self.progress_stages = {"pcb": ["extracta", "gerber", "drill", "route", "odb", "gerber_analysis",
                                        "odb_analysis", "verdict"],
                                "overall": ["extracta", "gerber", "drill", "route", "odb", "gerber_analysis",
                                            "odb_analysis", "smt_sources", "smt_rules", "smt_geometry",
                                            "smt_assembly", "verdict"],
                                "gerber": ["gerber_read", "gerber_analysis", "verdict"],
                                "smt": ["smt_sources", "smt_rules", "smt_geometry", "smt_assembly", "verdict"]}
        self.progress_stages["smt"] = ["smt_sources", "smt_rules", "smt_geometry", "smt_assembly", "verdict"]
        viewers = ttk.Frame(page)
        viewers.pack(fill=tk.X, pady=(2, 8))
        viewers.columnconfigure(0, weight=1)
        viewers.columnconfigure(1, weight=1)
        self.viewer_models = {}
        for column, (key, title, hint) in enumerate((
                ("gerber", "Gerber 图形查看器", "文件 / 整套目录 / 压缩包；只读显示，不参与判定"),
                ("odb", "ODB++ 图形查看器", "Job 目录或归档 + step；未对齐只查看，不判位置"))):
            card = Card(viewers, title, hint)
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 12), pady=4)
            row = FlowBar(card.content)
            row.pack(fill=tk.X)
            status = tk.StringVar(value="未导入")
            self.viewer_models[key] = dict(status=status, loaded=None, source="", pending=False)
            self.viewer_models[key]["buttons"] = []
            row.add(ttk.Label(row, textvariable=status, wraplength=330))
            if key == "gerber":
                row.add(ttk.Button(row, text="导入 Gerber…", command=lambda: self._import_viewer("gerber")))
                self.viewer_models[key]["buttons"].append(
                    row.add(ttk.Button(row, text="查看图形", command=lambda: self._open_viewer("gerber"))))
            else:
                row.add(ttk.Entry(row, textvariable=self.odb_step, width=9))
                row.add(ttk.Button(row, text="导入 ODB++…", command=lambda: self._import_viewer("odb")))
                self.viewer_models[key]["buttons"].append(
                    row.add(ttk.Button(row, text="查看图形", command=lambda: self._open_viewer("odb"))))
        self.overview_source = tk.StringVar()
        ttk.Label(page, textvariable=self.overview_source, wraplength=1050,
                  justify=tk.LEFT).pack(fill=tk.X, pady=(2, 10))
        actions = ttk.Frame(page)
        actions.pack(fill=tk.X)
        ttk.Button(actions, text="PCB 设置与结果 →", command=lambda: self.workbench.select(self.brd_body)).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Button(actions, text="SMT 供应商与结果 →", command=lambda: self.workbench.select(self.smt)).pack(side=tk.LEFT)
        ttk.Label(page, text="整体分析复用本轮 PCB 提取资料执行 SMT；独立 Gerber 缺 BRD 时 SMT 明确未执行。\n通过项不抵消失败或覆盖缺口；规则来源、输入版本和模型确认始终保留。查看器图形是显示简化后的真实底片，不能当量测值。", wraplength=1050).pack(anchor=tk.W, pady=10)

    # ------------------------------------------------------------------ 查看器
    def _import_viewer(self, kind):
        if kind == "gerber":
            path = filedialog.askopenfilename(parent=self.win, title="选择 Gerber 文件 / 压缩包（查看器）",
                filetypes=[("Gerber / 压缩包", "*.gbr *.ger *.gerber *.gtl *.gbl *.gts *.gbs *.gto *.gbo *.gko *.zip *.tgz *.tar *.gz"),
                           ("所有文件", "*.*")]) or filedialog.askdirectory(parent=self.win, title="或选择 Gerber 目录（查看器）")
        else:
            path = filedialog.askopenfilename(parent=self.win, title="选择 ODB++ 归档（查看器）",
                filetypes=[("ODB++", "*.tgz *.tar.gz *.tar *.zip")])
            if not path:
                path = filedialog.askdirectory(parent=self.win, title="或选择 ODB++ Job 目录（查看器）")
        if not path:
            return
        self._load_viewer(kind, path)

    def _load_viewer(self, kind, path):
        state = self.viewer_models[kind]
        state["pending"] = True
        state["status"].set("正在读取图形…")
        revision = self._input_revision
        step = self.odb_step.get().strip() or None if kind == "odb" else None
        def work():
            from src.application.geometry_view import gerber_view, odb_view
            return (gerber_view(path) if kind == "gerber" else odb_view(path, step=step))
        def done(result):
            state["pending"] = False
            if revision != self._input_revision:
                state["status"].set("读取期间输入已变化，已丢弃旧图形")
                return
            state["loaded"] = result
            state["source"] = result["source"]
            model = result["model"]
            state["status"].set(f"{len(model['layers'])} 层 · {len(model['issues'])} 个问题标记 · "
                                f"{os.path.basename(result['source']) if result['source'] else ''}"
                                + (f" · step {model.get('selected_step')}" if model.get("selected_step") else ""))
            self._refresh_action_states()
        def failed(error):
            state["pending"] = False
            state["loaded"] = None
            state["status"].set("读取失败：" + str(error).splitlines()[0])
            self._refresh_action_states()
        self.tasks.submit("viewer-" + kind, work, done, failed)

    def _open_viewer(self, kind, analysis=False):
        state = self.viewer_models.get(kind) or {}
        loaded = state.get("loaded")
        if loaded and not analysis:
            return self._show_viewer(kind, loaded)
        if state.get("pending"):
            self.status_var.set("图形正在后台读取，完成后自动打开查看器…")
            return None
        if not self.last_result:
            messagebox.showinfo("图形查看器", "请先导入资料或完成本轮 PCB 分析", parent=self.win)
            return None
        self._load_viewer_from_result(kind)
        return None

    def _show_viewer(self, kind, loaded):
        from src.ui.geometry_viewer import open_viewer
        model = loaded["model"]
        subtitle = "来源：" + str(loaded["source"]) + " · " + "；".join(model["notes"])
        return open_viewer(self.win, model, title=("Gerber 图形查看器" if kind == "gerber" else "ODB++ 图形查看器"),
                           subtitle=subtitle)

    def _load_viewer_from_result(self, kind):
        """读取本轮判定实际使用的同一批底片，避免“看到的是另一套文件”。"""
        state = self.viewer_models[kind]
        state["pending"] = True
        state["status"].set("正在读取本轮分析图形…")
        revision = self._input_revision
        result = deepcopy(self.last_result)

        def work():
            from src.application.geometry_view import result_view
            return result_view(result, kind)

        def done(payload):
            state["pending"] = False
            if revision != self._input_revision:
                state["status"].set("读取期间输入已变化，已丢弃旧图形")
                return
            state["loaded"] = payload
            state["source"] = payload["source"]
            model = payload["model"]
            state["status"].set(f"本轮图形已载入 · {len(model['layers'])} 层 · {len(model['issues'])} 个问题标记")
            self._show_viewer(kind, payload)
            self._refresh_action_states()

        def failed(error):
            state["pending"] = False
            state["status"].set("本轮图形读取失败：" + str(error).splitlines()[0])
            self._refresh_action_states()
        self.tasks.submit("viewer-live-" + kind, work, done, failed)

    # ------------------------------------------------------------------ 进度动画
    def _progress_start(self, kind):
        self.progress_matrix.set_stages(self.progress_stages[kind])
        self.progress_matrix.start(label=self.progress_labels.get(kind, kind))
        self.progress_note.set(f"{self.progress_labels.get(kind, kind)}进行中 · 共 {len(self.progress_stages[kind])} 个阶段\n"
                               "点只按阶段完成累计，不靠猜耗时前进")

    def _progress_stage(self, stage):
        if stage == "smt_start":
            # 单独执行 SMT 时由本动画显示 SMT 阶段；整体分析已在运行，不重启。
            if self.progress_matrix.state == "idle":
                self._progress_start("smt")
            return
        if stage == "smt_done":
            if "pcb-analysis" not in self.tasks.pending and self.progress_matrix.state == "running":
                result = self.smt.last_result or {}
                gaps = bool(result.get("assembly_errors")) or any(row.get("missing")
                                                                   for row in result.get("assembly_coverage", []))
                self._progress_finish("partial" if gaps else "complete")
            return
        if self.progress_matrix.state != "running":
            return
        self.progress_matrix.advance(stage, label=self.progress_step_labels.get(stage, stage))
        shot = self.progress_matrix.snapshot()
        self.progress_note.set(f"当前阶段：{self.progress_step_labels.get(stage, stage)}\n"
                               f"已完成 {shot['completed']}/{shot['stages']} 阶段 · {shot['filled']}/{shot['total']} 点")

    def _progress_finish(self, state="complete", label=""):
        if state == "idle":
            self.progress_matrix.reset("已丢弃过期结果 · 未执行")
            self.progress_note.set("结果已过期：重新执行后才会累计")
            return
        if self.progress_matrix.state == "idle":
            return
        self.progress_matrix.finish(state, label=label)
        notes = {"complete": "全部阶段已完成；仍需评审不符合项与缺口",
                 "partial": "阶段跑完但仍有资料未取得：黄点=缺口，不等于通过",
                 "error": "本轮未完成：红点为已尝试部分，灰点为未执行部分"}
        self.progress_note.set(notes.get(state, "") + f" · {self.progress_matrix.snapshot()['filled']} 点")

    def _brd_path_changed(self, *_args):
        self.workbench.select(self.brd_body)
        self._changing_board = True
        try:
            self.gerber_panel.source.set("")
            if self.brd_path.get().strip():
                self.auto_manufacturing.set(True)
                self.auto_gerber.set(True)
        finally:
            self._changing_board = False
        self._clear_imported_board("")
        self._refresh_source_display()

    def _gerber_path_edited(self, *_):
        if self._changing_board:
            return
        if self._gerber_active() and self.pcb_gerber_source.get().strip() != self.gerber_panel.source.get():
            self.gerber_panel.info = None
            self.gerber_panel.invalidate()
            self.status_var.set("Gerber 路径已变化，请点击读取 Gerber")
        elif not self._gerber_active() and self.pcb_gerber_source.get().strip() != self.gerber_dir.get():
            self.auto_manufacturing.set(False)
            self.auto_gerber.set(False)
            self.gerber_dir.set(self.pcb_gerber_source.get().strip())

    def _refresh_source_display(self):
        if not hasattr(self, "source_path_var"):
            return
        if not hasattr(self, "gerber_panel"):
            return
        brd = self.brd_path.get().strip()
        gerber = self.pcb_gerber_source.get().strip() or self.gerber_panel.source.get().strip() or self.gerber_dir.get().strip()
        lines = []
        if brd:
            lines.append("BRD：" + brd)
        if gerber:
            lines.append("Gerber：" + gerber)
        self.source_path_var.set("\n".join(lines) or "尚未导入资料")

    def _refresh_action_labels(self):
        self.report_buttons["pcb"].configure(text=i18n.translate("导出 PCB 报告"))
        self.gerber_view_button.configure(text=i18n.translate("Gerber 预览"))

    def _refresh_overview(self):
        if not hasattr(self,"overview_vars"): return
        pcb=self.last_result;smt=self.smt.last_result
        self.overview_vars["pcb"].set((pcb or {}).get("verdict") or "尚未分析")
        self.overview_vars["smt"].set((smt or {}).get("verdict") or ("未执行 · 缺 BRD" if self._gerber_active() else "尚未分析"))
        if self._overall_state=="running": text="正在执行 PCB + SMT…"
        elif self._overall_error: text=self._overall_error
        elif pcb and smt: text=combined_summary(pcb,smt)
        else: text="分析不完整 · 不可全板放行"
        self.overview_vars["overall"].set(text)
        self.overview_source.set(self.source_path_var.get()+"\nSMT："+self.smt.meta.get())

    def _clear_imported_board(self, reason):
        self._load_token += 1  # 旧读取线程的结果不能让新路径变为已导入
        self._loading = False
        self._loaded_path = ""
        self._loaded_signature = None
        self._brd_error = reason
        self.brd_info = None
        self._editor_analysis_request = None
        if hasattr(self, "impedance_panel"):
            self.impedance_panel.set_layers(())
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
        self._overall_state="idle";self._overall_error=""
        if hasattr(self, "progress_matrix"):
            self.progress_matrix.reset("输入已变化 · 本轮未执行")
            self.progress_note.set("未执行：导入资料后点击「整体分析 · PCB + SMT」")
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
        design["_editor_path"] = self.editor_path.get()
        if self.last_result and self.last_result.get("native_extraction"):
            design["_native_payload"] = deepcopy(self.last_result["native_extraction"])
        elif self.params._info.get("native_extraction"):
            design["_native_payload"] = deepcopy(self.params._info["native_extraction"])
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

    def _resolve_editor(self):
        editor = discover_editor(self.editor_path.get())
        if not editor:
            self._configure_editor()
            editor = discover_editor(self.editor_path.get())
        if not editor:
            messagebox.showwarning("PCB Editor", "未找到 PCB Editor，请设置 allegro.exe 或 orcadpcb.exe（不能使用 Free Viewer）", parent=self.win)
        return editor

    def _trace_board(self):
        reason = self._brd_block_reason()
        if reason:
            raise ValueError(reason)
        if self._pcb_inputs_busy():
            raise ValueError("请等待当前 PCB 读取/分析完成后再查找走线")
        return self.brd_path.get().strip()

    def _editor_for_current_board(self):
        session = self._editor_session
        path = self.brd_path.get().strip()
        return session if (session and session.running() and
                           os.path.normcase(os.path.abspath(path)) == os.path.normcase(session.brd_path)
                           and session.source_signature == self._loaded_signature
                           and session.status()[1] not in ("BOARD_CHANGED", "TIMER_FAILED", "EXPORT_FAILED")) else None

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
            return True
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
            return True
        editor = self._resolve_editor()
        if not editor:
            return False
        try:
            session = AllegroSession(path, editor)
            # Isolate each export from historical CSVs and leave customer folders unchanged.
            output = str(session.folder / "coordinates") if export else ""
            session.source_signature = self._loaded_signature
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
        return True

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
            self.editor_status.set("Allegro：已关闭或启动失败，请检查许可/板文件；日志：" + str(session.folder / "launch.log"))
            self._fail_editor_analysis("PCB Editor 已退出或启动失败，请检查许可/板文件；日志：" + str(session.folder / "launch.log"))
            return
        if not current_board:
            self.editor_status.set("Allegro：BRD 已切换，请重新打开当前板")
        elif (sequence, state) != self._editor_last_ack:
            labels = {"READY": "已连接", "LOCATED": "已定位选中问题点", "BOARD_CHANGED": "Editor 已换板，请重新打开",
                      "ZOOM_FAILED": "缩放失败", "TIMER_FAILED": "同步定时器不可用", "EXPORT_FAILED": "坐标导出失败"}
            if state != "WAITING":
                self.editor_status.set("Allegro：" + labels.get(state, state))
                self._editor_last_ack = (sequence, state)
        if current_board and self._editor_export_pending and state in ("READY", "LOCATED"):
            self._editor_export_pending = False
            try:
                session.validate_export()
            except (OSError, ValueError) as exc:
                self.editor_status.set("Allegro：坐标导出不完整 " + str(exc))
                if self._editor_analysis_request is not None:
                    self._fail_editor_analysis(str(exc))
                else:
                    messagebox.showerror("坐标导出失败", str(exc), parent=self.win)
                self._schedule_editor_poll()
                return
            request = self._editor_analysis_request
            self._editor_analysis_request = None
            if request is not None and request != (self.brd_path.get().strip(), self._input_revision):
                self.status_var.set("等待 PCB Editor 期间输入已变化，已取消旧分析，请重新执行")
                self._pcb_state = "idle"
            elif request is not None or not self._brd_block_reason():
                self.coordinate_dir.set(session.export_dir)
                self._refresh_component_coordinates(select_tab=request is None)
                if request is not None:
                    self._run(editor_ready=True)
            self._tasks_changed(tuple(self.tasks.pending))
        if current_board and self.beside_editor.get() and self._editor_layout_requested and not self._editor_arranged:
            self._editor_arranged = arrange_editor(session.process.pid, self.win.winfo_id())
            if self._editor_arranged:
                self._editor_layout_requested = False
        if current_board and state == "WAITING" and time.monotonic() - self._editor_started > 30:
            self.editor_status.set("Allegro：等待连接；请完成许可选择/打开板，再查看启动脚本")
        if self._editor_analysis_request is not None:
            if not current_board or state in ("EXPORT_FAILED", "TIMER_FAILED", "BOARD_CHANGED"):
                self._fail_editor_analysis("PCB Editor 坐标联动失败，请检查原生命令日志：" + str(session.folder))
            elif state == "WAITING" and time.monotonic() - self._editor_started > 120:
                self._fail_editor_analysis("PCB Editor 连接超时，请完成许可选择后重试；启动脚本：" + str(session.script))
        elif current_board and session.sequence > (sequence or 0) and time.monotonic() - session.request_time > 8:
            self.editor_status.set("Allegro：定位待响应，请结束 Editor 当前交互命令")
        self._schedule_editor_poll()

    def _fail_editor_analysis(self, message):
        if self._editor_analysis_request is None:
            return
        self._editor_analysis_request = None
        self._editor_export_pending = False
        self._pcb_state = "error"
        self.status_var.set(message)
        self._tasks_changed(tuple(self.tasks.pending))
        messagebox.showerror("PCB Editor 联动失败", message, parent=self.win)

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
                    self._run(editor_ready=True)

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
        """Select the three-input direct PCB Editor search page."""
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
        if self._pcb_inputs_busy():
            return
        path = filedialog.askopenfilename(
            title="Select Allegro BRD File",
            filetypes=[("BRD files", "*.brd"), ("All files", "*.*")])
        if path:
            self.brd_path.set(path)
            self._load_from_brd()

    def _load_from_brd(self):
        if self._pcb_inputs_busy():
            self.status_var.set("PCB 资料正在读取或分析，请等待完成后再读取 BRD")
            return
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
                        self.impedance_panel.set_layers(payload.get("layers", []))
                        self._loaded_path = path
                        self._loaded_signature = payload["_import_signature"]
                        self._brd_error = ""
                        self.result_var.set("BRD 已导入，确认逐层叠构和板厂能力后执行分析")
                        self.status_var.set("BRD 已导入，确认逐层叠构和板厂能力后执行分析")
                        self.gerber_panel.show_layers([dict(name=name, role="BRD 铜层", objects="", bounds_mm=[])
                                                      for name in payload.get("layers", [])])
                        self.results.show_components([], "未获取器件坐标；点击执行 PCB 分析后后台 extracta 提取，不打开 Editor")
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
        if self._loading or self.tasks.pending or self._editor_analysis_request is not None or self.impedance_panel.tasks.pending:
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
    def _run(self, editor_ready=False, overall=False):
        if self._pcb_inputs_busy() or any(k in self.tasks.pending for k in ("smt-analysis","smt-profile","smt-bom-import")):
            return
        if self._gerber_active():
            if self.pcb_gerber_source.get().strip() != self.gerber_panel.source.get():
                messagebox.showwarning("Gerber 未就绪", "路径已变化，请重新读取 Gerber", parent=self.win)
                return
            self.gerber_panel.run()
            if overall:
                self.smt.invalidate()
                self._overall_error="SMT 未执行 · 缺 BRD，不可整体放行"
                self._refresh_overview()
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
            output_dir=self._pcb_output_dir(), stackup_sources=tuple(deepcopy(self.params.stackup_sources)))

        smt_snapshot=None
        if overall:
            try: smt_snapshot=self.smt.capture_analysis()
            except (ValueError,OSError) as exc:
                messagebox.showwarning("整体分析条件",str(exc),parent=self.win);return
            self.smt.invalidate()
            self._overall_state="running";self._overall_error=""
        smt_revision=self.smt._revision

        extraction_output = self._pcb_output_dir()
        def compute():
            from dataclasses import replace
            from src.application.brd_extraction import prepare_brd_data
            self._manufacturing_stage_queue.put((revision, "extracta"))
            payload = prepare_brd_data(brd_path, extraction_output, editor_path, min(request.timeout, 90))
            # Deliver extraction on the main thread even if manufacturing later fails.
            self._manufacturing_stage_queue.put((revision, ("extracted", payload)))
            pcb=run_pcb_analysis(replace(request, native_data=payload), progress=lambda stage:
                                    self._manufacturing_stage_queue.put((revision, stage)))
            if not overall: return pcb
            self._manufacturing_stage_queue.put((revision, "smt"))
            actual=deepcopy(pcb.get("analyzed_design",design))
            actual.update(_input_brd=brd_path,_brd_sha256=design["_brd_sha256"],_brd_signature=design.get("_brd_signature"),
                          _native_payload=pcb.get("native_extraction",payload))
            from src.application.overall_analysis import compute_smt_snapshot
            try: smt=compute_smt_snapshot(smt_snapshot,actual,pcb)
            except Exception as exc:
                return dict(pcb=pcb,smt=None,smt_error=str(exc))
            from uuid import uuid4
            run_id=str(uuid4());pcb["overall_run_id"]=run_id;smt["overall_run_id"]=run_id
            return dict(pcb=pcb,smt=smt,smt_error="")

        def complete(result):
            if revision != self._input_revision or self._brd_block_reason() or (overall and smt_revision!=self.smt._revision):
                self._pcb_state = "idle"
                self.status_var.set("PCB 分析期间输入已变化，已丢弃过期结果，请重新分析")
                self._overall_state="idle"
                self._progress_finish("idle")
                return
            gaps = bool(result.get("geometry_coverage")) if not overall else bool(result["pcb"].get("geometry_coverage"))
            if overall:
                bundle=result;result=bundle["pcb"]
                self._overall_state="complete";self._overall_error=bundle["smt_error"]
            self._apply_extracted_data(result.get("native_extraction", {}))
            self._render_pcb(result, result.get("analyzed_design", design))
            self._pcb_state = "complete"
            if overall:
                if bundle["smt"]: self.smt.accept_result(bundle["smt"])
                else: self._overall_error="SMT 分析失败 · "+bundle["smt_error"]
                self.status_var.set("整体分析执行结束 · "+("PCB + SMT 已完成；请评审失败项与缺口" if bundle["smt"] else self._overall_error))
                self.workbench.select(self.overview)
                self._refresh_overview()
                self._progress_finish("error" if not bundle["smt"] else ("partial" if gaps else "complete"),
                                      label="整体分析完成 · 仍有资料缺口" if gaps and bundle["smt"] else "")
            else:
                self._progress_finish("partial" if gaps else "complete")

        def failed(error):
            if revision == self._input_revision:
                self._pcb_state = "error"
                if overall: self._overall_state="error";self._overall_error="整体分析失败 · PCB 未完成"
                self._progress_finish("error", label="执行失败 · 详见诊断日志")
                self.status_var.set("PCB 分析失败：" + error)
                self.result_var.set("PCB 分析失败：" + error)
                from src.infrastructure.diagnostics import log_path
                messagebox.showerror("PCB 分析失败", error + "\n\n诊断日志：" + str(log_path()), parent=self.win)

        # A failed rerun must not leave an older analysis looking like its result.
        self.last_result = None
        self.last_design = {}
        if not overall: self.smt.invalidate()
        self.results.clear(clear_components=False)
        self._pcb_state = "running"
        self._progress_start("overall" if overall else "pcb")
        if self.tasks.submit("pcb-analysis", compute, complete, failed):
            self.status_var.set("正在后台执行整体分析 · PCB → SMT…" if overall else "正在后台执行 PCB 分析，界面可继续操作…")

    def _pcb_output_dir(self):
        from src.ui.paths import default_output_dir
        return os.path.join(default_output_dir(), "PCB_Manufacturing")

    def _render_pcb(self, result, design):
        self.last_result = result
        self.last_design = design
        self.params.show_measurements(result)

        self.results.render_capability(result)
        if not result.get("component_coordinates"):
            self.results.show_components([], "未获取器件坐标；查看后台提取状态/日志，不使用猜测坐标")
        self.gerber_panel.show_layers(result.get("gerber_layers", []))
        self.status_var.set(f"PCB 分析完成 · 不符合 {result['error_count']} · 待复核 {result['warning_count']} · 查看结果与覆盖缺口")

    def _apply_extracted_data(self, payload):
        if (not payload or self._brd_block_reason()
                or payload.get("source_brd_sha256") != (self.brd_info or {}).get("_import_sha256")):
            return
        key = (payload.get("directory"), payload.get("source_brd_sha256"))
        if self._applied_extraction == key:
            return
        self._applied_extraction = key
        # Applying the current worker's evidence is not a new user input. It
        # must not invalidate that worker's revision or its result snapshot.
        changing = self._changing_board
        self._changing_board = True
        try:
            self.params.apply_native(payload)
        finally:
            self._changing_board = changing
        self.impedance_panel.set_layers(self.params.layers)
        from src.application.locations import component_coordinates
        coordinates = component_coordinates({"components": payload.get("components", [])}, payload.get("unit", ""))
        self._component_coordinates = coordinates
        self._component_brd_path = os.path.normcase(os.path.abspath(self.brd_path.get()))
        self.results.show_components(coordinates, "后台 extracta 提取；" + "；".join(payload.get("warnings", [])))


    # ------------------------------------------------------------ 导出
    def _export_analysis(self):
        self.smt.check_source_changes()
        available=[]
        if self.last_result and self.smt.last_result: available.append("dfm")
        if self.last_result: available.append("pcb")
        if self.smt.last_result: available.append("smt")
        if not available: return
        from src.ui.report_dialog import choose_report
        selected=choose_report(self.win,available,self.brd_path.get() or self.gerber_panel.source.get())
        if selected: self._export(*selected)

    def _export(self, kind="pcb", selected_path=None):
        if self._gerber_active():
            if kind == "pcb":
                self.gerber_panel.export(self.report_format.get(), self.hide_low_priority.get(), selected_path=selected_path)
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
        from src.ui.report_dialog import choose_report
        selected=(kind,selected_path) if selected_path else choose_report(self.win,[kind],board_meta["brd_path"],kind)
        path=selected[1] if selected else None
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

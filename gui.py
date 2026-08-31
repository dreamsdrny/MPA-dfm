"""
BRD-AI: GUI 图形界面 (tkinter)
支持选择 BRD 文件、Excel Checklist 文件、制程能力级别
"""
import os
import sys
import json
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.pcb_reader import extract_brd_info
from src.signal_classifier import classify_and_export
from src.rule_engine import compute_class_constraints
from src.dfm_engine import validate_constraints
from src.skill_generator import generate_skill_script
from src.report_generator import generate_excel_report, generate_text_report
from src.checklist_auto_fill import auto_fill_checklist, generate_checklist_summary
from src.checklist_reader import read_checklist_excel


class BRD_AIGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("BRD-AI: Cadence Allegro Constraint Generator")
        self.root.geometry("780x680")
        self.root.resizable(True, True)

        self.brd_path = tk.StringVar()
        self.checklist_path = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.factory = tk.StringVar(value="hongban")
        self.board_type = tk.StringVar(value="rigid")
        self.capability_level = tk.StringVar(value="standard")
        self.copper_um = tk.StringVar(value="35")
        self.copper_oz = tk.StringVar(value="1.0")
        self.net_list_var = tk.StringVar()

        self._build_ui()

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ---- Title ----
        title = ttk.Label(main_frame, text="BRD-AI: Cadence Allegro Constraint Generator",
                          font=("Arial", 14, "bold"))
        title.pack(pady=(0, 15))

        # ---- Input Files Frame ----
        input_frame = ttk.LabelFrame(main_frame, text="Input Files", padding=10)
        input_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(input_frame, text="BRD File:").grid(row=0, column=0, sticky=tk.W, pady=2)
        ttk.Entry(input_frame, textvariable=self.brd_path, width=60).grid(row=0, column=1, padx=5, pady=2)
        ttk.Button(input_frame, text="Browse...", command=self._browse_brd).grid(row=0, column=2, pady=2)

        ttk.Label(input_frame, text="Checklist Excel:").grid(row=1, column=0, sticky=tk.W, pady=2)
        ttk.Entry(input_frame, textvariable=self.checklist_path, width=60).grid(row=1, column=1, padx=5, pady=2)
        ttk.Button(input_frame, text="Browse...", command=self._browse_checklist).grid(row=1, column=2, pady=2)

        ttk.Label(input_frame, text="Output Dir:").grid(row=2, column=0, sticky=tk.W, pady=2)
        ttk.Entry(input_frame, textvariable=self.output_dir, width=60).grid(row=2, column=1, padx=5, pady=2)
        ttk.Button(input_frame, text="Browse...", command=self._browse_output).grid(row=2, column=2, pady=2)

        # ---- Options Frame ----
        opt_frame = ttk.LabelFrame(main_frame, text="Options", padding=10)
        opt_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(opt_frame, text="Factory:").grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
        factory_combo = ttk.Combobox(opt_frame, textvariable=self.factory,
                                     values=["hongban", "sihui"], state="readonly", width=8)
        factory_combo.grid(row=0, column=1, sticky=tk.W)
        factory_combo.bind("<<ComboboxSelected>>", lambda e: self._sync_level_combo())

        ttk.Label(opt_frame, text="Board Type:").grid(row=0, column=2, sticky=tk.W, padx=(20, 5))
        type_combo = ttk.Combobox(opt_frame, textvariable=self.board_type,
                                  values=["rigid", "flex"], state="readonly", width=8)
        type_combo.grid(row=0, column=3, sticky=tk.W)
        type_combo.bind("<<ComboboxSelected>>", lambda e: self._sync_level_combo())

        ttk.Label(opt_frame, text="Capability:").grid(row=0, column=4, sticky=tk.W, padx=(20, 5))
        self.cap_combo = ttk.Combobox(opt_frame, textvariable=self.capability_level,
                                      values=["standard", "automotive", "extreme"],
                                      state="readonly", width=10)
        self.cap_combo.grid(row=0, column=5, sticky=tk.W)
        self._sync_level_combo()

        ttk.Label(opt_frame, text="Copper(um):").grid(row=0, column=6, sticky=tk.W, padx=(20, 5))
        ttk.Entry(opt_frame, textvariable=self.copper_um, width=6).grid(row=0, column=7, sticky=tk.W, padx=(0, 10))
        ttk.Label(opt_frame, text="(oz):").grid(row=0, column=8, sticky=tk.W, padx=(5, 2))
        ttk.Entry(opt_frame, textvariable=self.copper_oz, width=6).grid(row=0, column=9, sticky=tk.W)

        self.autofill_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_frame, text="Auto-fill Checklist (自动填写Check和Approved列)",
                        variable=self.autofill_var).grid(
            row=1, column=0, columnspan=5, padx=2, pady=(6, 0), sticky=tk.W)

        ttk.Button(opt_frame, text="DFM 功能分析", command=self._open_dfm_dialog).grid(
            row=1, column=7, columnspan=2, padx=5, pady=(6, 0), sticky=tk.E)

        ttk.Label(opt_frame, text="Checker Name:").grid(row=2, column=0, sticky=tk.W, pady=(10, 0))
        self.checker_name = tk.StringVar(value="BRD-AI")
        ttk.Entry(opt_frame, textvariable=self.checker_name, width=15).grid(
            row=2, column=1, columnspan=2, sticky=tk.W, pady=(10, 0))

        ttk.Label(opt_frame, text="Net List (JSON):").grid(row=3, column=0, sticky=tk.W, pady=(10, 0))
        ttk.Entry(opt_frame, textvariable=self.net_list_var, width=60).grid(row=3, column=1, columnspan=6, padx=5, pady=(10, 0), sticky=tk.W)
        ttk.Button(opt_frame, text="Browse...", command=self._browse_nets).grid(row=3, column=7, pady=(10, 0))

        ttk.Label(opt_frame, text="(Optional: JSON file with net names instead of BRD)",
                  foreground="gray").grid(row=4, column=1, columnspan=4, sticky=tk.W)

        # ---- Buttons ----
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        self.run_btn = ttk.Button(btn_frame, text="Start Generation", command=self._run,
                                  width=20)
        self.run_btn.pack(side=tk.LEFT, padx=5)

        self.demo_btn = ttk.Button(btn_frame, text="Demo Run", command=self._run_demo,
                                   width=15)
        self.demo_btn.pack(side=tk.LEFT, padx=5)

        self.open_btn = ttk.Button(btn_frame, text="Open Output Folder", command=self._open_output,
                                   width=18)
        self.open_btn.pack(side=tk.LEFT, padx=5)

        # ---- Progress Bar ----
        self.progress = ttk.Progressbar(main_frame, mode="indeterminate")

        # ---- Log Output ----
        log_frame = ttk.LabelFrame(main_frame, text="Log", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self.log_text = tk.Text(log_frame, height=18, width=90, wrap=tk.WORD,
                                font=("Consolas", 10), bg="#1e1e1e", fg="#d4d4d4",
                                insertbackground="white")
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text.tag_configure("success", foreground="#4ec9b0")
        self.log_text.tag_configure("error", foreground="#f44747")
        self.log_text.tag_configure("warn", foreground="#dcdcaa")
        self.log_text.tag_configure("info", foreground="#569cd6")

        # ---- Status Bar ----
        self.status_var = tk.StringVar(value="Ready")
        status = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status.pack(fill=tk.X, pady=(5, 0))

        # ---- Set default output ----
        default_out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        self.output_dir.set(default_out)

    def _log(self, text, tag=None):
        self.log_text.insert(tk.END, text + "\n", tag)
        self.log_text.see(tk.END)
        self.root.update_idletasks()

    def _browse_brd(self):
        path = filedialog.askopenfilename(
            title="Select Allegro BRD File",
            filetypes=[("BRD files", "*.brd"), ("All files", "*.*")]
        )
        if path:
            self.brd_path.set(path)

    def _browse_checklist(self):
        path = filedialog.askopenfilename(
            title="Select Layout Checklist Excel",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")]
        )
        if path:
            self.checklist_path.set(path)

    def _browse_output(self):
        path = filedialog.askdirectory(title="Select Output Directory")
        if path:
            self.output_dir.set(path)

    def _browse_nets(self):
        path = filedialog.askopenfilename(
            title="Select Net List JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if path:
            self.net_list_var.set(path)

    def _sync_level_combo(self):
        from src.dfm_engine import list_levels, normalize_level
        levels = list_levels(self.board_type.get(), factory=self.factory.get()) or ["standard"]
        self.cap_combo.configure(values=levels)
        cur = normalize_level(self.capability_level.get())
        if cur not in levels:
            cur = levels[0]
        self.capability_level.set(cur)

    def _open_dfm_dialog(self):
        DFMDialog(self.root)

    def _open_output(self):
        out = self.output_dir.get()
        if out and os.path.exists(out):
            os.startfile(out)
        else:
            messagebox.showwarning("Warning", "Output directory does not exist. Run generation first.")

    def _run_demo(self):
        self.brd_path.set("")
        self.checklist_path.set("")
        self._run(use_demo=True)

    def _run(self, use_demo=False):
        if not use_demo:
            brd = self.brd_path.get().strip()
            nets_json = self.net_list_var.get().strip()
            if not brd and not nets_json:
                messagebox.showwarning("Warning", "Please select a BRD file or Net List JSON file.")
                return

        self.run_btn.config(state=tk.DISABLED)
        self.demo_btn.config(state=tk.DISABLED)
        self.progress.pack(fill=tk.X, pady=(5, 0))
        self.progress.start()
        self.log_text.delete(1.0, tk.END)
        self.status_var.set("Running...")

        thread = threading.Thread(
            target=self._execute_pipeline,
            args=(use_demo,),
            daemon=True,
        )
        thread.start()

    def _execute_pipeline(self, use_demo):
        try:
            self.root.after(0, lambda: self._log("=" * 60, "info"))
            self.root.after(0, lambda: self._log("  BRD-AI: Cadence Allegro Constraint Generator", "info"))
            self.root.after(0, lambda: self._log(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "info"))
            self.root.after(0, lambda: self._log("=" * 60, "info"))
            self.root.after(0, lambda: self._log(""))

            # Step 1: Read PCB
            self.root.after(0, lambda: self._log("[1/6] Reading PCB data...", "info"))
            if use_demo:
                from main import DEMO_NETS
                board_info = {
                    "board_name": "860-000776_PSE_Board (Demo)",
                    "file_path": "(demo)",
                    "file_size_mb": 0,
                    "nets": DEMO_NETS,
                    "net_count": len(DEMO_NETS),
                    "layers": [
                        {"name": "TOP", "type": "signal"},
                        {"name": "L2_GND", "type": "plane"},
                        {"name": "L3_SIG", "type": "signal"},
                        {"name": "L4_GND", "type": "plane"},
                        {"name": "L5_PWR", "type": "plane"},
                        {"name": "L6_SIG", "type": "signal"},
                        {"name": "L7_GND", "type": "plane"},
                        {"name": "BOTTOM", "type": "signal"},
                    ],
                    "layer_count": 8,
                    "power_nets": [],
                    "diff_pairs": [],
                }
            else:
                nets_json = self.net_list_var.get().strip()
                if nets_json:
                    with open(nets_json, "r", encoding="utf-8") as f:
                        net_list = json.load(f)
                    board_info = {
                        "board_name": os.path.basename(nets_json),
                        "file_path": nets_json,
                        "file_size_mb": 0,
                        "nets": net_list,
                        "net_count": len(net_list),
                        "layers": [],
                        "layer_count": 0,
                        "power_nets": [],
                        "diff_pairs": [],
                    }
                else:
                    brd = self.brd_path.get().strip()
                    board_info = extract_brd_info(brd)
                    if "error" in board_info:
                        self.root.after(0, lambda: self._log(f"  [ERROR] {board_info['error']}", "error"))
                        self._finish()
                        return

            self.root.after(0, lambda: self._log(f"  -> Nets: {board_info['net_count']}, Layers: {board_info['layer_count']}", "success"))
            self.root.after(0, lambda: self._log(""))

            # Step 2: Classify
            self.root.after(0, lambda: self._log("[2/6] Classifying signals...", "info"))
            classification = classify_and_export(board_info.get("nets", []))
            self.root.after(0, lambda: self._log(f"  -> Net Classes: {classification['total_classes']}", "success"))
            self.root.after(0, lambda: self._log(f"  -> Diff Pairs: {classification['total_diff_pairs']}", "success"))
            for cls_name, count in sorted(classification["summary"].items()):
                self.root.after(0, lambda n=cls_name, c=count: self._log(f"     {n:15s} {c:4d} nets"))
            self.root.after(0, lambda: self._log(""))

            # Step 3: Compute rules
            self.root.after(0, lambda: self._log("[3/6] Computing constraint rules...", "info"))
            constraints = compute_class_constraints(classification)
            self.root.after(0, lambda: self._log(f"  -> Physical: {len(constraints['physical_constraints'])}", "success"))
            self.root.after(0, lambda: self._log(f"  -> Spacing: {len(constraints['spacing_constraints'])}", "success"))
            self.root.after(0, lambda: self._log(f"  -> Electrical: {len(constraints['electrical_constraints'])}", "success"))
            self.root.after(0, lambda: self._log(f"  -> Via: {len(constraints['via_constraints'])}", "success"))
            self.root.after(0, lambda: self._log(""))

            # Step 4: DFM
            try:
                copper_um = float(self.copper_um.get() or 35)
            except ValueError:
                copper_um = 35.0
            try:
                copper_oz = float(self.copper_oz.get() or 0) or max(1.0, round(copper_um / 35.0, 1))
            except ValueError:
                copper_oz = max(1.0, round(copper_um / 35.0, 1))
            bt = self.board_type.get()
            capability = self.capability_level.get()
            factory = self.factory.get()
            self.root.after(0, lambda: self._log(
                f"[4/6] DFM check ({factory} {bt}-{capability}, copper {copper_um}um)...", "info"))
            dfm_result = validate_constraints(
                constraints, capability,
                board_type=bt, copper_um=copper_um, copper_oz=copper_oz,
                factory=factory)
            status = "PASS" if dfm_result["passed"] else "FAIL"
            tag = "success" if dfm_result["passed"] else "error"
            self.root.after(0, lambda: self._log(f"  -> Result: {status}", tag))
            for err in dfm_result.get("errors", []):
                self.root.after(0, lambda e=err: self._log(f"     [ERROR] {e}", "error"))
            for warn in dfm_result.get("warnings", []):
                self.root.after(0, lambda w=warn: self._log(f"     [WARN]  {w}", "warn"))
            self.root.after(0, lambda: self._log(""))

            # Step 5: Generate SKILL
            self.root.after(0, lambda: self._log("[5/6] Generating SKILL script...", "info"))
            out_dir = self.output_dir.get()
            skill_path = os.path.join(out_dir, "auto_constraint.il")
            skill_script = generate_skill_script(
                constraints=constraints,
                signal_classification=classification,
                board_name=board_info.get("board_name", "Untitled"),
                capability_level=capability,
                output_path=skill_path,
            )
            self.root.after(0, lambda: self._log(f"  -> Output: {skill_path}", "success"))
            self.root.after(0, lambda: self._log(f"  -> Size: {len(skill_script)} chars", "success"))
            self.root.after(0, lambda: self._log(""))

            # Step 6: Reports
            self.root.after(0, lambda: self._log("[6/6] Generating reports...", "info"))
            xlsx_path = generate_excel_report(
                constraints=constraints,
                signal_classification=classification,
                dfm_result=dfm_result,
                board_info=board_info,
                output_path=os.path.join(out_dir, "constraint_report.xlsx"),
            )
            self.root.after(0, lambda: self._log(f"  -> Excel: {xlsx_path}", "success"))

            txt_path = generate_text_report(
                constraints=constraints,
                signal_classification=classification,
                dfm_result=dfm_result,
                board_info=board_info,
                output_path=os.path.join(out_dir, "constraint_report.txt"),
            )
            self.root.after(0, lambda: self._log(f"  -> Text:  {txt_path}", "success"))

            json_path = os.path.join(out_dir, "constraint_data.json")
            os.makedirs(out_dir, exist_ok=True)
            export_data = {
                "board_info": {k: v for k, v in board_info.items() if k not in ("nets",)},
                "classification": classification,
                "constraints": {
                    "physical": [
                        {k: v for k, v in c.items() if k != "nets"}
                        for c in constraints.get("physical_constraints", [])
                    ],
                    "spacing": constraints.get("spacing_constraints", []),
                    "electrical": constraints.get("electrical_constraints", []),
                    "via": constraints.get("via_constraints", []),
                },
                "dfm": dfm_result,
            }
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False, default=str)
            self.root.after(0, lambda: self._log(f"  -> JSON:  {json_path}", "success"))

            # Step 7: Auto-fill Checklist (if enabled)
            checklist_xlsx = self.checklist_path.get().strip()
            do_autofill = self.autofill_var.get()
            if do_autofill and checklist_xlsx and os.path.exists(checklist_xlsx):
                self.root.after(0, lambda: self._log(""))
                self.root.after(0, lambda: self._log("[7/7] Auto-filling Checklist...", "info"))
                filled_path = os.path.join(out_dir, os.path.basename(checklist_xlsx).replace(".xlsx", "_filled.xlsx"))
                checker = self.checker_name.get().strip() or "BRD-AI"
                output_filled, stats = auto_fill_checklist(
                    checklist_path=checklist_xlsx,
                    brd_info=board_info,
                    classification=classification,
                    constraints=constraints,
                    dfm_result=dfm_result,
                    output_path=filled_path,
                    checker_name=checker,
                )
                self.root.after(0, lambda: self._log(f"  -> Output: {output_filled}", "success"))
                self.root.after(0, lambda: self._log(f"  -> Auto PASS: {stats['auto_pass']}, Manual: {stats['manual']}, N/A: {stats['na']}", "success"))
                auto_rate = stats['auto_pass'] / max(stats['total'], 1) * 100
                self.root.after(0, lambda: self._log(f"  -> Auto Rate: {auto_rate:.1f}%", "success"))

            self.root.after(0, lambda: self._log(""))
            self.root.after(0, lambda: self._log("=" * 60, "info"))
            self.root.after(0, lambda: self._log("  Execution Complete!", "success"))
            self.root.after(0, lambda: self._log("=" * 60, "info"))
            self.root.after(0, lambda: self._log(""))
            self.root.after(0, lambda: self._log("  Next Steps:", "info"))
            self.root.after(0, lambda: self._log(f"  1. Review: {xlsx_path}", "info"))
            self.root.after(0, lambda: self._log(f'  2. Load in Allegro: skill load "{skill_path}"', "info"))
            self.root.after(0, lambda: self._log(""))

            self.root.after(0, lambda: self.status_var.set("Done!"))

        except Exception as e:
            import traceback
            self.root.after(0, lambda: self._log(f"[ERROR] {str(e)}", "error"))
            self.root.after(0, lambda: self._log(traceback.format_exc(), "error"))
            self.root.after(0, lambda: self.status_var.set("Error"))
        finally:
            self._finish()

    def _finish(self):
        self.progress.stop()
        self.progress.pack_forget()
        self.run_btn.config(state=tk.NORMAL)
        self.demo_btn.config(state=tk.NORMAL)

    def run(self):
        self.root.mainloop()


DFM_FIELDS = [
    ("拼板/板尺寸", "board_size_x", "板尺寸X (mm)", "100"),
    ("拼板/板尺寸", "board_size_y", "板尺寸Y (mm)", "80"),
    ("线路", "min_line_width", "最小线宽 (mm)", "0.10"),
    ("线路", "min_spacing", "最小线距 (mm)", "0.10"),
    ("线路", "copper_um", "成品铜厚 (um)", "35"),
    ("线路", "pth_to_pth_spacing", "不同网络孔壁间距 (mm)", "0.3"),
    ("线路", "copper_to_edge_inner", "内层线路到板边 (mm)", "0.25"),
    ("线路", "copper_to_edge_outer", "外层线路到板边 (mm)", "0.20"),
    ("钻孔", "min_mech_drill", "最小机械钻孔 (mm)", "0.2"),
    ("钻孔", "max_mech_drill", "最大机械钻孔 (mm)", ""),
    ("钻孔", "min_laser_via", "最小激光孔 (mm)", ""),
    ("钻孔", "min_annular_ring", "单边孔环 (mm)", "0.076"),
    ("钻孔", "board_thickness", "板厚 (mm)", "1.6"),
    ("钻孔", "npth_to_edge", "NPTH孔到板边 (mm)", "0.5"),
    ("阻焊", "solder_mask_bridge", "阻焊桥 (mm)", "0.10"),
    ("阻焊", "solder_mask_opening", "阻焊开窗 (mm)", "0.10"),
    ("塞孔", "plug_hole_diameter", "塞孔孔径 (mm)", ""),
    ("丝印", "silkscreen_width", "丝印线宽 (mm)", "0.15"),
    ("丝印", "silkscreen_clearance", "丝印间隙 (mm)", "0.15"),
    ("覆盖膜(软板)", "coverlay_spacing", "覆盖膜开窗间距 (mm)", "0.5"),
    ("覆盖膜(软板)", "coverlay_to_pad", "开窗到焊盘 (mm)", "0.3"),
    ("覆盖膜(软板)", "coverlay_to_trace", "开窗到线路 (mm)", "0.15"),
    ("测试", "test_pad_2wire", "电测PAD二线 (mm)", "0.4"),
]


class DFMDialog:
    """DFM 功能分析对话框: 设计参数 -> 板厂制程能力比对 (支持红版/四会)"""

    def __init__(self, master):
        from src.dfm_engine import analyze_design, list_levels, level_label
        self._analyze = analyze_design
        self._level_label = level_label

        self.win = tk.Toplevel(master)
        self.win.title("DFM 功能分析 (制程能力校验)")
        self.win.geometry("900x640")

        top = ttk.Frame(self.win, padding=8)
        top.pack(fill=tk.X)

        ttk.Label(top, text="板厂:").pack(side=tk.LEFT)
        self.factory = tk.StringVar(value="hongban")
        self.factory_combo = ttk.Combobox(top, textvariable=self.factory,
                                          values=["hongban", "sihui"],
                                          state="readonly", width=8)
        self.factory_combo.pack(side=tk.LEFT, padx=(2, 12))
        self.factory_combo.bind("<<ComboboxSelected>>", lambda e: self._sync_levels())

        ttk.Label(top, text="板类型:").pack(side=tk.LEFT)
        self.board_type = tk.StringVar(value="rigid")
        self.type_combo = ttk.Combobox(top, textvariable=self.board_type,
                                       values=["rigid", "flex"], state="readonly", width=8)
        self.type_combo.pack(side=tk.LEFT, padx=(2, 12))
        self.type_combo.bind("<<ComboboxSelected>>", lambda e: self._sync_levels())

        ttk.Label(top, text="能力等级:").pack(side=tk.LEFT)
        self.level = tk.StringVar(value="standard")
        self.level_combo = ttk.Combobox(top, textvariable=self.level,
                                        values=["standard", "automotive", "extreme"],
                                        state="readonly", width=10)
        self.level_combo.pack(side=tk.LEFT, padx=(2, 12))
        self._sync_levels()

        ttk.Label(top, text="铜厚(oz):").pack(side=tk.LEFT)
        self.copper_oz = tk.StringVar(value="1.0")
        ttk.Entry(top, textvariable=self.copper_oz, width=5).pack(side=tk.LEFT, padx=(2, 12))

        ttk.Button(top, text="执行分析", command=self._run).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="关闭", command=self.win.destroy).pack(side=tk.RIGHT)

        body = ttk.Frame(self.win, padding=8)
        body.pack(fill=tk.BOTH, expand=True)

        left = ttk.LabelFrame(body, text="设计参数 (留空项跳过)", padding=6)
        left.pack(side=tk.LEFT, fill=tk.Y)

        canvas = tk.Canvas(left, width=250, height=520, highlightthickness=0)
        vsb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor=tk.NW)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.entries = {}
        current_cat = None
        for i, (cat, key, label, default) in enumerate(DFM_FIELDS):
            if cat != current_cat:
                current_cat = cat
                ttk.Label(inner, text=f"── {cat} ──", font=("Arial", 9, "bold"),
                          foreground="#0a5").grid(row=i, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))
                continue
            ttk.Label(inner, text=label).grid(row=i, column=0, sticky=tk.W, pady=1)
            var = tk.StringVar(value=default)
            ttk.Entry(inner, textvariable=var, width=10).grid(row=i, column=1, padx=4, pady=1)
            self.entries[key] = var

        right = ttk.LabelFrame(body, text="分析结果", padding=6)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))

        cols = ("status", "category", "message")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", height=22)
        self.tree.heading("status", text="结果")
        self.tree.heading("category", text="类别")
        self.tree.heading("message", text="详情")
        self.tree.column("status", width=55, anchor=tk.CENTER)
        self.tree.column("category", width=70, anchor=tk.CENTER)
        self.tree.column("message", width=520)
        tsb = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("pass", foreground="#0a7a3a")
        self.tree.tag_configure("warn_limit", foreground="#b8860b")
        self.tree.tag_configure("fail", foreground="#c00000")
        self.tree.tag_configure("info", foreground="#666666")

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self.win, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)

    def _sync_levels(self):
        from src.dfm_engine import list_levels
        levels = list_levels(self.board_type.get(), factory=self.factory.get()) or ["standard"]
        self.level_combo.configure(values=levels)
        if self.level.get() not in levels:
            self.level.set(levels[0])

    def _run(self):
        design = {}
        for key, var in self.entries.items():
            txt = var.get().strip()
            if not txt:
                continue
            try:
                design[key] = float(txt)
            except ValueError:
                messagebox.showwarning("参数错误", f"参数 {key} 不是有效数字: {txt}")
                return
        if "board_size_x" in design and "board_size_y" in design:
            design["board_size"] = [design.pop("board_size_x"), design.pop("board_size_y")]
        try:
            copper_oz = float(self.copper_oz.get() or 1.0)
        except ValueError:
            copper_oz = 1.0
        design["copper_oz"] = copper_oz

        result = self._analyze(design, board_type=self.board_type.get(),
                               capability_level=self.level.get(),
                               factory=self.factory.get())

        for row in self.tree.get_children():
            self.tree.delete(row)
        icon = {"pass": "PASS", "warn_limit": "WARN", "fail": "FAIL", "info": "INFO"}
        for it in result["items"]:
            msg = it["message"]
            if it.get("note"):
                msg += f"  [{it['note']}]"
            self.tree.insert("", tk.END,
                             values=(icon[it["status"]], it["category"], msg),
                             tags=(it["status"],))
        verdict = "DFM PASS" if result["passed"] else "DFM FAIL"
        self.status_var.set(
            f"{result['factory_label']} {result['board_type_label']} - {result['capability_label']} | "
            f"{verdict} (错误 {result['error_count']}, 警告 {result['warning_count']}, "
            f"共 {len(result['items'])} 项)")


def main():
    app = BRD_AIGUI()
    app.run()


if __name__ == "__main__":
    main()
"""Exercise event-loop responsiveness and stale completion rejection."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.smt_engine import load_smt_profile


class WorkbenchUI(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from src.ui.dfm_dialog import DFMDialog
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.prefs = patch("src.ui.prefs.load_prefs", return_value={})
        self.prefs.start()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.dialog = DFMDialog(self.root, standalone=True)

    def tearDown(self):
        if hasattr(self, "dialog"):
            self.dialog._close()
            self.prefs.stop()

    def wait(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)
        self.assertTrue(condition(), "Background operation timed out")
        self.assertFalse(self.errors, self.errors)

    def profile(self):
        path = Path(__file__).resolve().parents[1] / "config" / "suppliers" / "smt" / "smt_capability_lijing.yaml"
        if not path.is_file():
            self.skipTest("本地供应商能力配置缺失: config/suppliers/smt/smt_capability_lijing.yaml")
        doc = load_smt_profile(path)
        # Isolated UI fixture, not the deleted historical production template.
        doc["profiles"] = {"standard": {"rules": [dict(id="TEST-SPACING", label="器件间距",
            field="min_component_spacing", scope="design", unit="mm", op=">=", value=None, severity="high")]}}
        self.dialog.smt._apply_profile(doc)

    def import_board(self, board=None):
        board = board or Path(__file__).resolve().parents[1] / "demo" / "DEMO_BOARD.brd"
        self.dialog.brd_path.set(str(board))
        self.dialog.auto_gerber.set(False)
        self.dialog.auto_manufacturing.set(False)
        with patch("src.ui.dfm_dialog.extract_brd_design", return_value=dict(
                ok=True, layers=["TOP", "BOTTOM"], layer_count=2)), \
                patch.object(self.dialog, "_refresh_component_coordinates"):
            self.dialog._load_from_brd()
            self.wait(lambda: bool(self.dialog._loaded_path))
        self.assertEqual(str(self.dialog.run_button.cget("state")), "normal")
        # PCB analysis may no longer be bypassed: use real, isolated synthetic films.
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        film = "%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,1*%\nD10*\nX5000000Y5000000D03*\nM02*\n"
        for suffix in ("gtl", "gbl", "gts", "gbs"):
            (Path(directory.name) / f"board.{suffix}").write_text(film)
        self.dialog.gerber_dir.set(directory.name)

    def test_geometry_runs_with_checkbox_off_and_with_checkbox_on(self):
        self.import_board()
        folder = self.dialog.gerber_dir.get()
        from src.gerber_analysis import analyze_gerbers
        for checked in (False, True):
            self.dialog.auto_gerber.set(checked)
            with patch("src.pcb_geometry_data.ensure_geometry_folder", return_value=dict(
                    directory=folder, warnings=False, source="test")) as source, \
                    patch("src.gerber_analysis.analyze_gerbers", wraps=analyze_gerbers) as geometry:
                self.dialog._run()
                self.wait(lambda: not self.dialog.tasks.pending)
                source.assert_called_once()
                self.assertEqual(source.call_args.kwargs["force_export"], checked)
                geometry.assert_called_once()
                self.assertTrue(self.dialog.last_result["geometry_executed"])
                self.assertTrue(self.dialog.results.geometry_tree.get_children())
                self.assertFalse(self.dialog.last_result["releasable"])

    def test_odb_objects_run_in_primary_pcb_workflow(self):
        self.import_board()
        from tools.test_pcb_object_checks import odb_job
        from src.odb_dfm import analyze_odb
        with tempfile.TemporaryDirectory() as folder:
            odb = Path(folder)/"job.zip"
            odb_job(odb)
            self.dialog.odb_path.set(str(odb))
            self.dialog.auto_gerber.set(True)
            with patch("src.pcb_geometry_data.ensure_geometry_folder") as gerber, \
                    patch("src.odb_dfm.analyze_odb", wraps=analyze_odb) as engine:
                self.dialog._run()
                self.wait(lambda: not self.dialog.tasks.pending, timeout=8)
                gerber.assert_not_called()
                engine.assert_called_once()
                result = self.dialog.last_result
                self.assertEqual(result["odb_object_result"]["object_counts"]["verified_pads"], 2)
                self.assertTrue(result["problem_locations"])
                self.assertTrue(Path(result["gerber_preview"]).is_file())
                self.assertFalse(result["releasable"])
            self.dialog.odb_step.set("other")
            self.assertIsNone(self.dialog.last_result)

    def test_one_click_native_pipeline_captures_inputs_and_runs_worker(self):
        self.import_board()
        self.dialog.auto_manufacturing.set(True)
        self.dialog.odb_exporter.set("configured-brd2odb.exe")
        from src.dfm_engine import analyze_design
        threads = []
        def compute(brd_path, design, **kwargs):
            threads.append(threading.get_ident())
            self.assertEqual(kwargs["odb_exporter"], "configured-brd2odb.exe")
            kwargs["progress"]("drill")
            result = analyze_design(design)
            result["geometry_executed"] = True
            return result
        with patch("src.manufacturing_pipeline.analyze_brd_manufacturing", side_effect=compute) as pipeline, \
                patch("src.pcb_geometry_data.ensure_geometry_folder") as manual:
            self.dialog._run()
            self.wait(lambda: not self.dialog.tasks.pending)
            pipeline.assert_called_once()
            manual.assert_not_called()
            self.assertNotEqual(threads[0], threading.get_ident())
            self.assertTrue(self.dialog.last_result["geometry_executed"])

    def test_geometry_source_failure_never_falls_back_to_parameter_only(self):
        self.import_board()
        self.dialog.gerber_dir.set("")
        with patch("src.gerber_export.export_brd_gerbers", side_effect=ValueError("no artwork")) as export, \
                patch("src.ui.dfm_dialog.messagebox.showerror") as error, \
                patch("src.services.pcb_analysis.analyze_design") as parameter:
            self.dialog._run()
            self.wait(lambda: not self.dialog.tasks.pending)
            export.assert_called_once()
            parameter.assert_not_called()
            error.assert_called_once()
            self.assertIsNone(self.dialog.last_result)
            self.assertEqual(self.dialog._pcb_state, "error")

    def test_failed_rerun_clears_old_pcb_result(self):
        self.import_board()
        self.dialog._run()
        self.wait(lambda: not self.dialog.tasks.pending)
        self.assertIsNotNone(self.dialog.last_result)
        with patch("src.pcb_geometry_data.ensure_geometry_folder", side_effect=ValueError("资料失效")), \
                patch("src.ui.dfm_dialog.messagebox.showerror"):
            self.dialog._run()
            self.wait(lambda: not self.dialog.tasks.pending)
        self.assertIsNone(self.dialog.last_result)
        self.assertEqual(self.dialog._pcb_state, "error")

    def test_no_brd_disables_both_analysis_and_reports(self):
        self.profile()
        self.dialog.params.entries["min_line_width"].set("0.1")
        self.dialog.gerber_dir.set("gerber-only")
        self.assertEqual(str(self.dialog.run_button.cget("state")), "disabled")
        self.assertEqual(str(self.dialog.smt.run_button.cget("state")), "disabled")
        self.assertTrue(all(str(button.cget("state")) == "disabled"
                            for button in self.dialog.report_buttons.values()))
        # Unified PCB GUI keeps shared settings visible even before import;
        # analysis/report buttons still require successfully imported sources.
        self.assertTrue(self.dialog.params.design_frame.winfo_manager())
        self.assertFalse(self.dialog.params.empty_hint.winfo_manager())
        with patch("src.ui.dfm_dialog.messagebox.showwarning"), \
                patch("src.ui.smt_panel.messagebox.showwarning"), \
                patch("src.services.pcb_analysis.analyze_design") as pcb, \
                patch("src.ui.smt_panel.analyze_smt") as smt:
            self.dialog._run()
            self.dialog.smt.run()
            self.assertFalse(self.dialog.tasks.pending)
            pcb.assert_not_called()
            smt.assert_not_called()

    def test_path_text_without_successful_read_is_not_import(self):
        self.dialog.brd_path.set(str(Path(__file__).resolve().parents[1] / "demo" / "DEMO_BOARD.brd"))
        self.assertEqual(str(self.dialog.run_button.cget("state")), "disabled")
        with self.assertRaises(ValueError):
            self.dialog._smt_design()

    def test_successful_import_then_path_clear_removes_all_board_data(self):
        self.import_board()
        self.profile()
        self.assertEqual(str(self.dialog.smt.run_button.cget("state")), "normal")
        self.assertTrue(self.dialog.params.design_frame.winfo_manager())
        self.dialog.params.pads = {"ok": True}
        self.dialog.params.components = {"ok": True}
        self.dialog.coordinate_dir.set("old-board-coordinates")
        self.dialog.gerber_dir.set("old-board-gerber")
        self.dialog.last_result = {"items": []}
        self.dialog.brd_path.set("")
        self.assertIsNone(self.dialog.brd_info)
        self.assertIsNone(self.dialog.last_result)
        self.assertFalse(self.dialog._loaded_signature)
        self.assertEqual(self.dialog.params.pads, {})
        self.assertEqual(self.dialog.params.components, {})
        self.assertEqual(self.dialog.coordinate_dir.get(), "")
        self.assertEqual(self.dialog.gerber_dir.get(), "")
        self.assertEqual(str(self.dialog.run_button.cget("state")), "disabled")
        self.assertEqual(str(self.dialog.smt.run_button.cget("state")), "disabled")

    def test_failed_brd_return_value_does_not_enable_analysis(self):
        board = Path(__file__).resolve().parents[1] / "demo" / "DEMO_BOARD.brd"
        self.dialog.brd_path.set(str(board))
        with patch("src.ui.dfm_dialog.extract_brd_design", return_value={"ok": False}):
            self.dialog._load_from_brd()
            self.assertEqual(str(self.dialog.run_button.cget("state")), "disabled")
            self.wait(lambda: not self.dialog._loading)
        self.assertIn("BRD 结构未通过验证", self.dialog._brd_error)
        self.assertIsNone(self.dialog.brd_info)
        self.assertFalse(self.dialog._loaded_path)
        self.assertEqual(str(self.dialog.run_button.cget("state")), "disabled")

    def test_empty_or_non_brd_file_never_starts_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, data in (("empty.brd", b""), ("not-a-board.txt", b"data")):
                path = Path(directory)/name
                path.write_bytes(data)
                self.dialog.brd_path.set(str(path))
                with patch.object(self.dialog, "_read_brd_worker") as worker:
                    self.dialog._load_from_brd()
                    worker.assert_not_called()
                self.assertEqual(str(self.dialog.run_button.cget("state")), "disabled")

    def test_late_load_result_cannot_enable_changed_path(self):
        from src.ui.brd_input import brd_signature
        board = Path(__file__).resolve().parents[1] / "demo" / "DEMO_BOARD.brd"
        self.dialog.brd_path.set(str(board))
        with patch.object(self.dialog, "_read_brd_worker"):
            self.dialog._load_from_brd()
        token = self.dialog._load_token
        self.dialog.brd_path.set("")
        self.dialog._brd_queue.put((token, str(board), "ok", dict(ok=True,
            layer_count=2, layers=["TOP", "BOTTOM"], _import_signature=brd_signature(str(board)))))
        self.root.update()
        self.wait(lambda: self.dialog._brd_queue.empty())
        self.assertFalse(self.dialog._loaded_path)
        self.assertFalse(self.dialog._loading)
        self.assertEqual(str(self.dialog.run_button.cget("state")), "disabled")

    def test_changed_file_requires_reimport_and_invalidates_results(self):
        with tempfile.TemporaryDirectory() as directory:
            board = Path(directory)/"test.brd"
            board.write_bytes(b"mock board")
            self.import_board(board)
            self.dialog.last_result = {"items": []}
            board.write_bytes(b"different mock board version")
            with self.assertRaises(ValueError):
                self.dialog._smt_design()
            self.root.after_cancel(self.dialog._ui_poll_id)
            self.dialog._refresh_ui_labels()
            self.assertIsNone(self.dialog.last_result)
            self.assertIsNone(self.dialog.brd_info)
            self.assertEqual(str(self.dialog.run_button.cget("state")), "disabled")

    def test_smt_completion_after_clearing_brd_stays_disabled(self):
        from src.smt_engine import analyze_smt
        self.import_board()
        self.profile()
        def slow(*args):
            time.sleep(.15)
            return analyze_smt(*args)
        with patch("src.ui.smt_panel.analyze_smt", side_effect=slow):
            self.dialog.smt.run()
            self.dialog.brd_path.set("")
            self.wait(lambda: not self.dialog.tasks.pending)
        self.assertIsNone(self.dialog.smt.last_result)
        self.assertEqual(str(self.dialog.smt.run_button.cget("state")), "disabled")

    def test_pcb_keeps_event_loop_alive_and_rejects_stale_result(self):
        self.import_board()
        from src.dfm_engine import analyze_design
        ui_thread = threading.get_ident()
        worker_threads = []

        def slow(design, **options):
            worker_threads.append(threading.get_ident())
            time.sleep(0.25)
            return analyze_design(design, **options)

        ticks = []
        beat_id = [None]
        def beat():
            ticks.append(time.monotonic())
            if self.dialog._alive:
                beat_id[0] = self.root.after(10, beat)
        beat()
        with patch("src.services.pcb_analysis.analyze_design", side_effect=slow):
            self.assertEqual(self.dialog.task_dot.itemcget(self.dialog._dot, "fill"), "#94A3B8")
            self.dialog._run()
            self.assertEqual(self.dialog.task_dot.itemcget(self.dialog._dot, "fill"), "#2563EB")
            self.assertEqual(self.dialog.progress.cget("style"), "Running.Horizontal.TProgressbar")
            self.root.after(30, lambda: self.dialog.params.board_name.set("NEW"))
            self.wait(lambda: not self.dialog.tasks.pending)
            self.assertIsNone(self.dialog.last_result)
            self.assertGreater(len(ticks), 8)
            self.assertNotEqual(worker_threads[0], ui_thread)
            self.dialog._run()
            self.wait(lambda: self.dialog.last_result is not None)
            self.assertEqual(self.dialog.task_var.get(), "PCB 分析完成")
            self.assertEqual(self.dialog.progress.cget("style"), "Complete.Horizontal.TProgressbar")
        self.root.after_cancel(beat_id[0])

    def test_smt_run_and_invalidation(self):
        self.import_board()
        self.profile()
        self.dialog.smt.doc["profiles"]["standard"]["rules"][0]["value"] = 0.5
        self.dialog.params.entries["min_component_spacing"].set("0.4")
        self.dialog.smt.run()
        self.wait(lambda: self.dialog.smt.last_result is not None)
        self.assertEqual(self.dialog.smt.last_result["items"][0]["status"], "fail")
        self.dialog.params.entries["min_component_spacing"].set("0.6")
        self.assertIsNone(self.dialog.smt.last_result)
        self.assertEqual(set(self.dialog.report_buttons), {"dfm", "pcb", "smt"})

    def test_export_uses_frozen_snapshot_in_worker(self):
        self.import_board()
        self.profile()
        self.dialog._run()
        self.dialog.smt.run()
        self.wait(lambda: not self.dialog.tasks.pending)
        self.assertIsNotNone(self.dialog.last_result)
        captured = []
        ui_thread = threading.get_ident()

        def slow_export(kind, path, pcb, smt, meta):
            time.sleep(0.2)
            captured.append((kind, meta["board_name"], pcb is not None,
                             smt is not None, threading.get_ident()))
            return [path]

        with patch("src.ui.dfm_dialog.filedialog.asksaveasfilename", return_value="report.xlsx"), \
                patch("src.ui.dfm_dialog.export_report", side_effect=slow_export), \
                patch("src.ui.dfm_dialog.messagebox.showinfo"):
            self.dialog._export("dfm")
            self.dialog.params.board_name.set("changed-after-export")
            self.wait(lambda: not self.dialog.tasks.pending)
        self.assertEqual(captured[0][:4], ("dfm", "", True, True))
        self.assertNotEqual(captured[0][4], ui_thread)

    def test_export_default_filename_follows_brd_name(self):
        self.import_board()
        self.profile()
        self.dialog.last_result = dict(items=[], verdict="需复核（未满足放行条件）")
        self.dialog.last_design = {}
        self.dialog.smt.last_result = dict(items=[])
        self.dialog.output_pcb_report.set(True)
        brd = Path(self.dialog.brd_path.get())
        for kind, label in (("pcb", "PCB报告"), ("dfm", "DFM综合报告"), ("smt", "SMT报告")):
            with patch("src.ui.dfm_dialog.filedialog.asksaveasfilename", return_value="") as save:
                self.dialog._export(kind)
            kwargs = save.call_args.kwargs
            self.assertEqual(kwargs["initialfile"], f"{brd.stem}_{label}.xlsx")
            self.assertEqual(kwargs["initialdir"], str(brd.parent))
        self.assertFalse(self.dialog.tasks.pending)  # 取消保存不应启动导出

    def test_toplevel_callback_error_does_not_stop_task_polling(self):
        import tkinter as tk
        from src.ui.tasks import TaskRunner
        child = tk.Toplevel(self.root)
        runner = TaskRunner(child)
        handled = []
        self.root.report_callback_exception = lambda *args: handled.append(args)

        def broken(_value):
            raise ValueError("callback failure")

        runner.submit("broken", lambda: 1, broken, broken)
        self.wait(lambda: not runner.pending)
        self.assertEqual(len(handled), 1)
        completed = []
        runner.submit("next", lambda: 2, completed.append, broken)
        self.wait(lambda: bool(completed))
        self.assertEqual(completed, [2])
        runner.close()
        child.destroy()

    def test_problem_double_click_and_removed_header_buttons(self):
        from src.dfm_engine import analyze_design
        result = analyze_design({"min_mech_drill": 0.1})
        item = next(it for it in result["items"] if it.get("field") == "min_mech_drill")
        point = dict(item=item["item"], field="min_mech_drill", x_mm=1, y_mm=2,
                     x_brd=1, y_brd=2, brd_unit="mm", command="x 1 2")
        result["problem_locations"] = [point]
        self.dialog.results.render_capability(result)
        chosen = []
        self.dialog.results._on_open_point = chosen.append
        index = result["items"].index(item)
        self.dialog.tree.selection_set(self.dialog.tree.get_children()[index])
        self.dialog.results._locate_capability()
        self.assertEqual(chosen, [point])
        self.assertEqual(self.dialog.results.nb.index("current"), 1)
        self.root.update()
        self.assertEqual(str(self.dialog.results.open_point_button.cget("state")), "normal")

        def button_texts(widget):
            labels = []
            if widget.winfo_class() == "TButton":
                labels.append(widget.cget("text"))
            for child in widget.winfo_children():
                labels.extend(button_texts(child))
            return labels

        labels = button_texts(self.root)
        self.assertNotIn("Checklist", labels)
        self.assertNotIn("器件统计", labels)
        self.assertIn("打开 BRD", labels)

    def test_component_coordinates_visible_filterable_and_locatable(self):
        rows = [dict(refdes="U1", item="器件 U1", x_mm=-2.54, y_mm=0, x_brd=-100, y_brd=0,
                     brd_unit="mil", side="TOP", command="x -100 0"),
                dict(refdes="R1", item="器件 R1", x_mm=1, y_mm=2, brd_unit="mm", side="BOTTOM")]
        panel = self.dialog.results
        panel.show_components(rows)
        self.assertEqual(len(panel.component_tree.get_children()), 2)
        self.assertEqual(panel.component_tree.item("0", "values")[1:3], ("-2.54", "0"))
        chosen = []
        panel._on_open_point = chosen.append
        panel.component_tree.selection_set("0")
        panel._open_component()
        self.assertEqual(chosen, [rows[0]])
        panel.component_filter.set("U1")
        self.assertEqual(panel.component_tree.get_children(), ("0",))
        panel.clear(clear_components=False)
        self.assertEqual(panel.component_tree.get_children(), ("0",))
        with patch.object(self.dialog, "_open_export_editor") as extract:
            self.dialog._open_brd_editor()
            extract.assert_called_once()

    def test_language_theme_and_optional_pcb_report(self):
        from src.ui.theme import COLORS
        self.assertEqual(str(self.dialog.report_buttons["pcb"].cget("state")), "disabled")
        self.dialog.output_pcb_report.set(True)
        self.assertEqual(str(self.dialog.report_buttons["pcb"].cget("state")), "disabled")
        self.import_board()
        from src.dfm_engine import analyze_design
        self.dialog.last_result = analyze_design({})
        self.dialog._tasks_changed(())
        self.assertEqual(str(self.dialog.report_buttons["pcb"].cget("state")), "normal")
        with patch("src.ui.prefs.save_prefs"):
            self.dialog.language.set("English")
            self.dialog._apply_language()
            self.assertEqual(self.dialog.run_button.cget("text"), "Run PCB analysis")
            self.dialog.color_mode.set("Dark")
            self.dialog._apply_color_mode()
            self.assertEqual(COLORS["bg"], "#111827")
            self.dialog.language.set("中文")
            self.dialog._apply_language()
            self.assertEqual(self.dialog.run_button.cget("text"), "执行 PCB 分析")
            self.dialog.color_mode.set("Light")
            self.dialog._apply_color_mode()


if __name__ == "__main__":
    unittest.main()

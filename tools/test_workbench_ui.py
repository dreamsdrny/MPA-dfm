"""Exercise event-loop responsiveness and stale completion rejection."""
import sys
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
        doc = load_smt_profile(Path(__file__).resolve().parents[1] / "config" / "smt_capability_template.yaml")
        self.dialog.smt._apply_profile(doc)

    def test_pcb_keeps_event_loop_alive_and_rejects_stale_result(self):
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
        with patch("src.ui.dfm_dialog.analyze_design", side_effect=slow):
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
        from src.ui import i18n
        self.assertEqual(str(self.dialog.report_buttons["pcb"].cget("state")), "disabled")
        self.dialog.output_pcb_report.set(True)
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

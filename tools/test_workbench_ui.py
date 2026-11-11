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
            self.dialog._run()
            self.root.after(30, lambda: self.dialog.params.board_name.set("NEW"))
            self.wait(lambda: not self.dialog.tasks.pending)
            self.assertIsNone(self.dialog.last_result)
            self.assertGreater(len(ticks), 8)
            self.assertNotEqual(worker_threads[0], ui_thread)
            self.dialog._run()
            self.wait(lambda: self.dialog.last_result is not None)
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


if __name__ == "__main__":
    unittest.main()

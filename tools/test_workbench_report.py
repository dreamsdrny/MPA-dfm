"""Report integration tests: complete content, no lost detail links, no false PASS."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.brd_design import extract_brd_design
from src.dfm_inputs import brd_design_values
from src.dfm_engine import analyze_design
from src.smt_engine import analyze_smt, load_smt_profile
from src.workbench_report import build_combined_workbook, combined_verdict, export_report

ROOT = Path(__file__).resolve().parents[1]


class Reports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        info = extract_brd_design(str(ROOT / "demo" / "DEMO_BOARD.brd"))
        values, sources = brd_design_values(info)
        values["_sources"] = sources
        cls.pcb = analyze_design(values)
        cls.smt = analyze_smt(load_smt_profile(ROOT / "config" / "smt_capability_template.yaml"),
                              "standard", {})

    def test_combined_preserves_both_sections_and_links(self):
        wb = build_combined_workbook(self.pcb, self.smt, dict(board_name="TEST-BOARD"))
        self.assertEqual(wb.sheetnames[0], "DFM总览")
        self.assertIn("SMT_逐项比较", wb.sheetnames)
        self.assertIn("PCB_逐项能力", wb.sheetnames)
        self.assertEqual(wb["PCB_逐项能力"].max_row, len(self.pcb["items"]) + 1)
        self.assertEqual(wb["SMT_逐项比较"].max_row, len(self.smt["items"]) + 1)
        for row in wb["PCB_Issue summary"]:
            for cell in row:
                if cell.hyperlink:
                    sheet = cell.hyperlink.target.split("'")[1]
                    self.assertIn(sheet, wb.sheetnames)
        values = [c.value for c in wb["PCB_Main"]["B"]]
        self.assertIn("问题总数", values)
        self.assertIn("已解决总数", values)
        self.assertIn("问题汇总", values)
        wb.close()

    def test_all_three_report_types_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            for kind in ("pcb", "smt", "dfm"):
                path = Path(tmp) / f"{kind}.xlsx"
                outputs = export_report(kind, path, self.pcb, self.smt, dict(board_name="TEST"))
                self.assertIn(str(path), outputs)
                with path.open("rb") as handle:
                    wb = load_workbook(handle)
                    self.assertGreater(len(wb.sheetnames), 1)
                    wb.close()
            payload = json.loads((Path(tmp) / "dfm.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["pcb"]["items"], self.pcb["items"])
            self.assertEqual(payload["smt"]["items"], self.smt["items"])
            text = (Path(tmp) / "dfm.txt").read_text(encoding="utf-8")
            self.assertIn("PCB 制造能力分析", text)
            self.assertIn("SMT 工艺能力分析", text)

    def test_missing_results_do_not_replace_existing_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dfm.xlsx"
            path.write_bytes(b"existing user file")
            with self.assertRaises(ValueError):
                export_report("dfm", path, self.pcb, None)
            self.assertEqual(path.read_bytes(), b"existing user file")

    def test_fail_and_missing_never_average_to_pass(self):
        pcb = dict(releasable=True, items=[], issues=[])
        self.assertIn("不完整", combined_verdict(pcb, self.smt))
        smt = copy.deepcopy(self.smt)
        smt["items"][0]["status"] = "fail"
        self.assertEqual(combined_verdict(pcb, smt), "存在不符合项")

    def test_different_boards_rejected(self):
        pcb, smt = copy.deepcopy(self.pcb), copy.deepcopy(self.smt)
        pcb["input_brd"], smt["input_brd"] = "A.brd", "B.brd"
        with self.assertRaises(ValueError):
            build_combined_workbook(pcb, smt)


if __name__ == "__main__":
    unittest.main()

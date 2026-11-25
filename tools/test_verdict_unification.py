"""统一判定口径回归: 结论唯一、缺数据不放行、依据可追溯、报告按 BRD 名命名。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.dfm_engine import analyze_design
from src.dfm_report import build_dfm_workbook, generate_dfm_text
from src.gerber_analysis import attach_gerber_result
from src.verdict import finalize_verdict
from src.workbench_report import combined_verdict, default_report_name

ROOT = Path(__file__).resolve().parents[1]


def fake_bundle(checks=(), errors=(), complete=False):
    return dict(checks=list(checks), errors=list(errors), parsed_layers=[], folder="X:/gerber",
                copper_um=35.0, mask_bridge_checks=[], copper_layer_checks=[],
                geometry_coverage=[], geometry_requirements=[], geometry_data_source="",
                scan_stats=[], copper_density=[], copper_balance=[], elapsed_seconds=0.0,
                geometry_complete=complete)


def fail_check():
    return dict(item="最小线宽", field="min_trace_width", layer="L1 TOP", capability=0.1,
                measured=0.05, status="fail", unit="mm", source="X:/gerber/board.gtl",
                note="实测小于能力", x_mm=1.0, y_mm=2.0, brd_alignment=True)


class UnifiedVerdict(unittest.TestCase):
    def test_fail_outranks_missing_data(self):
        result = dict(items=[dict(status="fail")], release_blockers=["缺数据"],
                      gerber_errors=["缺层"])
        finalize_verdict(result, geometry=fake_bundle())
        self.assertEqual(result["verdict_code"], "fail")
        self.assertEqual(result["verdict"], "不通过（存在不符合项）")
        self.assertFalse(result["releasable"])
        self.assertFalse(result["passed"])
        self.assertIn("存在 1 项不符合制程能力", result["verdict_reasons"])
        self.assertIn("缺数据", result["verdict_reasons"])

    def test_missing_data_is_review_never_pass(self):
        result = dict(items=[], release_blockers=["外层阻焊桥缺测量"])
        finalize_verdict(result, geometry=fake_bundle())
        self.assertEqual(result["verdict_code"], "review")
        self.assertFalse(result["releasable"])
        self.assertTrue(result["passed"])  # 没有不符合项, 但绝不放行
        self.assertIn("制造几何为部分覆盖", "；".join(result["verdict_reasons"]))

    def test_pass_only_when_every_gate_clear(self):
        result = dict(items=[dict(status="warn_limit")])
        finalize_verdict(result, geometry=fake_bundle(complete=True))
        self.assertEqual(result["verdict_code"], "pass")
        self.assertTrue(result["releasable"])
        self.assertEqual(result["verdict"], "通过（有风险项）")
        self.assertEqual(result["verdict_reasons"], [])

    def test_parameter_analysis_reports_blockers(self):
        result = analyze_design({})
        self.assertEqual(result["verdict_code"], "review")
        self.assertFalse(result["releasable"])
        self.assertTrue(any("参数覆盖率" in r for r in result["verdict_reasons"]))

    def test_gerber_fail_dominates_and_partial_coverage_blocks_release(self):
        base = analyze_design({})
        failed = attach_gerber_result(base, fake_bundle(checks=[fail_check()]))
        self.assertEqual(failed["verdict_code"], "fail")
        self.assertFalse(failed["releasable"])
        self.assertEqual(failed["verdict"], "不通过（存在不符合项）")
        self.assertEqual(failed["items"][-1]["source_detail"], "X:/gerber/board.gtl")

        clean = attach_gerber_result(base, fake_bundle())
        self.assertEqual(clean["verdict_code"], "review")
        self.assertFalse(clean["releasable"])
        self.assertNotIn("通过", clean["verdict"])
        self.assertTrue(any("部分覆盖" in r for r in clean["verdict_reasons"]))

    def test_gerber_errors_and_copper_mismatch_are_reasons(self):
        base = analyze_design(dict(copper_um=70))
        result = attach_gerber_result(base, fake_bundle(errors=["缺少外层阻焊层"]))
        self.assertEqual(result["verdict_code"], "review")
        joined = "；".join(result["verdict_reasons"])
        self.assertIn("缺少外层阻焊层", joined)
        self.assertIn("铜厚不一致", joined)

    def test_combined_verdict_same_priority(self):
        smt = dict(counts={}, coverage=1.0, items=[dict(status="pass")])
        self.assertEqual(combined_verdict(dict(verdict_code="review"), smt),
                         "数据或能力不完整，需复核")
        self.assertEqual(combined_verdict(dict(verdict_code="fail"), smt), "存在不符合项")
        self.assertEqual(combined_verdict(dict(verdict_code="pass", items=[]), smt),
                         "本次所选 PCB / SMT 能力项匹配")
        # 旧快照没有 verdict_code 时按 releasable/错误数回退, 口径不变
        self.assertIn("不完整", combined_verdict(dict(releasable=True, items=[], issues=[]),
                                                 dict(counts={}, coverage=0.5, items=[])))


class ReportNamingAndEvidence(unittest.TestCase):
    def test_report_name_follows_brd_stem(self):
        name, folder = default_report_name(str(ROOT / "demo" / "DEMO_BOARD.brd"), "pcb")
        self.assertEqual(name, "DEMO_BOARD_PCB报告.xlsx")
        self.assertEqual(folder, str(ROOT / "demo"))
        self.assertEqual(default_report_name("A:/ boards /My Board.brd", "dfm", ".csv")[0],
                         "My Board_DFM综合报告.csv")
        self.assertEqual(default_report_name("", "smt")[0], "SMT报告.xlsx")

    def test_workbook_and_text_carry_verdict_reasons_and_sources(self):
        analysis = analyze_design({})
        analysis.update(input_brd="X:/boards/DEMO.brd", gerber_folder="X:/gerber",
                        geometry_data_source="X:/geo.json", analyzed_at="2026-11-20T10:00:00")
        analysis["items"].append(dict(category="Gerber", item="最小线宽", status="fail",
                                      message="实测不足", source="Gerber",
                                      source_detail="X:/gerber/board.gtl"))
        wb = build_dfm_workbook(analysis, {})
        main_keys = [c.value for c in wb["Main"]["B"]]
        self.assertIn("判定依据", main_keys)
        self.assertIn("BRD文件", main_keys)
        main_values = " ".join(str(c.value) for row in wb["Main"].iter_rows() for c in row)
        self.assertIn("X:/boards/DEMO.brd", main_values)
        self.assertIn("参数覆盖率", main_values)
        headers = [c.value for c in wb["逐项能力"][1]]
        self.assertIn("证据出处", headers)
        self.assertIn("X:/gerber/board.gtl",
                      [c.value for row in wb["逐项能力"].iter_rows() for c in row])
        wb.close()
        with tempfile.TemporaryDirectory() as tmp:
            text_path = str(Path(tmp) / "report.txt")
            generate_dfm_text(analysis, text_path, {})
            text = Path(text_path).read_text(encoding="utf-8")
        self.assertIn("判定依据", text)
        self.assertIn("X:/boards/DEMO.brd", text)
        self.assertIn("需复核", text)


if __name__ == "__main__":
    unittest.main()
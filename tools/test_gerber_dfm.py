import tempfile
import unittest
from pathlib import Path

from src.gerber_analysis import analyze_gerbers, read_film, attach_gerber_result, write_preview
from src.dfm_engine import analyze_design
from src.workbench_report import filter_report_priority

HEADER = "%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,0.1*%\nD10*\n"


class GerberDFMTests(unittest.TestCase):
    def test_real_mask_silk_and_outline_positions(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p/"test.gto").write_text(HEADER + "X1000000Y1000000D02*\nX2000000Y1000000D01*\nM02*\n")
            (p/"test.gts").write_text(HEADER.replace("0.1", "0.5") + "X1500000Y1000000D03*\nM02*\n")
            (p/"test.gko").write_text(HEADER + "X0Y1000000D02*\nX5000000Y1000000D01*\nM02*\n")
            bundle = analyze_gerbers(folder)
            fields = {c["field"] for c in bundle["checks"]}
            self.assertIn("gerber_silk_width", fields)
            self.assertIn("gerber_silk_mask", fields)
            self.assertIn("gerber_silk_edge", fields)
            self.assertTrue(all(0 <= c["x_mm"] <= 5 for c in bundle["checks"]))
            result = attach_gerber_result(analyze_design({}), bundle, "mil")
            self.assertFalse(result["passed"])
            self.assertFalse(result["releasable"])
            self.assertNotIn("command", result["problem_locations"][-1])
            preview = write_preview(bundle, p/"preview.html")
            self.assertIn("<svg", Path(preview).read_text())

    def test_clear_polarity_subtracts_opening(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"mask.gts"
            path.write_text("%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,2*%\n%ADD11C,1*%\n"
                "D10*\nX0Y0D03*\n%LPC*%\nD11*\nX0Y0D03*\nM02*\n")
            film = read_film(path)
            from shapely.geometry import Point
            self.assertFalse(film["geometry"].contains(Point(0,0)))
            self.assertAlmostEqual(film["geometry"].area, 3.1415926*.75, delta=.03)

    def test_filter_preserves_fail_verdict_and_source(self):
        pcb = dict(verdict="不通过",passed=False,error_count=1,issues=[
            dict(rule="info",severity="低"), dict(rule="failure",severity="高")],
            items=[dict(item="info",status="info"),dict(item="failure",status="fail")],
            problem_locations=[dict(item="info"),dict(item="failure")])
        filtered,_ = filter_report_priority(pcb,None)
        self.assertEqual(filtered["verdict"], pcb["verdict"])
        self.assertEqual(filtered["error_count"],1)
        self.assertEqual(len(filtered["issues"]),1)
        self.assertEqual(len(pcb["issues"]),2)

    def test_region_macro_and_inch_units_are_rendered(self):
        with tempfile.TemporaryDirectory() as folder:
            region = Path(folder)/"region.gts"
            region.write_text("%FSLAX46Y46*%\n%MOMM*%\nG36*\nX0Y0D02*\n"
                "X2000000Y0D01*\nX2000000Y2000000D01*\nX0Y2000000D01*\nX0Y0D01*\nG37*\nM02*\n")
            self.assertAlmostEqual(read_film(region)["geometry"].area,4,places=5)
            macro = Path(folder)/"macro.gts"
            macro.write_text("%FSLAX46Y46*%\n%MOMM*%\n%AMROUND*1,1,2,0,0*%\n"
                             "%ADD10ROUND*%\nD10*\nX0Y0D03*\nM02*\n")
            self.assertAlmostEqual(read_film(macro)["geometry"].area,3.1415926,delta=.04)
            inches = Path(folder)/"inches.gto"
            inches.write_text("%FSLAX25Y25*%\n%MOIN*%\n%ADD10C,0.01*%\nD10*\n"
                              "X100000Y100000D02*\nX200000Y100000D01*\nM02*\n")
            obj = read_film(inches)["objects"][0]
            self.assertAlmostEqual(obj["width"],.254)
            self.assertAlmostEqual(obj["centerline"].bounds[0],25.4)


if __name__ == "__main__":
    unittest.main()

"""Regression checks for units, real violation matching, and both report formats."""
import csv
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from src.dfm_engine import analyze_design, _num, get_line_spacing_capability
from src.pcb_locations import attach_locations
from src.pick import parse_excellon, parse_skill_csv, locate_failures
from src.workbench_report import export_report
from src.brd_analysis.imports import parse_drc_report


class LocationsTests(unittest.TestCase):
    def test_maximum_drill_and_all_minimum_violations(self):
        inventory = {"holes": [dict(x_mm=i, y_mm=-i, drill_mm=d)
                               for i, d in enumerate((0.1, 0.15, 0.2, 6, 7))]}
        result = {"items": [dict(field="min_mech_drill", design=0.1, capability=0.2, status="fail"),
                            dict(field="max_mech_drill", design=7, capability=5, status="fail")]}
        rows = locate_failures(result, inventory)
        self.assertEqual([r["count"] for r in rows], [2, 2])
        self.assertEqual(rows[1]["coords"][1]["drill_mm"], 7)

    def test_no_silent_coordinate_limit(self):
        inventory = {"holes": [dict(x_mm=i, y_mm=0, drill_mm=0.1) for i in range(85)]}
        result = {"items": [dict(field="min_mech_drill", design=0.1, capability=0.2, status="fail")]}
        self.assertEqual(len(locate_failures(result, inventory)[0]["coords"]), 85)

    def test_user_unit_export_negative_origin_and_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            holes = folder / "evan_holes.csv"
            holes.write_text('UNITS,mils,3,1000\nKIND,PADSTACK,X_UU,Y_UU,NET,REFDES\n'
                             'VIA,VIA20D5,-100.5,200.25,"GND,A",\n', encoding="utf-8")
            parsed = parse_skill_csv(str(holes))
            self.assertAlmostEqual(parsed["items"][0]["x_mm"], -100.5 * 0.0254)
            self.assertEqual(parsed["items"][0]["net"], "GND,A")
            result = attach_locations(analyze_design({"min_mech_drill": 0.127}),
                                      files=[str(holes)], design_unit="mils")
            point = next(p for p in result["problem_locations"] if "x_mm" in p)
            result["component_coordinates"] = [dict(refdes="U1", item="器件 U1", x_mm=-2.54, y_mm=0,
                x_brd=-100, y_brd=0, brd_unit="mil", side="TOP", command="x -100 0")]
            self.assertEqual(point["x_brd"], -100.5)
            self.assertEqual(point["command"], "x -100.500000 200.250000")
            for extension in (".csv", ".xlsx"):
                export_report("pcb", folder / ("report" + extension), pcb=result)
            with (folder / "report.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            coordinate = next(r for r in rows if r["记录类型"] == "问题坐标" and r["X(BRD)"])
            self.assertEqual(float(coordinate["X(BRD)"]), -100.5)
            component = next(r for r in rows if r["记录类型"] == "器件坐标")
            self.assertEqual(component["位号"], "U1")
            self.assertEqual(float(component["X(BRD)"]), -100)
            wb = load_workbook(folder / "report.xlsx")
            self.assertIn("问题坐标", wb.sheetnames)
            self.assertGreater(wb["问题坐标"].max_row, 1)
            self.assertEqual(wb["BRD器件坐标"]["A2"].value, "U1")
            wb.close()

    def test_excellon_comments_trailing_suppression_and_modal_axis(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "board.drl"
            path.write_text("M48\n;FILE_FORMAT=3:3\nMETRIC,TZ\nT01C0.1\n%\nT01\nX012Y034\nX013\nM30\n")
            parsed = parse_excellon(str(path))
            self.assertTrue(parsed["ok"])
            self.assertEqual([(p["x_mm"], p["y_mm"]) for p in parsed["items"]], [(12, 34), (13, 34)])
            path.write_text("M48\nMETRIC\nT01C0.1\n%\nT01\nX012Y034\nM30\n")
            self.assertFalse(parse_excellon(str(path))["ok"])

    def test_nonfinite_and_copper_gap(self):
        self.assertIsNone(_num("nan"))
        self.assertIsNone(_num("inf"))
        table = {"trace": {"min_line_spacing_by_copper": [
            dict(copper_min_um=0, copper_max_um=35, line_um=75, space_um=75),
            dict(copper_min_um=36, copper_max_um=70, line_um=100, space_um=100),
            dict(copper_min_um=71, copper_max_um=105, line_um=150, space_um=150)]}}
        self.assertEqual(get_line_spacing_capability(table, 70.5), (100, 100))

    def test_explicit_zero_drc_is_recognized(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "drc.rpt"
            path.write_text("Allegro DRC Report\n\nViolations: 0\n")
            parsed = parse_drc_report(str(path))
            self.assertTrue(parsed["ok"])
            self.assertEqual(parsed["count"], 0)
            path.write_text("Allegro DRC Report\n\nViolation something not understood\n")
            self.assertFalse(parse_drc_report(str(path))["ok"])

    def test_real_trace_width_locations_user_units(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "evan_traces.csv"
            path.write_text("UNITS,millimeters,3,1000\n"
                            "KIND,LAYER,WIDTH_UU,X1_UU,Y1_UU,X2_UU,Y2_UU,NET\n"
                            "TRACE,ETCH/TOP,0.05,-2,4,2,4,GND\n")
            result = {"items": [dict(field="min_line_width", item="最小线宽", status="fail",
                                     design=0.05, capability=0.1, unit="mm")]}
            point = attach_locations(result, files=[str(path)], design_unit="mm")["problem_locations"][0]
            self.assertEqual((point["x_brd"], point["y_brd"]), (0, 4))
            self.assertEqual(point["measured"], 0.05)


if __name__ == "__main__":
    unittest.main()

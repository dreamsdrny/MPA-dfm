"""真实轮廓规则回归：连通铜、槽孔、极性、孔偏心和铜平衡。"""
import json
import tempfile
import unittest
from pathlib import Path

from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from src.dfm_engine import analyze_design
from src.dfm_report import build_dfm_workbook
from src.gerber_analysis import analyze_gerbers, attach_gerber_result
from src.layout_drill import read_drill
from src.layout_geometry import (ScanBudgetExceeded, ScanLimits, UnionAccumulator,
                                 annular_ring, board_area, copper_density,
                                 nearby_pairs, topology_candidates)


def region(points, function):
    lines = ["%FSLAX46Y46*%", "%MOMM*%", f"%TF.FileFunction,{function}*%", "G36*"]
    for i, (x, y) in enumerate([*points, points[0]]):
        lines.append(f"X{round(x * 1e6)}Y{round(y * 1e6)}D{'02' if i == 0 else '01'}*")
    return "\n".join([*lines, "G37*", "M02*", ""])


def outline_file():
    return ("%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,0.01*%\nD10*\nX0Y0D02*\n"
            "X10000000Y0D01*\nX10000000Y10000000D01*\nX0Y10000000D01*\nX0Y0D01*\nM02*\n")


def drill_text(x=5, y=5, diameter=.3, extra=""):
    return f"M48\nMETRIC\nT01C{diameter}\n%\nT01\nX{x:.6f}Y{y:.6f}\n{extra}M30\n"


def drill_metadata(**extra):
    return dict(plated=True, process="mechanical", start_layer=1, end_layer=2,
                alignment_confirmed=True, **extra)


class LayoutGeometryTests(unittest.TestCase):
    def test_union_accumulator_preserves_all_batches(self):
        shapes = [box(i, 0, i + 2, 1) for i in range(30)]
        accumulator = UnionAccumulator(batch_size=3)
        for shape in shapes:
            accumulator.add(shape)
        self.assertTrue(accumulator.finish().equals(unary_union(shapes)))

    def test_same_connected_copper_neck_and_slot(self):
        neck = unary_union([box(0, 0, 1, 1), box(1, .475, 2, .525), box(2, 0, 3, 1)])
        self.assertEqual(neck.geom_type, "Polygon")
        narrow = list(topology_candidates(neck, .1, .1))
        self.assertTrue(any(c["kind"] == "width" and abs(c["measured"] - .05) < 1e-9 for c in narrow))
        slot = Polygon([(0, 0), (2, 0), (2, 2), (1.05, 2), (1.05, .5), (.95, .5), (.95, 2), (0, 2)])
        self.assertTrue(any(c["kind"] == "gap" and abs(c["measured"] - .1) < 1e-9
                            for c in topology_candidates(slot, .15, .15)))
        self.assertEqual(list(topology_candidates(Point(0, 0).buffer(1), .1, .1)), [])

    def test_annular_actual_offcenter_not_diameter_formula(self):
        copper = Point(0, 0).buffer(.3, quad_segs=128)
        hole = Point(.1, 0).buffer(.15, quad_segs=128)
        measured, _ = annular_ring(hole, copper, .15)
        self.assertAlmostEqual(measured, .05, delta=.001)
        # A centered diameter-only formula would incorrectly report .15mm.
        concentric = Point(0, 0).buffer(.15, quad_segs=128)
        solid, _ = annular_ring(concentric, copper, .15)
        donut, _ = annular_ring(concentric, copper.difference(concentric), .15)
        self.assertAlmostEqual(solid, donut, places=6)
        breakout, _ = annular_ring(Point(.2, 0).buffer(.15), copper, .15)
        self.assertLess(breakout, .001)

    def test_board_outline_nested_cutouts_and_open_failure(self):
        outline, error = board_area([box(0, 0, 10, 10).boundary, box(2, 2, 4, 4).boundary])
        self.assertFalse(error)
        self.assertEqual(outline.area, 96)
        outline, error = board_area([LineString([(0, 0), (2, 0), (2, 2)])])
        self.assertTrue(outline.is_empty)
        self.assertTrue(error)

    def test_density_is_clipped_to_board_not_copper_bbox(self):
        density = copper_density(box(-5, 0, 5, 10), box(0, 0, 10, 10), 5)
        self.assertEqual(density["density_percent"], 50)
        self.assertEqual(sorted(c["density_percent"] for c in density["cells"]), [0, 0, 100, 100])
        with self.assertRaises(ScanBudgetExceeded):
            copper_density(box(0, 0, 10, 10), box(0, 0, 10, 10), .1,
                           ScanLimits(max_density_cells=10))

    def test_spatial_pairs_no_duplicates_and_explicit_budget(self):
        shapes = [Point(i, 0) for i in range(5)]
        stats = {}
        pairs = list(nearby_pairs(shapes, 1.1, stats=stats))
        self.assertEqual(pairs, [(0, 1), (1, 2), (2, 3), (3, 4)])
        self.assertTrue(stats["complete"])
        with self.assertRaises(ScanBudgetExceeded):
            list(nearby_pairs(shapes, 100, ScanLimits(max_candidates=2), stats))
        self.assertFalse(stats["complete"])
        with self.assertRaises(ValueError):
            ScanLimits(batch_size=0)

    def test_excellon_inches_and_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "holes.drl"
            path.write_text("M48\nINCH\nT01C0.01\n%\nT01\nX1.0Y1.0\nM30\n")
            hole = read_drill(path)["holes"][0]
            self.assertAlmostEqual(hole["diameter_mm"], .254)
            self.assertAlmostEqual(hole["geometry"].centroid.x, 25.4)
            self.assertIsNone(hole["plated"])
            path.write_text(drill_text(extra="X5.0Y5.0G85X6.0Y5.0\n"))
            hole = read_drill(path)["holes"][-1]
            self.assertAlmostEqual(hole["geometry"].bounds[2] - hole["geometry"].bounds[0], 1.3, delta=.004)
            with self.assertRaises(ValueError):
                read_drill(path, dict(start_layer=2, end_layer=1))


class LayoutIntegrationTests(unittest.TestCase):
    def test_region_neck_not_just_aperture_width(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "top.gtl"
            path.write_text(region([(0, 0), (1, 0), (1, .475), (2, .475), (2, 0), (3, 0),
                                    (3, 1), (2, 1), (2, .525), (1, .525), (1, 1), (0, 1)], "Copper,L1,Top"))
            result = analyze_gerbers(directory)
            self.assertTrue(any(c["field"] == "gerber_copper_neck" for c in result["checks"]))
            self.assertFalse(result["geometry_complete"])

    def test_clear_slot_in_plane_and_connected_mask(self):
        with tempfile.TemporaryDirectory() as directory:
            text = region([(0, 0), (3, 0), (3, 3), (0, 3)], "Copper,L1,Top").replace("M02*", "")
            clear = region([(1, 1), (2, 1), (2, 1.05), (1, 1.05)], "Copper,L1,Top")
            clear = clear[clear.index("G36*"):]
            (Path(directory) / "top.gtl").write_text(text + "%LPC*%\n" + clear)
            result = analyze_gerbers(directory)
            self.assertTrue(any(c["field"] == "gerber_copper_slot" for c in result["checks"]))
            mask = [(0, 0), (2, 0), (2, 2), (1.025, 2), (1.025, .5), (.975, .5), (.975, 2), (0, 2)]
            (Path(directory) / "mask.gts").write_text(region(mask, "Soldermask,Top"))
            result = analyze_gerbers(directory)
            self.assertTrue(any(c["field"] == "gerber_mask_web" for c in result["checks"]))

    def test_drill_annular_overlap_and_metadata_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            flash = "%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,0.6*%\nD10*\nX5000000Y5000000D03*\nM02*\n"
            for suffix in ("gtl", "gbl"):
                (folder / f"board.{suffix}").write_text(flash)
            (folder / "holes.drl").write_text(drill_text(x=5.1, extra="X5.2Y5.0\n"))
            unknown = analyze_gerbers(folder, layer_count=2)
            self.assertFalse(any(c["field"] == "gerber_annular_ring" for c in unknown["checks"]))
            self.assertTrue(any("孔环未检查" in e for e in unknown["errors"]))
            result = analyze_gerbers(folder, layer_count=2,
                                    geometry_options=dict(drills={"holes.drl": drill_metadata()}))
            rings = [c for c in result["checks"] if c["field"] == "gerber_annular_ring"]
            self.assertEqual({c["layer"] for c in rings}, {"copper_top", "copper_bottom"})
            self.assertTrue(all(c["status"] == "fail" for c in rings))
            self.assertTrue(any(c["field"] == "gerber_drill_overlap" and c["status"] == "fail" for c in result["checks"]))
            attached = attach_gerber_result(analyze_design({}), result, "mm")
            overlap_rows = [r for r in attached["problem_locations"] if r["field"] == "gerber_drill_overlap"]
            self.assertNotIn("command", overlap_rows[0])  # Gerber alignment is not BRD alignment.
            npth = drill_metadata()
            npth["plated"] = False
            result = analyze_gerbers(folder, layer_count=2, geometry_options=dict(drills={"holes.drl": npth}))
            self.assertFalse(any(c["field"] == "gerber_annular_ring" for c in result["checks"]))

    def test_stacked_vias_not_overlapping_volumes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "top.gtl").write_text(region([(0, 0), (10, 0), (10, 10), (0, 10)], "Copper,L1,Top"))
            for name, x in (("a.drl", 5), ("b.drl", 5.1)):
                (folder / name).write_text(drill_text(x=x))
            meta = {"a.drl": drill_metadata(), "b.drl": drill_metadata()}
            meta["b.drl"].update(start_layer=2, end_layer=3)
            bundle = analyze_gerbers(folder, layer_count=3, geometry_options=dict(drills=meta))
            self.assertFalse(any(c["field"] == "gerber_drill_overlap" for c in bundle["checks"]))

    def test_manifest_density_report_and_partial_release(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "top.gtl").write_text(region([(0, 0), (5, 0), (5, 10), (0, 10)], "Copper,L1,Top"))
            (folder / "bottom.gbl").write_text(region([(0, 0), (2, 0), (2, 10), (0, 10)], "Copper,L2,Bot"))
            (folder / "board.gko").write_text(outline_file())
            (folder / "dfm_geometry.json").write_text(json.dumps(dict(
                copper_balance=dict(cell_mm=5, max_delta_percent=10))), encoding="utf-8")
            bundle = analyze_gerbers(folder, layer_count=2)
            self.assertEqual([d["density_percent"] for d in bundle["copper_density"]], [20, 50])
            self.assertEqual(bundle["copper_balance"][0]["delta_percent"], 30)
            result = attach_gerber_result(analyze_design({}), bundle, "mm")
            balance_rows = [c for c in result["items"] if c.get("field") == "gerber_copper_balance"]
            self.assertEqual(balance_rows[0]["unit"], "百分点")
            self.assertEqual(balance_rows[0]["design"], 60)
            self.assertFalse(result["releasable"])
            workbook = build_dfm_workbook(result)
            self.assertIn("铜密度与平衡", workbook.sheetnames)
            self.assertIn("局部铜密度", workbook.sheetnames)
            self.assertIn("几何扫描预算", workbook.sheetnames)
            workbook.save(folder / "result.xlsx")

    def test_per_layer_limits_not_collapsed_in_report(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            film = "%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,0.5*%\nD10*\nX0Y0D03*\nX550000Y0D03*\nM02*\n"
            (folder / "top.gtl").write_text(film)
            (folder / "bottom.gbl").write_text(film)
            bundle = analyze_gerbers(folder, stackup=[dict(index=1, copper_um=35), dict(index=2, copper_um=70)], layer_count=2)
            result = attach_gerber_result(analyze_design({}), bundle)
            rows = [r for r in result["items"] if r.get("field") == "gerber_copper_spacing"]
            self.assertEqual(len(rows), 2)
            self.assertEqual({r["layer"] for r in rows}, {"copper_top", "copper_bottom"})
            self.assertEqual(len({r["capability"] for r in rows}), 2)

    def test_budget_exhaustion_marks_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "top.gtl").write_text(region([(0, 0), (10, 0), (10, 10), (0, 10)], "Copper,L1,Top"))
            (folder / "board.gko").write_text(outline_file())
            bundle = analyze_gerbers(folder, geometry_options=dict(scan_limits=dict(max_density_cells=1),
                                                                   copper_balance=dict(cell_mm=1)))
            self.assertTrue(any("网格超过" in e for e in bundle["errors"]))
            self.assertFalse(attach_gerber_result(analyze_design({}), bundle)["releasable"])


if __name__ == "__main__":
    unittest.main()

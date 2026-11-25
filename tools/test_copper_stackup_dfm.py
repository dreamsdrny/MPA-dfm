"""单一 um 输入、12 铜层覆盖、外层阻焊范围回归。"""
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.dfm_engine import (analyze_design, get_sm_bridge_capability,
                            load_capability, normalize_copper_um, validate_constraints,
                            get_line_spacing_capability)
from src.dfm_stackup import copper_layers
from src.gerber_analysis import analyze_gerbers, identify_layer, attach_gerber_result


def film(function, diameter=0.5, second_x=0.6):
    return (f"%FSLAX46Y46*%\n%MOMM*%\n%TF.FileFunction,{function}*%\n"
            f"%ADD10C,{diameter}*%\nD10*\nX0Y0D03*\n"
            f"X{int(second_x * 1e6)}Y0D03*\nM02*\n")


class CopperStackupDFM(unittest.TestCase):
    def test_bridge_um_boundaries_and_no_extrapolation(self):
        cap = load_capability()
        for thickness, required in [(18, .0889), (35, .0889), (35.01, .1016),
                                     (52.5, .1016), (52.51, .1143), (70, .1143)]:
            with self.subTest(thickness=thickness):
                self.assertAlmostEqual(get_sm_bridge_capability(cap, thickness), required)
        self.assertIsNone(get_sm_bridge_capability(cap, 70.01))
        self.assertEqual(get_line_spacing_capability(cap, 106), (None, None))
        self.assertAlmostEqual(get_sm_bridge_capability(load_capability(factory="sihui"), 70), .1)

    def test_input_only_um_and_invalid_rejected(self):
        from main import run_pipeline
        from src.checklist_pipeline import run
        for function in (run_pipeline, validate_constraints, run, analyze_gerbers):
            self.assertNotIn("copper_oz", inspect.signature(function).parameters)
        for value in (None, "", "abc", 0, -1, "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_copper_um(value)
        with self.assertRaises(ValueError):
            analyze_design(dict(copper_oz=2))
        result = analyze_design(dict(copper_um=70, copper_oz=1, solder_mask_bridge=.1))
        row = next(row for row in result["items"] if row["field"] == "solder_mask_bridge")
        self.assertEqual(row["status"], "fail")
        self.assertEqual(result["copper_um"], 70)
        self.assertNotIn("copper_oz", result)

    def test_constraints_bridge_failure_is_error(self):
        constraints = dict(checklist_rules=dict(solder_mask=dict(bridge=dict(min=.1))))
        self.assertTrue(validate_constraints(constraints, copper_um=35)["passed"])
        self.assertFalse(validate_constraints(constraints, copper_um=70)["passed"])

    def test_12_layers_use_individual_thickness_and_mask_only_outer(self):
        rows = [dict(index=i, name=f"L{i}", type="plane" if i % 2 == 0 else "signal",
                     copper_um=70 if i == 6 else 18, min_line_width=.1, min_spacing=.1,
                     solder_mask_bridge=.001, pad_to_mask_clearance=.001,
                     pad_to_mask_clearance_min=.05) for i in range(1, 13)]
        rows[0].update(copper_um=35, solder_mask_bridge=.12, pad_to_mask_clearance=.06)
        rows[-1].update(copper_um=70, solder_mask_bridge=.12, pad_to_mask_clearance=.06)
        result = analyze_design(dict(layer_count=12, stackup=rows, copper_um=35))
        self.assertEqual(len(result["layer_checks"]), 12)
        layer_items = [row for row in result["items"] if row["category"] == "逐层线路"]
        self.assertEqual(len(layer_items), 24)
        sixth = [row for row in layer_items if row["layer"] == "L6"]
        self.assertTrue(all(row["status"] == "fail" and row["copper_um"] == 70 for row in sixth))
        outer = [row for row in result["items"] if row["category"] == "外层阻焊"]
        self.assertEqual({row["layer"] for row in outer}, {"L1", "L12"})
        self.assertFalse(any(row["status"] == "fail" for row in outer))

    def test_missing_inner_copper_not_replaced_by_outer(self):
        rows = copper_layers(dict(layer_count=12), 70)
        self.assertEqual(rows[0]["copper_um"], 70)
        self.assertEqual(rows[-1]["copper_um"], 70)
        self.assertTrue(all(row["copper_um"] is None for row in rows[1:-1]))
        result = analyze_design(dict(layer_count=12, copper_um=70, solder_mask_bridge=.2))
        self.assertEqual(len(result["layer_checks"]), 12)
        self.assertFalse(result["releasable"])

    def test_identify_all_copper_and_ignore_inner_mask(self):
        self.assertEqual(identify_layer("test.gbr", "%TF.FileFunction,Copper,L12,Bot*%"), "copper_bottom")
        self.assertEqual(identify_layer("test.gbr", "%TF.FileFunction,Copper,L2,Inr*%"), "copper_L2")
        self.assertEqual(identify_layer("L11_GND.art", ""), "copper_L11")
        self.assertEqual(identify_layer("board.g2", ""), "copper_L2")
        self.assertEqual(identify_layer("mask.gbr", "%TF.FileFunction,Soldermask,L2,Inr*%"), "unknown")
        self.assertEqual(identify_layer("SOLDERMASK_L2.art", ""), "unknown")

    def test_board_minimum_not_compared_to_outer_for_all_layers(self):
        rows = [dict(index=1, copper_um=70, min_line_width=.2, min_spacing=.2),
                dict(index=2, copper_um=18, min_line_width=.06, min_spacing=.06),
                dict(index=3, copper_um=70, min_line_width=.2, min_spacing=.2)]
        result = analyze_design(dict(layer_count=3, stackup=rows, copper_um=70,
                                     min_line_width=.06, min_spacing=.06))
        self.assertFalse(any(row["status"] == "fail" and "线路" in row["category"] for row in result["items"]))

    def test_12_gerbers_not_ambiguous_and_independent_spacing(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            rows = [dict(index=i, name=f"L{i}", copper_um=70 if i == 6 else 18) for i in range(1, 13)]
            for i in range(1, 13):
                side = "Top" if i == 1 else "Bot" if i == 12 else "Inr"
                (folder/f"layer{i}.gbr").write_text(film(f"Copper,L{i},{side}", second_x=.59))
            for suffix, side in (("gts", "Top"), ("gbs", "Bot")):
                (folder/f"mask.{suffix}").write_text(film(f"Soldermask,{side}", second_x=.65))
            bundle = analyze_gerbers(folder, stackup=rows, layer_count=12, copper_um=35)
            self.assertEqual(len(bundle["copper_layer_checks"]), 12)
            self.assertFalse(any("多个同类" in error for error in bundle["errors"]))
            self.assertFalse(any("缺少该铜层" in error for error in bundle["errors"]))
            gaps = [row for row in bundle["checks"] if row["field"] == "gerber_copper_spacing"]
            self.assertEqual({row["layer"] for row in gaps}, {"copper_L6"})
            self.assertEqual({row["layer"] for row in bundle["mask_bridge_checks"]}, {"mask_top", "mask_bottom"})

    def test_mask_35_vs_70_small_bridge_and_missing_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/"test.gts").write_text(film("Soldermask,Top"))
            thin = analyze_gerbers(folder, copper_um=35)
            thick = analyze_gerbers(folder, copper_um=70)
            self.assertFalse(any(row["field"] == "gerber_mask_bridge" for row in thin["checks"]))
            bridges = [row for row in thick["checks"] if row["field"] == "gerber_mask_bridge"]
            self.assertEqual(len(bridges), 1)
            self.assertEqual(bridges[0]["status"], "fail")
            result = attach_gerber_result(analyze_design({}), thick)
            self.assertFalse(result["passed"])
            unknown = analyze_gerbers(folder, copper_um=105)
            self.assertTrue(any("无阻焊桥能力" in error for error in unknown["errors"]))
            (folder/"test.gts").write_text(film("Soldermask,Top", second_x=.501))
            tiny = analyze_gerbers(folder, copper_um=35)
            self.assertTrue(any(row["field"] == "gerber_mask_bridge" and row["status"] == "fail"
                                for row in tiny["checks"]))

    def test_outer_mask_uses_each_outer_stackup_thickness(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/"test.gts").write_text(film("Soldermask,Top"))
            (folder/"test.gbs").write_text(film("Soldermask,Bot"))
            rows = [dict(index=1, name="TOP", copper_um=35),
                    dict(index=12, name="BOTTOM", copper_um=70)]
            bundle = analyze_gerbers(folder, stackup=rows, layer_count=12)
            bridges = [row for row in bundle["checks"] if row["field"] == "gerber_mask_bridge"]
            self.assertEqual({row["layer"] for row in bridges}, {"mask_bottom"})


class StackupParamPanel(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from src.ui.dfm_params import ParamPanel
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.panel = ParamPanel(self.root, lambda: None, lambda: None)

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.destroy()

    def test_only_um_field_and_invalid_input(self):
        self.assertFalse(hasattr(self.panel, "copper_oz"))
        self.panel.copper_um.set("70")
        self.assertEqual(self.panel.read_design()["copper_um"], 70)
        self.panel.copper_um.set("nan")
        with self.assertRaises(ValueError):
            self.panel.read_design()

    def test_import_12_layer_stackup_and_reset(self):
        self.panel.set_board_available(True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"stack.json"
            path.write_text(json.dumps(dict(layer_count=12,
                stackup=[dict(index=i, name=f"L{i}", copper_um=18) for i in range(1, 13)])), encoding="utf-8")
            with patch("src.ui.dfm_params.filedialog.askopenfilename", return_value=str(path)):
                self.panel._import_stackup()
            design = self.panel.read_design()
            self.assertEqual(design["layer_count"], 12)
            self.assertEqual(len(design["stackup"]), 12)
            self.panel.reset_design()
            self.assertEqual(self.panel.read_design()["stackup"], [])


if __name__ == "__main__":
    unittest.main()

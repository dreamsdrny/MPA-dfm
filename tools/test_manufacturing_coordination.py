"""Gerber/ODB++ 导入及 BOM 协同回归；归档不落地解包。"""
import gzip
import io
import json
import tarfile
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import openpyxl
from shapely.geometry import Point

from src.design_inputs import load_manufacturing_project
from src.design_inputs.archive import InputContainer
from src.design_inputs.coordination import infer_manufacturing_kind
from src.design_inputs.coordination_report import export_coordination, write_coordination_preview
from src.design_inputs.gerber_package import parse_gerber_package
from src.design_inputs.manufacturing import parse_odb
from src.design_inputs.odb_features import parse_features
from src.design_inputs.placement import read_placements


def odb_files(two_steps=False):
    matrix = """STEP {
COL=1
NAME=pcb
}
LAYER {
ROW=1
CONTEXT=BOARD
TYPE=COMPONENT
NAME=comp_+_top
SIDE=TOP
}
LAYER {
ROW=2
CONTEXT=BOARD
TYPE=SIGNAL
NAME=top
POLARITY=POSITIVE
}
LAYER {
ROW=3
CONTEXT=BOARD
TYPE=COMPONENT
NAME=comp_+_bot
SIDE=BOTTOM
}
"""
    if two_steps:
        matrix += "STEP {\nCOL=2\nNAME=panel\n}\n"
    return {
        "job/matrix/matrix": matrix.encode(),
        "job/misc/info": b"ODB_VERSION=8.1\n",
        "job/steps/pcb/eda/data": b"UNITS=MM\nNET GND\nNET VCC\nPKG 0603 0.5 -0.8 -0.4 0.8 0.4\nPKG QFN32 0.5 -3 -3 3 3\n",
        "job/steps/pcb/layers/comp_+_top/components": (
            "UNITS=MM\n@0 .no_pop\nCMP 0 1.5 2.5 90 N R1 PART-A\n"
            "PRP PART_NUMBER 'PN-R'\nPRP VALUE '10k'\n"
            "TOP 0 1 2.5 90 N 0 0 1\nTOP 1 2 2.5 90 N 1 0 2\n"
            "CMP 1 5 6 180 N U1 IC-A ;0\nPRP PART_NUMBER 'PN-U'\n").encode(),
        "job/steps/pcb/layers/comp_+_bot/components": b"UNITS=INCH\nCMP 0 1 2 270 M C1 PART-C\nPRP VALUE '100n'\n",
        "job/steps/pcb/layers/top/features": b"UNITS=MM\n$0 r1000\n$1 rect1000x2000\nP 1 1 0 P 0 0\nP 5 5 1 P 0 1\nL 0 0 5 0 0 P 0\n",
        "job/steps/pcb/profile": b"UNITS=MM\nOB 0 0 I\nOS 10 0\nOS 10 10\nOS 0 10\nOS 0 0\nOE\n",
    }


def write_zip(path, files):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)


GERBER = "%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,0.6*%\nD10*\nX1000000Y1000000D03*\nM02*\n"


class ManufacturingCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def job(self, files=None):
        path = self.root / "job.zip"
        write_zip(path, files or odb_files())
        return path

    def test_odb_components_packages_properties_pins_and_inches(self):
        job = self.job()
        result = parse_odb(job)
        self.assertEqual(result["selected_step"], "pcb")
        records = {row["refdes"]: row for row in result["component_records"]}
        self.assertEqual(records["R1"]["package"], "0603")
        self.assertEqual(records["R1"]["part_number"], "PN-R")
        self.assertEqual(records["R1"]["pins"][0]["net"], "GND")
        self.assertEqual(records["R1"]["pins"][1]["pin"], "2")
        self.assertTrue(records["U1"]["dnp"])
        self.assertTrue(records["C1"]["mirror"])
        self.assertEqual(records["C1"]["side"], "BOTTOM")
        self.assertAlmostEqual(records["C1"]["x_mm"], 25.4)
        self.assertAlmostEqual(result["profile"]["area_mm2"], 100)
        self.assertGreater(result["films"][0]["area_mm2"], 0)
        self.assertFalse(result["geometry_complete"])
        json.dumps(result, allow_nan=False)

    def test_odb_directory_tgz_and_individually_gzipped_files(self):
        files = odb_files()
        directory = self.root / "odb"
        for name, data in files.items():
            file = directory / (name + ".gz")
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(gzip.compress(data))
        self.assertEqual(len(parse_odb(directory)["component_records"]), 3)
        path = self.root / "job.tgz"
        with tarfile.open(path, "w:gz") as archive:
            for name, data in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        self.assertEqual(len(parse_odb(path)["component_records"]), 3)
        self.assertEqual(infer_manufacturing_kind(path), "odb")

    def test_multiple_steps_require_explicit_selection(self):
        job = self.job(odb_files(two_steps=True))
        result = load_manufacturing_project(job)
        self.assertEqual(result["status"], "needs_step")
        self.assertEqual(result["components"], [])
        self.assertEqual(len(load_manufacturing_project(job, step="pcb")["components"]), 3)
        with self.assertRaises(ValueError):
            parse_odb(job, step="nonexistent")

    def test_odb_bom_refdes_conflicts_missing_side_and_non_electrical(self):
        bom = self.root / "bom.csv"
        bom.write_text("refdes,part_number,footprint,value,side,dnp\n"
                       "R1,PN-R,0402,10k,BOTTOM,N\nU1,PN-U,QFN32,,TOP,N\n"
                       "J99,PN-J,HDR,,TOP,N\nPCB1,BOARD-A,,,,\n", encoding="utf-8")
        result = load_manufacturing_project(self.job(), bom)
        rec = result["reconciliation"]
        self.assertEqual(rec["status"], "incomplete")
        self.assertEqual(rec["missing_in_bom"][0]["refdes"], "C1")
        self.assertEqual(rec["missing_in_layout"][0]["refdes"], "J99")
        self.assertEqual({row["field"] for row in rec["conflicts"]}, {"footprint", "side", "dnp"})
        self.assertEqual(result["non_electrical"][0]["refdes"], "PCB1")
        r1 = next(row for row in result["components"] if row["refdes"] == "R1")
        self.assertEqual((r1["x_mm"], r1["y_mm"]), (1.5, 2.5))
        self.assertTrue(r1["spatial_verified"])
        self.assertEqual(r1["layout"]["package"], "0603")
        self.assertEqual(r1["bom"]["footprint"], "0402")
        self.assertFalse(result["releasable"])

    def test_duplicate_components_not_silently_collapsed(self):
        files = odb_files()
        key = "job/steps/pcb/layers/comp_+_bot/components"
        files[key] += b"CMP 0 1 1 0 M R1 PART-R\n"
        bom = self.root / "bom.csv"
        bom.write_text("refdes\nR1\nU1\nC1\n")
        result = load_manufacturing_project(self.job(files), bom)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["reconciliation"]["summary"]["duplicate_count"], 1)
        row = next(row for row in result["components"] if row["refdes"] == "R1")
        self.assertEqual(len(row["layout_evidence"]), 2)
        self.assertNotIn("x_mm", row)

    def test_matching_odb_bom_verified_but_not_releasable(self):
        bom = self.root / "bom.csv"
        bom.write_text("refdes,part_number,footprint,side\nR1,PN-R,0603,TOP\n"
                       "U1,PN-U,QFN32,TOP\nC1,,0603,BOTTOM\n", encoding="utf-8")
        result = load_manufacturing_project(self.job(), bom)
        self.assertEqual(result["status"], "verified")
        self.assertFalse(result["releasable"])
        self.assertFalse(any(c["field"] == "dnp" for c in result["reconciliation"]["conflicts"]))

    def test_incomplete_components_cannot_verify_matching_subset(self):
        files = odb_files()
        key = "job/steps/pcb/layers/comp_+_top/components"
        files[key] += b"CMP 0 invalid fields\nPRP VALUE 'bad'\n"
        bom = self.root / "bom.csv"
        bom.write_text("refdes\nR1\nU1\nC1\n")
        result = load_manufacturing_project(self.job(files), bom)
        self.assertEqual(result["status"], "review")
        self.assertFalse(result["layout"]["component_complete"])

    def test_unsupported_features_still_json_serializable(self):
        files = odb_files()
        files["job/steps/pcb/layers/top/features"] = b"UNITS=MM\n$0 user_symbol\nP 0 0 0 P 0 0\n"
        result = load_manufacturing_project(self.job(files))
        self.assertEqual(result["layout"]["films"][0]["bounds_mm"], [])
        json.dumps(result, allow_nan=False)

    def test_gerber_zip_without_refdes_never_invents_components(self):
        path = self.root / "gerber.zip"
        write_zip(path, {"films/board.gtl": GERBER.encode()})
        self.assertEqual(infer_manufacturing_kind(path), "gerber")
        bom = self.root / "bom.csv"
        bom.write_text("refdes\nR1\n")
        result = load_manufacturing_project(path, bom)
        self.assertEqual(result["reconciliation"]["status"], "unavailable")
        self.assertEqual(result["components"][0]["status"], "unverified")
        self.assertIsNone(result["components"][0].get("x_mm"))

    def test_gerber_x2_pins_not_component_centroid(self):
        path = self.root / "x2.gtl"
        text = GERBER.replace("D10*", "%TO.C,R1*%\n%TO.P,R1,1*%\nD10*")
        text = text.replace("M02*", "%TO.P,R1,2*%\nX2000000Y1000000D03*\nM02*")
        path.write_text(text)
        data = parse_gerber_package(path)
        self.assertEqual(data["component_records"][0]["refdes"], "R1")
        self.assertEqual(len(data["component_records"][0]["pins"]), 2)
        self.assertNotIn("x_mm", data["component_records"][0])
        self.assertEqual(data["component_records"][0]["coordinate_kind"], "not_provided")

    def test_gerber_placement_bom_and_alignment_gate(self):
        path = self.root / "top.gtl"
        path.write_text(GERBER)
        bom = self.root / "bom.csv"
        bom.write_text("refdes,part_number,footprint,value,side\nR1,=BAD(),0603,10k,TOP\n", encoding="utf-8")
        placement = self.root / "placement.csv"
        placement.write_text("refdes,x_mm,y_mm,rotation,side,footprint\nR1,1,1,90,TOP,0603\n")
        result = load_manufacturing_project(path, bom, placement_path=placement)
        row = result["components"][0]
        self.assertEqual((row["x_mm"], row["y_mm"]), (1, 1))
        self.assertFalse(row["spatial_verified"])
        html = Path(write_coordination_preview(result, self.root / "unconfirmed.html", "R1")).read_text(encoding="utf-8")
        self.assertNotIn("<circle", html.split("</svg>")[0])
        confirmed = load_manufacturing_project(path, bom, placement_path=placement, alignment_confirmed=True)
        self.assertTrue(confirmed["components"][0]["spatial_verified"])
        preview = Path(write_coordination_preview(confirmed, self.root / "confirmed.html", "R1")).read_text(encoding="utf-8")
        self.assertIn("<circle", preview.split("</svg>")[0])
        report = self.root / "report.xlsx"
        export_coordination(confirmed, report)
        workbook = openpyxl.load_workbook(report)
        self.assertEqual(workbook["器件BOM关联"]["C2"].value, "=BAD()")
        self.assertEqual(workbook["器件BOM关联"]["C2"].data_type, "s")
        workbook.close()
        export_coordination(confirmed, self.root / "report.json")
        self.assertEqual(json.loads((self.root / "report.json").read_text(encoding="utf-8"))["kind"], "gerber")

    def test_placement_invalid_units_duplicate_and_nonfinite(self):
        path = self.root / "placement.csv"
        path.write_text("refdes,x,y,unit,side\nR1,1,2,inch,TOP\n")
        self.assertEqual(read_placements(path)["records"][0]["x_mm"], 25.4)
        for text in ("refdes,x,y,side\nR1,1,2,TOP\n", "refdes,x_mm,y_mm,side\nR1,nan,2,TOP\n",
                     "refdes,x_mm,y_mm,side\nR1,1,2,TOP\nr1,1,2,BOTTOM\n"):
            path.write_text(text)
            with self.assertRaises(ValueError):
                read_placements(path)

    def test_archive_traversal_links_duplicate_and_size_budget(self):
        bad = self.root / "bad.zip"
        for name in ("../outside", "/absolute", "C:/escape"):
            write_zip(bad, {name: b"x"})
            with self.assertRaises(ValueError), InputContainer(bad):
                pass
        with zipfile.ZipFile(bad, "w") as archive:
            entry = zipfile.ZipInfo("link")
            entry.external_attr = (0o120777 << 16)
            archive.writestr(entry, "../outside")
        with self.assertRaises(ValueError), InputContainer(bad):
            pass
        write_zip(bad, {"data": b"123456"})
        with patch("src.design_inputs.archive.MAX_FILE_BYTES", 4), self.assertRaises(ValueError), InputContainer(bad):
            pass

    def test_changed_inputs_discard_entire_project(self):
        layout = self.root / "board.gtl"
        layout.write_text(GERBER)
        from src.design_inputs.coordination import parse_gerber_package as real
        def changed(path):
            result = real(path)
            layout.write_text(GERBER + "G04 changed*\n")
            return result
        with patch("src.design_inputs.coordination.parse_gerber_package", side_effect=changed), self.assertRaises(ValueError):
            load_manufacturing_project(layout)


class ODBFeatureTests(unittest.TestCase):
    def test_positive_negative_surface_hole(self):
        text = ("UNITS=MM\nS P 0\nOB 0 0 I\nOS 2 0\nOS 2 2\nOS 0 2\nOS 0 0\nOE\n"
                "OB .5 .5 H\nOS 1.5 .5\nOS 1.5 1.5\nOS .5 1.5\nOS .5 .5\nOE\nSE\n")
        result = parse_features(text)
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["geometry"].area, 3)
        self.assertFalse(result["geometry"].contains(Point(1, 1)))

    def test_pad_clockwise_orientation_and_symbol_units(self):
        result = parse_features("UNITS=MM\n$0 rect1000x2000\nP 1 2 0 P 0 1\n")
        self.assertEqual(result["geometry"].bounds, (0, 1.5, 2, 2.5))
        inch = parse_features("UNITS=INCH\n$0 r10\nP 1 1 0 P 0 0\n")
        self.assertAlmostEqual(inch["geometry"].centroid.x, 25.4)
        self.assertAlmostEqual(inch["geometry"].bounds[2]-inch["geometry"].bounds[0], .254, delta=.002)

    def test_unsupported_and_missing_units_not_false_parsed(self):
        result = parse_features("UNITS=MM\n$0 custom_symbol\nP 1 2 0 P 0 0\nT unsupported text\n")
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["geometry"].is_empty)
        with self.assertRaises(ValueError):
            parse_features("$0 r100\nP 0 0 0 P 0 0\n")

    def test_arc_and_clear_polarity(self):
        result = parse_features("UNITS=MM\n$0 r200\nA 1 0 -1 0 0 0 0 P 0 N\n")
        self.assertEqual(result["status"], "parsed")
        self.assertGreater(result["geometry"].bounds[3], 1.09)
        self.assertLess(result["geometry"].bounds[1], 0)
        clear = parse_features("UNITS=MM\n$0 r2000\n$1 r1000\nP 0 0 0 P 0 0\nP 0 0 1 N 0 0\n")
        self.assertFalse(clear["geometry"].contains(Point(0, 0)))
        self.assertAlmostEqual(clear["geometry"].area, 3.1415926*.75, delta=.04)


class ManufacturingUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from src.ui.manufacturing_dialog import ManufacturingDialog
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.dialog = ManufacturingDialog(self.root)

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.destroy()

    def wait(self):
        deadline = time.monotonic()+4
        while self.dialog.tasks.pending and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)
        self.assertFalse(self.dialog.tasks.pending)
        self.assertFalse(self.errors)

    def test_standalone_import_without_brd_and_stale_result(self):
        with tempfile.TemporaryDirectory() as directory:
            layout = Path(directory)/"top.gtl"
            layout.write_text(GERBER)
            self.dialog.layout_path.set(str(layout))
            self.dialog.run()
            self.wait()
            self.assertEqual(self.dialog.project["kind"], "gerber")
            self.assertEqual(len(self.dialog.layers.get_children()), 1)
            main = threading.get_ident()
            threads = []
            def slow(**args):
                threads.append(threading.get_ident())
                time.sleep(.1)
                return load_manufacturing_project(**args)
            with patch("src.ui.manufacturing_dialog.load_manufacturing_project", side_effect=slow):
                self.dialog.run()
                self.dialog.bom_path.set("new-bom.csv")
                self.wait()
            self.assertNotEqual(threads[0], main)
            self.assertIsNone(self.dialog.project)
            self.assertEqual(str(self.dialog.export_button.cget("state")), "disabled")

    def test_main_window_and_dfm_have_import_entry(self):
        from src.ui.main_panels import build_footer
        from src.ui.main_window import MainWindow
        from src.ui.dfm_dialog import DFMDialog
        self.assertTrue(callable(MainWindow._open_manufacturing))
        self.assertTrue(callable(DFMDialog._open_manufacturing))
        from types import SimpleNamespace
        win = SimpleNamespace(**{name: lambda: None for name in (
            "_run_checklist", "_run_demo", "_open_analyze", "_open_store_check",
            "_open_manufacturing", "_open_output", "_open_trace_filter")})
        build_footer(self.root, win)
        self.root.update_idletasks()
        self.assertIn("Gerber/ODB++", win.manufacturing_btn.cget("text"))


if __name__ == "__main__":
    unittest.main()

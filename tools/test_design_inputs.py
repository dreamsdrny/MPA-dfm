"""多格式输入合同及 BRD 主资料优先的回归测试。"""
import csv
import io
import json
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.design_inputs import (brd_component_records, export_bom, import_bom,
                               load_project, reconcile_bom)
from src.design_inputs.gerber import parse_gerber
from src.design_inputs.manufacturing import parse_odb, parse_step


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_gerber_roles_units_and_modal_geometry(self):
        p = self.root / "board.gbl"
        p.write_text("%FSLAX24Y24*%\n%MOIN*%\n%TF.FileFunction,Profile,NP*%\n"
                     "%ADD10C,0.010*%\nD10*\nX010000Y020000D02*\n"
                     "X020000D01*\nY030000*\nX010000Y010000D03*\nM02*", encoding="ascii")
        result = parse_gerber(p)
        self.assertEqual(result["role"], "outline")
        self.assertEqual(result["role_source"], "x2_file_function")
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["geometry"][0]["start_mm"], [25.4, 50.8])
        self.assertEqual(result["geometry"][1]["end_mm"], [50.8, 76.19999999999999])
        self.assertEqual(result["geometry"][2]["type"], "flash")
        self.assertAlmostEqual(result["apertures"]["10"]["parameters_mm"][0], 0.254)

    def test_bom_roundtrip_and_quantity(self):
        p = self.root / "bom.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Reference", "Part Number", "Qty"])
            writer.writerow(["R1,R2", "123", "2"])
        rows = import_bom(p)
        self.assertEqual([r["refdes"] for r in rows], ["R1", "R2"])
        self.assertEqual(rows[0]["dnp"], False)
        export_bom(p, rows)
        self.assertEqual(import_bom(p), rows)
        p.write_text("refdes,qty\nR1,2\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            import_bom(p)
        with self.assertRaises(ValueError):
            export_bom(p, [{"refdes": "R1"}, {"refdes": "r1"}])
        self.assertEqual(p.read_text(encoding="utf-8"), "refdes,qty\nR1,2\n")

    def test_odb_archives_and_structure(self):
        files = {"job/matrix/matrix": b"MATRIX", "job/misc/info": b"ODB_VERSION=8.1\n",
                 "job/steps/pcb/layers/top/features": b"ignored"}
        z = self.root / "job.zip"
        with zipfile.ZipFile(z, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        t = self.root / "job.tgz"
        with tarfile.open(t, "w:gz") as archive:
            for name, data in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        for path in (z, t):
            result = parse_odb(path)
            self.assertEqual(result["version"], "8.1")
            self.assertEqual(result["steps"], ["pcb"])
            self.assertEqual(result["layers"], ["pcb/top"])

    def test_step_does_not_invent_height(self):
        p = self.root / "part.stp"
        p.write_text("ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('AP214'));\nENDSEC;\nDATA;\n"
                     "#1=MANIFOLD_SOLID_BREP('x',#2);\nENDSEC;\nEND-ISO-10303-21;", encoding="ascii")
        result = parse_step(p)
        self.assertEqual(result["schema"], ["AP214"])
        self.assertEqual(result["entity_counts"]["MANIFOLD_SOLID_BREP"], 1)
        self.assertIsNone(result["height_mm"])

    def test_bom_side_dnp_normalization(self):
        p = self.root / "bom.csv"
        p.write_text("refdes,side,dnp\nR1,Top,Yes\nR2,B,No\n", encoding="utf-8")
        rows = import_bom(p)
        self.assertEqual((rows[0]["side"], rows[0]["dnp"]), ("TOP", True))
        self.assertEqual((rows[1]["side"], rows[1]["dnp"]), ("BOTTOM", False))

    def test_reconcile_verified(self):
        records = [{"refdes": "R1", "package": "0603"}, {"refdes": "C2", "package": ""}]
        rows = [{"refdes": "R1", "footprint": "0603", "part_number": "A"},
                {"refdes": "C2", "footprint": "", "part_number": "B"}]
        result = reconcile_bom(records, rows)
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["consistent"])
        self.assertEqual(result["summary"]["matched_count"], 2)

    def test_reconcile_missing_extra_conflict(self):
        records = [{"refdes": "R1", "package": "0603"},
                   {"refdes": "C1", "package": "0402"},
                   {"refdes": "U1", "package": "QFN"}]
        rows = [{"refdes": "R1", "footprint": "0603"},
                {"refdes": "U1", "footprint": "LQFP"},
                {"refdes": "J1", "footprint": "HDR", "dnp": False}]
        result = reconcile_bom(records, rows)
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["consistent"])
        self.assertEqual([x["refdes"] for x in result["missing_in_bom"]], ["C1"])
        self.assertEqual([x["refdes"] for x in result["missing_in_brd"]], ["J1"])
        self.assertEqual(result["conflicts"][0]["field"], "footprint")
        statuses = {row["refdes"]: row["status"] for row in result["rows"]}
        self.assertEqual(statuses, {"R1": "matched", "C1": "missing_in_bom",
                                    "U1": "conflict", "J1": "missing_in_brd"})

    def test_reconcile_value_keeps_decimal_difference(self):
        result = reconcile_bom([{"refdes": "R1", "value": "1.0k"}],
                               [{"refdes": "R1", "value": "10k"}])
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["conflicts"][0]["field"], "value")

    def test_reconcile_without_brd_components_is_unavailable(self):
        result = reconcile_bom([], [{"refdes": "R1"}])
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["consistent"])
        self.assertIn("不能判定缺漏", result["warnings"][0])
        self.assertEqual(result["missing_in_brd"], [])
        self.assertEqual(result["rows"][0]["status"], "unverified")

    def test_load_project_attaches_bom_reconciliation(self):
        board = ROOT / "demo" / "DEMO_BOARD.brd"
        records = brd_component_records(board)
        self.assertTrue(records["ok"])
        p = self.root / "full-bom.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["refdes", "part_number"])
            for record in records["records"]:
                writer.writerow([record["refdes"], "PN-" + record["refdes"]])
            writer.writerow(["J99", "EXTRA"])
        project = load_project(board, [p])
        self.assertEqual(len(project["bom_checks"]), 1)
        result = project["bom_checks"][0]["reconciliation"]
        self.assertEqual(result["status"], "review")
        self.assertEqual([x["refdes"] for x in result["missing_in_brd"]], ["J99"])
        self.assertEqual(project["supplements"][0]["reconciliation"]["status"], "review")

    def test_primary_and_failed_supplement(self):
        board = ROOT / "demo" / "DEMO_BOARD.brd"
        p = self.root / "bom.csv"
        p.write_text("refdes,part_number\nR1,ABC\n", encoding="utf-8")
        project = load_project(board, [p, self.root / "bad.unknown", self.root / "missing.csv"])
        self.assertTrue(project["primary"]["design"]["ok"])
        self.assertIsNotNone(project["primary"]["snapshot"])
        self.assertEqual(project["primary"]["geometry"], project["primary"]["design"]["board_geometry"])
        self.assertEqual([x["status"] for x in project["supplements"]], ["parsed", "error", "error"])
        self.assertEqual(project["supplements"][1]["kind"], "unknown")
        self.assertEqual(project["supplements"][2]["kind"], "bom")
        json.dumps(project)


if __name__ == "__main__":
    unittest.main()

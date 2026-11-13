"""store-brd / store-bom 联动检查 + Excel BOM 导入的回归测试。

覆盖三件事:
  1. 真实 Cadence 风格 Excel BOM (前面有标题行、表头是 Part Reference / PCB Footprint /
     IPN / MPN) 能读出来, 数量不符/重复位号在宽容模式下变成提示而不是整表失败;
  2. 文件配对 (BOM 的 PCB 行料号 = BRD 文件名料号; 其次共用数字核);
  3. 联动检查: 有位号清单时逐位号核对; 没有 Part.xml (FAB 拼板图) 时如实给"数据不足",
     同目录有 place_txt.txt 时能补上位号清单并恢复逐位号核对。
"""
import io
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import openpyxl
from openpyxl import Workbook

from src.design_inputs import store as store_module
from src.design_inputs.bom import import_bom, read_bom
from src.design_inputs.reconcile import brd_component_records
from src.pick import parse_placement_txt
from src.store_report import write_report

DEMO = ROOT / "demo" / "DEMO_BOARD.brd"
DEMO_PLACE = ROOT / "demo" / "place_txt.txt"
CADENCE_HEADERS = ["Item", "IPN", "Quantity", "PCB Footprint", "Value",
                   "Part Reference", "Manufacturer", "MPN", "Description",
                   "Schematic Page"]


def write_bom(path, rows, headers=CADENCE_HEADERS, title_rows=3, divider=True):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "S-870-000739_SCH_REVG"
    for index in range(title_rows):
        sheet.append(["Bill Of Materials      September" if index == 0 else ""])
    sheet.append(list(headers))
    if divider:
        sheet.append(["______________________"])
    for row in rows:
        sheet.append(list(row))
    workbook.save(path)
    return path


def bom_rows_from_records(records, part_number, footprint=False):
    """BOM 行: 第一行是 PCB 项 (料号 = BRD 料号), 之后一行一个位号。"""
    rows = [["PCB", part_number, 1, "", "BOARD", "PCB1", "", "", "", ""]]
    for index, record in enumerate(records, start=2):
        package = record.get("package") or "" if footprint else ""
        rows.append([index, "PN-" + record["refdes"], 1, package, "",
                     record["refdes"], "", "", "", ""])
    return rows


def build_brd_without_partxml(path):
    """合成一块 .brd 后删掉 objects/Part.xml —— 真实 FAB 拼板图就是这个样子。"""
    tools_dir = str(ROOT / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import make_demo_brd

    from src.brd_design import _carve_entries

    brd_bytes, _exports = make_demo_brd.build_sc_brd("NO_PART", with_exports=False)
    prefix = brd_bytes[:brd_bytes.find(b"PK\x03\x04")]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for _offset, name, data in _carve_entries(brd_bytes):
            if name == "objects/Part.xml":
                continue
            archive.writestr(name, data)
    path.write_bytes(prefix + buffer.getvalue())
    return path


class ExcelBomTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_cadence_style_xlsx_is_read(self):
        path = write_bom(self.root / "bom.xlsx", [
            [2, "E-120-000389", 2, "capc1005X75n-sh", "0.1uF", "C1,C13",
             "VIIYONG", "A104K0402X7R500NBT", "CAP CER", ""],
            [3, "E-180-000082", 1, "resc1608x95n", "2R", "R1",
             "YAGEO", "AC0603FR-072RL", "RES SMD", ""],
        ])
        info = read_bom(path)
        self.assertEqual(info["format"], "xlsx")
        self.assertEqual(info["header_row"], 4)
        self.assertEqual(len(info["rows"]), 3)
        self.assertEqual([row["refdes"] for row in info["rows"]], ["C1", "C13", "R1"])
        self.assertEqual(info["columns"]["Part Reference"], "refdes")
        self.assertEqual(info["columns"]["PCB Footprint"], "footprint")
        self.assertEqual(info["columns"]["IPN"], "part_number")
        self.assertEqual(info["columns"]["MPN"], "manufacturer_part_number")
        self.assertEqual(info["unmapped"], [])
        self.assertEqual(info["warnings"], [])
        self.assertIn("E-120-000389", info["part_numbers"])
        self.assertEqual(info["rows"][0]["footprint"], "capc1005X75n-sh")
        self.assertEqual(info["rows"][0]["manufacturer_part_number"], "A104K0402X7R500NBT")

    def test_quantity_mismatch_is_warning_not_failure(self):
        path = write_bom(self.root / "bad.xlsx", [
            [2, "E-1", 5, "", "", "R1,R2", "", "", "", ""],
            [3, "E-2", 1, "", "", "R3", "", "", "", ""],
        ])
        info = read_bom(path, strict=False)
        self.assertEqual(len(info["rows"]), 3)
        self.assertTrue(any("数量 5" in warning for warning in info["warnings"]))
        with self.assertRaises(ValueError):
            read_bom(path, strict=True)

    def test_duplicate_refdes_and_blank_rows(self):
        path = write_bom(self.root / "dup.xlsx", [
            [2, "E-1", 1, "", "", "R1", "", "", "", ""],
            [3, "E-2", 1, "", "", "R1", "", "", "", ""],
            [4, "", 1, "", "", "", "", "", "", ""],
        ], divider=False)
        info = read_bom(path, strict=False)
        self.assertEqual([row["refdes"] for row in info["rows"]], ["R1"])
        self.assertEqual(info["stats"]["duplicate_refdes"], ["R1"])
        self.assertEqual(info["stats"]["skipped_no_refdes"], 1)
        self.assertTrue(any("位号重复" in warning for warning in info["warnings"]))

    def test_chinese_headers_and_dnp(self):
        path = write_bom(self.root / "cn.xlsx", [
            [1, "C1,C2", "0603", 2, ""],
            [2, "R1", "0402", 1, "是"],
        ], headers=["序号", "位号", "封装", "数量", "不贴"], title_rows=0, divider=False)
        info = read_bom(path)
        self.assertEqual([row["refdes"] for row in info["rows"]], ["C1", "C2", "R1"])
        self.assertEqual(info["rows"][0]["footprint"], "0603")
        self.assertTrue(info["rows"][2]["dnp"])

    def test_csv_contract_still_strict(self):
        path = self.root / "bom.csv"
        path.write_text("refdes,qty\nR1,2\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            import_bom(path)


class StoreCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.brd_dir = self.root / "store-brd"
        self.bom_dir = self.root / "store-bom"
        self.brd_dir.mkdir()
        self.bom_dir.mkdir()
        shutil.copy(DEMO, self.brd_dir / "S-860-000777_FAB_BRD_Rev_B.brd")

    def tearDown(self):
        self.temp.cleanup()

    def _pair(self):
        return store_module.check_pair(
            store_module.scan_brd(str(self.brd_dir))[0],
            store_module.scan_bom(str(self.bom_dir))[0])

    def test_name_info_extracts_part_and_revision(self):
        info = store_module.name_info("S-860-000000_FAB_BRD_Rev_E.brd")
        self.assertEqual(info["part_number"], "S-860-000000")
        self.assertEqual(info["core"], "860-000739")
        self.assertEqual(info["rev"], "E")
        info = store_module.name_info("S-850-000000_BOM_REVG.BOM.xlsx")
        self.assertEqual(info["part_number"], "S-850-000000")
        self.assertEqual(info["rev"], "G")

    def test_pair_by_bom_part_number(self):
        records = brd_component_records(DEMO)["records"]
        bom = write_bom(self.bom_dir / "S-850-000777_BOM_REVB.BOM.xlsx",
                        bom_rows_from_records(records, "S-860-000777"))
        brd_files = store_module.scan_brd(str(self.brd_dir))
        bom_files = store_module.scan_bom(str(self.bom_dir))
        infos = {item["path"]: read_bom(item["path"]) for item in bom_files}
        pairing = store_module.pair_stores(brd_files, bom_files, infos)
        self.assertEqual(len(pairing["pairs"]), 1)
        self.assertEqual(pairing["pairs"][0]["bom"]["path"], str(bom))
        self.assertEqual(pairing["unmatched_brd"], [])
        self.assertGreaterEqual(pairing["pairs"][0]["score"], 100)

    def test_check_pair_verified(self):
        records = brd_component_records(DEMO)["records"]
        write_bom(self.bom_dir / "S-850-000777_BOM_REVB.BOM.xlsx",
                  bom_rows_from_records(records, "S-860-000777"))
        pair = self._pair()
        self.assertEqual(pair["verdict"], "pass", pair["checks"])
        self.assertEqual(pair["reconciliation"]["status"], "verified")
        self.assertEqual(pair["reconciliation"]["summary"]["matched_count"], len(records))
        self.assertEqual([item["refdes"] for item in pair["bom_items"]["non_electrical"]],
                         ["PCB1"])
        self.assertEqual(pair["bom"]["summary"]["rows"], len(records) + 1)
        by_id = {item["id"]: item for item in pair["checks"]}
        self.assertEqual(by_id["id.part"]["status"], "pass")
        self.assertEqual(by_id["id.rev"]["status"], "pass")
        self.assertEqual(by_id["brd.refdes"]["status"], "pass")
        self.assertTrue(all(item["status"] != "fail" for item in pair["checks"]))

    def test_extra_bom_refdes_needs_review(self):
        records = brd_component_records(DEMO)["records"]
        rows = bom_rows_from_records(records, "S-860-000777")
        rows.append([999, "PN-J99", 1, "", "", "J99", "", "", "", ""])
        write_bom(self.bom_dir / "S-850-000777_BOM_REVB.BOM.xlsx", rows)
        pair = self._pair()
        self.assertEqual(pair["verdict"], "warn")
        self.assertEqual([item["refdes"] for item in
                          pair["reconciliation"]["missing_in_brd"]], ["J99"])

    def test_without_partxml_reports_unavailable(self):
        build_brd_without_partxml(self.brd_dir / "S-860-000777_FAB_BRD_Rev_B.brd")
        records = brd_component_records(DEMO)["records"]
        write_bom(self.bom_dir / "S-850-000777_BOM_REVB.BOM.xlsx",
                  bom_rows_from_records(records, "S-860-000777"))
        pair = self._pair()
        self.assertFalse(pair["brd_records"]["ok"])
        self.assertEqual(pair["verdict"], "warn")
        self.assertEqual(pair["reconciliation"]["status"], "unavailable")
        self.assertTrue(all(row["status"] == "unverified"
                            for row in pair["reconciliation"]["rows"]))
        check = next(item for item in pair["checks"] if item["id"] == "brd.refdes")
        self.assertEqual(check["status"], "unavailable")
        self.assertIn("place_txt.txt", check["detail"])

    def test_placement_export_supplies_refdes(self):
        build_brd_without_partxml(self.brd_dir / "S-860-000777_FAB_BRD_Rev_B.brd")
        shutil.copy(DEMO_PLACE, self.brd_dir / "place_txt.txt")
        placement = parse_placement_txt(str(self.brd_dir / "place_txt.txt"))
        records = [{"refdes": item["refdes"], "package": item["footprint"]}
                   for item in placement["items"]]
        write_bom(self.bom_dir / "S-850-000777_BOM_REVB.BOM.xlsx",
                  bom_rows_from_records(records, "S-860-000777", footprint=True))
        pair = self._pair()
        self.assertTrue(pair["brd_records"]["ok"])
        self.assertIn("Placement", pair["brd_records"]["source"])
        self.assertEqual(pair["reconciliation"]["status"], "verified", pair["checks"])
        self.assertEqual(pair["verdict"], "pass", pair["checks"])

    def test_run_check_and_reports(self):
        records = brd_component_records(DEMO)["records"]
        write_bom(self.bom_dir / "S-850-000777_BOM_REVB.BOM.xlsx",
                  bom_rows_from_records(records, "S-860-000777"))
        bundle = store_module.run_check(brd_dir=str(self.brd_dir),
                                        bom_dir=str(self.bom_dir))
        self.assertTrue(bundle["ok"])
        self.assertEqual(bundle["stats"]["pair_count"], 1)
        self.assertEqual(bundle["verdict"], "pass", bundle["warnings"])
        files = write_report(bundle, str(self.root / "out"), base_name="check")
        for key in ("xlsx", "txt", "html", "json"):
            self.assertTrue(Path(files[key]).is_file(), key)
        workbook = openpyxl.load_workbook(files["xlsx"])
        self.assertIn("配对结论", workbook.sheetnames)
        self.assertIn("位号核对", workbook.sheetnames)
        self.assertIn("verified", Path(files["json"]).read_text(encoding="utf-8"))

    def test_corrupt_bom_does_not_abort_run(self):
        (self.bom_dir / "S-850-000777_BOM_REVB.BOM.xlsx").write_bytes(b"not a zip")
        with self.assertRaises(ValueError):
            read_bom(self.bom_dir / "S-850-000777_BOM_REVB.BOM.xlsx")
        bundle = store_module.run_check(brd_dir=str(self.brd_dir),
                                        bom_dir=str(self.bom_dir))
        self.assertTrue(bundle["ok"])
        self.assertEqual(bundle["stats"]["pair_count"], 1)
        pair = bundle["pairs"][0]
        self.assertEqual(pair["verdict"], "fail")
        self.assertIsNone(pair["reconciliation"])
        bom_read = next(item for item in pair["checks"] if item["id"] == "bom.read")
        self.assertEqual(bom_read["status"], "fail")
        self.assertTrue(any("无法打开 Excel" in item for item in bundle["warnings"]))
        files = write_report(bundle, str(self.root / "out"), base_name="bad")
        self.assertTrue(Path(files["xlsx"]).is_file())

    def test_load_project_excludes_non_electrical(self):
        from src.design_inputs import load_project

        records = brd_component_records(DEMO)["records"]
        path = write_bom(self.root / "bom.xlsx",
                         bom_rows_from_records(records, "S-860-000777"))
        project = load_project(str(DEMO), [path])
        entry = project["supplements"][0]
        self.assertEqual(entry["status"], "parsed")
        self.assertEqual([item["refdes"] for item in entry["non_electrical"]], ["PCB1"])
        self.assertEqual(len(entry["rows"]), len(records) + 1)
        self.assertEqual(entry["reconciliation"]["status"], "verified")

    def test_legacy_xls_is_flagged(self):
        (self.bom_dir / "S-850-000777_BOM_REVB.BOM.xls").write_bytes(b"legacy")
        bundle = store_module.run_check(brd_dir=str(self.brd_dir),
                                        bom_dir=str(self.bom_dir))
        self.assertEqual(bundle["stats"]["bom_count"], 0)
        self.assertEqual(bundle["verdict"], "unavailable")
        self.assertTrue(any(".xls" in item and ".xlsx" in item
                            for item in bundle["warnings"]))

    def test_unmatched_reported(self):
        records = brd_component_records(DEMO)["records"]
        write_bom(self.bom_dir / "S-850-111111_BOM_REVA.BOM.xlsx",
                  bom_rows_from_records(records, "S-880-111111"))
        bundle = store_module.run_check(brd_dir=str(self.brd_dir),
                                        bom_dir=str(self.bom_dir))
        self.assertEqual(bundle["stats"]["pair_count"], 0)
        self.assertEqual(bundle["stats"]["unmatched_brd"], 1)
        self.assertEqual(bundle["stats"]["unmatched_bom"], 1)
        self.assertEqual(bundle["verdict"], "warn")
        self.assertIn("没有料号对应", bundle["unmatched_brd"][0]["reason"])


if __name__ == "__main__":
    unittest.main()

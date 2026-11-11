"""Supplier-profile contract tests; use temp files, not production outputs."""
import copy
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.smt_engine import analyze_smt, export_smt_report, load_smt_profile


class SMTContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "supplier.yaml"
        self.rule = dict(id="spacing", label="间距", field="spacing", scope="design",
                         unit="mm", op=">=", value=0.1, severity="high")
        self.doc = dict(schema_version=1, meta=dict(factory="测试厂", revision="v1"),
                        profiles=dict(standard=dict(rules=[self.rule])))

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, doc=None):
        self.path.write_text(yaml.safe_dump(doc or self.doc, allow_unicode=True), encoding="utf-8")
        return load_smt_profile(self.path)

    def test_units_and_threshold_boundary(self):
        doc = self.load()
        for value, expected in [(100, "pass"), (99, "fail"), (0, "fail")]:
            result = analyze_smt(doc, "standard", {("design", "spacing"):
                                 dict(value=value, unit="um", source="实测")})
            self.assertEqual(result["items"][0]["status"], expected)
        self.assertEqual(result["risk"], "高")

    def test_unknown_is_not_pass(self):
        result = analyze_smt(self.load(), "standard", {})
        self.assertEqual(result["items"][0]["status"], "missing")
        self.assertEqual(result["coverage"], 0)
        self.rule["value"] = None
        result = analyze_smt(self.load(), "standard", {})
        self.assertEqual(result["items"][0]["status"], "unconfigured")

    def test_process_not_filled_by_design(self):
        self.rule["scope"] = "process"
        result = analyze_smt(self.load(), "standard", {("design", "spacing"):
                             dict(value=1, unit="mm", source="CAD")})
        self.assertEqual(result["items"][0]["status"], "missing")

    def test_ranges_and_partial_ranges(self):
        self.rule.update(op="range", min=0.1, max=0.2)
        for value, expected in [(0.1, "pass"), (0.2, "pass"), (0.21, "fail")]:
            result = analyze_smt(self.load(), "standard", {("design", "spacing"):
                                 dict(value=value, unit="mm", source="人工")})
            self.assertEqual(result["items"][0]["status"], expected)
        self.rule["max"] = None
        self.assertEqual(analyze_smt(self.load(), "standard", {})["items"][0]["status"], "unconfigured")

    def test_invalid_configs_rejected(self):
        for changes in [dict(unit="oz"), dict(value=math.nan), dict(value=True),
                        dict(op="evil"), dict(scope="auto"), dict(severity="critical")]:
            doc = copy.deepcopy(self.doc)
            doc["profiles"]["standard"]["rules"][0].update(changes)
            with self.assertRaises(ValueError):
                self.load(doc)
        doc = copy.deepcopy(self.doc)
        doc["profiles"]["standard"]["rules"].append(copy.deepcopy(self.rule))
        with self.assertRaises(ValueError):
            self.load(doc)

    def test_input_contract_and_explicit_profile(self):
        doc = self.load()
        with self.assertRaises(ValueError):
            analyze_smt(doc, "automotive", {})
        for fact in [dict(value=1, unit="mm"), dict(value=math.inf, unit="mm", source="CAD"),
                     dict(value=1, unit="ratio", source="CAD")]:
            with self.assertRaises(ValueError):
                analyze_smt(doc, "standard", {("design", "spacing"): fact})

    def test_report_preserves_configuration_and_text(self):
        from openpyxl import load_workbook
        self.rule["advice"] = "=1+2"
        result = analyze_smt(self.load(), "standard", {})
        output = Path(self.tmp.name) / "result.xlsx"
        export_smt_report(result, output)
        with output.open("rb") as handle:
            wb = load_workbook(handle)
            self.assertEqual(wb["逐项比较"]["K2"].value, "=1+2")
            self.assertEqual(wb["逐项比较"]["K2"].data_type, "s")
            self.assertIn(str(self.path.resolve()), [c.value for c in wb["概览"]["B"]])
            wb.close()
        self.assertTrue(output.with_suffix(".json").is_file())

    def test_repository_template_loads_without_pass(self):
        path = Path(__file__).resolve().parents[1] / "config" / "smt_capability_template.yaml"
        doc = load_smt_profile(path)
        result = analyze_smt(doc, "standard", {})
        self.assertEqual(result["counts"]["pass"], 0)
        self.assertEqual(result["counts"]["unconfigured"], len(result["items"]))


if __name__ == "__main__":
    unittest.main()

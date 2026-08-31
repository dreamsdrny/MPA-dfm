"""
BRD-AI: 规则计算引擎 (Module 3)
根据Checklist规则和信号分类结果，计算每个Net Class的约束参数
"""
import yaml
import os
from typing import Dict, Any, Optional


def _load_yaml(filename: str) -> dict:
    config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
    path = os.path.join(config_dir, filename)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


DEFAULT_WIDTH_TABLE: Dict[str, dict] = {
    "POWER":       {"min": 0.30, "preferred": 0.50, "max": 2.00, "unit": "mm"},
    "GND":         {"min": 0.30, "preferred": 0.50, "max": 2.00, "unit": "mm"},
    "CLOCK":       {"min": 0.10, "preferred": 0.15, "max": 0.20, "unit": "mm"},
    "RESET":       {"min": 0.10, "preferred": 0.15, "max": 0.20, "unit": "mm"},
    "DDR":         {"min": 0.08, "preferred": 0.10, "max": 0.12, "unit": "mm"},
    "PCIE":        {"min": 0.08, "preferred": 0.10, "max": 0.12, "unit": "mm"},
    "USB3":        {"min": 0.09, "preferred": 0.10, "max": 0.12, "unit": "mm"},
    "USB2":        {"min": 0.10, "preferred": 0.15, "max": 0.20, "unit": "mm"},
    "MIPI":        {"min": 0.08, "preferred": 0.09, "max": 0.10, "unit": "mm"},
    "LVDS":        {"min": 0.08, "preferred": 0.10, "max": 0.12, "unit": "mm"},
    "ETHERNET":    {"min": 0.09, "preferred": 0.10, "max": 0.12, "unit": "mm"},
    "SPI":         {"min": 0.10, "preferred": 0.15, "max": 0.20, "unit": "mm"},
    "I2C":         {"min": 0.10, "preferred": 0.15, "max": 0.20, "unit": "mm"},
    "UART":        {"min": 0.10, "preferred": 0.15, "max": 0.20, "unit": "mm"},
    "CAN":         {"min": 0.10, "preferred": 0.15, "max": 0.20, "unit": "mm"},
    "RF":          {"min": 0.15, "preferred": 0.18, "max": 0.25, "unit": "mm"},
    "GATE_DRIVE":  {"min": 0.30, "preferred": 0.50, "max": 1.00, "unit": "mm"},
    "SENSE":       {"min": 0.10, "preferred": 0.15, "max": 0.20, "unit": "mm"},
    "LED":         {"min": 0.10, "preferred": 0.15, "max": 0.20, "unit": "mm"},
    "JTAG":        {"min": 0.10, "preferred": 0.15, "max": 0.20, "unit": "mm"},
    "DEFAULT":     {"min": 0.10, "preferred": 0.15, "max": 0.20, "unit": "mm"},
}

DEFAULT_SPACING_TABLE: Dict[str, dict] = {
    "POWER":       {"line_to_line": 0.30, "line_to_pin": 0.30, "line_to_via": 0.25, "unit": "mm"},
    "GND":         {"line_to_line": 0.30, "line_to_pin": 0.30, "line_to_via": 0.25, "unit": "mm"},
    "CLOCK_3W":    {"line_to_line": "3W", "line_to_pin": 0.30, "line_to_via": 0.25, "unit": "mm"},
    "RESET_5W":    {"line_to_line": "5W", "line_to_pin": 0.30, "line_to_via": 0.25, "unit": "mm"},
    "DDR_3W":      {"line_to_line": "3W", "line_to_pin": 0.20, "line_to_via": 0.20, "unit": "mm"},
    "PCIE_3W":     {"line_to_line": "3W", "line_to_pin": 0.20, "line_to_via": 0.20, "unit": "mm"},
    "USB3":        {"line_to_line": 0.15, "line_to_pin": 0.15, "line_to_via": 0.15, "unit": "mm"},
    "USB2":        {"line_to_line": 0.15, "line_to_pin": 0.15, "line_to_via": 0.15, "unit": "mm"},
    "MIPI_3W":     {"line_to_line": "3W", "line_to_pin": 0.15, "line_to_via": 0.15, "unit": "mm"},
    "LVDS_5H":     {"line_to_line": "5H", "line_to_pin": 0.20, "line_to_via": 0.20, "unit": "mm"},
    "ETHERNET":    {"line_to_line": 0.20, "line_to_pin": 0.20, "line_to_via": 0.20, "unit": "mm"},
    "SPI_3W":      {"line_to_line": "3W", "line_to_pin": 0.15, "line_to_via": 0.15, "unit": "mm"},
    "CAN":         {"line_to_line": 0.20, "line_to_pin": 0.20, "line_to_via": 0.20, "unit": "mm"},
    "RF":          {"line_to_line": 0.30, "line_to_pin": 0.30, "line_to_via": 0.25, "unit": "mm"},
    "GATE_DRIVE":  {"line_to_line": 0.50, "line_to_pin": 0.50, "line_to_via": 0.50, "unit": "mm"},
    "SENSE":       {"line_to_line": 0.20, "line_to_pin": 0.15, "line_to_via": 0.15, "unit": "mm"},
    "default":     {"line_to_line": 0.15, "line_to_pin": 0.15, "line_to_via": 0.15, "unit": "mm"},
}

DEFAULT_VIA_TABLE: Dict[str, dict] = {
    "POWER":       {"pad": 0.50, "drill": 0.25, "unit": "mm"},
    "GND":         {"pad": 0.50, "drill": 0.25, "unit": "mm"},
    "CLOCK":       {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "RESET":       {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "DDR":         {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "PCIE":        {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "USB3":        {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "USB2":        {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "MIPI":        {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "LVDS":        {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "ETHERNET":    {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "SPI":         {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "I2C":         {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "UART":        {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "CAN":         {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "RF":          {"pad": 0.50, "drill": 0.25, "unit": "mm"},
    "GATE_DRIVE":  {"pad": 0.60, "drill": 0.30, "unit": "mm"},
    "SENSE":       {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "LED":         {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "JTAG":        {"pad": 0.40, "drill": 0.20, "unit": "mm"},
    "DEFAULT":     {"pad": 0.40, "drill": 0.20, "unit": "mm"},
}

DIFF_PAIR_CONFIG: Dict[str, dict] = {
    "DDR":      {"min_line": 0.08, "pref_line": 0.10, "max_line": 0.12,
                 "min_gap": 0.08, "pref_gap": 0.10, "max_gap": 0.15, "unit": "mm"},
    "PCIE":     {"min_line": 0.08, "pref_line": 0.10, "max_line": 0.12,
                 "min_gap": 0.08, "pref_gap": 0.10, "max_gap": 0.15, "unit": "mm"},
    "USB3":     {"min_line": 0.09, "pref_line": 0.10, "max_line": 0.12,
                 "min_gap": 0.09, "pref_gap": 0.10, "max_gap": 0.15, "unit": "mm"},
    "USB2":     {"min_line": 0.10, "pref_line": 0.15, "max_line": 0.20,
                 "min_gap": 0.10, "pref_gap": 0.15, "max_gap": 0.20, "unit": "mm"},
    "MIPI":     {"min_line": 0.08, "pref_line": 0.09, "max_line": 0.10,
                 "min_gap": 0.08, "pref_gap": 0.09, "max_gap": 0.12, "unit": "mm"},
    "LVDS":     {"min_line": 0.08, "pref_line": 0.10, "max_line": 0.12,
                 "min_gap": 0.08, "pref_gap": 0.10, "max_gap": 0.15, "unit": "mm"},
    "ETHERNET": {"min_line": 0.09, "pref_line": 0.10, "max_line": 0.12,
                 "min_gap": 0.09, "pref_gap": 0.10, "max_gap": 0.15, "unit": "mm"},
    "CAN":      {"min_line": 0.10, "pref_line": 0.15, "max_line": 0.20,
                 "min_gap": 0.10, "pref_gap": 0.15, "max_gap": 0.20, "unit": "mm"},
}


def _get_width_rule(rule_name: str) -> dict:
    rule = DEFAULT_WIDTH_TABLE.get(rule_name, DEFAULT_WIDTH_TABLE["DEFAULT"])
    return dict(rule)


def _get_spacing_rule(rule_name: str) -> dict:
    rule = DEFAULT_SPACING_TABLE.get(rule_name, DEFAULT_SPACING_TABLE["default"])
    return dict(rule)


def _get_via_rule(rule_name: str) -> dict:
    rule = DEFAULT_VIA_TABLE.get(rule_name, DEFAULT_VIA_TABLE["DEFAULT"])
    return dict(rule)


def compute_class_constraints(signal_classification: dict) -> dict:
    net_classes = signal_classification.get("net_classes", {})
    diff_pairs = signal_classification.get("diff_pairs", [])
    checklist_rules = _load_yaml("checklist_rules.yaml")

    physical_constraints = []
    spacing_constraints = []
    electrical_constraints = []
    via_constraints = []

    for cls_name, cls_info in net_classes.items():
        width_rule = cls_info.get("width_rule", "default")
        spacing_rule = cls_info.get("spacing_rule", "default")
        protocol = cls_info.get("protocol")

        width = _get_width_rule(width_rule)
        spacing = _get_spacing_rule(spacing_rule)
        via = _get_via_rule(width_rule)

        physical_constraints.append({
            "name": f"PHYS_{cls_name}",
            "net_class": cls_name,
            "nets": cls_info.get("nets", []),
            "width_min": width["min"],
            "width_preferred": width["preferred"],
            "width_max": width["max"],
            "unit": width["unit"],
        })

        spacing_constraints.append({
            "name": f"SPC_{cls_name}",
            "net_class": cls_name,
            "line_to_line": spacing.get("line_to_line", 0.15),
            "line_to_pin": spacing.get("line_to_pin", 0.15),
            "line_to_via": spacing.get("line_to_via", 0.15),
            "unit": spacing.get("unit", "mm"),
        })

        via_constraints.append({
            "name": f"VIA_{cls_name}",
            "net_class": cls_name,
            "pad": via["pad"],
            "drill": via["drill"],
            "unit": via["unit"],
        })

        if protocol and cls_info.get("is_diff_pair") and protocol in DIFF_PAIR_CONFIG:
            dp_cfg = DIFF_PAIR_CONFIG[protocol]
            impedance_cfg = checklist_rules.get("differential_impedance", {}).get(
                protocol, checklist_rules["differential_impedance"]["default"]
            )

            electrical_constraints.append({
                "name": f"ELEC_{cls_name}",
                "net_class": cls_name,
                "protocol": protocol,
                "is_diff_pair": True,
                "min_line_width": dp_cfg["min_line"],
                "pref_line_width": dp_cfg["pref_line"],
                "max_line_width": dp_cfg["max_line"],
                "min_gap": dp_cfg["min_gap"],
                "pref_gap": dp_cfg["pref_gap"],
                "max_gap": dp_cfg["max_gap"],
                "impedance_target": impedance_cfg["impedance"],
                "impedance_tolerance": impedance_cfg["tolerance"],
                "unit": dp_cfg["unit"],
            })

        if cls_info.get("impedance_target") and not cls_info.get("is_diff_pair"):
            impedance_cfg = checklist_rules.get("differential_impedance", {}).get(
                protocol, {"impedance": 50, "tolerance": 10, "unit": "ohm"}
            ) if protocol else {"impedance": 50, "tolerance": 10, "unit": "ohm"}
            electrical_constraints.append({
                "name": f"ELEC_{cls_name}",
                "net_class": cls_name,
                "protocol": protocol,
                "is_diff_pair": False,
                "impedance_target": impedance_cfg["impedance"],
                "impedance_tolerance": impedance_cfg["tolerance"],
            })

    return {
        "physical_constraints": physical_constraints,
        "spacing_constraints": spacing_constraints,
        "electrical_constraints": electrical_constraints,
        "via_constraints": via_constraints,
        "diff_pairs": diff_pairs,
        "checklist_rules": checklist_rules,
    }
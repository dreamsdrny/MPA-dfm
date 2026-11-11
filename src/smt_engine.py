"""SMT capability profiles: validated YAML -> explicit, traceable comparisons.

This layer compares supplied design requirements/process measurements with a
supplier profile. It does not infer geometry or certify an actual production line.
"""
import math
import re
from pathlib import Path

import yaml

UNITS = {"mm": ("length", 1.0), "um": ("length", 0.001),
         "mil": ("length", 0.0254), "count": ("count", 1.0),
         "ratio": ("ratio", 1.0), "percent": ("percent", 1.0),
         "deg": ("angle", 1.0)}
SEVERITIES = ("high", "medium", "low")
STATUSES = {"pass": "通过", "fail": "不符合", "missing": "数据不足",
            "unconfigured": "能力未提供"}


def number(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label}: 布尔值不能作为数值")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: 需要有效数值") from None
    if not math.isfinite(result):
        raise ValueError(f"{label}: 不能是 NaN 或无穷大")
    return result


def load_smt_profile(path):
    """Load a complete file atomically; never silently fall back to another level."""
    with open(path, encoding="utf-8-sig") as handle:
        doc = yaml.safe_load(handle)
    if not isinstance(doc, dict) or doc.get("schema_version") != 1:
        raise ValueError("SMT YAML 必须包含 schema_version: 1")
    meta = doc.get("meta")
    if not isinstance(meta, dict) or not all(str(meta.get(k) or "").strip()
                                            for k in ("factory", "revision")):
        raise ValueError("meta.factory 和 meta.revision 必须填写")
    profiles = doc.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("profiles 必须包含至少一个产线/工艺配置")
    for key, profile in profiles.items():
        if not isinstance(key, str) or not isinstance(profile, dict):
            raise ValueError("profiles 的名称必须是字符串，内容必须是映射")
        rules = profile.get("rules")
        if not isinstance(rules, list) or not rules:
            raise ValueError(f"{key}.rules 必须是非空列表")
        ids, fields = set(), {}
        for rule in rules:
            if not isinstance(rule, dict):
                raise ValueError(f"{key}: 每条 rule 必须是映射")
            for name in ("id", "field", "label"):
                if not isinstance(rule.get(name), str) or not rule[name].strip():
                    raise ValueError(f"{key}: rule.{name} 必须填写")
            if rule["id"] in ids:
                raise ValueError(f"{key}: 重复规则 ID {rule['id']}")
            ids.add(rule["id"])
            if not re.fullmatch(r"[a-z][a-z0-9_]*", rule["field"]):
                raise ValueError(f"{rule['id']}: field 使用小写字母/数字/下划线")
            if rule.get("unit") not in UNITS:
                raise ValueError(f"{rule['id']}: unit 必须为 {', '.join(UNITS)}")
            scope = rule.get("scope", "design")
            if scope not in ("design", "process"):
                raise ValueError(f"{rule['id']}: scope 必须为 design 或 process")
            rule["scope"] = scope
            if rule.get("severity", "medium") not in SEVERITIES:
                raise ValueError(f"{rule['id']}: severity 必须为 high/medium/low")
            if rule.get("op") not in (">=", "<=", "range"):
                raise ValueError(f"{rule['id']}: op 必须为 >=、<= 或 range")
            names = ("min", "max") if rule["op"] == "range" else ("value",)
            for name in names:
                if name not in rule:
                    raise ValueError(f"{rule['id']}: 缺少 {name} (未知能力请写 null)")
                if rule[name] is not None:
                    rule[name] = number(rule[name], f"{rule['id']}.{name}")
            if (rule["op"] == "range" and rule["min"] is not None
                    and rule["max"] is not None and rule["min"] > rule["max"]):
                raise ValueError(f"{rule['id']}: min 不能大于 max")
            field_key = (scope, rule["field"])
            signature = UNITS[rule["unit"]][0]
            if field_key in fields and fields[field_key] != signature:
                raise ValueError(f"{rule['id']}: 同一字段的单位量纲冲突")
            fields[field_key] = signature
    doc["path"] = str(Path(path).resolve())
    return doc


def analyze_smt(doc, profile_key, facts):
    """facts[(scope, field)] = {value, unit, source}; zero is a valid value."""
    if profile_key not in doc["profiles"]:
        raise ValueError(f"没有 SMT 配置: {profile_key}")
    profile = doc["profiles"][profile_key]
    items = []
    for rule in profile["rules"]:
        limits = [rule.get(n) for n in
                  (("min", "max") if rule["op"] == "range" else ("value",))]
        item = {"id": rule["id"], "label": rule["label"],
                "field": rule["field"], "scope": rule["scope"], "unit": rule["unit"],
                "severity": rule.get("severity", "medium"),
                "advice": str(rule.get("advice") or "请与 SMT 工艺工程师核对"),
                "note": str(rule.get("note") or ""), "source": "", "actual": None,
                "margin": None,
                "requirement": (f"{limits[0]} ~ {limits[1]}" if rule["op"] == "range"
                                else f"{rule['op']} {limits[0]}")}
        fact = facts.get((rule["scope"], rule["field"]))
        if any(v is None for v in limits):
            item.update(status="unconfigured", reason="厂商尚未提供完整定量能力")
        elif not fact or fact.get("value") is None:
            item.update(status="missing", reason=f"缺少 {rule['label']} 的已确认输入")
        else:
            unit = fact.get("unit")
            if unit not in UNITS or UNITS[unit][0] != UNITS[rule["unit"]][0]:
                raise ValueError(f"{rule['id']}: 输入单位 {unit} 与规则单位不兼容")
            if not str(fact.get("source") or "").strip():
                raise ValueError(f"{rule['id']}: 输入必须注明来源")
            actual = number(fact["value"], rule["field"]) * UNITS[unit][1] / UNITS[rule["unit"]][1]
            if rule["op"] == ">=":
                margin = actual - limits[0]
            elif rule["op"] == "<=":
                margin = limits[0] - actual
            else:
                margin = min(actual - limits[0], limits[1] - actual)
            # Allow only rounding noise, not a configurable manufacturing tolerance.
            ok = margin >= -1e-10
            item.update(actual=actual, margin=margin, source=fact["source"],
                        status="pass" if ok else "fail",
                        reason="满足配置能力" if ok else "超出配置能力")
        items.append(item)
    counts = {s: sum(it["status"] == s for it in items) for s in STATUSES}
    measured = counts["pass"] + counts["fail"]
    failed = [it for it in items if it["status"] == "fail"]
    risk = next((s for s in SEVERITIES if any(it["severity"] == s for it in failed)), None)
    verdict = ("不符合所选能力" if failed else
               "数据或能力不完整" if measured < len(items) else "所选能力项匹配")
    return {"meta": dict(doc["meta"]), "profile": profile_key,
            "profile_label": profile.get("label", profile_key), "config_path": doc["path"],
            "items": items, "counts": counts, "coverage": measured / len(items),
            "risk": {"high": "高", "medium": "中", "low": "低"}.get(risk, "未发现不符合"),
            "verdict": verdict,
            "conclusion_note": "能力匹配反映本次输入与配置的比较；产线实际管控水平需测量及过程数据验证。"}


def append_smt_sheets(wb, result, prefix=""):
    """Append the same SMT sheets to a standalone or combined workbook."""
    overview = wb.create_sheet(prefix + "概览")
    for row in [("项目", "内容"), ("厂商", result["meta"]["factory"]),
                ("板名", result.get("board_name", "")),
                ("配置版本", result["meta"]["revision"]), ("配置文件", result["config_path"]),
                ("产线/工艺", result["profile_label"]), ("结论", result["verdict"]),
                ("判定覆盖率", result["coverage"]), ("风险", result["risk"]),
                ("结论口径", result["conclusion_note"])]:
        overview.append(row)
    ws = wb.create_sheet(prefix + "逐项比较")
    keys = ["id", "label", "scope", "actual", "requirement", "unit", "margin",
            "status", "source", "reason", "advice", "note"]
    ws.append(["规则", "检查项", "输入范围", "输入值", "能力限制", "单位", "裕量",
               "状态", "来源", "依据", "改善建议", "备注"])
    for it in result["items"]:
        ws.append([STATUSES[it[k]] if k == "status" else it.get(k) for k in keys])
    for sheet in (overview, ws):
        sheet.freeze_panes = "A2"
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = 24
        # Imported YAML/input text is data, including strings beginning with '='.
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
    return overview, ws


def export_smt_report(result, path):
    import json
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    append_smt_sheets(wb, result)
    wb.save(path)
    with open(str(Path(path).with_suffix(".json")), "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)

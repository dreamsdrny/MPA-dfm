"""SMT capability profiles: validated YAML -> explicit, traceable comparisons.

This layer compares supplied design requirements/process measurements with a
supplier profile. It does not infer geometry or certify an actual production line.
"""
import math
import re

UNITS = {"mm": ("length", 1.0), "um": ("length", 0.001),
         "mil": ("length", 0.0254), "count": ("count", 1.0),
         "ratio": ("ratio", 1.0), "percent": ("percent", 1.0),
         "deg": ("angle", 1.0)}
SEVERITIES = ("high", "medium", "low")
STATUSES = {"pass": "通过", "fail": "不符合", "missing": "数据不足",
            "unconfigured": "能力未提供", "not_applicable": "不适用", "review": "待复核"}


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


def validate_profile(doc):
    """Validate supplied profile values. Reading/YAML/version checks are adapters."""
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
    for key in ("requirements", "design_baseline"):
        rows = doc.get(key, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"{key} 必须是条目映射列表")
        if key == "design_baseline":
            for row in rows:
                entries = row.get("items", [])
                if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
                    raise ValueError("design_baseline.items 必须是条目映射列表")
    return doc


def analyze_smt(doc, profile_key, facts):
    """facts[(scope, field)] = {value, unit, source}; zero is a valid value."""
    if profile_key not in doc["profiles"]:
        raise ValueError(f"没有 SMT 配置: {profile_key}")
    profile = doc["profiles"][profile_key]
    facts = normalize_smt_facts(doc, facts)
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
    result = {"meta": dict(doc["meta"]), "profile": profile_key,
            "profile_label": profile.get("label", profile_key), "config_path": doc["path"],
            "profile_sources": [{"path": doc["path"], "sha256": doc["sha256"]}] if doc.get("sha256") else [],
            "items": items,
            "conclusion_note": "能力匹配反映本次输入与配置的比较；产线实际管控水平需测量及过程数据验证。"}
    result["manual_requirements"] = manual_requirements(doc)
    if str(doc["meta"]["factory"]).strip().lower() in ("立景", "lijing"):
        from src.domain.smt_geometry import _apply_panel_review
        _apply_panel_review(result, facts)
    return summarize_smt(result)


def normalize_smt_facts(doc, facts):
    """Lijing's axes mean long/short sides, regardless of input orientation/unit."""
    facts = dict(facts)
    if str(doc["meta"]["factory"]).strip().lower() in ("立景", "lijing"):
        keys = [("design", "panel_size_x"), ("design", "panel_size_y")]
        sides = [facts.get(key) for key in keys]
        if all(f and f.get("value") is not None for f in sides):
            converted = []
            for fact in sides:
                if fact.get("unit") not in UNITS or UNITS[fact["unit"]][0] != "length":
                    raise ValueError("拼板尺寸需要长度单位")
                converted.append(dict(fact, value=number(fact["value"], "拼板尺寸") * UNITS[fact["unit"]][1], unit="mm"))
            for key, fact in zip(keys, sorted(converted, key=lambda f: f["value"], reverse=True)):
                facts[key] = fact
        thickness = facts.get(("design", "board_thickness"))
        if thickness and thickness.get("value") is not None and thickness.get("unit") in UNITS and UNITS[thickness["unit"]][0] == "length":
            facts[("design", "board_thickness")] = dict(thickness,
                value=number(thickness["value"], "板厚") * UNITS[thickness["unit"]][1], unit="mm")
    return facts


def manual_requirements(doc):
    """Preserve qualitative conditions as review items, never parse prose into PASS."""
    rows = []
    for row in doc.get("requirements", []):
        rows.append(dict(row, id=f"REQ-{row.get('no', len(rows) + 1)}", status="review",
                         reason="需工艺工程师结合设备、治具及实际生产条件确认"))
    for group in doc.get("design_baseline", []):
        for row in group.get("items", []):
            rows.append(dict(row, category=group.get("category", ""), status="review",
                             supplier_value=row.get("requirement", ""), reason="内部非定量基线需人工评审"))
    return rows


def compare_smt_baseline(supplier, supplier_key, baseline, baseline_key):
    """Compare allowed numeric intervals by scope/field, with dimensional conversion."""
    rules = {}
    for rule in supplier["profiles"][supplier_key]["rules"]:
        rules.setdefault((rule["scope"], rule["field"]), []).append(rule)
    rows = []
    def interval(rule, scale=1):
        if rule["op"] == "range":
            return rule["min"] * scale, rule["max"] * scale
        value = rule["value"] * scale
        return (value, math.inf) if rule["op"] == ">=" else (-math.inf, value)
    def limit(rule):
        return (f"{rule['min']:g} ~ {rule['max']:g}" if rule["op"] == "range"
                else f"{rule['op']} {rule['value']:g}")
    for base in baseline["profiles"][baseline_key]["rules"]:
        candidates = rules.get((base["scope"], base["field"]), [])
        rule = candidates[0] if len(candidates) == 1 else None
        row = {"field": base["field"], "label": base["label"], "unit": base["unit"],
                   "baseline_id": base["id"], "supplier_id": rule["id"] if rule else "",
                   "baseline_limit": "未提供", "supplier_limit": "未提供", "status": "unconfigured"}
        bound_names = lambda r: ("min", "max") if r["op"] == "range" else ("value",)
        if all(base.get(k) is not None for k in bound_names(base)):
            row["baseline_limit"] = limit(base)
        if len(candidates) > 1:
            row.update(status="review", supplier_id="、".join(r["id"] for r in candidates),
                       supplier_limit="；".join(r["id"] for r in candidates),
                       reason="同一字段有多条能力规则，需核实适用范围，不能取最后一条")
        elif rule and all(rule.get(k) is not None for k in bound_names(rule)):
            row["supplier_limit"] = limit(rule) + " " + rule["unit"]
            if UNITS[rule["unit"]][0] != UNITS[base["unit"]][0]:
                row.update(status="review", reason="相同字段单位量纲不同，不能比较")
            elif row["baseline_limit"] != "未提供":
                lo, hi = interval(rule, UNITS[rule["unit"]][1] / UNITS[base["unit"]][1])
                blo, bhi = interval(base)
                covers = lo <= blo + 1e-10 and hi >= bhi - 1e-10
                row.update(status="pass" if covers else "fail",
                           reason="供应商能力覆盖内部基线允许范围" if covers else "供应商能力不能覆盖内部基线全部允许范围，需逐板确认")
        row.setdefault("reason", "供应商或内部基线未提供完整定量能力")
        rows.append(row)
    return rows


def summarize_smt(result):
    items = result["items"]
    counts = {s: sum(it["status"] == s for it in items) for s in STATUSES}
    measured = counts["pass"] + counts["fail"] + counts["not_applicable"]
    failed = [it for it in items if it["status"] == "fail"]
    risk = next((s for s in SEVERITIES if any(it["severity"] == s for it in failed)), None)
    pending = len(result.get("manual_requirements", []))
    verdict = ("不符合所选能力" if failed else
               "数据或能力不完整，需复核" if measured < len(items) or result.get("object_errors") else
               "定量能力项匹配，工艺条件待评审" if pending else "所选能力项匹配")
    result.update(counts=counts, coverage=measured / len(items) if items else 0,
                  manual_review_count=pending,
                  risk={"high": "高", "medium": "中", "low": "低"}.get(risk, "未发现不符合"),
                  verdict=verdict)
    return result


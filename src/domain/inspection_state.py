"""One authoritative lifecycle per rule/scope; evidence is not full-board approval."""
from collections import defaultdict

LABELS = {"unavailable": "未获取", "available": "已取得数据", "checked": "已检查", "partial": "部分检查"}
VERDICTS = {"fail": "不符合", "review": "需复核", "within_scope": "满足当前检查范围"}
ALIASES = {
    "solder_mask_bridge": "solder_mask_bridge", "layer_solder_mask_bridge": "solder_mask_bridge",
    "gerber_mask_bridge": "solder_mask_bridge", "gerber_mask_web": "solder_mask_bridge", "gerber_mask_bridge_scan": "solder_mask_bridge",
    "min_line_width": "line_width", "layer_min_line_width": "line_width", "gerber_trace_width": "line_width", "gerber_copper_width": "line_width", "gerber_copper_neck": "line_width",
    "min_spacing": "line_spacing", "layer_min_spacing": "line_spacing", "gerber_copper_spacing": "line_spacing", "gerber_copper_slot": "line_spacing",
    "silkscreen_width": "silkscreen_width", "gerber_silk_width": "silkscreen_width",
    "silkscreen_to_mask": "silkscreen_to_mask", "gerber_silk_mask": "silkscreen_to_mask",
    "silkscreen_to_outline": "silkscreen_to_outline", "gerber_silk_edge": "silkscreen_to_outline",
    "npth_to_edge": "npth_to_edge", "pad_to_mask_clearance": "pad_to_mask_clearance", "layer_pad_to_mask_clearance": "pad_to_mask_clearance",
    "gerber_npth_edge": "npth_to_edge", "gerber_pad_mask_clearance": "pad_to_mask_clearance",
    "gerber_pad_mask_covered": "pad_to_mask_clearance",
}


def check_key(field):
    return ALIASES.get(field, field)


def reconcile_inspections(result):
    """Reconcile data gaps with real scans; never erase failures or unrelated gaps."""
    items = [row for row in result.setdefault("items", []) if row.get("source") != "统一检查状态"]
    groups = defaultdict(list)
    for item in items:
        key = item.get("check_key") or check_key(item.get("field", "")) or item.get("item", "")
        if key:
            item["check_key"] = key
            groups[key].append(item)
    evidence = defaultdict(list)
    for row in result.get("gerber_mask_bridge_checks", []):
        evidence["solder_mask_bridge"].append(dict(row, scope=row.get("layer", ""), inspected=bool(row.get("checked")), complete=False))
    if result.get("geometry_executed"):
        for side in ("mask_top", "mask_bottom"):
            if not any(row["scope"] == side for row in evidence["solder_mask_bridge"]):
                evidence["solder_mask_bridge"].append(dict(scope=side, inspected=False, complete=False))
    for row in result.get("gerber_copper_layer_checks", []):
        for key in ("line_width", "line_spacing"):
            evidence[key].append(dict(row, scope=row.get("layer", ""), inspected=bool(row.get("checked")), complete=False))
    expected_layers = len(result.get("stackup", []))
    if expected_layers:
        for key in ("line_width", "line_spacing"):
            if len(evidence[key]) < expected_layers:
                evidence[key].append(dict(scope="未取得的铜层", inspected=False, complete=False))
    for row in result.get("inspection_evidence", []):
        evidence[row["check_key"]].append(dict(row, scope=row.get("scope", ""), inspected=bool(row.get("inspected")), complete=bool(row.get("complete"))))
    coverage_rules = {"npth_to_edge": ("npth_to_edge",), "pad_to_mask_clearance": ("pad_to_mask_clearance",)}
    for row in result.get("geometry_coverage", []):
        for key in coverage_rules.get(row.get("rule"), ()):
            evidence[key].append(dict(row, scope=row.get("layer", ""), inspected=row.get("status") in ("partial", "measured"),
                                      complete=row.get("status") == "measured"))
    states, resolved_data = [], set()
    removed = set()
    for key in sorted(set(groups) | set(evidence)):
        entries, scans = groups[key], evidence[key]
        inspected = [row for row in scans if row["inspected"]]
        available = bool(inspected) or any(row.get("design") is not None for row in entries)
        actual = [row for row in entries if row.get("status") in ("fail", "warn_limit", "pass")]
        failures = [row for row in entries if row.get("status") == "fail"]
        partial = bool(scans) and (not all(row["inspected"] and row["complete"] for row in scans))
        phase = "partial" if (inspected or actual) and partial else "checked" if inspected or actual else "available" if available else "unavailable"
        verdict = "fail" if failures else "review" if phase != "checked" or any(row.get("status") == "warn_limit" for row in entries) else "within_scope"
        state = dict(check_key=key, label=next((row.get("item") for row in entries if row.get("item")), key),
                     phase=phase, phase_label=LABELS[phase], verdict=verdict, verdict_label=VERDICTS[verdict],
                     scopes=[dict(scope=row.get("scope", ""), phase="checked" if row["inspected"] and row["complete"] else "partial" if row["inspected"] else "unavailable") for row in scans],
                     full_board_complete=False, sources=sorted({str(row.get("source") or row.get("source_detail") or "") for row in entries + scans} - {""}),
                     evidence_count=len(actual), failure_count=len(failures))
        states.append(state)
        if inspected:
            for row in entries:
                # Legacy missing-value placeholders are replaced, not actual decisions.
                if row.get("design") is None and row.get("status") == "info" and not row.get("field", "").startswith("gerber_"):
                    removed.add(id(row))
            if all(row["inspected"] for row in scans):
                resolved_data.add(key)
    result["items"] = [row for row in items if id(row) not in removed]
    for state in states:
        if any(row["inspected"] for row in evidence[state["check_key"]]):
            result["items"].append(dict(category="检查状态", item=state["label"], check_key=state["check_key"],
                field="inspection_" + state["check_key"], design=None, capability=None, unit="", status="info",
                source="统一检查状态", message=f"{state['phase_label']} · {state['verdict_label']}；范围："
                + " / ".join(s['scope'] + ':' + LABELS[s['phase']] for s in state['scopes']) + "；部分扫描不等于全板通过"))
    states_by_key = {state["check_key"]: state for state in states}
    for row in result["items"]:
        state = states_by_key.get(row.get("check_key") or check_key(row.get("field", "")))
        if state:
            row["inspection_phase"] = state["phase"]
            row["inspection_verdict"] = state["verdict"]
    result["inspection_states"] = states
    cap = result.get("capability_coverage", {})
    if cap and not cap.get("error"):
        resolved_rows = sum(id(row) in removed and row.get("source") == "能力库" for row in items)
        cap["missing"] = max(0, cap.get("missing", 0) - resolved_rows)
        cap["covered"] = cap.get("covered", 0) + resolved_rows
        for row in result["items"]:
            if row.get("item") == "能力项覆盖清单":
                row["message"] = f"能力库共 {cap.get('total', 0)} 项；已有证据 {cap.get('covered', 0) + cap.get('judged', 0)} 项，未获取 {cap.get('missing', 0)} 项，未接入 {cap.get('unmapped', 0)} 项；已有证据不等于全板完成"
    # Data availability is distinct from inspection completeness.
    coverage = result.get("coverage") or {}
    from src.domain.pcb_rules import COVERAGE_ITEMS, FLEX_COVERAGE_ITEMS
    label_keys = {label: check_key(field) for label, field in COVERAGE_ITEMS + FLEX_COVERAGE_ITEMS}
    missing = coverage.get("missing_items", [])
    newly_available = [label for label in missing if label_keys.get(label) in resolved_data]
    coverage["missing_items"] = [label for label in missing if label not in newly_available]
    coverage["checked_items"] = list(dict.fromkeys(coverage.get("checked_items", []) + newly_available))
    if coverage.get("total"):
        coverage.update(checked=len(coverage["checked_items"]), ratio=round(len(coverage["checked_items"]) / coverage["total"], 2))
        coverage["level"] = "高" if coverage["ratio"] >= .75 else "中" if coverage["ratio"] >= .5 else "低"
        coverage["note"] = "统计数据可用性，不代表几何完整性；检查状态以 inspection_states 为准"
        result["coverage"] = coverage
    blockers = []
    for text in result.get("release_blockers", []):
        if "外层阻焊桥缺测量或铜厚对应能力" in text and "solder_mask_bridge" in resolved_data:
            continue
        if text.startswith("关键输入缺失："):
            if coverage.get("missing_items"):
                blockers.append("关键输入缺失：" + "、".join(coverage["missing_items"]))
            continue
        if text.startswith("参数覆盖率 "):
            if coverage.get("ratio", 0) < .6:
                blockers.append(f"数据可用率 {coverage.get('ratio', 0):.0%} 低于 60%，仍需补充；不代表算法覆盖率")
            continue
        if text.startswith("YAML 能力项缺数据 "):
            if cap.get("missing") or cap.get("unmapped"):
                blockers.append(f"YAML 能力项未获取 {cap.get('missing', 0)} 项，未接入 {cap.get('unmapped', 0)} 项，需确认适用性")
            continue
        blockers.append(text)
    # Never lose a partial-scope barrier even if all inputs were obtained.
    if any(state["phase"] == "partial" for state in states):
        blockers.append("检查项存在部分检查范围，满足局部范围不代表全板通过")
    result["release_blockers"] = list(dict.fromkeys(blockers))
    result["errors"] = [row.get("message", "") for row in result["items"] if row.get("status") == "fail"]
    result["warnings"] = [row.get("message", "") for row in result["items"] if row.get("status") == "warn_limit"]
    return result

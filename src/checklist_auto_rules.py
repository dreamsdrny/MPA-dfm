"""Checklist 判定规则: 按条目文字匹配规则, 用 BRD/DFM 数据给出结论.

设计目标: 换模板/加条目时只改 config/checklist_auto_rules.yaml, 不改代码。
匹配靠内容 (关键词), 不靠序号, 所以行号变动、增删条目都不影响。
"""
import re

from src.checklist_template import load_config, normalize

PASS, FAIL, MANUAL, NA, VERIFY = "PASS", "FAIL", "MANUAL", "N/A", "VERIFY"


def load_rules(config: dict = None) -> list:
    config = config or load_config()
    rules = []
    for raw in config.get("rules") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        rules.append({
            "id": str(raw["id"]),
            "name": str(raw.get("name") or raw["id"]),
            "all": [normalize(k) for k in (raw.get("all") or [])],
            "any": [normalize(k) for k in (raw.get("any") or [])],
            "not": [normalize(k) for k in (raw.get("not") or [])],
            "category": [normalize(k) for k in (raw.get("category") or [])],
            "result": str(raw.get("result") or "manual").lower(),
            "judge": raw.get("judge") or {},
            "when": raw.get("when") or {},
            "pass": raw.get("pass") or "",
            "fail": raw.get("fail") or "",
            "verify": raw.get("verify") or "",
            "manual": raw.get("manual") or "",
            "missing": raw.get("missing") or "",
        })
    return rules


def match_rule(item_text: str, category: str, rules: list):
    """返回第一条命中的规则; 关键词都按 normalize 后比较。"""
    text = normalize(item_text)
    cat = normalize(category)
    if not text:
        return None
    for rule in rules:
        if rule["not"] and any(k in text for k in rule["not"]):
            continue
        if rule["category"] and not any(k in cat for k in rule["category"]):
            continue
        if rule["all"] and not all(k in text for k in rule["all"]):
            continue
        if rule["any"] and not any(k in text for k in rule["any"]):
            continue
        if not rule["all"] and not rule["any"]:
            continue
        return rule
    return None


# --------------------------------------------------------------------------- 事实
def build_facts(design: dict, classification: dict = None, constraints: dict = None,
                dfm_result: dict = None, board_info: dict = None,
                extra_facts: dict = None):
    """把 BRD 提取 + 分类 + 约束 + DFM 结果压成一个扁平字典, 供规则取值。"""
    facts = {}
    for key, value in (design or {}).items():
        if not key.startswith("_"):
            facts[key] = value
    for key, value in (extra_facts or {}).items():
        facts[key] = value
    board_info = board_info or {}
    for key in ("board_name", "net_count", "layer_count", "file_path"):
        if facts.get(key) in (None, "", 0) and board_info.get(key):
            facts[key] = board_info[key]

    classification = classification or {}
    facts["net_classes"] = classification.get("total_classes", 0)
    facts["diff_pairs"] = classification.get("total_diff_pairs", 0)

    constraints = constraints or {}
    for key, fact in (("physical_constraints", "physical_count"),
                      ("spacing_constraints", "spacing_count"),
                      ("electrical_constraints", "electrical_count"),
                      ("via_constraints", "via_count")):
        facts[fact] = len(constraints.get(key) or [])
    facts["constraint_total"] = sum(facts[k] for k in
                                    ("physical_count", "spacing_count",
                                     "electrical_count", "via_count"))

    dfm = dfm_result or {}
    facts["dfm_error_count"] = dfm.get("error_count", 0)
    facts["dfm_warning_count"] = dfm.get("warning_count", 0)
    coverage = dfm.get("coverage") or {}
    facts["dfm_coverage"] = f"{coverage.get('ratio', 0):.0%}"
    facts["dfm_checked"] = coverage.get("checked", 0)
    facts["dfm_total"] = coverage.get("total", 0)
    facts["dfm_no_data"] = max(0, coverage.get("total", 0) - coverage.get("checked", 0))
    facts["dfm_fail_count"] = sum(1 for it in (dfm.get("items") or [])
                                  if it.get("status") == "fail")
    facts["_dfm_items"] = dfm.get("items") or []
    return facts


def _lookup(facts: dict, field: str):
    if not field:
        return None
    value = facts.get(field)
    if value is None:
        value = facts.get(field.replace("_mm", ""))
    return value


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _find_dfm_item(items: list, name: str):
    name = normalize(name)
    for item in items:
        if normalize(item.get("item")) == name:
            return item
    for item in items:
        if name and name in normalize(item.get("item")):
            return item
    return None


def _fmt(template: str, facts: dict) -> str:
    """把 {field} 换成事实值; 不存在的字段显示为 —, 不抛异常。"""
    def repl(match):
        key = match.group(1)
        value = facts.get(key)
        if isinstance(value, float):
            return f"{value:g}"
        if value in (None, ""):
            return "—"
        return str(value)

    return re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, template or "")


def _compare(left, op: str, right) -> bool:
    ops = {
        ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b, "<": lambda a, b: a < b,
        "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    }
    fn = ops.get(op)
    if fn is None:
        return False
    return fn(left, right)


def _eval_condition(cond: dict, facts: dict) -> bool:
    field = cond.get("field")
    op = str(cond.get("op") or "==")
    left = _num(_lookup(facts, field))
    if left is None:
        return False
    if "value" in cond:
        right = _num(cond.get("value"))
    else:
        right = _num(_lookup(facts, cond.get("other")))
    if right is None:
        return False
    scale = _num(cond.get("scale")) or 1.0
    return _compare(left, op, right * scale)


def evaluate(rule: dict, facts: dict) -> dict:
    """返回 {status, reason, rule_id, rule_name, evidence}。"""
    base = {"rule_id": rule["id"], "rule_name": rule["name"], "evidence": {}}

    if rule["when"] and _eval_condition(rule["when"], facts):
        return {**base, "status": NA,
                "reason": _fmt(rule["manual"] or "条件不满足, 不适用", facts)}

    kind = rule["result"]
    if kind == "na":
        return {**base, "status": NA, "reason": _fmt(rule["manual"] or "不适用", facts)}
    if kind == "manual":
        return {**base, "status": MANUAL, "reason": _fmt(rule["manual"] or "需人工确认", facts)}
    if kind == "verify":
        # 条件成立 -> VERIFY (需 DRC/仿真确认), 不成立 -> 人工
        inner = evaluate({**rule, "result": "judge"}, facts)
        if inner["status"] == PASS:
            reason = _fmt(rule["verify"] or rule["pass"] or "", facts)
            if not reason:
                reason = inner["reason"]
            return {**base, "status": VERIFY, "reason": reason,
                    "evidence": inner.get("evidence", {})}
        return {**base, "status": MANUAL, "reason": inner["reason"]}

    judge = rule["judge"] or {}
    judge_kind = str(judge.get("kind") or "compare").lower()

    if judge_kind == "compare":
        left = _num(_lookup(facts, judge.get("field")))
        if "value" in judge:
            right = _num(judge.get("value"))
        else:
            right = _num(_lookup(facts, judge.get("other")))
            scale = _num(judge.get("scale")) or 1.0
            if right is not None:
                right *= scale
        if left is None or right is None:
            return {**base, "status": MANUAL,
                    "reason": _fmt(rule["missing"] or "缺数据, 需人工确认", facts)}
        ok = _compare(left, str(judge.get("op") or ">="), right)
        text = rule["pass"] if ok else (rule["fail"] or rule["missing"])
        if not text:
            text = "满足要求" if ok else "不满足要求"
        return {**base, "status": PASS if ok else FAIL, "reason": _fmt(text, facts),
                "evidence": {"value": left, "limit": round(right, 4)}}

    if judge_kind == "dfm":
        item = _find_dfm_item(facts.get("_dfm_items") or [], judge.get("item") or "")
        if item is None:
            return {**base, "status": MANUAL,
                    "reason": _fmt(rule["missing"] or "DFM 缺该项数据, 需人工确认", facts)}
        status = str(item.get("status") or "")
        if status == "pass":
            return {**base, "status": PASS,
                    "reason": _fmt(rule["pass"] or item.get("message", ""), facts),
                    "evidence": {"dfm_item": item.get("item"), "dfm_status": status}}
        if status == "warn_limit":
            return {**base, "status": PASS,
                    "reason": _fmt(rule["pass"] or item.get("message", ""), facts) + " (接近能力极限)",
                    "evidence": {"dfm_item": item.get("item"), "dfm_status": status}}
        return {**base, "status": FAIL,
                "reason": _fmt(rule["fail"] or item.get("message", ""), facts),
                "evidence": {"dfm_item": item.get("item"), "dfm_status": status}}

    if judge_kind == "dfm_all":
        fails = facts.get("dfm_fail_count", 0)
        errors = facts.get("dfm_error_count", 0)
        if fails or errors:
            return {**base, "status": FAIL,
                    "reason": _fmt(rule["fail"] or "DFM 校验不通过", facts),
                    "evidence": {"dfm_fail": fails, "error_count": errors}}
        if facts.get("dfm_no_data", 0):
            return {**base, "status": MANUAL,
                    "reason": _fmt(rule["missing"] or "DFM 有缺数据项", facts),
                    "evidence": {"dfm_no_data": facts.get("dfm_no_data")}}
        return {**base, "status": PASS, "reason": _fmt(rule["pass"], facts)}

    if judge_kind == "count":
        value = _num(_lookup(facts, judge.get("field")))
        minimum = _num(judge.get("min")) or 1
        if value is None:
            return {**base, "status": MANUAL,
                    "reason": _fmt(rule["missing"] or "缺数据", facts)}
        if value >= minimum:
            return {**base, "status": PASS, "reason": _fmt(rule["pass"], facts),
                    "evidence": {"count": value, "min": minimum}}
        return {**base, "status": FAIL,
                "reason": _fmt(rule["fail"] or rule["missing"] or "数量不足", facts),
                "evidence": {"count": value, "min": minimum}}

    return {**base, "status": MANUAL, "reason": _fmt(rule["manual"] or "需人工确认", facts)}

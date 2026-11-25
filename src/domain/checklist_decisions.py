"""Checklist rule application and coverage counts, without Excel objects."""
from src.domain.checklist_rules import MANUAL, NA, VERIFY, evaluate, match_rule


def decide_items(template, facts, rules):
    decisions = []
    for item in template["items"]:
        rule = match_rule(item["item"], item["category"], rules)
        if rule is None:
            verdict = {"status": MANUAL, "rule_id": "", "rule_name": "",
                       "reason": "规则库未覆盖该条目, 需人工确认 (可在 config/checklist_auto_rules.yaml 增加规则)",
                       "evidence": {}}
        else:
            verdict = evaluate(rule, facts)
        decisions.append({**{key: item[key] for key in ("row", "no", "category", "item", "grade_type")},
                          **{key: verdict[key] for key in ("status", "rule_id", "rule_name", "reason")},
                          "evidence": verdict.get("evidence", {})})
    return decisions


def _stats(decisions, rules_hit):
    stats = {"total": len(decisions), "auto_pass": 0, "auto_fail": 0,
             "auto_verify": 0, "manual": 0, "na": 0, "unmatched": 0, "rule_matched": len(rules_hit)}
    counts = {"PASS": "auto_pass", "FAIL": "auto_fail", VERIFY: "auto_verify", NA: "na"}
    for decision in decisions:
        stats[counts.get(decision["status"], "manual")] += 1
        if not decision["rule_id"]:
            stats["unmatched"] += 1
    return stats

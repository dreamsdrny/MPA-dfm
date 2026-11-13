"""Checklist 自动填写: 读取模板 -> 按规则判定 -> 写回 Check/签核/原因说明.

模板结构由 checklist_template 识别 (列名别名), 判定由 checklist_auto_rules 完成
(按条目文字匹配, 不依赖序号), 所以换一版 checklist.xlsx 通常不用改代码。
"""
# 导入操作系统接口模块，用于文件路径处理等
import os

# 导入日期时间模块，用于生成时间戳
from datetime import datetime

# 导入 openpyxl 库，用于读写 Excel 文件
import openpyxl
# 从 openpyxl 导入样式相关类，用于设置单元格格式
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

# 导入检查规则相关函数和常量
# MANUAL: 需人工检查, NA: 不适用, VERIFY: 需复核
# build_facts: 构建设计事实, evaluate: 评估规则, load_rules: 加载规则, match_rule: 匹配规则
from src.checklist_auto_rules import (MANUAL, NA, VERIFY, build_facts, evaluate,
                                      load_rules, match_rule)
# 导入清单模板相关函数
# load_config: 加载配置, read_template: 读取模板
from src.checklist_template import load_config, read_template

# ==================== 状态背景色定义 ====================
# PASS 状态填充色：浅绿色
PASS_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
# FAIL 状态填充色：浅红色
FAIL_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
# WARN/VERIFY 状态填充色：浅黄色
WARN_FILL = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
# MANUAL 状态填充色：浅蓝色
MANUAL_FILL = PatternFill(start_color="D9E2F3", end_color="D9E2F3", fill_type="solid")
# N/A 状态填充色：浅灰色
NA_FILL = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")

# ==================== 单元格边框定义 ====================
# 定义细线边框样式，用于表格单元格
THIN_BORDER = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"), bottom=Side(style="thin"),
)

# ==================== 状态文本映射 ====================
# 将内部状态标识映射为 Excel 中显示的文字
STATUS_TEXT = {
    "PASS": "PASS",       # 通过
    "FAIL": "FAIL",       # 失败
    VERIFY: "VERIFY",     # 需复核
    MANUAL: "MANUAL",     # 需人工检查
    NA: "N/A"             # 不适用
}

# ==================== 状态背景色映射 ====================
# 将内部状态标识映射为对应的单元格填充样式
STATUS_FILL = {
    "PASS": PASS_FILL,    # 通过 → 绿色
    "FAIL": FAIL_FILL,    # 失败 → 红色
    VERIFY: WARN_FILL,    # 复核 → 黄色
    MANUAL: MANUAL_FILL,  # 人工 → 蓝色
    NA: NA_FILL           # 不适用 → 灰色
}


def decide_items(template: dict, facts: dict, rules: list) -> list:
    """对每条检查项给出结论, 不改工作簿。"""
    decisions = []
    for item in template["items"]:
        rule = match_rule(item["item"], item["category"], rules)
        if rule is None:
            decisions.append({
                "row": item["row"], "no": item["no"], "category": item["category"],
                "item": item["item"], "grade_type": item["grade_type"],
                "status": MANUAL, "rule_id": "", "rule_name": "",
                "reason": "规则库未覆盖该条目, 需人工确认 (可在 config/checklist_auto_rules.yaml 增加规则)",
            })
            continue
        verdict = evaluate(rule, facts)
        decisions.append({
            "row": item["row"], "no": item["no"], "category": item["category"],
            "item": item["item"], "grade_type": item["grade_type"],
            "status": verdict["status"], "rule_id": verdict["rule_id"],
            "rule_name": verdict["rule_name"], "reason": verdict["reason"],
        })
    return decisions


def _stats(decisions: list, rules_hit: set) -> dict:
    stats = {"total": len(decisions), "auto_pass": 0, "auto_fail": 0,
             "auto_verify": 0, "manual": 0, "na": 0, "unmatched": 0,
             "rule_matched": len(rules_hit)}
    for decision in decisions:
        status = decision["status"]
        if status == "PASS":
            stats["auto_pass"] += 1
        elif status == "FAIL":
            stats["auto_fail"] += 1
        elif status == VERIFY:
            stats["auto_verify"] += 1
        elif status == NA:
            stats["na"] += 1
        else:
            stats["manual"] += 1
        if not decision["rule_id"]:
            stats["unmatched"] += 1
    return stats


def _append_note(cell, reason: str, tag: str = ""):
    if not reason:
        return
    existing = str(cell.value or "").strip()
    if tag:
        reason = f"{tag} {reason}"
    if not existing or existing.lower() == "none":
        cell.value = reason
    elif reason not in existing:
        cell.value = f"{existing}; {reason}"


def fill_checklist(template: dict, facts: dict, output_path: str,
                   checker_name: str = "BRD-AI", static_approver: str = "",
                   rules: list = None, config: dict = None) -> dict:
    """按判定结果写回 checklist 并另存; 返回结果明细 (供报告使用)。"""
    config = config or load_config()
    rules = rules if rules is not None else load_rules(config)
    decisions = decide_items(template, facts, rules)
    stats = _stats(decisions, {d["rule_id"] for d in decisions if d["rule_id"]})

    wb = openpyxl.load_workbook(template["path"])
    ws = wb[template["sheet"]]
    cols = template["columns"]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    approver = static_approver or f"{checker_name} / {timestamp}"

    check_col = cols.get("check")
    approved_col = cols.get("approved")
    note_col = cols.get("note")

    for decision in decisions:
        row = decision["row"]
        status = decision["status"]
        if check_col:
            cell = ws.cell(row, check_col)
            if not str(cell.value or "").strip():
                cell.value = STATUS_TEXT.get(status, "MANUAL")
                cell.fill = STATUS_FILL.get(status, MANUAL_FILL)
                cell.font = Font(name="Arial", size=10, bold=True)
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = THIN_BORDER
        if approved_col:
            cell = ws.cell(row, approved_col)
            if not str(cell.value or "").strip():
                cell.value = approver
                cell.font = Font(name="Arial", size=9)
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border = THIN_BORDER
        if note_col:
            tag = {"PASS": "[自动]", "FAIL": "[不符合]", VERIFY: "[待验证]",
                   NA: "[不适用]"}.get(status, "[人工]")
            _append_note(ws.cell(row, note_col), decision["reason"], tag)

    # summary 页追加一行填写记录
    summary_name = next((s for s in wb.sheetnames if s.lower() == "summary"), None)
    summary_ws = wb[summary_name] if summary_name else wb.create_sheet("summary")
    last_row = summary_ws.max_row or 1
    if summary_ws.cell(last_row, 1).value:
        last_row += 1
    summary_ws.cell(last_row, 1, value=timestamp).font = Font(name="Arial", size=10)
    summary_ws.cell(last_row, 2, value="Auto").font = Font(name="Arial", size=10)
    summary_ws.cell(last_row, 3, value=checker_name).font = Font(name="Arial", size=10)
    summary_ws.cell(last_row, 4, value=(
        f"Checklist: {stats['total']} 项, PASS {stats['auto_pass']}, "
        f"VERIFY {stats['auto_verify']}, FAIL {stats['auto_fail']}, "
        f"MANUAL {stats['manual']}, N/A {stats['na']}"
    )).font = Font(name="Arial", size=10)

    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    wb.save(output_path)
    wb.close()

    return {
        "template": {k: template[k] for k in
                     ("path", "sheet", "header_row", "columns", "column_headers",
                      "method", "count", "warnings")},
        "decisions": decisions,
        "stats": stats,
        "checker": checker_name,
        "timestamp": timestamp,
        "filled": output_path,
    }


# --------------------------------------------------------------------------- 兼容旧接口
def auto_fill_checklist(checklist_path: str, brd_info: dict, classification: dict,
                        constraints: dict, dfm_result: dict, output_path: str,
                        checker_name: str = "BRD-AI", static_approver: str = "") -> tuple:
    """旧接口: 保持命令行流水线可用; 内部改用模板识别 + 内容规则。"""
    template = read_template(checklist_path)
    facts = build_facts({}, classification, constraints, dfm_result, brd_info)
    result = fill_checklist(template, facts, output_path, checker_name, static_approver)
    stats = dict(result["stats"])
    stats.setdefault("auto_verify", 0)
    return output_path, stats


def generate_checklist_summary(stats: dict, output_path: str) -> str:
    """旧接口: 纯文本摘要 (新报告请用 src.checklist_report)。"""
    auto_rate = stats.get("auto_pass", 0) / max(stats.get("total", 0), 1) * 100
    lines = [
        "=" * 60,
        "  BRD-AI: Checklist Auto-Fill Summary",
        "=" * 60,
        f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60,
        "",
        f"  Total Items:    {stats.get('total', 0)}",
        f"  Auto PASS:      {stats.get('auto_pass', 0)}  (自动判定通过)",
        f"  Auto FAIL:      {stats.get('auto_fail', 0)}  (判定不符合)",
        f"  Manual Check:   {stats.get('manual', 0)}  (需人工检查)",
        f"  N/A:            {stats.get('na', 0)}  (不适用)",
        "",
        f"  自动完成率: {auto_rate:.1f}%",
        "=" * 60,
    ]
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return output_path

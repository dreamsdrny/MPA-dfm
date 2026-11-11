"""Checklist 填写流水线 (无 tkinter): BRD 分析 -> 填写 -> 报告.

这是软件的主流程:
    1 读取 BRD (网络/层/约束库)
    2 信号分类 (网络类 / 差分对)
    3 计算约束 (作为判定依据, 不单独导出)
    4 DFM 校验 (板厂能力比对, 作为判定依据)
    5 识别 checklist 模板 (列/表头, 换模板也能读)
    6 逐项判定并填写
    7 输出填好的 checklist + 填写报告 (Excel/TXT)

界面只负责传参和展示, 进度通过 log(text, tag) 回调。
"""
import os
from datetime import datetime

from src.brd_design import extract_brd_design, to_design_params
from src.checklist_auto_fill import fill_checklist
from src.checklist_auto_rules import build_facts
from src.checklist_report import write_report
from src.checklist_template import create_demo_template, load_config, read_template
from src.dfm_engine import analyze_design
from src.pcb_reader import extract_brd_info
from src.rule_engine import compute_class_constraints
from src.signal_classifier import classify_and_export


DEMO_DESIGN = {
    "board_name": "860-000776_PSE_Board (Demo)",
    "layer_count": 8, "board_thickness": 1.6,
    "min_line_width": 0.10, "min_spacing": 0.10,
    "min_mech_drill": 0.20, "min_annular_ring": 0.10,
    "min_laser_via": 0.10, "copper_um": 35.0, "copper_oz": 1.0,
    "_data_origin": "演示数据",
}


def _log(log, text, tag=None):
    if log:
        log(text, tag)


def _read_board(*, brd_path: str, nets_path: str, demo: bool) -> dict:
    if demo:
        from main import DEMO_NETS

        return {
            "board_name": "860-000776_PSE_Board (Demo)",
            "file_path": "(demo)",
            "nets": DEMO_NETS,
            "net_count": len(DEMO_NETS),
            "layers": [{"name": n} for n in
                       ("TOP", "L2_GND", "L3_SIG", "L4_GND", "L5_PWR",
                        "L6_SIG", "L7_GND", "BOTTOM")],
            "layer_count": 8,
        }
    info = extract_brd_info(brd_path)
    if info.get("error"):
        raise RuntimeError(info["error"])
    if nets_path:
        import json

        with open(nets_path, "r", encoding="utf-8") as f:
            net_list = json.load(f)
        info["nets"] = net_list
        info["net_count"] = len(net_list)
    return info


def _read_design(brd_path: str, layer_count: int = 0) -> tuple:
    """BRD 约束库 -> (设计参数, 原始提取结果); 解析失败不致命。"""
    if not brd_path or not os.path.exists(brd_path):
        return {}, {}
    try:
        extracted = extract_brd_design(brd_path)
        if not extracted.get("ok"):
            return {}, {}
        design = to_design_params(extracted)
        if layer_count:
            design.setdefault("layer_count", layer_count)
        return design, extracted
    except Exception:  # noqa: BLE001
        return {}, {}


def _design_extra_facts(extracted: dict) -> dict:
    """从 BRD 约束库再挖几个"规则是否已定义"的事实, 供 VERIFY 类规则使用。"""
    if not extracted:
        return {}
    spacing = extracted.get("spacing_csets") or {}
    rule_keys = set()
    spacing_min = None
    spacing_3w = None
    for name, cset in spacing.items():
        if not isinstance(cset, dict):
            continue
        rule_keys.update(k for k in cset if k.endswith("_SPACING"))
        value = cset.get("LINE_TO_LINE_SPACING")
        if value:
            spacing_min = value if spacing_min is None else min(spacing_min, value)
        if "3w" in str(name).lower() and value:
            spacing_3w = value

    padstacks = extracted.get("via_padstacks") or []
    return {
        "spacing_rule_count": len(rule_keys),
        "spacing_rule_min": spacing_min,
        "has_3w_rule": 1 if spacing_3w else 0,
        "spacing_3w": spacing_3w,
        "impedance_rule_count": 1 if extracted.get("impedance") else 0,
        "via_padstack_count": len(padstacks),
        "part_count": extracted.get("part_count") or 0,
        "hole_count": extracted.get("hole_count") or 0,
        "drill_span_count": len(extracted.get("drill_spans") or []),
    }


def _default_checklist_name(stem: str) -> str:
    return f"{stem}_filled.xlsx"


def run(*, checklist_path: str = "", output_dir: str = "", brd_path: str = "",
        nets_path: str = "", factory: str = "hongban", board_type: str = "rigid",
        level: str = "standard", copper_um="35", copper_oz="1.0",
        checker: str = "BRD-AI", demo: bool = False, log=None) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    _log(log, "=" * 60, "info")
    _log(log, "  BRD-AI: Checklist 自动填写", "info")
    _log(log, f"  时间: {datetime.now():%Y-%m-%d %H:%M:%S}", "info")
    _log(log, "=" * 60, "info")
    _log(log, "")

    # ---- 1 读取 BRD ----
    _log(log, "[1/6] 读取 BRD 数据...", "info")
    board_info = _read_board(brd_path=brd_path, nets_path=nets_path, demo=demo)
    _log(log, f"  -> 网络 {board_info.get('net_count', 0)} / 层数 "
              f"{board_info.get('layer_count', 0)}", "success")

    # ---- 2 信号分类 ----
    _log(log, "[2/6] 信号分类...", "info")
    classification = classify_and_export(board_info.get("nets", []))
    _log(log, f"  -> 网络类 {classification['total_classes']} / "
              f"差分对 {classification['total_diff_pairs']}", "success")

    # ---- 3 计算约束 (判定依据) ----
    _log(log, "[3/6] 计算约束 (作为判定依据)...", "info")
    constraints = compute_class_constraints(classification)
    total_constraints = sum(len(constraints.get(k) or []) for k in
                            ("physical_constraints", "spacing_constraints",
                             "electrical_constraints", "via_constraints"))
    _log(log, f"  -> 约束 {total_constraints} 条", "success")

    # ---- 4 DFM 校验 ----
    copper_text = str(copper_um or "35")
    try:
        copper_val = float(copper_text)
    except ValueError:
        copper_val = 35.0
    try:
        copper_oz_val = float(copper_oz or 0) or max(1.0, round(copper_val / 35.0, 1))
    except ValueError:
        copper_oz_val = 1.0

    _log(log, f"[4/6] DFM 校验 ({factory} {board_type}-{level})...", "info")
    if demo:
        design, extracted = dict(DEMO_DESIGN), {}
    else:
        design, extracted = _read_design(brd_path, board_info.get("layer_count", 0))
    design.setdefault("copper_um", copper_val)
    design.setdefault("copper_oz", copper_oz_val)
    design.setdefault("board_type", board_type)
    dfm_result = analyze_design(design, board_type=board_type,
                               capability_level=level, factory=factory)
    coverage = dfm_result.get("coverage") or {}
    _log(log, f"  -> 结论 {dfm_result.get('verdict', '')}, 错误 "
              f"{dfm_result.get('error_count', 0)} / 警告 "
              f"{dfm_result.get('warning_count', 0)}, 覆盖 "
              f"{coverage.get('ratio', 0):.0%}", "success" if dfm_result.get("passed") else "warn")
    for line in dfm_result.get("data_warnings") or []:
        _log(log, f"     [数据] {line}", "warn")

    # ---- 5 识别模板 ----
    _log(log, "[5/6] 识别 Checklist 模板...", "info")
    config = load_config()
    if demo or not checklist_path:
        demo_dir = os.path.join(output_dir, "_demo")
        checklist_path = create_demo_template(os.path.join(demo_dir, "demo_checklist.xlsx"))
        _log(log, f"  -> 使用内置演示模板: {checklist_path}", "info")

    template = read_template(checklist_path, config)
    _log(log, f"  -> 工作表 {template['sheet']} / 表头第 {template['header_row']} 行 / "
              f"识别方式 {template['method']}", "success")
    _log(log, f"  -> 检查项 {template['count']} 项", "success")
    for warning in template.get("warnings") or []:
        _log(log, f"     [模板] {warning}", "warn")

    # ---- 6 填写 ----
    _log(log, "[6/6] 逐项判定并填写...", "info")
    facts = build_facts(design, classification, constraints, dfm_result, board_info,
                        _design_extra_facts(extracted))
    stem = os.path.splitext(os.path.basename(checklist_path))[0]
    filled_path = os.path.join(output_dir, _default_checklist_name(stem))
    result = fill_checklist(template, facts, filled_path,
                            checker_name=checker or "BRD-AI", config=config)
    stats = result["stats"]
    _log(log, f"  -> PASS {stats['auto_pass']} / FAIL {stats['auto_fail']} / "
              f"需人工 {stats['manual']} / 不适用 {stats['na']}", "success")
    _log(log, f"  -> 规则命中 {stats['rule_matched']} 条, 未覆盖 {stats['unmatched']} 项", "info")

    # ---- 报告 ----
    report_xlsx = os.path.join(output_dir, f"{stem}_checklist_report.xlsx")
    board_meta = {
        "board_name": board_info.get("board_name", ""),
        "brd_path": brd_path if not demo else "(演示数据)",
        "factory": factory, "board_type": board_type, "level": level,
    }
    report_xlsx, report_txt = write_report(result, report_xlsx, board_meta=board_meta,
                                           dfm=dfm_result)
    _log(log, f"  -> 填写结果: {filled_path}", "success")
    _log(log, f"  -> 填写报告: {report_xlsx}", "success")
    _log(log, f"  -> 文本摘要: {report_txt}", "success")

    _log(log, "")
    _log(log, "=" * 60, "info")
    _log(log, "  完成", "success")
    _log(log, "=" * 60, "info")

    return {
        "board_info": board_info,
        "classification": classification,
        "constraints": constraints,
        "design": design,
        "dfm": dfm_result,
        "template": template,
        "result": result,
        "stats": stats,
        "files": {"filled": filled_path, "report_xlsx": report_xlsx,
                  "report_txt": report_txt},
    }

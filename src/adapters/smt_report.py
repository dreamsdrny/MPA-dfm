"""SMT XLSX/JSON writers; contain no capability decisions."""
import json
from pathlib import Path

from src.domain.smt_rules import STATUSES


def append_smt_sheets(wb, result, prefix=""):
    overview = wb.create_sheet(prefix + "概览")
    for row in [("项目", "内容"), ("厂商", result["meta"]["factory"]),
                ("板名", result.get("board_name", "")),
                ("配置版本", result["meta"]["revision"]), ("配置文件", result["config_path"]),
                ("产线/工艺", result["profile_label"]), ("结论", result["verdict"]),
                ("判定覆盖率", result["coverage"]), ("风险", result["risk"]),
                ("非定量待评审条目", result.get("manual_review_count", 0)),
                ("检测方式", result.get("inspection_mode", "已确认输入与能力阈值比较")),
                ("结论口径", result["conclusion_note"])]:
        overview.append(row)
    if result.get("baseline_config"):
        overview.append(["内部基线版本", result["baseline_config"]["meta"]["revision"]])
        overview.append(["内部基线文件", result["baseline_config"]["path"]])
        overview.append(["内部基线检测结论", result["baseline_verdict"]])
    ws = wb.create_sheet(prefix + "逐项比较")
    keys = ["id", "label", "scope", "actual", "requirement", "unit", "margin",
            "status", "source", "reason", "advice", "note"]
    ws.append(["规则", "检查项", "输入范围", "输入值", "能力限制", "单位", "裕量",
               "状态", "来源", "依据", "改善建议", "备注"])
    for it in result["items"]:
        ws.append([STATUSES[it[k]] if k == "status" else it.get(k) for k in keys])
    detail = wb.create_sheet(prefix + "对象问题")
    detail.append(["规则", "检查项", "状态", "位号", "相邻位号", "面别", "X(mm)", "Y(mm)",
                   "实测值", "规则限制", "实际采用规则", "实际采用阈值", "单位", "来源", "原因", "建议"])
    for row in result.get("object_findings", []):
        detail.append([row.get("id"), row.get("label"), STATUSES.get(row.get("status"), row.get("status")),
                       row.get("refdes"), row.get("other_refdes"), row.get("side"), row.get("x_mm"), row.get("y_mm"),
                       row.get("actual"), row.get("requirement"), row.get("applied_rule"), row.get("applied_limit"),
                       row.get("unit"), row.get("source"), row.get("reason"), row.get("advice")])
    for error in result.get("object_errors", []):
        detail.append(["", "资料核对", "数据不足", "", "", "", "", "", "", "", "", "", "", "", error])
    sheets = [overview, ws, detail]
    if result.get("baseline_comparison"):
        comparison = wb.create_sheet(prefix + "内部基线对比")
        comparison.append(["检查项", "字段", "内部基线", "供应商能力", "单位", "匹配", "依据"])
        for row in result["baseline_comparison"]:
            comparison.append([row["label"], row["field"], row["baseline_limit"], row["supplier_limit"],
                               row["unit"], STATUSES[row["status"]], row["reason"]])
        sheets.append(comparison)
    if result.get("baseline_result"):
        baseline = wb.create_sheet(prefix + "内部基线检测")
        baseline.append(["规则", "检查项", "实测", "内部基线", "单位", "状态", "来源", "依据"])
        for row in result["baseline_result"]["items"]:
            baseline.append([row["id"], row["label"], row["actual"], row["requirement"], row["unit"],
                             STATUSES[row["status"]], row["source"], row["reason"]])
        sheets.append(baseline)
    if result.get("manual_requirements"):
        review = wb.create_sheet(prefix + "工艺条件评审")
        review.append(["编号", "分类", "制程", "条目", "评估内容", "供应商条件", "设备", "检测方法", "设计建议", "参考", "状态"])
        for row in result["manual_requirements"]:
            review.append([row.get(k, "") for k in ("id", "category", "process", "item", "spec", "supplier_value",
                                                   "equipment", "inspection", "design_guidance", "reference")] + ["待人工评审"])
        sheets.append(review)
    for sheet in sheets:
        sheet.freeze_panes = "A2"
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = 24
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
    return overview, ws


def export_smt_report(result, path):
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    append_smt_sheets(wb, result)
    try:
        wb.save(path)
        Path(path).with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        wb.close()

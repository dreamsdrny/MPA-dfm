"""SMT XLSX/JSON writers; contain no capability decisions."""
import json
from pathlib import Path

from src.domain.smt_rules import STATUSES


def append_smt_sheets(wb, result, prefix=""):
    if result.get("source_workbook"):
        raw = wb.create_sheet(prefix + "采集表原文")
        raw.append(["工作表", "单元格", "原始内容", "类型", "公式缓存", "批注", "链接"])
        for sheet in result["source_workbook"]["sheets"]:
            for cell in sheet["cells"]:
                raw.append([sheet["name"], cell["coordinate"], str(cell["value"]) if cell["value"] is not None else None,
                    cell["data_type"], cell.get("cached_value"), cell.get("comment"), cell.get("hyperlink")])
        for row in raw:
            for cell in row:
                if isinstance(cell.value, str): cell.data_type = "s"
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
                   "实测值", "规则限制", "实际采用规则", "实际采用阈值", "单位", "来源", "原因", "建议", "引脚", "相邻引脚", "量测依据", "重叠面积mm2"])
    for row in result.get("object_findings", []):
        detail.append([row.get("id"), row.get("label"), STATUSES.get(row.get("status"), row.get("status")),
                       row.get("refdes"), row.get("other_refdes"), row.get("side"), row.get("x_mm"), row.get("y_mm"),
                       row.get("actual"), row.get("requirement"), row.get("applied_rule"), row.get("applied_limit"),
                       row.get("unit"), row.get("source"), row.get("reason"), row.get("advice")])
    for row in result.get("assembly_findings",[]):
        detail.append([row.get("id"),row.get("label"),STATUSES.get(row.get("status")),row.get("refdes"),row.get("other_refdes"),row.get("side"),
            row.get("x_mm"),row.get("y_mm"),row.get("actual"),row.get("requirement"),"",row.get("requirement"),row.get("unit"),row.get("source"),row.get("reason"),row.get("advice"),row.get("pin"),row.get("other_pin"),row.get("measurement_basis"),row.get("overlap_area_mm2")])
    for error in result.get("object_errors", []):
        detail.append(["", "资料核对", "数据不足", "", "", "", "", "", "", "", "", "", "", "", error])
    if result.get("bom_analysis"):
        bom_sheet = wb.create_sheet(prefix + "BOM核对")
        bom_sheet.append(["位号", "面", "封装", "料号", "DNP", "状态", "冲突/缺失", "BOM来源", "SHA256"])
        for row in result["bom_analysis"]["rows"]:
            bom_sheet.append([row.get(k) for k in ("refdes", "side", "footprint", "part_number", "dnp", "status")]
                             + ["；".join(row.get("issues", [])), result["bom_analysis"]["path"], result["bom_analysis"]["source_sha256"]])
        for row in bom_sheet:
            for cell in row:
                if isinstance(cell.value,str):
                    cell.data_type="s"
        alternates=wb.create_sheet(prefix+"BOM主副料")
        alternates.append(["位号","料源角色","料号","MPN","Excel行","原标识","标识列","是否采用"])
        for row in result["bom_analysis"]["rows"]:
            if row.get("alternatives"):
                alternates.append([row["refdes"],"主料",row.get("part_number"),row.get("manufacturer_part_number"),row.get("source_row"),row.get("sourcing_marker"),row.get("sourcing_marker_column"),"是（按明确主标识）"])
                for alt in row["alternatives"]:
                    alternates.append([row["refdes"],"副/次料",alt.get("part_number"),alt.get("manufacturer_part_number"),alt.get("source_row"),alt.get("sourcing_marker"),alt.get("sourcing_marker_column"),"否（保留供核对）"])
        for cells in alternates:
            for cell in cells:
                if isinstance(cell.value,str):cell.data_type="s"
    sheets = [overview, ws, detail]
    if result.get("supplement_analysis"):
        supplement=wb.create_sheet(prefix+"ODB_STP协同核对")
        supplement.append(["资料","字段","核对/缺口"])
        for kind,record in result["supplement_analysis"].items():
            for key,value in record.items():supplement.append([kind,key,json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else value])
        sheets.append(supplement)
    if result.get("assembly_coverage"):
        assembly_sheet=wb.create_sheet(prefix+"装配补充检查")
        assembly_sheet.append(["检查键","检查项","阶段","判定","测量数","未取得数","所需资料","规则来源"])
        for row in result["assembly_coverage"]:
            assembly_sheet.append([row.get(k) for k in ("check_key","label","phase","verdict","checked","missing","required_data","source")])
        for error in result.get("assembly_errors",[]):assembly_sheet.append(["未完成",error])
        sheets.append(assembly_sheet)
    if result.get("inspection_states"):
        state_sheet = wb.create_sheet(prefix + "统一检查状态")
        state_sheet.append(["检查键", "阶段", "判定", "全板完整"])
        for state in result["inspection_states"]:
            state_sheet.append([state["check_key"], state.get("phase_label", state["phase"]),
                                state.get("verdict_label", state["verdict"]), "否"])
        sheets.append(state_sheet)
    if result.get("source_association"):
        association = wb.create_sheet(prefix + "资料关联")
        association.append(["位号", "面", "关联状态", "轮廓依据", "类别", "高度 mm", "封装", "冲突"])
        for row in result["source_association"].get("rows", []):
            association.append([row.get(k) for k in ("refdes", "side", "status", "boundary", "category", "height_mm", "footprint")] + ["；".join(row.get("issues", []))])
        for side, completeness in result.get("assembly_completeness", {}).items():
            association.append(["完整性", side, json.dumps(completeness, ensure_ascii=False)])
        for error in result["source_association"].get("errors", []):
            association.append(["资料问题", "", error])
        sheets.append(association)
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
    from src.adapters.issue_pictures import add_issue_summary, smt_entries
    add_issue_summary(wb, smt_entries(result), smt=result, name=prefix + "Issue summary")
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

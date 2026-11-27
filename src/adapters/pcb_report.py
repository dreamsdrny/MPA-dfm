"""
BRD-AI: DFM 报告生成器
=============================================
将 dfm_engine.analyze_design() 的结果输出为:
  - Excel DFM 报告 (*.xlsx): 结构对齐 Valor DFM 报告
      Main           封面 (单板信息 / 问题汇总 / 板及装配统计)
      Issue summary  问题清单 (序号/问题点/问题描述/专家建议/数量/优先级/状态/...)
      Info           制造及装配信息 (工艺统计)
      Issue1..N      单项问题明细 (规则名称/涉及层/测量值/规则值/X/Y/严重度)
  - 文本 DFM 报告 (*.txt)
"""
import os
import re
from datetime import datetime
from typing import Any, List, Optional

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from src.dfm_rules import SEVERITY_LEVELS, SEVERITY_COLOR
from src.pcb_locations import LOCATION_HEADERS, location_values, COMPONENT_HEADERS, component_values


def _thin_border():
    return Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4g}"
    return _ILLEGAL_CELL_RE.sub("", str(value))[:32000]


_ILLEGAL_CELL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _sanitize_workbook(wb) -> None:
    """Remove control chars / force literal strings so openpyxl cannot fail or run formulas."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if not isinstance(cell.value, str):
                    continue
                text = _ILLEGAL_CELL_RE.sub("", cell.value)[:32000]
                if text != cell.value:
                    cell.value = text
                if text.startswith("="):
                    cell.data_type = "s"


def _clean_value(value):
    if isinstance(value, str):
        return _ILLEGAL_CELL_RE.sub("", value)[:32000]
    if isinstance(value, dict):
        return {k: _clean_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_value(v) for v in value]
    return value


def _components_dict(value) -> dict:
    if isinstance(value, (list, tuple)):
        return {"ok": bool(value), "count": len(value),
                "refdes": [str(c.get("ref") or c.get("refdes") or "") if isinstance(c, dict) else str(c)
                           for c in value]}
    return value if isinstance(value, dict) else {}


def _write_kv(ws, start_row, col, pairs, key_font=None, border=None):
    r = start_row
    for key, val in pairs:
        kc = ws.cell(row=r, column=col, value=key)
        kc.font = key_font or Font(bold=True)
        vc = ws.cell(row=r, column=col + 1, value=_fmt(val))
        vc.alignment = Alignment(vertical="top", wrap_text=True)
        if border:
            kc.border = border
            vc.border = border
        r += 1
    return r


def build_dfm_workbook(analysis: dict, board_meta: Optional[dict] = None):
    """Build the PCB workbook in memory, reusable by the combined DFM report."""
    analysis = _clean_value(analysis or {})
    board_meta = _clean_value(board_meta or {})
    issues: List[dict] = analysis.get("issues", [])
    risk = analysis.get("risk", {})
    info_stats = analysis.get("info_stats", {})
    summary = analysis.get("issue_summary", {})

    wb = openpyxl.Workbook()
    stackup = analysis.get("stackup") or []
    if stackup:
        from openpyxl.drawing.image import Image as ExcelImage
        from src.adapters.stackup_picture import render_stackup
        section = wb.create_sheet("叠构剖面")
        section.append(["铜层", "BRD设计 μm", "采用 μm", "外/内", "来源", "剖面依据"])
        for row in stackup:
            section.append([f"L{row['index']} {row['name']}", row.get("design_copper_um", row.get("copper_um")),
                            row.get("copper_um"), "外层" if row.get("outer") else "内层", row.get("source", ""),
                            "厚度未获取" if row.get("copper_um") is None else "按已知铜厚等比"])
        section.freeze_panes = "A2"
        for column, width in zip("ABCDEF", (28, 15, 12, 10, 42, 26)):
            section.column_dimensions[column].width = width
        picture = ExcelImage(render_stackup(stackup, board_thickness_mm=next(
            (item.get("design") for item in analysis.get("items", []) if item.get("field") == "board_thickness"), None),
            surface_finish=board_meta.get("surface_finish", "")))
        picture.width, picture.height = 920, picture.height
        section.add_image(picture, "H2")
        section["H1"] = "剖面图：铜厚按已知值等比；介质/阻焊厚度未提供时仅示意，数值仍为未获取"
    component_sheet = wb.create_sheet("BRD器件坐标")
    component_sheet.append(COMPONENT_HEADERS)
    for component in analysis.get("component_coordinates", []):
        component_sheet.append(component_values(component))
    component_sheet.freeze_panes = "A2"
    component_sheet.auto_filter.ref = component_sheet.dimensions
    for column in component_sheet.columns:
        component_sheet.column_dimensions[column[0].column_letter].width = 24
    coordinate_sheet = wb.create_sheet("问题坐标")
    coordinate_sheet.append(LOCATION_HEADERS)
    for coordinate in analysis.get("problem_locations", []):
        coordinate_sheet.append(location_values(coordinate))
    coordinate_sheet.freeze_panes = "A2"
    coordinate_sheet.auto_filter.ref = coordinate_sheet.dimensions
    for cell in coordinate_sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F5496")
    for i in range(1, len(LOCATION_HEADERS) + 1):
        coordinate_sheet.column_dimensions[get_column_letter(i)].width = 90 if i == 19 else 24
    source_sheet = wb.create_sheet("坐标说明与来源")
    source_sheet.append(["坐标口径", analysis.get("coordinate_note", "未提供对象级坐标来源")])
    source_sheet.append(["BRD设计单位", analysis.get("coordinate_unit", "未知")])
    source_sheet.append(["报告筛选", analysis.get("report_filter_note", "显示全部优先级")])
    if analysis.get("analysis_mode") == "gerber_only":
        source_sheet.cell(row=1, column=2, value="Gerber 原点，毫米坐标；未绑定 BRD，不生成 Allegro 跳转命令")
        source_sheet.cell(row=2, column=1, value="Gerber坐标单位")
        source_sheet.cell(row=2, column=2, value="mm")
        for source in analysis.get("input_fingerprints", []):
            source_sheet.append(["输入SHA-256", source["path"], source["sha256"]])
        if not analysis.get("component_coordinates"):
            wb.remove(component_sheet)
    if analysis.get("manufacturing_export"):
        export = analysis["manufacturing_export"]
        source_sheet.append(["一键制造导出", export["manifest_path"], export["brd_sha256"]])
        exports_sheet = wb.create_sheet("原生导出与校验")
        exports_sheet.append(["阶段", "状态", "产物数", "源BRD SHA256", "错误/未完成", "日志"])
        for kind, stage in export["stages"].items():
            exports_sheet.append([kind, stage["status"], len(stage["files"]), stage["source_brd_sha256"],
                                  stage.get("error") or stage.get("note", ""), stage.get("log", "")])
        exports_sheet.freeze_panes = "A2"
        exports_sheet.auto_filter.ref = exports_sheet.dimensions
    if analysis.get("odb_object_result"):
        objects = analysis["odb_object_result"]
        source_sheet.append(["ODB++ 对象范围", objects["scope"]])
        source_sheet.append(["ODB++ 原始资料", objects["input"]["path"], objects["input"]["sha256"]])
        source_sheet.append(["对象规则 YAML", objects["rule_file"], objects["rule_sha256"]])
        source_sheet.append(["有效对象规则 SHA256", objects["effective_rules_sha256"]])
        for warning in objects.get("warnings", []):
            source_sheet.append(["ODB++ 提示", warning])
    for error in analysis.get("gerber_errors", []):
        source_sheet.append(["Gerber检查缺失", error])
    for layer in analysis.get("gerber_layers", []):
        source_sheet.append([layer.get("role"), layer.get("path"), layer.get("objects")])
    for row in analysis.get("geometry_coverage", []):
        source_sheet.append(["几何覆盖", row.get("rule"), row.get("layer"), row.get("status"), row.get("note")])
    if analysis.get("geometry_coverage"):
        geometry_sheet = wb.create_sheet("几何覆盖与缺数据")
        geometry_sheet.append(["规则", "图层", "覆盖状态", "所需资料/能力", "范围/未完成原因"])
        for row in analysis["geometry_coverage"]:
            geometry_sheet.append([row.get(key) for key in ("rule", "layer", "status", "required_data", "note")])
        geometry_sheet.freeze_panes = "A2"
        geometry_sheet.auto_filter.ref = geometry_sheet.dimensions
        for col, width in (("A", 34), ("B", 24), ("C", 18), ("D", 85), ("E", 95)):
            geometry_sheet.column_dimensions[col].width = width
        geometry_sheet.append(["补充资料来源", analysis.get("geometry_data_source", "")])
    source_sheet.append(["来源文件", "类型", "条数", "单位"])
    for source in analysis.get("coordinate_sources", []):
        source_sheet.append([source.get("file"), source.get("kind"), source.get("count"), source.get("unit")])
    for error in analysis.get("coordinate_errors", []):
        source_sheet.append(["解析问题", error])
    source_sheet.column_dimensions["A"].width = 35
    source_sheet.column_dimensions["B"].width = 110
    capability_sheet = wb.create_sheet("逐项能力")
    capability_keys = ("category", "item", "design", "capability", "unit", "status", "message",
                       "source", "source_detail", "note")
    capability_sheet.append(["类别", "检查项", "输入值", "能力值", "单位", "状态", "依据", "来源", "证据出处", "备注"])
    for item in analysis.get("items", []):
        capability_sheet.append([item.get(key) for key in capability_keys])
    capability_sheet.freeze_panes = "A2"
    capability_sheet.auto_filter.ref = capability_sheet.dimensions
    for col in capability_sheet.columns:
        capability_sheet.column_dimensions[col[0].column_letter].width = 26
    if analysis.get("copper_density"):
        density_sheet = wb.create_sheet("铜密度与平衡")
        density_sheet.append(["铜层", "板面积 mm²", "铜面积 mm²", "铺铜率 %", "网格 mm", "来源"])
        for row in analysis["copper_density"]:
            density_sheet.append([row.get(key) for key in
                                  ("layer", "board_area_mm2", "copper_area_mm2", "density_percent", "cell_mm", "source")])
        density_sheet.append([])
        density_sheet.append(["镜像层1", "镜像层2", "总差值 百分点", "最大局部差值 百分点", "配置上限", "状态"])
        for row in analysis.get("copper_balance", []):
            density_sheet.append([row.get(key) for key in ("first_layer", "second_layer", "delta_percent",
                                  "max_cell_delta_percent", "capability", "status")])
        density_sheet.append(["说明", "无显式板厂上限仅作测量；铺铜率不能替代铜厚/压合/翘曲模型"])
        density_sheet.freeze_panes = "A2"
        for column in density_sheet.columns:
            density_sheet.column_dimensions[column[0].column_letter].width = 30
        local_sheet = wb.create_sheet("局部铜密度")
        local_sheet.append(["铜层", "网格 X", "网格 Y", "X mm", "Y mm", "板面积 mm²", "铜面积 mm²", "铺铜率 %"])
        for row in analysis["copper_density"]:
            for cell in row.get("cells", []):
                local_sheet.append([row["layer"], *[cell.get(key) for key in
                                   ("ix", "iy", "x_mm", "y_mm", "board_area_mm2", "copper_area_mm2", "density_percent")]])
        local_sheet.freeze_panes = "A2"
        local_sheet.auto_filter.ref = local_sheet.dimensions
        for column in local_sheet.columns:
            local_sheet.column_dimensions[column[0].column_letter].width = 22
    if analysis.get("geometry_scan_stats"):
        scan_sheet = wb.create_sheet("几何扫描预算")
        scan_sheet.append(["规则", "图层", "索引对象数", "候选对数", "扫描完成"])
        for row in analysis["geometry_scan_stats"]:
            scan_sheet.append([row.get(key) for key in ("rule", "layer", "objects", "candidates", "complete")])
        scan_sheet.append(["总耗时 s", analysis.get("geometry_elapsed_seconds")])
    border = _thin_border()
    title_font = Font(name="Arial", size=14, bold=True)
    header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
    section_font = Font(name="Arial", size=11, bold=True, color="1F3864")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    wrap = Alignment(vertical="top", wrap_text=True)

    def style_header(ws, row, cols):
        for c in range(1, cols + 1):
            cell = ws.cell(row=row, column=c)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center
            cell.border = border

    # ==================== Main ====================
    ws = wb.active
    ws.title = "Main"
    ws.column_dimensions["A"].width = 4
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 70
    ws.column_dimensions["D"].width = 18
    ws.cell(row=1, column=2, value="DFM 分析报告").font = title_font
    ws.cell(row=2, column=2, value=f"板厂: {analysis.get('factory_label','')}   "
                                   f"板类型: {analysis.get('board_type_label','')}   "
                                   f"能力等级: {analysis.get('capability_label','')}")
    ws.cell(row=3, column=2, value=f"铜厚: {_fmt(analysis.get('copper_um'))}um")

    comps = _components_dict(analysis.get("components"))
    holes = analysis.get("holes") or {}
    if comps.get("ok"):
        cats = comps.get("by_category") or {}
        comps_line = f"{comps['count']} 个 (" + " / ".join(f"{k} {v}" for k, v in list(cats.items())[:5]) + ")"
    else:
        comps_line = f"— ({comps.get('reason', 'BRD 无 Part.xml')})"
    if holes.get("pth_span_count") or holes.get("npth_span_count"):
        holes_line = f"PTH {holes.get('pth_span_count', 0)} 种 / NPTH {holes.get('npth_span_count', 0)} 种"
    else:
        holes_line = "— (BRD 无钻孔表)"
    cap_cov_line = _cap_cov_text(analysis)

    ws.cell(row=5, column=2, value="单板信息").font = section_font
    board_pairs = [
        ("客户名", board_meta.get("customer", "")),
        ("单板名", board_meta.get("board_name") or analysis.get("board_name", "")),
        ("单板版本", board_meta.get("version", "")),
        ("装配名", board_meta.get("assembly_name", "")),
        ("规则集", board_meta.get("rule_set", "")),
        ("报告显示", analysis.get("report_filter_note", "全部优先级")),
        ("EDA同步工具", board_meta.get("eda_tool", "无需 CAD / 独立 Gerber" if analysis.get("analysis_mode") == "gerber_only" else "Cadence Allegro")),
        ("提交时间", board_meta.get("submit_time", "")),
        ("完成时间", board_meta.get("finish_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))),
        ("提交者", board_meta.get("submitter", "BRD-AI")),
        ("器件数量", comps_line),
        ("钻孔分类", holes_line),
        ("能力库覆盖", cap_cov_line),
        ("问题总数", summary.get("总计", len(issues))),
        ("已解决总数", sum(1 for it in issues if it.get("status") == "关闭")),
    ]
    next_row = _write_kv(ws, 6, 2, board_pairs, border=border)

    summary_row = next_row + 1
    ws.cell(row=summary_row, column=2, value="问题汇总").font = section_font
    summary_pairs = [(s, summary.get(s, 0)) for s in SEVERITY_LEVELS]
    summary_pairs.append(("总计", summary.get("总计", len(issues))))
    _write_kv(ws, summary_row + 1, 2, summary_pairs, border=border)
    risk_pairs = [
        ("可制造性评分", f"{risk.get('score','-')} / 100"),
        ("风险等级", risk.get('risk_level', '-')),
        ("预估良率区间", f"{risk.get('yield_estimate',['-','-'])[0]}% ~ "
                          f"{risk.get('yield_estimate',['-','-'])[1]}%"),
        ("综合结论", risk.get('verdict', '-')),
    ]
    _write_kv(ws, summary_row + 1, 4, risk_pairs, border=border)

    # ---- 数据覆盖率 / 结论 ----
    coverage = analysis.get("coverage") or {}
    cov_pairs = [
        ("数据覆盖率", f"{coverage.get('ratio', 0):.0%} "
                       f"({coverage.get('checked', 0)}/{coverage.get('total', 0)} 项关键输入)"),
        ("覆盖率等级", coverage.get("level", "-")),
        ("判定结论", analysis.get("verdict", "-")),
        ("判定依据", "；".join(analysis.get("verdict_reasons") or []) or "无不符合项/缺数据"),
        ("可否放行", "可以" if analysis.get("releasable") else "不可以（见判定依据）"),
        ("数据来源", "Gerber 几何 + 已确认铜厚/叠构" if analysis.get("analysis_mode") == "gerber_only" else "BRD 提取" if analysis.get("data_origin") == "BRD" else "手工输入"),
        ("Gerber输入" if analysis.get("analysis_mode") == "gerber_only" else "BRD文件", analysis.get("input_gerber", analysis.get("input_brd", ""))),
        ("Gerber资料" if analysis.get("analysis_mode") == "gerber_only" else "Gerber目录", analysis.get("gerber_folder", "")),
        ("几何输入来源", analysis.get("geometry_input_source", "")),
        ("几何补充资料", analysis.get("geometry_data_source", "")),
        ("PCB规则文件", analysis.get("rule_source", "")),
        ("PCB规则版本", analysis.get("rule_revision", "")),
        ("PCB规则SHA-256", analysis.get("rule_sha256", "")),
        ("分析时间", analysis.get("analyzed_at", "")),
    ]
    coverage_row = summary_row + 2 + max(len(summary_pairs), len(risk_pairs))
    _write_kv(ws, coverage_row, 2, cov_pairs, border=border)
    if coverage.get("missing_items"):
        ws.cell(row=coverage_row + len(cov_pairs) + 1, column=2,
                value="缺数据未检查: " + "、".join(coverage["missing_items"]))

    stats_row = coverage_row + len(cov_pairs) + 4
    ws.cell(row=stats_row, column=2, value="单板及装配统计信息").font = section_font
    man = info_stats.get("manufacturing", {})
    asm = info_stats.get("assembly", {})
    _write_kv(ws, stats_row + 1, 2, list(man.items())[:12], border=border)
    _write_kv(ws, stats_row + 1, 4, list(asm.items())[:12], border=border)

    # ==================== Issue summary ====================
    ws2 = wb.create_sheet("Issue summary")
    ws2.cell(row=1, column=1, value="DFM 问题清单").font = title_font
    ws2.cell(row=2, column=1, value=f"问题总数: {summary.get('总计', len(issues))}    "
                                     f"危险 {summary.get('危险',0)} / 高 {summary.get('高',0)} / "
                                     f"中 {summary.get('中',0)} / 低 {summary.get('低',0)}")

    headers = ["序号", "问题点(code)", "问题描述", "专家建议", "数量",
               "优先级", "状态", "涉及层", "X位置", "Y位置", "备注", "问题图片", "图片来源/状态"]
    start = 4
    for c, h in enumerate(headers, 1):
        ws2.cell(row=start, column=c, value=h)
    style_header(ws2, start, len(headers))

    widths = [6, 22, 46, 46, 6, 8, 8, 10, 10, 10, 18, 47, 52]
    for i, w in enumerate(widths, 1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    from src.adapters.issue_pictures import IssuePictureContext, embed_picture, pcb_entries
    picture_context = IssuePictureContext(pcb=analysis)
    picture_entries = pcb_entries(analysis)
    for idx, it in enumerate(issues, 1):
        r = start + idx
        detail_sheet = f"Issue{idx}"
        ws2.cell(row=r, column=1, value=idx)
        code_cell = ws2.cell(row=r, column=2, value=f"{it.get('rule','')} [{it.get('code','')}]")
        code_cell.hyperlink = f"#'{detail_sheet}'!A1"
        code_cell.font = Font(color="0563C1", underline="single")
        ws2.cell(row=r, column=3, value=it.get("description", ""))
        ws2.cell(row=r, column=4, value=it.get("suggestion", ""))
        ws2.cell(row=r, column=5, value=it.get("count", 1))
        sev_cell = ws2.cell(row=r, column=6, value=it.get("severity", ""))
        sev_cell.fill = PatternFill(start_color=SEVERITY_COLOR.get(it.get("severity"), "FFFFFF"),
                                    end_color=SEVERITY_COLOR.get(it.get("severity"), "FFFFFF"),
                                    fill_type="solid")
        ws2.cell(row=r, column=7, value=it.get("status", ""))
        ws2.cell(row=r, column=8, value=it.get("layer", ""))
        ws2.cell(row=r, column=9, value=_fmt(it.get("x")))
        ws2.cell(row=r, column=10, value=_fmt(it.get("y")))
        ws2.cell(row=r, column=11, value=it.get("note", ""))
        for c in range(1, len(headers) + 1):
            cell = ws2.cell(row=r, column=c)
            cell.border = border
            cell.alignment = wrap
        sev_cell.alignment = center
        embed_picture(ws2, r, 12, picture_entries[idx-1], picture_context, 13)

    # ==================== Info ====================
    ws3 = wb.create_sheet("Info")
    ws3.column_dimensions["A"].width = 4
    ws3.column_dimensions["B"].width = 20
    ws3.column_dimensions["C"].width = 16
    ws3.column_dimensions["D"].width = 10
    ws3.column_dimensions["F"].width = 20
    ws3.column_dimensions["G"].width = 16
    ws3.cell(row=1, column=2, value="DFM 报告 - 制造及装配信息").font = title_font
    ws3.cell(row=3, column=2, value="制造工艺").font = section_font
    ws3.cell(row=4, column=2, value="规格")
    ws3.cell(row=4, column=3, value="数值")
    ws3.cell(row=4, column=4, value="单位")
    style_header(ws3, 4, 4)
    r = 5
    units = {"最小孔径": "mm", "单板厚度": "mm", "微孔最小孔径": "mm",
             "外层线宽": "mm", "外层线距": "mm", "内层线宽": "mm", "内层线距": "mm",
             "单板Pin密度": "Pin/cm²", "拼板铆孔密度": "Via/cm²"}
    for k, v in info_stats.get("manufacturing", {}).items():
        ws3.cell(row=r, column=2, value=k).border = border
        ws3.cell(row=r, column=3, value=_fmt(v)).border = border
        ws3.cell(row=r, column=4, value=units.get(k, "")).border = border
        r += 1
    ws3.cell(row=3, column=6, value="装配工艺").font = section_font
    ws3.cell(row=4, column=6, value="详细资料")
    ws3.cell(row=4, column=7, value="数值")
    style_header(ws3, 4, 4)
    r2 = 5
    for k, v in info_stats.get("assembly", {}).items():
        ws3.cell(row=r2, column=6, value=k).border = border
        ws3.cell(row=r2, column=7, value=_fmt(v)).border = border
        r2 += 1
    notes = info_stats.get("capability_notes", {})
    if notes:
        ws3.cell(row=r + 2, column=2, value="制程能力备注").font = section_font
        _write_kv(ws3, r + 3, 2, list(notes.items()), border=border)

    # ==================== Issue detail sheets ====================
    detail_cols = ["序号", "规则名称", "涉及层", "从…开始", "到…结束",
                   "测量值mm", "规则值mm", "X位置mm", "Y位置mm",
                   "严重度", "状态", "首次版本", "客户/研发反馈"]
    for idx, it in enumerate(issues, 1):
        wsd = wb.create_sheet(f"Issue{idx}")
        wsd.cell(row=1, column=1, value=f"DFM 报告 - 问题 {idx}").font = title_font
        wsd.column_dimensions["B"].width = 26
        for i in range(3, len(detail_cols) + 1):
            wsd.column_dimensions[get_column_letter(i)].width = 14
        wsd.cell(row=3, column=2, value="问题描述:").font = Font(bold=True)
        wsd.cell(row=3, column=3, value=it.get("description", ""))
        wsd.cell(row=4, column=2, value="专家建议:").font = Font(bold=True)
        wsd.cell(row=4, column=3, value=it.get("suggestion", ""))
        for c, h in enumerate(detail_cols, 1):
            wsd.cell(row=6, column=c, value=h)
        style_header(wsd, 6, len(detail_cols))
        row = 7
        wsd.cell(row=row, column=1, value=1)
        wsd.cell(row=row, column=2, value=f"{it.get('rule','')} [{it.get('code','')}]")
        wsd.cell(row=row, column=3, value=it.get("layer", ""))
        wsd.cell(row=row, column=4, value=it.get("from", ""))
        wsd.cell(row=row, column=5, value=it.get("to", ""))
        wsd.cell(row=row, column=6, value=_fmt(it.get("measured")))
        wsd.cell(row=row, column=7, value=_fmt(it.get("required")))
        wsd.cell(row=row, column=8, value=_fmt(it.get("x")))
        wsd.cell(row=row, column=9, value=_fmt(it.get("y")))
        sev_cell = wsd.cell(row=row, column=10, value=it.get("severity", ""))
        sev_cell.fill = PatternFill(start_color=SEVERITY_COLOR.get(it.get("severity"), "FFFFFF"),
                                    end_color=SEVERITY_COLOR.get(it.get("severity"), "FFFFFF"),
                                    fill_type="solid")
        wsd.cell(row=row, column=11, value=it.get("status", ""))
        wsd.cell(row=row, column=12, value=board_meta.get("version", ""))
        wsd.cell(row=row, column=13, value=it.get("note", ""))
        for c in range(1, len(detail_cols) + 1):
            wsd.cell(row=row, column=c).border = border

    # ==================== 数据来源 ====================
    sources = analysis.get("sources") or {}
    if sources or analysis.get("data_warnings"):
        ws4 = wb.create_sheet("数据来源")
        ws4.cell(row=1, column=1, value="DFM 数据来源与可信度").font = title_font
        ws4.column_dimensions["A"].width = 26
        ws4.column_dimensions["B"].width = 46
        ws4.column_dimensions["C"].width = 12
        headers4 = ["字段", "来源", "置信度"]
        for c, h in enumerate(headers4, 1):
            ws4.cell(row=3, column=c, value=h)
        style_header(ws4, 3, len(headers4))
        labels = _field_labels()
        r = 4
        for key, meta in sources.items():
            ws4.cell(row=r, column=1, value=labels.get(key, key)).border = border
            ws4.cell(row=r, column=2, value=(meta or {}).get("source", "")).border = border
            ws4.cell(row=r, column=3, value=(meta or {}).get("confidence", "")).border = border
            r += 1
        warnings = analysis.get("data_warnings") or []
        if warnings:
            ws4.cell(row=r + 1, column=1, value="数据校验提示").font = section_font
            for i, w in enumerate(warnings, 1):
                ws4.cell(row=r + 1 + i, column=1, value=w)

    # ==================== 器件清单 ====================
    comps = _components_dict(analysis.get("components"))
    if comps.get("ok") and comps.get("refdes"):
        wsc = wb.create_sheet("器件清单")
        wsc.cell(row=1, column=1, value=f"器件清单 (共 {comps['count']} 个)").font = title_font
        wsc.cell(row=2, column=1,
                 value="来源: BRD 归档 objects/Part.xml 的位号节点; FAB 拼板图不含该对象")
        headers_c = ["序号", "位号", "类别"]
        for c, h in enumerate(headers_c, 1):
            wsc.cell(row=4, column=c, value=h)
        style_header(wsc, 4, len(headers_c))
        wsc.column_dimensions["A"].width = 8
        wsc.column_dimensions["B"].width = 16
        wsc.column_dimensions["C"].width = 14
        prefix_cat = _refdes_category_map(comps.get("by_prefix") or {})
        for idx, ref in enumerate(comps["refdes"], 1):
            wsc.cell(row=4 + idx, column=1, value=idx).border = border
            wsc.cell(row=4 + idx, column=2, value=ref).border = border
            wsc.cell(row=4 + idx, column=3, value=prefix_cat.get(_refdes_prefix(ref), "")).border = border

    _sanitize_workbook(wb)
    if analysis.get("inspection_states"):
        state_sheet = wb.create_sheet("统一检查状态")
        state_sheet.append(["检查键", "检查项", "数据/检查阶段", "判定", "范围", "来源", "全板完整"])
        for row in analysis["inspection_states"]:
            state_sheet.append([row["check_key"], row["label"], row["phase_label"], row["verdict_label"],
                                " / ".join(s.get("scope", "") for s in row.get("scopes", [])),
                                "；".join(row.get("sources", [])), "否"])
    return wb


def generate_dfm_excel(analysis: dict, output_path: str,
                       board_meta: Optional[dict] = None) -> str:
    wb = build_dfm_workbook(analysis, board_meta)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    wb.save(output_path)
    return output_path


def _cap_cov_text(analysis: dict) -> str:
    """能力库覆盖: 共多少项、已判定/缺数据/未接入各多少。"""
    cov = analysis.get("capability_coverage") or {}
    if not cov.get("total"):
        return "未统计"
    judged = cov.get("covered", 0) + cov.get("judged", 0)
    return (f"共 {cov['total']} 项: 已判定 {judged} 项, "
            f"缺数据 {cov.get('missing', 0)} 项, 未接入 {cov.get('unmapped', 0)} 项")


def generate_dfm_text(analysis: dict, output_path: str,
                      board_meta: Optional[dict] = None) -> str:
    board_meta = board_meta or {}
    issues = analysis.get("issues", [])
    risk = analysis.get("risk", {})
    lines = []
    if analysis.get("report_filter_note"):
        lines.append(analysis["report_filter_note"])
    lines.append("=" * 70)
    lines.append("  DFM 制程能力分析报告")
    if analysis.get("manufacturing_export"):
        native = analysis["manufacturing_export"]
        lines.append(f"  原生制造导出清单: {native['manifest_path']}")
        lines.append(f"  源BRD SHA256: {native['brd_sha256']}")
        for kind, stage in native["stages"].items():
            lines.append(f"  原生导出 {kind}: {stage['status']}；产物 {len(stage['files'])}；"
                         + (stage.get("error") or stage.get("note", "")))
    lines.append("=" * 70)
    lines.append(f"  板厂: {analysis.get('factory_label','')}    "
                 f"板类型: {analysis.get('board_type_label','')}    "
                 f"能力等级: {analysis.get('capability_label','')}")
    lines.append(f"  板名: {board_meta.get('board_name') or analysis.get('board_name') or '未命名'}    "
                 f"铜厚: {_fmt(analysis.get('copper_um'))}um")
    lines.append(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if analysis.get("geometry_coverage"):
        lines.append("  制造几何：部分覆盖；无已发现缺陷不代表完整 DFM 放行")
        for row in analysis["geometry_coverage"]:
            lines.append(f"    {row.get('layer', '')} {row.get('rule')}: {row.get('status')} — {row.get('note')}")
            if row.get("required_data") and row.get("status") != "measured":
                lines.append(f"      需补充: {row['required_data']}")
        for error in analysis.get("gerber_errors", []):
            lines.append(f"    未完成: {error}")
        for row in analysis.get("copper_density", []):
            lines.append(f"    {row['layer']} 铺铜率 {row['density_percent']:.3f}% / 板面积 {row['board_area_mm2']:.3f}mm²")
    comps = _components_dict(analysis.get("components"))
    holes = analysis.get("holes") or {}
    if comps.get("ok"):
        cats = comps.get("by_category") or {}
        lines.append(f"  器件: {comps['count']} 个  ("
                     + " / ".join(f"{k} {v}" for k, v in list(cats.items())[:6]) + ")")
        lines.append(f"  位号: {'、'.join(comps['refdes'][:16])}"
                     + (" …" if comps["count"] > 16 else ""))
    elif comps:
        lines.append(f"  器件: 读不到 — {comps.get('reason', 'BRD 无 Part.xml')}")
    if holes.get("pth_span_count") or holes.get("npth_span_count"):
        lines.append(f"  能力库覆盖: {_cap_cov_text(analysis)}")
        lines.append(f"  钻孔分类: PTH {holes.get('pth_span_count', 0)} 种 / "
                     f"NPTH {holes.get('npth_span_count', 0)} 种"
                     + (f" (NPTH 孔径 {'/'.join(f'{v:.3f}' for v in holes['npth_sizes_mm'][:4])}mm)"
                        if holes.get("npth_sizes_mm") else ""))
        lines.append("  孔到成型线: 需钻孔坐标 (BRD 快照只有孔径/跨度, 无坐标)")
    laser = next((it for it in (analysis.get("items") or []) if it.get("item") == "最小激光孔"), None)
    if laser and laser.get("design") is not None:
        lines.append(f"  最小激光孔: {laser['design']}mm"
                     + (f" (能力 {laser['capability']}mm)" if laser.get("capability") is not None else ""))
    silk_row = next((it for it in (analysis.get("items") or [])
                     if it.get("item") in ("最小文字线宽", "丝印线宽")), None)
    if silk_row:
        lines.append(f"  丝印线宽: {silk_row.get('message', '')}")
    silk_edge_row = next((it for it in (analysis.get("items") or [])
                          if it.get("item") == "丝印离成型线"), None)
    if silk_edge_row:
        lines.append(f"  丝印离成型线: {silk_edge_row.get('message', '')}")
    for _item in ("丝印到器件", "测试点"):
        row = next((it for it in (analysis.get("items") or [])
                    if it.get("item") == _item), None)
        if row:
            lines.append(f"  {_item}: {row.get('message', '')}")
    pad_row = next((it for it in (analysis.get("items") or [])
                    if it.get("item") == "引脚间距 / 焊盘间距"), None)
    if pad_row:
        lines.append(f"  引脚/焊盘间距: {pad_row.get('message', '')}")
    lines.append("-" * 70)
    summary = analysis.get("issue_summary", {})
    lines.append(f"  问题总数: {summary.get('总计', len(issues))}   "
                 f"危险 {summary.get('危险',0)} / 高 {summary.get('高',0)} / "
                 f"中 {summary.get('中',0)} / 低 {summary.get('低',0)}")
    lines.append(f"  可制造性评分: {risk.get('score','-')}/100    "
                 f"风险等级: {risk.get('risk_level','-')}    "
                 f"预估良率: {risk.get('yield_estimate',['-','-'])[0]}%~"
                 f"{risk.get('yield_estimate',['-','-'])[1]}%")
    lines.append(f"  结论: {risk.get('verdict','-')}")
    coverage = analysis.get("coverage") or {}
    if coverage:
        lines.append(f"  数据覆盖率: {coverage.get('ratio', 0):.0%} "
                     f"({coverage.get('checked', 0)}/{coverage.get('total', 0)})    "
                     f"判定: {analysis.get('verdict', '-')}    "
                     f"可否放行: {'可以' if analysis.get('releasable') else '不可以'}")
        for reason in analysis.get("verdict_reasons") or []:
            lines.append(f"  判定依据: {reason}")
        for label, key in (("BRD文件", "input_brd"), ("Gerber目录", "gerber_folder"),
                           ("几何输入来源", "geometry_input_source"), ("几何补充资料", "geometry_data_source"),
                           ("PCB规则文件", "rule_source"), ("PCB规则版本", "rule_revision"),
                           ("PCB规则SHA-256", "rule_sha256")):
            if analysis.get(key):
                lines.append(f"  {label}: {analysis[key]}")
        if coverage.get("missing_items"):
            lines.append(f"  缺数据未参与判定: {'、'.join(coverage['missing_items'])}")
        brd_n = analysis.get("brd_item_count") or 0
        manual_n = sum(1 for it in (analysis.get("items") or [])
                       if it.get("source") == "手工")
        if brd_n or manual_n:
            lines.append(f"  判定取值: BRD 读到 {brd_n} 项 / 人工确认 {manual_n} 项")
        assumed = analysis.get("assumed_fields") or []
        if assumed:
            lines.append(f"  示例值未确认 (不参与判定): {'、'.join(assumed)}")
    lines.append("=" * 70)
    sources = analysis.get("sources") or {}
    if sources:
        lines.append("")
        lines.append("  数据来源:")
        labels = _field_labels()
        for key, meta in sources.items():
            lines.append(f"    {labels.get(key, key):10s} "
                         f"{(meta or {}).get('source','')}"
                         f"（{(meta or {}).get('confidence','')}）")
    for w in (analysis.get("data_warnings") or []):
        lines.append(f"    [数据校验] {w}")

    if issues:
        lines.append("")
        lines.append("  问题清单:")
        for idx, it in enumerate(issues, 1):
            lines.append(f"  [{it.get('severity','')}] {idx:>2}. {it.get('code','')} "
                         f"{it.get('rule','')}")
            lines.append(f"        {it.get('description','')}")
            if it.get("measured") is not None or it.get("required") is not None:
                lines.append(f"        测量值={_fmt(it.get('measured'))}  规则值={_fmt(it.get('required'))}")
            lines.append(f"        建议: {it.get('suggestion','')}")
    else:
        lines.append("")
        lines.append("  未发现 DFM 问题。")

    man = analysis.get("info_stats", {}).get("manufacturing", {})
    asm = analysis.get("info_stats", {}).get("assembly", {})
    if man or asm:
        lines.append("")
        lines.append("-" * 70)
        lines.append("  制造工艺 / 装配工艺统计:")
        for k, v in man.items():
            lines.append(f"    {k:16s} {_fmt(v)}")
        for k, v in asm.items():
            lines.append(f"    {k:16s} {_fmt(v)}")
    lines.append("")
    lines.append("=" * 70)

    content = "\n".join(lines)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return output_path

def _refdes_prefix(refdes: str) -> str:
    import re as _re
    m = _re.match(r"^([A-Za-z]+)\d", refdes or "")
    return m.group(1).upper() if m else ""


def _refdes_category_map(by_prefix: dict) -> dict:
    """位号前缀 -> 类别 (与 src/brd_components 的口径一致)。"""
    from src.brd_components import _PREFIX_CATEGORY
    return {prefix: _PREFIX_CATEGORY.get(prefix, prefix) for prefix in (by_prefix or {})}


def _field_labels() -> dict:
    """字段名 -> 中文标签, 与界面参数表同一份定义 (src/dfm_inputs.py)。"""
    labels = {}
    try:
        from src.dfm_inputs import DFM_FIELDS
        labels.update({key: label for _cat, key, label, _default in DFM_FIELDS})
    except Exception:  # noqa: BLE001
        pass
    labels.update({
        "min_line_width_mm": "最小线宽", "min_neck_width_mm": "最小颈宽",
        "min_spacing_mm": "最小线距", "min_drill_mm": "最小机械钻孔",
        "min_laser_via_mm": "激光孔", "min_annular_ring_mm": "单边孔环",
        "min_annular_ring_laser_mm": "激光孔单边孔环",
        "board_thickness_mm": "板厚", "layer_count": "层数", "design_name": "设计名",
        "board_size": "板尺寸", "min_drill_for_ar": "纵横比用最小孔径",
        "silkscreen_char_width": "丝印字符宽", "coverlay_window": "覆盖膜最小开窗",
    })
    return labels

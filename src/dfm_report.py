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
from datetime import datetime
from typing import Any, Dict, List, Optional

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from src.dfm_rules import SEVERITY_LEVELS, SEVERITY_COLOR, SEVERITY_ORDER


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
    return str(value)


def _write_kv(ws, start_row, col, pairs, key_font=None, border=None):
    r = start_row
    for key, val in pairs:
        kc = ws.cell(row=r, column=col, value=key)
        kc.font = key_font or Font(bold=True)
        vc = ws.cell(row=r, column=col + 1, value=_fmt(val))
        if border:
            kc.border = border
            vc.border = border
        r += 1
    return r


def build_dfm_workbook(analysis: dict, board_meta: Optional[dict] = None):
    """Build the PCB workbook in memory, reusable by the combined DFM report."""
    board_meta = board_meta or {}
    issues: List[dict] = analysis.get("issues", [])
    risk = analysis.get("risk", {})
    info_stats = analysis.get("info_stats", {})
    summary = analysis.get("issue_summary", {})

    wb = openpyxl.Workbook()
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
    ws.column_dimensions["C"].width = 40
    ws.column_dimensions["D"].width = 18
    ws.cell(row=1, column=2, value="DFM 分析报告").font = title_font
    ws.cell(row=2, column=2, value=f"板厂: {analysis.get('factory_label','')}   "
                                   f"板类型: {analysis.get('board_type_label','')}   "
                                   f"能力等级: {analysis.get('capability_label','')}")
    ws.cell(row=3, column=2, value=f"铜厚: {_fmt(analysis.get('copper_um'))}um")

    comps = analysis.get("components") or {}
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
        ("单板名", board_meta.get("board_name", "")),
        ("单板版本", board_meta.get("version", "")),
        ("装配名", board_meta.get("assembly_name", "")),
        ("规则集", board_meta.get("rule_set", "")),
        ("EDA同步工具", board_meta.get("eda_tool", "Cadence Allegro")),
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
        ("可否放行", "可以" if analysis.get("releasable") else "不可以（数据不足或存在问题）"),
        ("数据来源", "BRD 提取" if analysis.get("data_origin") == "BRD" else "手工输入"),
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
               "优先级", "状态", "涉及层", "X位置", "Y位置", "备注"]
    start = 4
    for c, h in enumerate(headers, 1):
        ws2.cell(row=start, column=c, value=h)
    style_header(ws2, start, len(headers))

    widths = [6, 22, 46, 46, 6, 8, 8, 10, 10, 10, 18]
    for i, w in enumerate(widths, 1):
        ws2.column_dimensions[get_column_letter(i)].width = w

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
    comps = analysis.get("components") or {}
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
    lines.append("=" * 70)
    lines.append("  DFM 制程能力分析报告")
    lines.append("=" * 70)
    lines.append(f"  板厂: {analysis.get('factory_label','')}    "
                 f"板类型: {analysis.get('board_type_label','')}    "
                 f"能力等级: {analysis.get('capability_label','')}")
    lines.append(f"  板名: {board_meta.get('board_name', analysis.get('board_type_label',''))}    "
                 f"铜厚: {_fmt(analysis.get('copper_um'))}um")
    lines.append(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    comps = analysis.get("components") or {}
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

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


def generate_dfm_excel(analysis: dict, output_path: str,
                       board_meta: Optional[dict] = None) -> str:
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
        ("问题总数", summary.get("总计", len(issues))),
        ("已解决总数", sum(1 for it in issues if it.get("status") == "关闭")),
    ]
    _write_kv(ws, 6, 2, board_pairs, border=border)

    ws.cell(row=18, column=2, value="问题汇总").font = section_font
    summary_pairs = [(s, summary.get(s, 0)) for s in SEVERITY_LEVELS]
    summary_pairs.append(("总计", summary.get("总计", len(issues))))
    _write_kv(ws, 19, 2, summary_pairs, border=border)
    risk_pairs = [
        ("可制造性评分", f"{risk.get('score','-')} / 100"),
        ("风险等级", risk.get('risk_level', '-')),
        ("预估良率区间", f"{risk.get('yield_estimate',['-','-'])[0]}% ~ "
                          f"{risk.get('yield_estimate',['-','-'])[1]}%"),
        ("综合结论", risk.get('verdict', '-')),
    ]
    _write_kv(ws, 19, 4, risk_pairs, border=border)

    ws.cell(row=27, column=2, value="单板及装配统计信息").font = section_font
    man = info_stats.get("manufacturing", {})
    asm = info_stats.get("assembly", {})
    _write_kv(ws, 28, 2, list(man.items())[:12], border=border)
    _write_kv(ws, 28, 4, list(asm.items())[:12], border=border)

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

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    wb.save(output_path)
    return output_path


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
    lines.append("=" * 70)
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
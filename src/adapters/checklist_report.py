"""Checklist 填写报告: Excel 明细 + TXT 摘要.

报告回答三个问题:
    1. 这次填了哪些项, 结论是什么 (逐项判定)
    2. 哪些项工具无法判定, 需要人工 (按类别分组)
    3. 模板是怎么识别的, 哪些条目还没有规则覆盖 (便于下次更新模板/补规则)
"""
import os
from datetime import datetime

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", start_color="2F5496")
HEADER_FONT = Font(color="FFFFFF", bold=True, name="Microsoft YaHei", size=10)
TITLE_FONT = Font(bold=True, name="Microsoft YaHei", size=14)
LABEL_FONT = Font(name="Microsoft YaHei", size=10, color="44546A")
VALUE_FONT = Font(name="Microsoft YaHei", size=10)
THIN = Border(*[Side(style="thin", color="D0D7E5")] * 4)

STATUS_FILL = {
    "PASS": PatternFill("solid", start_color="E7F5EC"),
    "VERIFY": PatternFill("solid", start_color="FDF6DF"),
    "FAIL": PatternFill("solid", start_color="FDE8E8"),
    "MANUAL": PatternFill("solid", start_color="EAF1FD"),
    "N/A": PatternFill("solid", start_color="F3F4F6"),
}


def _write_header(ws, headers, row=1):
    for col, text in enumerate(headers, 1):
        cell = ws.cell(row, col, text)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN
    ws.row_dimensions[row].height = 26


def _autosize(ws, widths):
    for col, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = width


def _write_rows(ws, rows, start_row=2, status_col=None):
    for offset, values in enumerate(rows):
        row = start_row + offset
        for col, value in enumerate(values, 1):
            cell = ws.cell(row, col, value)
            cell.font = VALUE_FONT
            cell.border = THIN
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        if status_col:
            status = str(values[status_col - 1] or "")
            fill = STATUS_FILL.get(status)
            if fill:
                ws.cell(row, status_col).fill = fill
                ws.cell(row, status_col).font = Font(
                    bold=True, name="Microsoft YaHei", size=10)
                ws.cell(row, status_col).alignment = Alignment(
                    horizontal="center", vertical="center")


def _overview_rows(result: dict, board_meta: dict) -> list:
    template = result.get("template") or {}
    stats = result.get("stats") or {}
    total = max(stats.get("total", 0), 1)
    auto_rate = (stats.get("auto_pass", 0) + stats.get("auto_fail", 0)) / total
    handled = (stats.get("auto_pass", 0) + stats.get("auto_fail", 0)
               + stats.get("auto_verify", 0) + stats.get("na", 0)) / total
    rows = [
        ("单板名称", board_meta.get("board_name") or "—"),
        ("BRD 文件", board_meta.get("brd_path") or "—"),
        ("Checklist 模板", template.get("path") or "—"),
        ("工作表 / 表头行", f"{template.get('sheet')} / 第 {template.get('header_row')} 行"),
        ("列识别方式", template.get("method") or "—"),
        ("板厂 / 板型 / 等级", " / ".join(str(board_meta.get(k) or "—")
                                    for k in ("factory", "board_type", "level"))),
        ("填写人 / 时间", f"{result.get('checker')} / {result.get('timestamp')}"),
        ("检查项总数", stats.get("total", 0)),
        ("自动 PASS", stats.get("auto_pass", 0)),
        ("自动 FAIL", stats.get("auto_fail", 0)),
        ("待 DRC/仿真验证", stats.get("auto_verify", 0)),
        ("需人工确认", stats.get("manual", 0)),
        ("不适用", stats.get("na", 0)),
        ("规则命中数", stats.get("rule_matched", 0)),
        ("未覆盖条目", stats.get("unmatched", 0)),
        ("自动判定率 (PASS+FAIL)", f"{auto_rate:.0%}"),
        ("已处理率 (含验证/不适用)", f"{handled:.0%}"),
    ]
    warnings = template.get("warnings") or []
    if warnings:
        rows.append(("模板警告", "；".join(warnings)))
    return rows


def write_excel_report(result: dict, path: str, board_meta: dict = None,
                       dfm: dict = None) -> str:
    board_meta = board_meta or {}
    template = result.get("template") or {}
    decisions = result.get("decisions") or []

    wb = openpyxl.Workbook()

    # ---- 概览 ----
    ws = wb.active
    ws.title = "概览"
    ws["A1"] = "Checklist 自动填写报告"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A1:D1")
    ws["A2"] = "由 BRD 数据分析结果自动填写, 未覆盖项保留给人工确认"
    ws["A2"].font = LABEL_FONT
    ws.merge_cells("A2:D2")

    for offset, (label, value) in enumerate(_overview_rows(result, board_meta)):
        row = 4 + offset
        ws.cell(row, 1, label).font = LABEL_FONT
        cell = ws.cell(row, 2, value)
        cell.font = VALUE_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    _autosize(ws, [18, 92, 14, 14])

    # ---- 逐项判定 ----
    ws2 = wb.create_sheet("逐项判定")
    _write_header(ws2, ["序号", "类别", "级别", "检查项", "结论", "依据 / 原因", "规则"])
    rows = []
    for decision in decisions:
        rows.append([
            decision["no"] if decision["no"] is not None else "",
            decision["category"], decision["grade_type"], decision["item"],
            decision["status"], decision["reason"], decision["rule_id"] or "—",
        ])
    _write_rows(ws2, rows, status_col=5)
    _autosize(ws2, [7, 22, 11, 62, 10, 62, 26])
    ws2.freeze_panes = "A2"
    ws2.auto_filter.ref = f"A1:G{max(len(rows) + 1, 2)}"

    # ---- 需人工确认 ----
    ws3 = wb.create_sheet("需人工确认")
    _write_header(ws3, ["序号", "类别", "检查项", "为什么需要人工"])
    manual = [d for d in decisions if d["status"] == "MANUAL"]
    _write_rows(ws3, [[d["no"] or "", d["category"], d["item"], d["reason"]] for d in manual])
    _autosize(ws3, [7, 22, 62, 60])
    ws3.freeze_panes = "A2"

    # ---- 不符合 ----
    ws4 = wb.create_sheet("不符合")
    _write_header(ws4, ["序号", "类别", "检查项", "判定依据", "规则"])
    fails = [d for d in decisions if d["status"] == "FAIL"]
    _write_rows(ws4, [[d["no"] or "", d["category"], d["item"], d["reason"],
                       d["rule_id"] or "—"] for d in fails], status_col=None)
    _autosize(ws4, [7, 22, 62, 60, 26])

    # ---- 待验证 ----
    ws5 = wb.create_sheet("待验证")
    _write_header(ws5, ["序号", "类别", "检查项", "依据 (需 DRC/仿真确认)", "规则"])
    verifies = [d for d in decisions if d["status"] == "VERIFY"]
    _write_rows(ws5, [[d["no"] or "", d["category"], d["item"], d["reason"],
                       d["rule_id"] or "—"] for d in verifies])
    _autosize(ws5, [7, 22, 58, 58, 26])

    # ---- 模板识别 ----
    ws6 = wb.create_sheet("模板识别")
    ws6["A1"] = "识别结果"
    ws6["A1"].font = TITLE_FONT
    info = [
        ("工作表", template.get("sheet")),
        ("表头行", template.get("header_row")),
        ("识别方式", template.get("method")),
        ("检查项数量", template.get("count")),
    ]
    for offset, (label, value) in enumerate(info, start=3):
        ws6.cell(offset, 1, label).font = LABEL_FONT
        ws6.cell(offset, 2, value).font = VALUE_FONT

    start = 3 + len(info) + 1
    ws6.cell(start, 1, "列映射").font = Font(bold=True, name="Microsoft YaHei")
    headers = template.get("column_headers") or {}
    _write_header(ws6, ["逻辑列", "Excel 列号", "表头文字"], row=start + 1)
    mapping_rows = [[key, col, headers.get(key, "")]
                    for key, col in (template.get("columns") or {}).items()]
    _write_rows(ws6, mapping_rows, start_row=start + 2)

    warn_row = start + 2 + len(mapping_rows) + 1
    ws6.cell(warn_row, 1, "模板警告").font = Font(bold=True, name="Microsoft YaHei")
    warnings = template.get("warnings") or ["无"]
    for offset, text in enumerate(warnings):
        ws6.cell(warn_row + 1 + offset, 1, text).font = VALUE_FONT

    unmatched = [d for d in decisions if not d["rule_id"]]
    unm_row = warn_row + len(warnings) + 2
    ws6.cell(unm_row, 1, f"未覆盖条目 ({len(unmatched)})").font = Font(
        bold=True, name="Microsoft YaHei")
    for offset, decision in enumerate(unmatched):
        ws6.cell(unm_row + 1 + offset, 1,
                 f"{decision['no'] or ''}. {decision['item'][:120]}").font = VALUE_FONT
    _autosize(ws6, [16, 14, 40])

    # ---- DFM 校验摘要 (与 checklist 判定互为参考) ----
    if dfm and dfm.get("items"):
        ws7 = wb.create_sheet("DFM 校验摘要")
        ws7["A1"] = f"DFM 制程能力比对: {dfm.get('verdict', '')}"
        ws7["A1"].font = TITLE_FONT
        coverage = dfm.get("coverage") or {}
        ws7["A2"] = (f"错误 {dfm.get('error_count', 0)} / 警告 {dfm.get('warning_count', 0)} / "
                     f"覆盖率 {coverage.get('ratio', 0):.0%} "
                     f"({coverage.get('checked', 0)}/{coverage.get('total', 0)})")
        ws7["A2"].font = LABEL_FONT
        _write_header(ws7, ["分类", "校验项", "设计值", "制程能力", "结论", "说明"], row=4)
        rows = []
        for item in dfm["items"]:
            rows.append([
                item.get("category", ""), item.get("item", ""),
                item.get("design", ""), item.get("capability", ""),
                item.get("status", ""), item.get("message", ""),
            ])
        _write_rows(ws7, rows, start_row=5)
        _autosize(ws7, [12, 20, 12, 14, 12, 60])

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    wb.save(path)
    wb.close()
    return path


def write_text_report(result: dict, path: str, board_meta: dict = None,
                      dfm: dict = None) -> str:
    board_meta = board_meta or {}
    template = result.get("template") or {}
    stats = result.get("stats") or {}
    decisions = result.get("decisions") or []
    total = max(stats.get("total", 0), 1)
    auto_rate = (stats.get("auto_pass", 0) + stats.get("auto_fail", 0)) / total

    lines = [
        "=" * 64,
        "  Checklist 自动填写报告 (BRD-AI)",
        "=" * 64,
        f"  时间      : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  单板      : {board_meta.get('board_name') or '—'}",
        f"  BRD       : {board_meta.get('brd_path') or '—'}",
        f"  模板      : {template.get('path') or '—'}",
        f"  识别      : {template.get('method')} / 表头第 {template.get('header_row')} 行",
        "=" * 64,
        "",
        f"  检查项总数 : {stats.get('total', 0)}",
        f"  自动 PASS  : {stats.get('auto_pass', 0)}",
        f"  自动 FAIL  : {stats.get('auto_fail', 0)}",
        f"  待验证     : {stats.get('auto_verify', 0)}  (规则已定义, 需 DRC/仿真确认)",
        f"  需人工确认 : {stats.get('manual', 0)}",
        f"  不适用     : {stats.get('na', 0)}",
        f"  未覆盖条目 : {stats.get('unmatched', 0)}",
        f"  自动判定率 : {auto_rate:.0%}",
        "",
    ]

    fails = [d for d in decisions if d["status"] == "FAIL"]
    lines.append(f"-- 不符合 ({len(fails)}) " + "-" * 40)
    for decision in fails:
        lines.append(f"  [{decision['no']}] {decision['item'][:60]}")
        lines.append(f"        {decision['reason']}")
    if not fails:
        lines.append("  无")
    lines.append("")

    verifies = [d for d in decisions if d["status"] == "VERIFY"]
    lines.append(f"-- 待 DRC/仿真验证 ({len(verifies)}) " + "-" * 30)
    for decision in verifies:
        lines.append(f"  [{decision['no']}] {decision['item'][:60]}")
        lines.append(f"        {decision['reason']}")
    if not verifies:
        lines.append("  无")
    lines.append("")

    manual = [d for d in decisions if d["status"] == "MANUAL"]
    groups = {}
    for decision in manual:
        groups.setdefault(decision["category"] or "未分类", []).append(decision)
    lines.append(f"-- 需人工确认 ({len(manual)}) " + "-" * 34)
    for category, items in groups.items():
        lines.append(f"  {category} ({len(items)})")
        for decision in items[:8]:
            lines.append(f"    [{decision['no']}] {decision['item'][:56]}")
        if len(items) > 8:
            lines.append(f"    ... 其余 {len(items) - 8} 项见 Excel 报告")
    lines.append("")

    unmatched = [d for d in decisions if not d["rule_id"]]
    if unmatched:
        lines.append(f"-- 规则库未覆盖 ({len(unmatched)}) " + "-" * 30)
        for decision in unmatched[:12]:
            lines.append(f"  [{decision['no']}] {decision['item'][:70]}")
        if len(unmatched) > 12:
            lines.append(f"  ... 其余 {len(unmatched) - 12} 项见 Excel 报告")
        lines.append("")
        lines.append("  提示: 在 config/checklist_auto_rules.yaml 的 rules 里补一条规则即可自动判定。")
        lines.append("")

    if dfm and dfm.get("items"):
        coverage = dfm.get("coverage") or {}
        lines.append("-- DFM 校验摘要 " + "-" * 40)
        lines.append(f"  结论: {dfm.get('verdict', '')}  错误 {dfm.get('error_count', 0)} / "
                     f"警告 {dfm.get('warning_count', 0)} / 覆盖率 "
                     f"{coverage.get('ratio', 0):.0%}")
        for item in dfm["items"]:
            if item.get("status") in ("fail", "warn_limit"):
                lines.append(f"  [{item.get('status')}] {item.get('message', '')}")
        lines.append("")

    lines.append("=" * 64)

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def write_report(result: dict, xlsx_path: str, txt_path: str = None,
                 board_meta: dict = None, dfm: dict = None) -> tuple:
    write_excel_report(result, xlsx_path, board_meta, dfm)
    if txt_path is None:
        txt_path = os.path.splitext(xlsx_path)[0] + ".txt"
    write_text_report(result, txt_path, board_meta, dfm)
    return xlsx_path, txt_path

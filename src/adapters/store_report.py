"""资料库联动检查报告: 总览 / 配对结论 / 检查项 / 位号核对 / 差异 / 未配对。

只读 ``store.run_check`` 的 bundle, 不重新解析 BRD/BOM; 输出 JSON、TXT、HTML、XLSX。
"""
import json
import os
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from src.design_inputs.store import STATUS_LABEL

_TITLE = "BRD ↔ BOM 资料库联动检查"
_HEADER_FONT = Font(name="Arial", size=10, bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
_FILLS = {
    "pass": PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid"),
    "warn": PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid"),
    "fail": PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid"),
    "unavailable": PatternFill(start_color="E7E6E6", end_color="E7E6E6", fill_type="solid"),
}
_THIN = Border(left=Side(style="thin"), right=Side(style="thin"),
               top=Side(style="thin"), bottom=Side(style="thin"))

_ROW_STATUS = {
    "matched": "匹配", "missing_in_bom": "BOM 漏项", "missing_in_brd": "BOM 多出",
    "conflict": "属性冲突", "unverified": "未核对", "unavailable": "无法核对",
}


def _sheets(bundle):
    return {
        "overview": _overview_rows(bundle),
        "pairs": _pair_rows(bundle),
        "checks": _check_rows(bundle),
        "refdes": _refdes_rows(bundle),
        "diff": _diff_rows(bundle),
        "unmatched": _unmatched_rows(bundle),
    }


# ---------------------------------------------------------------- 行数据
def _overview_rows(bundle):
    stats = bundle.get("stats") or {}
    dirs = bundle.get("dirs") or {}
    rows = [
        ("生成时间", bundle.get("generated") or ""),
        ("总体结论", bundle.get("verdict_label") or ""),
        ("BRD 目录", dirs.get("brd", "")),
        ("BOM 目录", dirs.get("bom", "")),
        ("BRD 文件", stats.get("brd_count", 0)),
        ("BOM 文件", stats.get("bom_count", 0)),
        ("配对数", stats.get("pair_count", 0)),
        ("通过", stats.get("pass", 0)),
        ("需确认", stats.get("warn", 0)),
        ("不符合", stats.get("fail", 0)),
        ("未配对 BRD", stats.get("unmatched_brd", 0)),
        ("未配对 BOM", stats.get("unmatched_bom", 0)),
        ("BRD 位号数", stats.get("brd_refdes", 0)),
        ("BOM 位号数", stats.get("bom_refdes", 0)),
    ]
    for index, warning in enumerate(bundle.get("warnings") or [], start=1):
        rows.append((f"警告 {index}", warning))
    return rows


def _pair_rows(bundle):
    rows = []
    for pair in bundle.get("pairs") or []:
        brd, bom = pair.get("brd") or {}, pair.get("bom") or {}
        summary = bom.get("summary") or {}
        rec = (pair.get("reconciliation") or {}).get("status") or "unavailable"
        rows.append([
            brd.get("name", ""), brd.get("part_number", "") or brd.get("core", ""),
            brd.get("rev", ""), bom.get("name", ""),
            bom.get("part_number", "") or bom.get("core", ""), bom.get("rev", ""),
            summary.get("rows", 0), (pair.get("brd_records") or {}).get("count", 0),
            _ROW_STATUS.get(rec, rec), pair.get("verdict_label", ""),
            "、".join(pair.get("match_reasons") or []),
        ])
    return rows


def _check_rows(bundle):
    rows = []
    for pair in bundle.get("pairs") or []:
        brd_name = (pair.get("brd") or {}).get("name", "")
        for check in pair.get("checks") or []:
            rows.append([brd_name, check.get("name", ""),
                         check.get("status_label", STATUS_LABEL.get(check.get("status"), "")),
                         check.get("detail", "")])
    return rows


def _refdes_rows(bundle):
    rows = []
    for pair in bundle.get("pairs") or []:
        brd_name = (pair.get("brd") or {}).get("name", "")
        for row in (pair.get("reconciliation") or {}).get("rows") or []:
            brd = row.get("brd") or {}
            bom = row.get("bom") or {}
            rows.append([
                brd_name, row.get("refdes") or bom.get("refdes") or "",
                _ROW_STATUS.get(row.get("status"), row.get("status")),
                "是" if row.get("in_brd") else "否",
                "是" if row.get("in_bom") else "否",
                row.get("side") or bom.get("side") or "",
                "是" if row.get("dnp") else "否",
                row.get("package") or "",
                brd.get("part_number") or "", bom.get("part_number") or "",
                bom.get("manufacturer_part_number") or "", bom.get("value") or "",
            ])
    return rows


def _diff_rows(bundle):
    rows = []
    for pair in bundle.get("pairs") or []:
        brd_name = (pair.get("brd") or {}).get("name", "")
        rec = pair.get("reconciliation") or {}
        for item in rec.get("missing_in_bom") or []:
            rows.append([brd_name, item.get("refdes", ""), "BOM 漏项", "",
                         item.get("package", ""), item.get("note", "")])
        for item in rec.get("missing_in_brd") or []:
            rows.append([brd_name, item.get("refdes", ""), "BOM 多出",
                         item.get("package", ""), "",
                         item.get("note", "")])
        for item in rec.get("conflicts") or []:
            rows.append([brd_name, item.get("refdes", ""),
                         "属性冲突(" + item.get("field", "") + ")",
                         item.get("brd", ""), item.get("bom", ""), item.get("note", "")])
        for item in (pair.get("bom_items") or {}).get("non_electrical") or []:
            rows.append([brd_name, item.get("refdes", ""), "非电气项",
                         "", item.get("part_number") or item.get("value") or "",
                         item.get("description") or ""])
    return rows


def _unmatched_rows(bundle):
    rows = []
    for item in bundle.get("unmatched_brd") or []:
        rows.append(["BRD", item.get("name", ""), item.get("part_number", ""),
                     item.get("reason", "")])
    for item in bundle.get("unmatched_bom") or []:
        rows.append(["BOM", item.get("name", ""), "", item.get("reason", "")])
    return rows


# ---------------------------------------------------------------- Excel
def _write_sheet(workbook, title, headers, rows, widths=None, status_col=None):
    sheet = workbook.create_sheet(title)
    for index, header in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=index, value=header)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = _THIN
    for offset, row in enumerate(rows, start=2):
        for index, value in enumerate(row, start=1):
            cell = sheet.cell(row=offset, column=index, value=value)
            cell.border = _THIN
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        if status_col is not None and status_col < len(row) + 1:
            fill = _FILLS.get(str(row[status_col]))
            if fill:
                sheet.cell(row=offset, column=status_col + 1).fill = fill
    for index, width in enumerate(widths or [], start=1):
        if width:
            sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    sheet.freeze_panes = "A2"
    if rows:
        sheet.auto_filter.ref = f"A1:{sheet.cell(row=len(rows) + 1, column=len(headers)).coordinate}"
    return sheet


def write_excel(bundle, path):
    sheets = _sheets(bundle)
    workbook = Workbook()
    overview = workbook.active
    overview.title = "总览"
    overview.cell(row=1, column=1, value=_TITLE).font = Font(size=14, bold=True)
    overview.merge_cells("A1:B1")
    for offset, (key, value) in enumerate(sheets["overview"], start=3):
        overview.cell(row=offset, column=1, value=key).font = Font(bold=True)
        overview.cell(row=offset, column=2, value=value)
        overview.cell(row=offset, column=2).alignment = Alignment(wrap_text=True, vertical="top")
    overview.column_dimensions["A"].width = 20
    overview.column_dimensions["B"].width = 100

    _write_sheet(workbook, "配对结论",
                 ["BRD 文件", "BRD 料号", "BRD 版本", "BOM 文件", "BOM 料号",
                  "BOM 版本", "BOM 位号数", "BRD 位号数", "位号核对", "结论", "匹配依据"],
                 sheets["pairs"], [34, 18, 8, 34, 18, 8, 11, 11, 11, 10, 34])
    _write_sheet(workbook, "检查项", ["BRD 文件", "检查项", "状态", "说明"],
                 sheets["checks"], [30, 18, 10, 90], status_col=2)
    _write_sheet(workbook, "位号核对",
                 ["BRD 文件", "位号", "状态", "在 BRD", "在 BOM", "面别", "DNP",
                  "封装", "BRD 料号", "BOM 料号", "BOM MPN", "BOM 值"],
                 sheets["refdes"], [30, 10, 10, 7, 7, 8, 6, 26, 18, 18, 26, 16])
    _write_sheet(workbook, "差异明细", ["BRD 文件", "位号", "类型", "BRD 值", "BOM 值", "说明"],
                 sheets["diff"], [30, 10, 20, 24, 24, 60])
    _write_sheet(workbook, "未配对", ["类型", "文件", "料号", "原因"],
                 sheets["unmatched"], [8, 36, 18, 60])
    workbook.save(path)
    return path


# ---------------------------------------------------------------- 文本 / HTML
def write_text(bundle, path):
    stats = bundle.get("stats") or {}
    lines = ["=" * 78, "  " + _TITLE, "=" * 78,
             f"  生成时间: {bundle.get('generated')}",
             f"  总体结论: {bundle.get('verdict_label')}",
             f"  BRD 目录: {(bundle.get('dirs') or {}).get('brd')}",
             f"  BOM 目录: {(bundle.get('dirs') or {}).get('bom')}",
             f"  文件: BRD {stats.get('brd_count', 0)} 个 / BOM {stats.get('bom_count', 0)} 个",
             (f"  配对: {stats.get('pair_count', 0)} 对 (通过 {stats.get('pass', 0)} / "
              f"需确认 {stats.get('warn', 0)} / 不符合 {stats.get('fail', 0)})"), ""]

    for index, pair in enumerate(bundle.get("pairs") or [], start=1):
        brd, bom = pair.get("brd") or {}, pair.get("bom") or {}
        lines += ["-" * 78,
                  f"  [{index}] {brd.get('name')}  ↔  {bom.get('name')}",
                  (f"      结论: {pair.get('verdict_label')}   "
                   f"料号: BRD {brd.get('part_number') or '—'} / "
                   f"BOM {bom.get('part_number') or '—'}   "
                   f"版本: BRD {brd.get('rev') or '—'} / BOM {bom.get('rev') or '—'}"),
                  f"      匹配依据: {'、'.join(pair.get('match_reasons') or []) or '—'}", ""]
        for check in pair.get("checks") or []:
            lines.append(f"      [{check.get('status_label')}] {check.get('name')}: "
                         f"{check.get('detail')}")
        reconciliation = pair.get("reconciliation") or {}
        if reconciliation.get("missing_in_bom"):
            sample = "、".join(item["refdes"] for item in reconciliation["missing_in_bom"][:12])
            lines.append(f"      BOM 漏项: {sample}")
        if reconciliation.get("missing_in_brd"):
            sample = "、".join(item["refdes"] for item in reconciliation["missing_in_brd"][:12])
            lines.append(f"      BOM 多出: {sample}")
        if reconciliation.get("conflicts"):
            sample = "、".join(f"{item['refdes']}({item['field']})"
                               for item in reconciliation["conflicts"][:12])
            lines.append(f"      属性冲突: {sample}")
        lines.append("")

    unmatched = (bundle.get("unmatched_brd") or []) + (bundle.get("unmatched_bom") or [])
    if unmatched:
        lines += ["-" * 78, "  未配对:"]
        for item in unmatched:
            lines.append(f"    {item.get('name')}: {item.get('reason')}")
        lines.append("")
    if bundle.get("warnings"):
        lines += ["-" * 78, "  警告:"]
        lines += [f"    ! {warning}" for warning in bundle["warnings"]]
        lines.append("")
    lines += ["=" * 78, "  说明: 位号核对只做双向比对, 不自动修改 BRD 或 BOM;",
              "        BOM 多出/属性冲突必须人工确认 (DNP/替代料/机构件/版本串用)。",
              "=" * 78, ""]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return path


def _html_escape(value):
    return (str(value if value is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _html_table(headers, rows, status_index=None):
    parts = ["<table><thead><tr>"]
    parts += [f"<th>{_html_escape(header)}</th>" for header in headers]
    parts.append("</tr></thead><tbody>")
    for row in rows:
        cls = ""
        if status_index is not None and status_index < len(row):
            label = str(row[status_index])
            cls = {"通过": "pass", "需确认": "warn", "不符合": "fail",
                   "数据不足": "na"}.get(label, "")
        parts.append(f'<tr class="{cls}">')
        parts += [f"<td>{_html_escape(cell)}</td>" for cell in row]
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def write_html(bundle, path):
    sheets = _sheets(bundle)
    stats = bundle.get("stats") or {}
    sections = [
        ("总览", [("项目", "内容")], [list(row) for row in sheets["overview"]]),
        ("配对结论", ["BRD 文件", "BRD 料号", "BRD 版本", "BOM 文件", "BOM 料号",
                      "BOM 版本", "BOM 位号数", "BRD 位号数", "位号核对", "结论", "匹配依据"],
         sheets["pairs"]),
        ("检查项", ["BRD 文件", "检查项", "状态", "说明"], sheets["checks"]),
        ("位号核对", ["BRD 文件", "位号", "状态", "在 BRD", "在 BOM", "面别", "DNP",
                      "封装", "BRD 料号", "BOM 料号", "BOM MPN", "BOM 值"],
         sheets["refdes"]),
        ("差异明细", ["BRD 文件", "位号", "类型", "BRD 值", "BOM 值", "说明"], sheets["diff"]),
        ("未配对", ["类型", "文件", "料号", "原因"], sheets["unmatched"]),
    ]
    body = []
    for title, headers, rows in sections:
        body.append(f"<h2>{_html_escape(title)}"
                    f'<span class="count">{len(rows)}</span></h2>')
        body.append(_html_table(headers, rows, status_index=2 if title == "检查项" else None))
    if not sheets["unmatched"]:
        body.append("<p class='hint'>所有文件都已完成配对。</p>")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{_TITLE}</title>
<style>
body{{font-family:"Microsoft YaHei UI","Microsoft YaHei",Segoe UI,sans-serif;
margin:0;padding:28px 34px;background:#F3F5F9;color:#1F2937;}}
h1{{font-size:22px;margin:0 0 6px;}}
h2{{font-size:16px;margin:26px 0 10px;border-left:4px solid #2563EB;padding-left:8px;}}
h2 .count{{font-size:12px;color:#94A3B8;font-weight:400;margin-left:8px;}}
.meta{{color:#6B7280;font-size:13px;margin-bottom:6px;}}
.verdict{{display:inline-block;padding:3px 10px;border-radius:10px;font-size:13px;
background:#E8EEFB;color:#1D4ED8;}}
table{{border-collapse:collapse;width:100%;background:#fff;font-size:12px;
box-shadow:0 1px 2px rgba(15,23,42,.08);}}
th{{background:#EFF3FA;color:#475569;font-weight:600;text-align:left;
padding:7px 9px;border:1px solid #E1E6EF;}}
td{{padding:6px 9px;border:1px solid #E1E6EF;vertical-align:top;}}
tr.pass td{{background:#F1FBF4;}} tr.warn td{{background:#FFF9E8;}}
tr.fail td{{background:#FDF0F1;}} tr.na td{{background:#F5F6F8;}}
.hint{{color:#6B7280;font-size:12px;}}
</style></head><body>
<h1>{_TITLE}</h1>
<div class="meta">生成时间 {_html_escape(bundle.get('generated'))} ·
结论 <span class="verdict">{_html_escape(bundle.get('verdict_label'))}</span> ·
配对 {stats.get('pair_count', 0)} 对 (通过 {stats.get('pass', 0)} / 需确认 {stats.get('warn', 0)} /
不符合 {stats.get('fail', 0)})</div>
{''.join(body)}
<p class="hint">位号核对只做双向比对, 不自动修改 BRD 或 BOM; BOM 多出与属性冲突必须人工确认。</p>
</body></html>"""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(html)
    return path


def write_report(bundle, out_dir, base_name=None):
    """写出 xlsx/txt/html/json, 返回 {"xlsx","txt","html","json"} 路径字典。"""
    os.makedirs(out_dir, exist_ok=True)
    if base_name is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"BRD_BOM联动检查_{stamp}"
    files = {
        "xlsx": os.path.join(out_dir, base_name + ".xlsx"),
        "txt": os.path.join(out_dir, base_name + ".txt"),
        "html": os.path.join(out_dir, base_name + ".html"),
        "json": os.path.join(out_dir, base_name + ".json"),
    }
    write_excel(bundle, files["xlsx"])
    write_text(bundle, files["txt"])
    write_html(bundle, files["html"])
    with open(files["json"], "w", encoding="utf-8") as handle:
        json.dump(bundle, handle, ensure_ascii=False, indent=2, default=str)
    return files

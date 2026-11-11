"""报告输出: 把一次分析的 bundle 写成 Excel / TXT / HTML / CSV / JSON。

报告回答四个问题 (顺序固定, 便于对照):
    1. 这块板哪些能力给出了结论、结论是什么      -> 总览 + 各大类页签
    2. 具体数字和明细在哪                        -> 每项能力的指标与明细页签
    3. 哪些能力因为缺数据没判、缺什么、怎么补     -> "缺失数据与补法"页签
    4. 这次到底生成了哪些文件                    -> "产物清单"页签
"""
import csv
import html
import json
import os
import re
from typing import Dict, List, Optional

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from src.brd_analysis.model import (CATEGORY_LABELS, CATEGORY_ORDER, FAIL,
                                    STATUS_COLORS, STATUS_LABELS)

_MAX_SHEETS = 16
_MAX_ROWS = 800
_SHEET_BAD = re.compile(r"[\[\]\*\?/\\:]")


def _border():
    return Border(left=Side(style="thin"), right=Side(style="thin"),
                  top=Side(style="thin"), bottom=Side(style="thin"))


# Excel 单元格不允许出现控制字符 (除了 \t \n \r)。上游如果把某个文件的原始
# 二进制片段当文本带进来 (例如误把 .brd 当报告解析), openpyxl 会直接抛
# IllegalCharacterError 让整份报告写不出来 —— 这里统一在写盘前清掉, 并且
# 顺手把超长文本截断, 免得一个字段撑爆单元格。
_CELL_BAD = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")
_MAX_CELL = 32000          # Excel 单元格上限 32767, 留点余量


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, (list, tuple)):
        return "、".join(_fmt(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}={_fmt(v)}" for k, v in value.items())
    text = str(value)
    if _CELL_BAD.search(text):
        text = _CELL_BAD.sub(" ", text)
    if len(text) > _MAX_CELL:
        text = text[:_MAX_CELL] + " …(已截断)"
    return text


def _safe_sheet(name: str, used: set) -> str:
    out = _SHEET_BAD.sub("_", name)[:28] or "Sheet"
    base, i = out, 2
    while out in used:
        out = f"{base[:26]}_{i}"
        i += 1
    used.add(out)
    return out


# Excel 页签名上限 31 字符。类别标签写成「制造文件输出 (Manufacturing Output)」
# 会被砍在半个英文单词上, 很难看 —— 生成页签名时先把括号里的英文副标题去掉。
_SUBTITLE_RE = re.compile(r"\s*[（(][^（()）]*[)）]\s*$")


def _sheet_label(label: str) -> str:
    stripped = _SUBTITLE_RE.sub("", label or "").strip()
    return stripped or (label or "Sheet")


def _status_font(status: str) -> Font:
    return Font(name="Arial", size=10, bold=True,
                color=STATUS_COLORS.get(status, "000000"))


def _style_header(ws, row: int, columns: List[str]) -> None:
    fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
    for idx, name in enumerate(columns, start=1):
        cell = ws.cell(row=row, column=idx, value=name)
        cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _border()


def _autosize(ws, widths: Dict[int, int]) -> None:
    for col, width in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = min(width, 70)


def _overview_rows(bundle: dict) -> List[dict]:
    rows = []
    for res in bundle.get("results") or []:
        rows.append({
            "类别": CATEGORY_LABELS.get(res["category"], res["category"]),
            "能力": res["name_cn"],
            "英文名": res["name"],
            "编号": res["id"],
            "状态": res["status_label"],
            "结论": res["summary"],
            "指标": len(res.get("metrics") or []),
            "明细": len(res.get("items") or []),
            "缺失数据": "；".join(res.get("missing") or []) or "",
            "产物": "；".join(os.path.basename(a.get("path", ""))
                            for a in (res.get("artifacts") or [])) or "",
        })
    return rows


def _board_pairs(bundle: dict) -> List[tuple]:
    b = bundle.get("board") or {}
    stats = bundle.get("stats") or {}
    return [
        ("设计名 / 板名", b.get("board_name")),
        ("BRD 文件", b.get("brd_path")),
        ("文件大小 (MB)", b.get("file_size_mb")),
        ("层数", b.get("layer_count")),
        ("网络数", b.get("net_count")),
        ("器件数", b.get("part_count")),
        ("差分对", b.get("diff_pair_count")),
        ("板尺寸 (mm)", b.get("board_size_mm")),
        ("内嵌快照", "有 (%s 条图形)" % b["snapshot_entries"] if b.get("has_snapshot") else "无"),
        ("坐标标定", "%s (%s)" % (b.get("scale_mm_per_unit"), b.get("scale_method"))
                    if b.get("scale_mm_per_unit") else "未标定"),
        ("导入的导出文件", b.get("export_files")),
        ("元件坐标", "有" if b.get("has_components_xy") else "无"),
        ("孔位坐标", "有" if b.get("has_holes_xy") else "无"),
        ("分析时间", bundle.get("generated_at")),
        ("能力总数", stats.get("total")),
        ("通过 / 不符合 / 需关注", "%s / %s / %s" % (stats.get("pass", 0),
                                                stats.get("fail", 0), stats.get("warn", 0))),
        ("仅信息 / 数据不足", "%s / %s" % (stats.get("info", 0), stats.get("unavailable", 0))),
    ]


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------
def write_excel(bundle: dict, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    wb = openpyxl.Workbook()
    border = _border()
    used: set = set()

    # --- 总览 ---
    ws = wb.active
    ws.title = _safe_sheet("总览", used)
    title_font = Font(name="Arial", size=14, bold=True, color="1F3864")
    ws.cell(row=1, column=1, value="BRD 分析与输出报告").font = title_font
    ws.cell(row=2, column=1, value=f"板名: {bundle.get('board', {}).get('board_name', '')}")
    row = 4
    for key, value in _board_pairs(bundle):
        kc = ws.cell(row=row, column=1, value=key)
        kc.font = Font(name="Arial", size=10, bold=True)
        kc.border = border
        vc = ws.cell(row=row, column=2, value=_fmt(value))
        vc.border = border
        row += 1
    row += 1
    ws.cell(row=row, column=1, value="能力总览").font = Font(name="Arial", size=12, bold=True,
                                                          color="1F3864")
    row += 1
    columns = ["类别", "能力", "英文名", "编号", "状态", "结论", "指标", "明细",
               "缺失数据", "产物"]
    _style_header(ws, row, columns)
    header_row = row
    for item in _overview_rows(bundle):
        row += 1
        for idx, name in enumerate(columns, start=1):
            cell = ws.cell(row=row, column=idx, value=_fmt(item.get(name)))
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=(name in ("结论", "缺失数据")))
            if name == "状态":
                cell.font = _status_font(_status_of(bundle, item.get("编号")))
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    _autosize(ws, {1: 26, 2: 22, 3: 20, 4: 26, 5: 8, 6: 60, 7: 6, 8: 6, 9: 46, 10: 26})

    # --- 每个大类一个页签 ---
    grouped: Dict[str, List[dict]] = {}
    for res in bundle.get("results") or []:
        grouped.setdefault(res["category"], []).append(res)
    for category in CATEGORY_ORDER:
        rows = grouped.get(category) or []
        if not rows:
            continue
        ws = wb.create_sheet(_safe_sheet(_sheet_label(CATEGORY_LABELS[category]), used))
        ws.cell(row=1, column=1, value=CATEGORY_LABELS[category]).font = title_font
        r = 3
        for res in rows:
            cell = ws.cell(row=r, column=1, value=f"{res['name_cn']}  ({res['name']} / {res['id']})")
            cell.font = Font(name="Arial", size=11, bold=True)
            r += 1
            sc = ws.cell(row=r, column=1, value=f"状态: {res['status_label']}")
            sc.font = _status_font(res["status"])
            vc = ws.cell(row=r, column=2, value=res["summary"])
            vc.alignment = Alignment(vertical="top", wrap_text=True)
            r += 1
            if res.get("sources"):
                ws.cell(row=r, column=1, value="数据出处").font = Font(bold=True)
                ws.cell(row=r, column=2, value="；".join(res["sources"]))
                r += 1
            for m in res.get("metrics") or []:
                ws.cell(row=r, column=1, value=m.get("label"))
                ws.cell(row=r, column=2,
                        value=f"{_fmt(m.get('value'))} {m.get('unit') or ''}".strip())
                note = "；".join(x for x in (m.get("source"), m.get("confidence")) if x)
                ws.cell(row=r, column=3, value=note)
                r += 1
            for miss in res.get("missing") or []:
                ws.cell(row=r, column=1, value="缺数据").font = Font(bold=True, color="808080")
                ws.cell(row=r, column=2, value=miss)
                r += 1
            for how in res.get("howto") or []:
                ws.cell(row=r, column=1, value="怎么补")
                ws.cell(row=r, column=2, value=how)
                r += 1
            for warn in res.get("warnings") or []:
                ws.cell(row=r, column=1, value="提示")
                ws.cell(row=r, column=2, value=warn)
                r += 1
            if res.get("columns") and res.get("items"):
                _style_header(ws, r, list(res["columns"]))
                shown = res["items"][:_MAX_ROWS]
                for item in shown:
                    r += 1
                    for idx, col in enumerate(res["columns"], start=1):
                        c = ws.cell(row=r, column=idx, value=_fmt(item.get(col)))
                        c.border = border
                if len(res["items"]) > len(shown):
                    r += 1
                    ws.cell(row=r, column=1,
                            value=f"(明细共 {len(res['items'])} 行, 此处只列前 {len(shown)} 行)")
            r += 2
        _autosize(ws, {1: 34, 2: 60, 3: 40, 4: 16, 5: 16, 6: 14, 7: 14, 8: 14})

    # --- 明细页签 (只给有明细的能力, 限制页数) ---
    detailed = [r for r in bundle.get("results") or [] if r.get("items")]
    for res in detailed[:_MAX_SHEETS]:
        ws = wb.create_sheet(_safe_sheet(res["name_cn"], used))
        ws.cell(row=1, column=1, value=f"{res['name_cn']} 明细").font = title_font
        cols = list(res["columns"])
        _style_header(ws, 3, cols)
        row = 3
        for item in res["items"][:_MAX_ROWS]:
            row += 1
            for idx, col in enumerate(cols, start=1):
                c = ws.cell(row=row, column=idx, value=_fmt(item.get(col)))
                c.border = border
        if len(res["items"]) > _MAX_ROWS:
            row += 1
            ws.cell(row=row, column=1,
                    value=f"(共 {len(res['items'])} 行, 只列前 {_MAX_ROWS} 行; "
                          f"完整明细见 CSV)")
        ws.freeze_panes = ws.cell(row=4, column=1)
        _autosize(ws, {i + 1: 22 for i in range(len(cols))})

    # --- 缺失数据与补法 ---
    ws = wb.create_sheet(_safe_sheet("缺失数据与补法", used))
    ws.cell(row=1, column=1, value="数据不足的能力: 缺什么, 怎么补").font = title_font
    columns = ["类别", "能力", "缺什么", "怎么补", "说明"]
    _style_header(ws, 3, columns)
    row = 3
    for res in bundle.get("results") or []:
        if not res.get("missing"):
            continue
        howto = res.get("howto") or [""]
        for idx, miss in enumerate(res["missing"]):
            row += 1
            values = [CATEGORY_LABELS.get(res["category"], ""), res["name_cn"], miss,
                      howto[idx] if idx < len(howto) else (howto[0] if howto else ""),
                      res["summary"] if idx == 0 else ""]
            for cidx, value in enumerate(values, start=1):
                cell = ws.cell(row=row, column=cidx, value=_fmt(value))
                cell.border = border
                cell.alignment = Alignment(vertical="top", wrap_text=True)
    _autosize(ws, {1: 22, 2: 20, 3: 50, 4: 60, 5: 40})

    # --- 产物清单 ---
    ws = wb.create_sheet(_safe_sheet("产物清单", used))
    ws.cell(row=1, column=1, value="本次生成的文件").font = title_font
    columns = ["能力", "类型", "文件", "说明"]
    _style_header(ws, 3, columns)
    row = 3
    for art in bundle.get("artifacts") or []:
        row += 1
        for cidx, value in enumerate([art.get("capability_cn"), art.get("kind"),
                                      art.get("path"), art.get("note")], start=1):
            cell = ws.cell(row=row, column=cidx, value=_fmt(value))
            cell.border = border
    _autosize(ws, {1: 24, 2: 16, 3: 64, 4: 34})

    wb.save(path)
    return path


def _status_of(bundle: dict, cap_id: str) -> str:
    for res in bundle.get("results") or []:
        if res["id"] == cap_id:
            return res["status"]
    return "info"


# --------------------------------------------------------------------------
# 文本
# --------------------------------------------------------------------------
def write_text(bundle: dict, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    L: List[str] = []
    line = "=" * 78
    L.append(line)
    L.append("  BRD 分析与输出报告")
    L.append(line)
    for key, value in _board_pairs(bundle):
        L.append(f"  {key:<22}: {_fmt(value)}")
    stats = bundle.get("stats") or {}
    L.append("")
    L.append(f"  能力 {stats.get('total', 0)} 项: 通过 {stats.get('pass', 0)}, "
             f"不符合 {stats.get('fail', 0)}, 需关注 {stats.get('warn', 0)}, "
             f"信息 {stats.get('info', 0)}, 数据不足 {stats.get('unavailable', 0)}")
    L.append("")

    grouped: Dict[str, List[dict]] = {}
    for res in bundle.get("results") or []:
        grouped.setdefault(res["category"], []).append(res)

    for category in CATEGORY_ORDER:
        rows = grouped.get(category) or []
        if not rows:
            continue
        L.append("-" * 78)
        L.append(f"  {CATEGORY_LABELS[category]}  ({len(rows)} 项)")
        L.append("-" * 78)
        for res in rows:
            L.append(f"  [{res['status_label']}] {res['name_cn']} ({res['id']})")
            L.append(f"        {res['summary']}")
            for m in res.get("metrics") or []:
                unit = f" {m['unit']}" if m.get("unit") else ""
                src = f"   ({m['source']})" if m.get("source") else ""
                L.append(f"        - {m['label']}: {_fmt(m.get('value'))}{unit}{src}")
            if res.get("items"):
                L.append(f"        - 明细 {len(res['items'])} 行, 前几行:")
                for item in res["items"][:5]:
                    L.append("            " + " | ".join(
                        f"{k}={_fmt(v)}" for k, v in item.items() if v not in (None, "")))
            for miss in res.get("missing") or []:
                L.append(f"        ! 缺数据: {miss}")
            for how in res.get("howto") or []:
                L.append(f"          -> {how}")
            for warn in res.get("warnings") or []:
                L.append(f"        * {warn}")
            L.append("")

    if bundle.get("artifacts"):
        L.append("-" * 78)
        L.append("  生成的文件")
        L.append("-" * 78)
        for art in bundle["artifacts"]:
            L.append(f"  [{art['kind']}] {art['path']}  ({art['capability_cn']})")
        L.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return path


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
_HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>BRD 分析与输出报告 - {board}</title>
<style>
body{{font-family:"Microsoft YaHei",Arial,sans-serif;margin:0;background:#f4f6f9;color:#222}}
.wrap{{max-width:1180px;margin:0 auto;padding:24px}}
h1{{font-size:22px;color:#1F3864;margin:0 0 4px}}
h2{{font-size:17px;color:#1F3864;border-left:5px solid #2F5496;padding-left:10px;margin:26px 0 10px}}
table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px}}
th{{background:#2F5496;color:#fff;text-align:left;padding:7px 9px;font-weight:600}}
td{{border:1px solid #dfe3e8;padding:6px 9px;vertical-align:top}}
tr:nth-child(even) td{{background:#fafbfd}}
.kv td:first-child{{width:210px;font-weight:600;background:#eef2f8}}
.pass{{color:#1E7B34;font-weight:700}} .fail{{color:#C00000;font-weight:700}}
.warn{{color:#BF8F00;font-weight:700}} .info{{color:#2F5496;font-weight:700}}
.unavailable{{color:#808080;font-weight:700}}
.cap{{margin:14px 0 6px;font-weight:700;color:#1F3864}}
.sum{{margin:2px 0 8px}}
.miss{{color:#808080}} .art{{font-family:Consolas,monospace;font-size:12px}}
.small{{color:#666;font-size:12px}}
</style></head><body><div class="wrap">
<h1>BRD 分析与输出报告</h1>
<div class="small">{board} &nbsp;|&nbsp; 生成时间 {time}</div>
"""


def write_html(bundle: dict, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    board = html.escape(str((bundle.get("board") or {}).get("board_name") or ""))
    out: List[str] = [_HTML_HEAD.format(board=board,
                                        time=html.escape(bundle.get("generated_at", "")))]
    out.append("<h2>板级信息</h2><table class='kv'>")
    for key, value in _board_pairs(bundle):
        out.append(f"<tr><td>{html.escape(str(key))}</td><td>{html.escape(_fmt(value))}</td></tr>")
    out.append("</table>")

    stats = bundle.get("stats") or {}
    out.append("<h2>结论统计</h2><table><tr>"
               "<th>通过</th><th>不符合</th><th>需关注</th><th>信息</th><th>数据不足</th>"
               "</tr><tr>")
    for key in ("pass", "fail", "warn", "info", "unavailable"):
        cls = key if key != "unavailable" else "unavailable"
        out.append(f"<td class='{cls}'>{stats.get(key, 0)}</td>")
    out.append("</tr></table>")

    grouped: Dict[str, List[dict]] = {}
    for res in bundle.get("results") or []:
        grouped.setdefault(res["category"], []).append(res)

    for category in CATEGORY_ORDER:
        rows = grouped.get(category) or []
        if not rows:
            continue
        out.append(f"<h2>{html.escape(CATEGORY_LABELS[category])} ({len(rows)} 项)</h2>")
        for res in rows:
            out.append(f"<div class='cap'>"
                       f"<span class='{res['status']}'>[{html.escape(res['status_label'])}]</span> "
                       f"{html.escape(res['name_cn'])} "
                       f"<span class='small'>({html.escape(res['id'])})</span></div>")
            out.append(f"<div class='sum'>{html.escape(res['summary'])}</div>")
            if res.get("metrics"):
                out.append("<table><tr><th>指标</th><th>数值</th><th>出处 / 置信</th></tr>")
                for m in res["metrics"]:
                    unit = f" {m['unit']}" if m.get("unit") else ""
                    note = "；".join(x for x in (m.get("source"), m.get("confidence")) if x)
                    out.append(f"<tr><td>{html.escape(str(m['label']))}</td>"
                               f"<td>{html.escape(_fmt(m.get('value')) + unit)}</td>"
                               f"<td class='small'>{html.escape(note)}</td></tr>")
                out.append("</table>")
            if res.get("columns") and res.get("items"):
                cols = list(res["columns"])
                out.append("<table><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in cols)
                           + "</tr>")
                for item in res["items"][:60]:
                    out.append("<tr>" + "".join(
                        f"<td>{html.escape(_fmt(item.get(c)))}</td>" for c in cols) + "</tr>")
                out.append("</table>")
                if len(res["items"]) > 60:
                    out.append(f"<div class='small'>共 {len(res['items'])} 行, "
                               f"此处只列前 60 行 (完整明细见 CSV/Excel)</div>")
            for miss in res.get("missing") or []:
                out.append(f"<div class='miss'>缺数据: {html.escape(str(miss))}</div>")
            for how in res.get("howto") or []:
                out.append(f"<div class='small'>补法: {html.escape(str(how))}</div>")
            for warn in res.get("warnings") or []:
                out.append(f"<div class='small'>提示: {html.escape(str(warn))}</div>")

    if bundle.get("artifacts"):
        out.append("<h2>生成的文件</h2><table><tr><th>类型</th><th>文件</th><th>能力</th></tr>")
        for art in bundle["artifacts"]:
            out.append(f"<tr><td>{html.escape(str(art.get('kind')))}</td>"
                       f"<td class='art'>{html.escape(str(art.get('path')))}</td>"
                       f"<td>{html.escape(str(art.get('capability_cn')))}</td></tr>")
        out.append("</table>")

    out.append("</div></body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return path


# --------------------------------------------------------------------------
# CSV / JSON
# --------------------------------------------------------------------------
def write_csv(bundle: dict, out_dir: str) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    written: List[str] = []

    overview = os.path.join(out_dir, "capabilities.csv")
    rows = _overview_rows(bundle)
    if rows:
        with open(overview, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        written.append(overview)

    detail_dir = os.path.join(out_dir, "details")
    for res in bundle.get("results") or []:
        if not res.get("items"):
            continue
        os.makedirs(detail_dir, exist_ok=True)
        name = _SHEET_BAD.sub("_", res["id"])
        path = os.path.join(detail_dir, f"{name}.csv")
        cols = list(res["columns"])
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            for item in res["items"]:
                writer.writerow({c: _fmt(item.get(c)) for c in cols})
        written.append(path)
    return written


def write_json(bundle: dict, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2, ensure_ascii=False, default=str)
    return path


def write_all(bundle: dict, out_dir: str, base_name: str = "BRD分析报告",
              formats: Optional[List[str]] = None) -> Dict[str, str]:
    """一次写出全部格式, 返回 {格式: 路径}。"""
    os.makedirs(out_dir, exist_ok=True)
    want = set(formats or ["xlsx", "txt", "html", "json", "csv"])
    files: Dict[str, str] = {}
    if "xlsx" in want:
        files["xlsx"] = write_excel(bundle, os.path.join(out_dir, f"{base_name}.xlsx"))
    if "txt" in want:
        files["txt"] = write_text(bundle, os.path.join(out_dir, f"{base_name}.txt"))
    if "html" in want:
        files["html"] = write_html(bundle, os.path.join(out_dir, f"{base_name}.html"))
    if "json" in want:
        files["json"] = write_json(bundle, os.path.join(out_dir, f"{base_name}.json"))
    if "csv" in want:
        listed = write_csv(bundle, os.path.join(out_dir, f"{base_name}_csv"))
        files["csv"] = os.path.dirname(listed[0]) if listed else ""
        files["csv_files"] = listed
    return files

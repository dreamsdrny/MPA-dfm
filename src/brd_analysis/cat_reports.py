"""第 5 类: PCB 报告输出 (Reports)。

对应 PCB Editor 的 Report 菜单: Board / Component / Pin / Net / Via / Etch /
Layer / Unconnected / DRC / Constraint / Testpoint Report —— 每一项既是一次
"读数 -> 结论", 也是一组可交付的文件 (RPT 定宽文本 + CSV + HTML)。

设计纪律 (这一节比代码重要)
--------------------------
1. **缺数据 != 通过**: 数据源不足时状态一律 `UNAVAILABLE`, 并写清缺什么
   (`res.need`) 与怎么补 (`res.howto`)。绝不输出一张空表让用户以为
   "这板子没有这一项"。
2. **口径只来自可信来源**: 网络名只取 `objects/Net.xml` 的节点名 (facts.net_names),
   不用二进制文本正则猜; 器件只取 `objects/Part.xml` (facts.components);
   引脚级数据只来自用户导入的网表; DRC/未连接只来自用户导入的 DRC 报告。
3. **能说明出处就说明出处**: 每一行/每一项尽量带"来源 / 说明"列或 metric source。
4. **合并用户导入的报告**: 用户手上常已有 Allegro 自己导出的同类报告, 只要标题
   或表头关键字对得上, 就把它附在明细后面 (加一列「来源」= 文件名), 并在结论里
   写明"同时合并了用户导入的 N 份 Allegro 报告", 让数据可追溯、不冒充直读结果。

产物目录: `<ctx.out_dir>/reports/`; `ctx.out_dir` 为空时用 `.brd` 所在目录;
再不行用系统临时目录。文件名形如 `Board_Report.rpt / .csv / .html`。

本模块不使用 `writers.py` (那是全量报告生成器, 由主流程调用), 这里的定宽/CSV/HTML
输出是为"单项报告"独立实现的。
"""
import csv
import html
import os
import re
import tempfile
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Sequence, Tuple

from src.brd_analysis import facts
from src.brd_analysis.context import Context
from src.brd_analysis.model import (FAIL, INFO, PASS, WARN, Capability, Result,
                                    unavailable)

# 位号前缀分类表与 src.brd_components 保持同一份口径 (那边是唯一权威定义);
# 取不到时退回"前缀原样显示", 不影响报告生成。
try:  # pragma: no cover - 正常路径
    from src.brd_components import _PREFIX_CATEGORY, _prefix_of
except Exception:  # noqa: BLE001  项目结构变动时不能让报告模块 import 失败
    _PREFIX_CATEGORY: Dict[str, str] = {}

    def _prefix_of(refdes: str) -> str:
        m = re.match(r"^([A-Za-z]+)\d", refdes or "")
        return m.group(1).upper() if m else ""


# --------------------------------------------------------------------------
# 通用格式化
# --------------------------------------------------------------------------
def _fmt(value: Any) -> str:
    """明细取值 -> 文本。口径对齐 src/dfm_report.py 的 _fmt (浮点最多 4 位小数),
    但保留 4 位小数而不是 4 位有效数字 —— 走线总长 1234.5678mm 不能被截成 1235。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        if value != value:               # NaN
            return ""
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.4f}".rstrip("0").rstrip(".") or "0"
    if isinstance(value, (list, tuple, set)):
        return "、".join(_fmt(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}={_fmt(v)}" for k, v in value.items())
    return str(value)


def _cell(value: Any) -> str:
    """表格单元格文本: 去掉换行 (定宽/HTML 表格里换行会破坏排版)。"""
    return _fmt(value).replace("\r", " ").replace("\n", " ").strip()


def _known(value: Any) -> bool:
    """值是否有效。注意 0 与 False 算有效值, 只有 None / 空串 / 空容器算缺失。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _display(value: Any, missing: str = "未解析到") -> str:
    return _cell(value) if _known(value) else missing


def _join_list(items: Sequence[Any], limit: int = 40) -> str:
    """列表 -> 顿号连接文本; 超长截断并注明总数。"""
    texts = [str(x) for x in (items or [])]
    if len(texts) <= limit:
        return "、".join(texts)
    return "、".join(texts[:limit]) + f" …（共 {len(texts)} 个, 已截断）"


def _source_text(ctx: Context, key: str, fallback: str = "") -> str:
    """design['sources'] 里的取值出处 -> 人类可读文本。"""
    entry = (ctx.design.get("sources") or {}).get(key) or {}
    if isinstance(entry, dict) and entry.get("source"):
        text = str(entry["source"])
        conf = entry.get("confidence")
        return f"{text}（置信度 {conf}）" if conf else text
    return fallback


# --------------------------------------------------------------------------
# 输出: RPT 定宽文本 / CSV / HTML
# --------------------------------------------------------------------------
def _out_dir(ctx: Context) -> str:
    """产物目录: <out_dir>/reports/; out_dir 为空退到 .brd 目录, 再退到临时目录。"""
    base = (ctx.out_dir or "").strip()
    if not base:
        base = os.path.dirname(ctx.brd_path) if ctx.brd_path else ""
    if not base:
        base = tempfile.gettempdir()
    path = os.path.join(base, "reports")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = os.path.join(tempfile.gettempdir(), "brd_reports")
        os.makedirs(path, exist_ok=True)
    return path


def _disp_width(text: str) -> int:
    """显示宽度: 东亚宽字符(中文/全角)按 2 计, 组合符按 0 计。

    局限: Unicode "Ambiguous"(A) 类字符 (如 ° ± × 希腊字母) 按 1 计,
    在部分等宽字体/终端下会被排成 2 —— 所以列宽只保证"内容不被截断 + 用同一
    套宽度算法对齐", 逐字符像素级对齐仍取决于查看器字体。
    """
    width = 0
    for ch in str(text):
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _pad(text: str, width: int) -> str:
    text = text if text is not None else ""
    return text + " " * max(0, width - _disp_width(text))


def _write_rpt(path: str, name_en: str, name_cn: str, columns: List[str],
               rows: List[dict], note: str = "") -> None:
    """Allegro 风格的定宽文本: 表头 + `----` 分隔线 + 左对齐补白的明细行。"""
    lines: List[str] = []
    rule = "=" * 78
    lines.append(rule)
    lines.append(f"  {name_cn} / {name_en}")
    lines.append(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                 f"    明细行数: {len(rows)}")
    if note:
        lines.append(f"  说明: {note}")
    lines.append(rule)
    lines.append("")

    widths: List[int] = []
    for col in columns:
        width = _disp_width(str(col))
        for row in rows:
            width = max(width, _disp_width(_cell(row.get(col))))
        widths.append(max(width, 1))

    lines.append("  ".join(_pad(str(c), w) for c, w in zip(columns, widths)).rstrip())
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(_pad(_cell(row.get(c)), w)
                               for c, w in zip(columns, widths)).rstrip())
    lines.append("")
    lines.append(f"  (定宽列按东亚宽字符计算: 中文/全角按 2 列; 行尾无多余空格)")

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines) + "\n")


def _write_csv(path: str, name_en: str, name_cn: str, columns: List[str],
               rows: List[dict], note: str = "") -> None:
    """CSV: utf-8-sig (Excel 直接双击不乱码)。"""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: _cell(row.get(c)) for c in columns})


_HTML_CSS = """
body{font-family:"Microsoft YaHei",Arial,sans-serif;margin:0;background:#f4f6f9;color:#222}
.wrap{max-width:1320px;margin:0 auto;padding:20px}
h1{font-size:20px;color:#1F3864;margin:0 0 6px}
h1 .sub{font-size:13px;color:#666;font-weight:400}
.meta{color:#555;font-size:12px;margin-bottom:12px}
.tbl{overflow-x:auto}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}
th{background:#2F5496;color:#fff;text-align:left;padding:7px 9px;font-weight:600;white-space:nowrap}
td{border:1px solid #dfe3e8;padding:6px 9px;vertical-align:top}
tbody tr:nth-child(even) td{background:#f2f5fa}
.foot{margin-top:10px;color:#888;font-size:12px}
"""


def _write_html(path: str, name_en: str, name_cn: str, columns: List[str],
                rows: List[dict], note: str = "") -> None:
    """HTML: 深蓝表头白字 (#2F5496) + 斑马纹 + 中文 Microsoft YaHei。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out: List[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        f"<title>{html.escape(name_cn)} - {html.escape(name_en)}</title>",
        f"<style>{_HTML_CSS}</style></head><body><div class='wrap'>",
        f"<h1>{html.escape(name_cn)} <span class='sub'>{html.escape(name_en)}</span></h1>",
        f"<div class='meta'>生成时间 {html.escape(now)} ｜ 明细 {len(rows)} 行"
        + (f" ｜ {html.escape(note)}" if note else "") + "</div>",
        "<div class='tbl'><table>",
        "<thead><tr>" + "".join(f"<th>{html.escape(str(c))}</th>" for c in columns)
        + "</tr></thead>",
        "<tbody>",
    ]
    for row in rows:
        out.append("<tr>" + "".join(f"<td>{html.escape(_cell(row.get(c)))}</td>"
                                    for c in columns) + "</tr>")
    out += [
        "</tbody></table></div>",
        "<div class='foot'>本报告由 BRD 分析与输出引擎生成; 数据出处见各行「来源」列。"
        "缺数据不等于通过, 请同时阅读能力结论里的缺失说明。</div>",
        "</div></body></html>",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(out) + "\n")


def _emit(res: Result, ctx: Context, name_en: str, name_cn: str,
          columns: List[str], rows: List[dict], note: str = "") -> Result:
    """统一落盘: 同一份明细同时写 RPT / CSV / HTML, 并登记 artifact。

    - `res.columns` 与明细列完全一致 (下游 writer / GUI 按此渲染);
    - 三份文件都写成功才逐个 `res.artifact(...)`, 写失败降级为 warning;
    - 调用方必须在"没有数据"时先返回 `unavailable(...)`, 不要拿空表来调用本函数。
    """
    columns = list(columns)
    rows = [dict(r) for r in rows]
    if not rows:                      # 兜底: 明确告诉读者这张表没有明细, 不糊弄
        rows = [{c: "" for c in columns}]
        rows[0][columns[0]] = "（无明细数据, 结论见报告摘要 / missing 说明）"

    # 保证「同时合并了用户导入的 N 份报告」一定出现在结论里
    # (各能力在合并后可能重写 summary, 这里兜一次底, 重复则由 in 判断挡住)。
    merge_note = str(res.extra.get("merge_note") or "")
    if merge_note and merge_note not in res.summary:
        res.summary = f"{res.summary}；{merge_note}".strip("；")

    res.columns = columns
    res.items = rows

    slug = (name_en or name_cn).replace(" ", "_")
    out_dir = _out_dir(ctx)
    written: List[str] = []
    for kind, writer in (("rpt", _write_rpt), ("csv", _write_csv), ("html", _write_html)):
        path = os.path.join(out_dir, f"{slug}.{kind}")
        try:
            writer(path, name_en, name_cn, columns, rows, note)
        except OSError as exc:
            res.warn(f"{kind.upper()} 写出失败: {exc}")
            continue
        res.artifact(kind, path, f"{name_cn}（{len(rows)} 行明细）")
        written.append(path)
    if written:
        res.source(f"产物目录: {out_dir}")
    return res


# --------------------------------------------------------------------------
# 合并用户导入的同类 Allegro 报告
# --------------------------------------------------------------------------
def _report_blob(doc: dict) -> Tuple[str, str, List[str]]:
    """(标题, 表头文字, 列名列表) —— 前两者用于关键字匹配, 列名用于精确匹配。

    列名做成精确匹配 (而非子串), 是为了避免 "REFDES" 被当成 "PIN" 这类误合并;
    多词的关键字 (如 "NET REPORT") 仍然允许在标题/表头文字里做包含匹配。
    """
    title = str(doc.get("title") or "").upper()
    headers: List[str] = []
    columns: List[str] = []
    for sec in doc.get("sections") or []:
        headers.append(str(sec.get("header") or ""))
        for col in (sec.get("columns") or []):
            name = str(col).strip().upper()
            if name:
                columns.append(name)
    return title, " ".join(headers).upper(), columns


# 目标列名 -> 可接受的来源列名 (大写)。用户导入的 Allegro 报告是英文表头,
# 有了这张对映表, 合并进来的明细才能落到正确的列里, 而不是一堆空单元格。
_COLUMN_ALIASES: Dict[str, Tuple[str, ...]] = {
    "项目": ("ITEM", "FIELD", "PARAMETER"),
    "值": ("VALUE", "SETTING"),
    "网络": ("NET", "NET NAME", "NETNAME"),
    "位号": ("REFDES", "REF", "SYMBOL"),
    "器件": ("REFDES", "SYMBOL"),
    "类别": ("CLASS", "ERROR CLASS"),
    "层": ("LAYER", "SUBCLASS"),
    "层名": ("LAYER NAME", "LAYER"),
    "层号": ("LAYER NO", "LAYER NUMBER"),
    "用途": ("FUNCTION", "USAGE"),
    "实测": ("ACTUAL", "MEASURED"),
    "规则": ("REQUIRED", "EXPECTED"),
    "位置": ("LOCATION", "COORDINATES", "POSITION"),
    "摘要": ("DETAIL", "DETAILS", "MESSAGE"),
    "引脚数": ("PIN COUNT", "PINS", "PINCOUNT"),
    "引脚列表": ("PIN LIST", "PINS"),
    "焊盘栈": ("PADSTACK", "PAD STACK", "VIA"),
    "钻孔mm": ("DRILL", "DRILL SIZE", "HOLE SIZE"),
    "数量": ("QTY", "QUANTITY", "COUNT"),
    "类型": ("TYPE", "KIND"),
    "约束": ("CONSTRAINT", "RULE"),
    "X": ("X", "X_MM"),
    "Y": ("Y", "Y_MM"),
    "来源 / 说明": ("SOURCE", "NOTE", "COMMENT"),
}


# 已知的 Allegro 报告类型关键字。用途: 某份报告标题已经明确写着"我是 Pin Report",
# 就不要因为它碰巧有 NET 列而被合并进 Net Report —— 标题比列名更能说明它是什么。
_KNOWN_REPORT_TYPES: Tuple[str, ...] = (
    "BOARD REPORT", "BOARD SUMMARY", "COMPONENT REPORT", "COMPONENT LIST",
    "PIN REPORT", "PIN LIST", "NET REPORT", "NET LIST", "VIA REPORT", "VIA LIST",
    "DRILL REPORT", "ETCH REPORT", "LAYER REPORT", "LAYER STACKUP", "CROSS SECTION",
    "UNCONNECTED", "RATSNEST", "DESIGN RULE", "DRC", "CONSTRAINT REPORT",
    "TESTPOINT REPORT", "TEST POINT REPORT", "IPC-356", "PLACEMENT REPORT",
)


def _match_reports(ctx: Context, keywords: Sequence[str],
                   header_keys: Sequence[str] = (),
                   own_titles: Sequence[str] = ()) -> List[dict]:
    """按标题关键字或表头列名关键字, 找出用户导入的同类报告。"""
    kws = [k.upper() for k in keywords]
    hks = [k.upper() for k in header_keys]
    own = {str(t).upper() for t in own_titles}
    others = [t for t in _KNOWN_REPORT_TYPES if t not in own]
    found: List[dict] = []
    for doc in (ctx.exports.get("reports") or []):
        if not isinstance(doc, dict):
            continue
        title, headers, col_names = _report_blob(doc)
        if any(k in title for k in kws):
            found.append(doc)
            continue
        # 标题已明确是别的报告类型: 不再拿列名去"猜同类"
        if any(t in title for t in others):
            continue
        if headers and any(k in headers for k in kws):
            found.append(doc)
            continue
        if hks and any(c in hks for c in col_names):
            found.append(doc)
    return found


def _merge_imported(ctx: Context, keywords: Sequence[str], header_keys: Sequence[str],
                    columns: List[str], rows: List[dict],
                    own_titles: Sequence[str] = ()) -> Tuple[List[dict], List[dict]]:
    """把用户导入的同类报告 sections 追加到明细后面, 返回 (rows, 命中的报告)。

    映射规则: 目标列名与来源列名 (忽略大小写、忽略首尾空格) 相同或命中别名表
    (_COLUMN_ALIASES) 则直取; 一个字段都映射不上时, 把整行摊成 `键=值` 放进第一列,
    映射不上的个别字段挂到「来源」列 —— 保证原始数据不丢失。
    """
    docs = _match_reports(ctx, keywords, header_keys, own_titles)
    if not docs:
        return rows, []
    if "来源" not in columns:
        columns.append("来源")

    def blank() -> Dict[str, Any]:
        return {c: "" for c in columns}

    for doc in docs:
        source = str(doc.get("source") or os.path.basename(str(doc.get("path") or "")) or "导入报告")
        sections = doc.get("sections") or []
        if not sections:
            # 只有 `键: 值` 的报告 (parse_allegro_report 的 key_values): 摊平成行
            for key, value in (doc.get("key_values") or {}).items():
                row = blank()
                row[columns[0]] = f"[导入] {key}"
                if len(columns) > 1:
                    row[columns[1]] = value
                row["来源"] = source
                rows.append(row)
            continue
        for sec in sections:
            sec_cols = [str(c) for c in (sec.get("columns") or [])]
            banner = blank()
            banner[columns[0]] = f"【导入报告】{source} — {sec.get('header') or '表格'}"
            if len(columns) > 1:
                banner[columns[1]] = "列: " + " | ".join(sec_cols)
            banner["来源"] = source
            rows.append(banner)
            for src_row in (sec.get("rows") or []):
                if not isinstance(src_row, dict):
                    continue
                row = blank()
                mapped = False
                used: set = set()      # 一个来源列只落一次, 避免 PINS 同时进"引脚数/引脚列表"
                for col in columns:
                    if col == "来源":
                        continue
                    cu = str(col).strip().upper()
                    for key, value in src_row.items():
                        ku = str(key).strip().upper()
                        if ku in used:
                            continue
                        if ku == cu or ku in _COLUMN_ALIASES.get(col, ()):
                            row[col] = value
                            used.add(ku)
                            mapped = True
                            break
                row["来源"] = source
                unmapped = [f"{k}={v}" for k, v in src_row.items()
                            if str(k).strip().upper() not in used]
                if not mapped:
                    # 一个字段都映射不上: 整行摊成 键=值, 原始数据不丢
                    row[columns[0]] = " | ".join(unmapped)
                    row["来源"] = source
                elif unmapped:
                    # 部分字段没有对映列: 挂到「来源」列, 不污染语义列
                    row["来源"] = f"{source} ; 未映射: " + "; ".join(unmapped)
                rows.append(row)
    return rows, docs


def _merge_note(docs: Sequence[dict]) -> str:
    if not docs:
        return ""
    names = "、".join(str(d.get("source") or "?") for d in docs)
    return f"同时合并了用户导入的 {len(docs)} 份 Allegro 报告: {names}"


def _merge_into(res: Result, ctx: Context, keywords: Sequence[str],
                header_keys: Sequence[str], columns: List[str],
                rows: List[dict], own_titles: Sequence[str] = ()) -> Tuple[List[dict], str]:
    """合并 + 把合并说明写进结论与出处。"""
    rows, docs = _merge_imported(ctx, keywords, header_keys, columns, rows, own_titles)
    note = _merge_note(docs)
    if note:
        res.extra["merge_note"] = note
        res.summary = f"{res.summary}；{note}".strip("；")
        for doc in docs:
            res.source(f"用户导入的报告: {doc.get('source')} "
                       f"({doc.get('title') or '无标题'})")
    return rows, note


# ==========================================================================
# 5.1 Board Report
# ==========================================================================
_BOARD_COLUMNS = ["项目", "值", "单位", "来源 / 说明"]


def cap_board_report(ctx: Context) -> Result:
    cap = CATALOG_MAP["report.board"]
    if not ctx.design_ok:
        return unavailable(
            cap, f"BRD 解析失败, 板级报告无法生成: {ctx.design_error or '未知原因'}",
            "确认文件是 Allegro .brd (17.x 归档或二进制平面文件), 且未加密/截断")
    d = ctx.design
    raw_note = d.get("raw_note") if isinstance(d.get("raw_note"), dict) else {}
    qv = raw_note.get("quickview") if isinstance(raw_note.get("quickview"), dict) else {}
    comp = facts.components(ctx)
    has_net_obj = "objects/Net.xml" in ctx.objects
    has_pair_obj = "objects/DiffPair.xml" in ctx.objects

    net_names = facts.net_names(ctx)
    diffs = facts.diff_pair_names(ctx)
    vias = facts.via_rows(ctx)
    n_phys = len(d.get("physical_csets") or {})
    n_space = len(d.get("spacing_csets") or {})

    rows: List[dict] = []

    def put(project: str, value: Any, unit: str = "", source: str = "") -> None:
        rows.append({"项目": project,
                     "值": _display(value),
                     "单位": unit if _known(value) else "",
                     "来源 / 说明": source})

    # ---- 设计标识 / 文件 ----
    put("设计名 / 板名", ctx.board_name, "",
        _source_text(ctx, "design_name", "objects/Design.xml 或 .brd 文件名"))
    put("BRD 文件", ctx.brd_path, "", "命令行 / 界面传入的路径")
    put("文件大小", d.get("file_size_mb"), "MB", "文件系统")

    # ---- 结构规模 ----
    put("层数", ctx.layer_count, "层",
        _source_text(ctx, "layer_count", d.get("layer_count_source") or "未解析到层数"))
    put("网络数", (len(net_names) if has_net_obj else (d.get("net_count") or None)), "个",
        "objects/Net.xml 节点数" if has_net_obj
        else "未解析到（.brd 归档里没有 objects/Net.xml; 本工具不用二进制正则猜网络名）")
    put("器件数", (comp.get("count") if comp.get("ok") else None), "个",
        comp.get("source") if comp.get("ok") else comp.get("reason", "未解析到器件清单"))
    put("差分对", (len(diffs) if has_pair_obj else None), "对",
        "objects/DiffPair.xml 节点名" if has_pair_obj
        else "未解析到（归档里没有 objects/DiffPair.xml）")
    put("过孔焊盘栈", (len(vias) or None), "种",
        "PhysicalCSet VIA_LIST / 二进制焊盘栈名")

    # ---- 板尺寸 / 图纸单位 ----
    size = d.get("board_size_mm")
    if isinstance(size, (list, tuple)) and len(size) >= 2 and _known(size[0]):
        size_text = f"{_fmt(size[0])} × {_fmt(size[1])}"
        size_src = _source_text(ctx, "board_size_mm", "内嵌设计快照 BOARD GEOMETRY/OUTLINE")
    else:
        area = facts.region_areas(ctx)
        if area.get("ok"):
            size_text = f"{area['width_mm']} × {area['height_mm']}"
            size_src = "内嵌快照板框折线 bbox（未取到 Draw Size 标定说明）"
        else:
            size_text = None
            size_src = f"未解析到（{area.get('reason', '没有可用的板框几何')}）"
    put("板尺寸", size_text, "mm", size_src)

    if qv.get("found"):
        put("图纸单位与精度", f"{qv.get('unit')} / 精度 {qv.get('accuracy')} 位", "",
            f"二进制 _QuickViewText（图幅 {qv.get('drawing_w_mm')} × "
            f"{qv.get('drawing_h_mm')} mm）")
    else:
        put("图纸单位与精度", None, "",
            qv.get("source") or "未解析到 _QuickViewText 元数据")
    put("解析用单位系数", d.get("units_factor"), "mm/unit",
        d.get("units_source") or "未解析到单位声明")

    # ---- 钻孔 / 制程 ----
    put("钻孔跨度", d.get("drill_spans"), "",
        "二进制钻孔表 (PTH/NPTH 各跨度)")
    hole_total = d.get("hole_count")
    if hole_total is None and d.get("hole_count_partial"):
        hole_total = d.get("hole_count_partial")
        put("钻孔总数", hole_total, "个",
            "二进制钻孔表（部分行缺数量列, 这是已知数量的合计）")
    else:
        put("钻孔总数", hole_total, "个", "二进制钻孔表")
    put("板厚", d.get("board_thickness_mm"), "mm",
        "二进制制程说明文本（低置信度, 需与叠层表核对）")
    put("表面处理", d.get("surface_finishes"), "",
        "二进制制程说明文本（低置信度）")
    copper_oz = ((raw_note.get("notes") or {}) if isinstance(raw_note.get("notes"), dict)
                 else {}).get("copper_oz")
    put("成品铜厚", copper_oz, "oz", "二进制制程说明文本（低置信度）")
    put("约束集数量", f"Physical {n_phys} / Spacing {n_space}" if (n_phys or n_space) else None,
        "个", "objects/PhysicalCSet.xml + objects/SpacingCSet.xml")

    # ---- 快照 / 标定 / 导入文件 ----
    put("内嵌设计快照", (f"{len(ctx.raw_entries)} 条图形" if ctx.has_snapshot else None), "",
        "design.comps 内嵌快照（几何类分析的唯一来源）")
    put("坐标标定", (f"{ctx.scale_mm_per_unit}（{ctx.outline.get('method') or '未知方法'}）"
                    if ctx.has_scale else None), "mm/unit",
        "板框 Draw Size 标定" if ctx.has_scale else "未标定（尺寸类结论不可用）")
    exports = ctx.exports.get("files") or []
    put("用户导入的导出文件", (len(exports) or None), "个",
        "、".join(sorted({str(f.get("kind")) for f in exports if isinstance(f, dict)}))
        or "无（DRC/网表/坐标/IPC-356 都会在这里体现）")

    res = Result(cap, INFO, "")
    res.metric("层数", ctx.layer_count, "层", "预览元数据 / SubClass.xml")
    res.metric("网络数", len(net_names) if has_net_obj else None, "个", "objects/Net.xml")
    res.metric("器件数", comp.get("count") if comp.get("ok") else None, "个",
               comp.get("source") if comp.get("ok") else comp.get("reason", ""))
    res.metric("差分对", len(diffs) if has_pair_obj else None, "对",
               "objects/DiffPair.xml" if has_pair_obj else "无该对象")
    if isinstance(size, (list, tuple)) and len(size) >= 2 and _known(size[0]):
        res.metric("板宽", size[0], "mm", size_src)
        res.metric("板高", size[1], "mm", size_src)

    if not _known(size_text):
        res.need("板尺寸（板宽 × 板高）",
                 "确认 .brd 有 BOARD GEOMETRY/OUTLINE 且能取到 Draw Size 做标定; "
                 "或提供机械图/DXF")
    if not _known(d.get("board_thickness_mm")) or not d.get("surface_finishes"):
        res.need("板厚与表面处理", ctx.howto_stackup())
    if not has_net_obj:
        res.need("网络清单", "确认 .brd 是 17.x 归档且保留 objects/Net.xml")
    if not comp.get("ok"):
        res.need("器件清单（位号）", comp.get("reason", "BRD 无 objects/Part.xml"))

    res.summary = (
        f"{ctx.board_name}: {ctx.layer_count or '?'} 层 / "
        f"{len(net_names) if has_net_obj else '?'} 网络 / "
        f"{comp.get('count') if comp.get('ok') else '?'} 器件 / "
        f"板尺寸 {size_text if _known(size_text) else '未解析到'}")
    for warn in (d.get("warnings") or []):
        res.warn(warn)

    rows, note = _merge_into(res, ctx, ("BOARD REPORT", "BOARD SUMMARY", "BOARD STATISTICS"),
                             (), _BOARD_COLUMNS, rows,
                             own_titles=("BOARD REPORT", "BOARD SUMMARY", "BOARD STATISTICS"))
    return _emit(res, ctx, "Board Report", "板级基本信息", _BOARD_COLUMNS, rows,
                 note or "缺失项已如实写「未解析到」并注明来源, 不静默省略")


# ==========================================================================
# 5.2 Component Report
# ==========================================================================
_COMPONENT_COLUMNS = ["位号", "前缀", "类别"]


def cap_component_report(ctx: Context) -> Result:
    cap = CATALOG_MAP["report.component"]
    comp = facts.components(ctx)
    cols = list(_COMPONENT_COLUMNS)
    rows: List[dict] = []
    if comp.get("ok"):
        for refdes in comp.get("refdes") or []:
            prefix = _prefix_of(refdes)
            rows.append({"位号": refdes, "前缀": prefix or "—",
                         "类别": _PREFIX_CATEGORY.get(prefix, prefix or "未知前缀")})
        for prefix, count in (comp.get("by_prefix") or {}).items():
            rows.append({"位号": f"[前缀汇总] {prefix or '未知'}",
                         "前缀": prefix or "—", "类别": f"{count} 个"})
        for label, count in (comp.get("by_category") or {}).items():
            rows.append({"位号": f"[类别汇总] {label}", "前缀": "—",
                         "类别": f"{count} 个"})

    has_native = bool(rows)
    res = Result(cap, INFO, "")
    if has_native:
        res.metric("器件总数", comp.get("count"), "个",
                   comp.get("source", "objects/Part.xml"), "high")
        res.metric("位号前缀种类", len(comp.get("by_prefix") or {}), "种", "位号前缀统计")
        res.metric("器件类别数", len(comp.get("by_category") or {}), "类", "位号前缀分类")
        for label, count in list((comp.get("by_category") or {}).items())[:8]:
            res.metric(f"其中 {label}", count, "个", "位号前缀分类")
        res.summary = (f"{comp.get('count')} 个器件, "
                       f"{len(comp.get('by_prefix') or {})} 种前缀, "
                       f"明细含逐位号清单与汇总行")
        res.source(comp.get("source", "objects/Part.xml 位号清单"))

    rows, note = _merge_into(res, ctx, ("COMPONENT REPORT", "COMPONENT LIST", "REFDES LIST"),
                             ("REFDES", "COMPONENT"), cols, rows,
                             own_titles=("COMPONENT REPORT", "COMPONENT LIST", "REFDES LIST"))
    if not rows:
        return unavailable(cap, comp.get("reason", "BRD 里没有器件清单"),
                           "确认 .brd 归档保留 objects/Part.xml; "
                           "FAB 拼板图/纯板框图纸没有位号, 需提供真实 PCB 设计文件")
    if not has_native:
        res.need("器件清单（位号）", comp.get("reason", "BRD 无 objects/Part.xml"))
        res.summary = "本工具未从 .brd 读到器件清单; 以下明细来自用户导入的报告"
        res.warn("器件清单不是从 .brd 直读, 数量/位号以导入报告为准")
    return _emit(res, ctx, "Component Report", "元件数量与位号",
                 cols, rows, note)


# ==========================================================================
# 5.3 Pin Report
# ==========================================================================
_PIN_COLUMNS = ["网络", "引脚数", "引脚列表", "来源"]


def cap_pin_report(ctx: Context) -> Result:
    cap = CATALOG_MAP["report.pin"]
    netlist = ctx.exports.get("netlist")
    cols = list(_PIN_COLUMNS)
    rows: List[dict] = []
    if netlist and netlist.get("ok"):
        for net in sorted((netlist.get("nets") or {}).keys()):
            pins = netlist["nets"].get(net) or []
            rows.append({"网络": net, "引脚数": len(pins),
                         "引脚列表": _join_list(pins, 40),
                         "来源": netlist.get("source", "导入的网表")})

    has_native = bool(rows)
    res = Result(cap, INFO, "")
    if has_native:
        res.metric("网络数", netlist.get("count"), "个",
                   f"导入的网表 ({netlist.get('source')})", "high")
        res.metric("引脚/连接点总数", netlist.get("pin_count"), "个", "同上")
        res.summary = (f"网表共 {netlist.get('count')} 个网络、"
                       f"{netlist.get('pin_count')} 个引脚连接点")
        res.source(f"导入的网表: {netlist.get('source')}")

    rows, note = _merge_into(res, ctx, ("PIN REPORT", "PIN LIST"),
                             ("PIN", "PINS", "PIN COUNT"), cols, rows,
                             own_titles=("PIN REPORT", "PIN LIST"))
    if not rows:
        res = unavailable(cap, "引脚级数据只存在于网表里, 当前没有可用的网表",
                          "Allegro: Tools > Reports > Netlist, 或第三方网表 pstxnet.dat")
        res.need("引脚连接关系（网表）",
                 "Allegro: Tools > Reports > Netlist, 或第三方网表 pstxnet.dat")
        return res
    if not has_native:
        res.need("引脚连接关系（网表）",
                 "Allegro: Tools > Reports > Netlist, 或第三方网表 pstxnet.dat")
        res.summary = "本工具没读到网表; 以下引脚明细来自用户导入的报告"
        res.warn("引脚明细不是从网表直读, 完整性以导入报告为准")
    return _emit(res, ctx, "Pin Report", "引脚信息", cols, rows, note)


# ==========================================================================
# 5.4 Net Report
# ==========================================================================
_NET_COLUMNS = ["网络", "类型", "引脚数", "说明"]


def cap_net_report(ctx: Context) -> Result:
    cap = CATALOG_MAP["report.net"]
    names = facts.net_names(ctx)
    diffs = set(facts.diff_pair_names(ctx))
    power = set(facts.power_ground_nets(names))
    netlist = ctx.exports.get("netlist")
    pin_counts: Dict[str, int] = {}
    if netlist and netlist.get("ok"):
        pin_counts = {n: len(v or []) for n, v in (netlist.get("nets") or {}).items()}

    cols = list(_NET_COLUMNS)
    rows: List[dict] = []
    for name in names:
        if name in power:
            kind, memo = "电源/地", "按网络名前缀判定 (GND/VCC/VDD/…)"
        elif name in diffs:
            kind, memo = "差分对", "见 objects/DiffPair.xml"
        else:
            kind, memo = "信号", ""
        rows.append({"网络": name, "类型": kind,
                     "引脚数": pin_counts.get(name, ""), "说明": memo})

    has_native = bool(rows)
    res = Result(cap, INFO, "")
    if has_native:
        res.metric("网络总数", len(names), "个", "objects/Net.xml 节点名", "high")
        res.metric("电源/地网络", len(power), "个", "网络名前缀判定", "medium")
        res.metric("差分对", len(diffs), "对",
                   "objects/DiffPair.xml" if "objects/DiffPair.xml" in ctx.objects
                   else "无 DiffPair.xml（0 表示没有该对象）")
        if pin_counts:
            res.metric("有引脚数信息的网络", len(pin_counts), "个",
                       f"导入的网表 ({netlist.get('source')})")
        else:
            res.need("每个网络的引脚数",
                     "Allegro: Tools > Reports > Netlist, 或第三方网表 pstxnet.dat")
        res.summary = (f"{len(names)} 个网络（电源/地 {len(power)}, 差分对 {len(diffs)}）"
                       + (f"; 引脚数来自导入网表" if pin_counts else "; 无网表, 未给引脚数"))
        res.source("objects/Net.xml（网络名只取归档节点名, 不用二进制正则猜）")

    rows, note = _merge_into(res, ctx, ("NET REPORT", "NET LIST"), ("NET",), cols, rows,
                             own_titles=("NET REPORT", "NET LIST"))
    if not rows:
        res = unavailable(cap, "读不到 objects/Net.xml, 无法列出网络清单",
                          "确认 .brd 是 17.x 归档且保留 objects/Net.xml; "
                          "16.x 纯二进制或 FAB 图纸没有网络对象")
        res.need("网络清单（objects/Net.xml）",
                 "确认 .brd 归档完整; 本工具不用二进制文本正则猜网络名")
        return res
    if not has_native:
        res.need("网络清单（objects/Net.xml）", "确认 .brd 归档完整且保留 objects/Net.xml")
        res.summary = "本工具没读到 Net.xml; 以下网络清单来自用户导入的报告"
        res.warn("网络清单不是从 .brd 直读, 请核对是否漏网/多网")
    return _emit(res, ctx, "Net Report", "网络名称与连接", cols, rows, note)


# ==========================================================================
# 5.5 Via Report
# ==========================================================================
_VIA_COLUMNS = ["焊盘栈", "类型", "钻孔mm", "焊盘mm", "孔环mm", "跨层",
                "数量", "网络", "位号", "X", "Y", "来源"]
_MAX_HOLE_ROWS = 2000


def cap_via_report(ctx: Context) -> Result:
    cap = CATALOG_MAP["report.via"]
    vias = facts.via_rows(ctx)
    drills = facts.drill_by_size(ctx)
    holes = ctx.exports.get("holes") or []

    cols = list(_VIA_COLUMNS)
    rows: List[dict] = []
    for via in vias:
        rows.append({"焊盘栈": via.get("name"), "类型": facts.hole_kind_label(via.get("kind")),
                     "钻孔mm": via.get("drill_mm"), "焊盘mm": via.get("pad_mm"),
                     "孔环mm": via.get("annular_mm"),
                     "跨层": (f"{via['span'][0]}-{via['span'][1]}"
                             if via.get("span") else ""),
                     "数量": "", "网络": "", "位号": "", "X": "", "Y": "",
                     "来源": "PhysicalCSet VIA_LIST / 焊盘栈命名"})
    for row in drills:
        rows.append({"焊盘栈": f"钻孔 Φ{_fmt(row.get('size_mm'))}mm",
                     "类型": row.get("plating"), "钻孔mm": row.get("size_mm"),
                     "焊盘mm": "", "孔环mm": "", "跨层": row.get("spans") or "",
                     "数量": row.get("qty"), "网络": "", "位号": "", "X": "", "Y": "",
                     "来源": "二进制钻孔表（按孔径聚合）"})

    hole_rows: List[dict] = []
    for hole in holes:
        if not isinstance(hole, dict):
            continue
        pad, drill = hole.get("pad_mm"), hole.get("drill_mm")
        ring = round((pad - drill) / 2.0, 4) if (pad and drill) else ""
        hole_rows.append({
            "焊盘栈": hole.get("padstack") or hole.get("tool") or "",
            "类型": "孔位", "钻孔mm": drill, "焊盘mm": pad, "孔环mm": ring,
            "跨层": "", "数量": 1, "网络": hole.get("net") or "",
            "位号": hole.get("refdes") or "", "X": hole.get("x_mm"),
            "Y": hole.get("y_mm"), "来源": "导入的钻孔 / 坐标文件"})
    hole_rows.sort(key=lambda r: (r["钻孔mm"] if r["钻孔mm"] is not None else 9e9,
                                  r["X"] if r["X"] is not None else 0))
    truncated = len(hole_rows) > _MAX_HOLE_ROWS
    rows.extend(hole_rows[:_MAX_HOLE_ROWS])

    has_native = bool(rows)
    res = Result(cap, INFO, "")
    if has_native:
        res.metric("过孔焊盘栈", len(vias), "种",
                   "PhysicalCSet VIA_LIST / 二进制焊盘栈名")
        res.metric("钻孔孔径种类", len(drills), "种", "二进制钻孔表（按孔径聚合）")
        if ctx.has_holes_xy:
            res.metric("有坐标的孔", len(hole_rows), "个", "导入的钻孔 / 坐标文件", "high")
        else:
            res.need("每个孔的坐标与孔径（孔到孔/孔到边要用）", ctx.howto_holes_xy())
        drill_sizes = [r.get("size_mm") for r in drills if r.get("size_mm")]
        if drill_sizes:
            res.metric("最小钻孔", min(drill_sizes), "mm", "二进制钻孔表", "medium")
        res.summary = (f"{len(vias)} 种过孔焊盘栈, {len(drills)} 种钻孔孔径"
                       + (f", {len(hole_rows)} 个有坐标的孔"
                          if ctx.has_holes_xy else "; 缺孔位坐标（见 missing）"))
        res.source("PhysicalCSet.xml VIA_LIST + 二进制钻孔表 + 导入的钻孔坐标")

    rows, note = _merge_into(res, ctx, ("VIA REPORT", "VIA LIST", "DRILL REPORT"),
                             ("VIA", "DRILL", "PADSTACK"), cols, rows,
                             own_titles=("VIA REPORT", "VIA LIST", "DRILL REPORT"))
    if not rows:
        res = unavailable(cap, "BRD 里既没有过孔焊盘栈, 也没有钻孔表和孔位坐标",
                          "确认 PhysicalCSet 的 VIA_LIST, 或提供 NC Drill/Excellon 文件")
        res.need("每个孔的坐标与孔径", ctx.howto_holes_xy())
        return res
    if not has_native:
        res.need("每个孔的坐标与孔径", ctx.howto_holes_xy())
        res.summary = "本工具没读到过孔/钻孔数据; 以下明细来自用户导入的报告"
        res.warn("过孔明细不是从 .brd 直读, 数量/孔径以导入报告为准")

    notes = [note] if note else []
    if truncated:
        notes.append(f"孔位明细共 {len(hole_rows)} 行, 按孔径排序后只列前 {_MAX_HOLE_ROWS} 行"
                     f"（完整数据见导入的钻孔文件）")
    notes.append("本表统一列结构: 焊盘栈行给尺寸, 钻孔聚合行用『钻孔mm』表示孔径、"
                 "『数量』表示孔数, 孔位行给坐标")
    return _emit(res, ctx, "Via Report", "过孔数量与位置", cols, rows, "；".join(notes))


# ==========================================================================
# 5.6 Etch Report
# ==========================================================================
_ETCH_COLUMNS = ["层", "图形数", "走线总长mm", "最小线宽mm", "中位线宽mm",
                 "最大线宽mm", "铜面积估算mm2", "铜占比", "说明", "来源"]


def cap_etch_report(ctx: Context) -> Result:
    cap = CATALOG_MAP["report.etch"]
    stats = facts.copper_stats(ctx)
    declared_w = ctx.design.get("min_line_width_mm")
    declared_s = ctx.design.get("min_spacing_mm")

    cols = list(_ETCH_COLUMNS)
    rows: List[dict] = []
    for row in stats:
        item = {c: "" for c in cols}
        item.update({k: v for k, v in row.items() if k in cols})
        item["来源"] = "内嵌快照 ETCH 图形（轮廓级）"
        rows.append(item)
    if declared_w or declared_s:
        item = {c: "" for c in cols}
        item["层"] = "【约束】最小线宽"
        item["最小线宽mm"] = declared_w if declared_w else ""
        item["说明"] = ("PhysicalCSet.xml 声明的全板最严 MIN_LINE_WIDTH"
                       if declared_w else "未声明")
        item["来源"] = _source_text(ctx, "min_line_width_mm", "objects/PhysicalCSet.xml")
        rows.append(item)
        item = {c: "" for c in cols}
        item["层"] = "【约束】最小线距"
        item["说明"] = (f"SpacingCSet LINE_TO_LINE_SPACING = {_fmt(declared_s)}mm"
                       if declared_s else "未声明")
        item["来源"] = _source_text(ctx, "min_spacing_mm", "objects/SpacingCSet.xml")
        rows.append(item)

    has_native = bool(rows)
    res = Result(cap, INFO, "")
    if has_native:
        if stats:
            res.metric("有铜箔图形的层", len(stats), "层", "内嵌快照 ETCH 图形")
            res.metric("铜箔图形总数", sum(int(r.get("图形数") or 0) for r in stats), "条",
                       "内嵌快照 ETCH 图形")
            res.metric("走线总长", round(sum(float(r.get("走线总长mm") or 0)
                                        for r in stats), 3), "mm",
                       "各层折线长度求和", "medium")
        if declared_w:
            res.metric("最小线宽约束", declared_w, "mm", "PhysicalCSet.xml", "high")
        if declared_s:
            res.metric("最小线距约束", declared_s, "mm", "SpacingCSet.xml", "high")
        if stats:
            total_len = round(sum(float(r.get("走线总长mm") or 0) for r in stats), 3)
            res.summary = (f"{len(stats)} 层铜箔统计, 走线总长合计 {total_len}mm"
                           + (f", 约束最小线宽 {declared_w}mm" if declared_w else "")
                           + (f", 约束最小线距 {declared_s}mm" if declared_s else ""))
        else:
            res.summary = ("内嵌快照没有铜箔图形明细, 只给出约束里的最小线宽/最小线距"
                           + (f"（线宽 {declared_w}mm）" if declared_w else "")
                           + (f"（线距 {declared_s}mm）" if declared_s else ""))
        res.source("内嵌快照 ETCH 图形 + PhysicalCSet/SpacingCSet 约束")
        if stats:
            res.warn("铜占比是按「折线长度 × 平均线宽」估算, 不含铜皮填充; "
                     "快照 ETCH 图形混合走线与平面轮廓, 精细判定请用 Allegro Etch Report")

    rows, note = _merge_into(res, ctx, ("ETCH REPORT", "ETCH", "VIA REPORT"),
                             ("ETCH", "WIDTH", "LAYER"), cols, rows,
                             own_titles=("ETCH REPORT", "ETCH", "VIA REPORT", "VIA LIST"))
    if not rows:
        res = unavailable(cap, "内嵌快照没有 ETCH 铜箔图形, 约束里也没有线宽/线距",
                          "确认设计快照导出包含 ETCH 层; 或提供 Allegro Etch/Via Report、Gerber")
        res.need("Etch Report 或含 ETCH 图形的设计快照",
                 "Allegro: Tools > Reports > Etch Report; 或导出含 ETCH 层的设计快照")
        return res
    if not has_native:
        res.need("Etch Report 或含 ETCH 图形的设计快照",
                 "Allegro: Tools > Reports > Etch Report")
        res.summary = "本工具没读到铜箔几何; 以下走线明细来自用户导入的 Etch/Via 报告"
        res.warn("走线明细不是从快照直读, 数据以导入报告为准")
    return _emit(res, ctx, "Etch Report", "走线信息", cols, rows, note)


# ==========================================================================
# 5.7 Layer Report
# ==========================================================================
_LAYER_COLUMNS = ["层号", "层名", "用途", "有铜箔图形", "来源"]


def cap_layer_report(ctx: Context) -> Result:
    cap = CATALOG_MAP["report.layer"]
    rows = facts.stackup_rows(ctx)
    cols = list(_LAYER_COLUMNS)

    has_native = bool(rows)
    res = Result(cap, INFO, "")
    if has_native:
        res.metric("层数", ctx.layer_count or len(rows), "层",
                   _source_text(ctx, "layer_count", "层名推算"))
        res.metric("信号层", sum(1 for r in rows if "信号" in str(r.get("用途"))), "层",
                   "按层名关键字判定")
        res.metric("平面层", sum(1 for r in rows if "平面" in str(r.get("用途"))), "层",
                   "按层名关键字判定")
        etch_layers = ctx.etch_layers
        if etch_layers:
            res.metric("快照里有铜箔的层", len(etch_layers), "层",
                       "内嵌快照 ETCH 图形", "medium")
        res.summary = (f"{ctx.layer_count or len(rows)} 层: "
                       f"{sum(1 for r in rows if '信号' in str(r.get('用途')))} 信号 / "
                       f"{sum(1 for r in rows if '平面' in str(r.get('用途')))} 平面")
        res.source("二进制 _QuickViewText + objects/SubClass.xml")
        if ctx.design.get("layer_note"):
            res.warn(ctx.design["layer_note"])

    rows, note = _merge_into(res, ctx, ("LAYER REPORT", "LAYER STACKUP", "CROSS SECTION",
                                        "STACKUP"),
                             ("LAYER", "STACKUP"), cols, rows,
                             own_titles=("LAYER REPORT", "LAYER STACKUP", "CROSS SECTION",
                                         "STACKUP"))
    if not rows:
        res = unavailable(cap, "没有解析到层信息（层数与层名都为空）",
                          ctx.howto_stackup())
        res.need("层结构与叠层截面", ctx.howto_stackup())
        return res
    if not has_native:
        res.need("层结构（层数/层名/用途）", "确认 .brd 的 _QuickViewText 或 SubClass.xml 完整")
        res.summary = "本工具没读到层信息; 以下层表来自用户导入的叠层报告"
        res.warn("层表不是从 .brd 直读, 叠层信息以导入报告为准")
    return _emit(res, ctx, "Layer Report", "层信息", cols, rows, note)


# ==========================================================================
# 5.8 Unconnected Report
# ==========================================================================
_UNCONN_COLUMNS = ["类别", "层", "网络", "位置", "器件", "摘要"]
_UNCONN_KEYS = ("UNCONNECTED", "RATSNEST", "UNROUTED", "FLY LINE", "未连接", "飞线")


def _drc_hits(drc: dict, keys: Sequence[str]) -> List[dict]:
    hits = []
    for item in (drc.get("items") or []):
        blob = f"{item.get('class', '')} {item.get('detail', '')}".upper()
        if any(str(k).upper() in blob for k in keys):
            hits.append(item)
    return hits


def cap_unconnected_report(ctx: Context) -> Result:
    cap = CATALOG_MAP["report.unconnected"]
    drc = ctx.exports.get("drc")
    cols = list(_UNCONN_COLUMNS)
    rows: List[dict] = []
    have_drc = bool(drc and drc.get("ok"))
    if have_drc:
        for item in _drc_hits(drc, _UNCONN_KEYS):
            rows.append({"类别": item.get("class"), "层": item.get("layer"),
                         "网络": item.get("net"), "位置": item.get("location"),
                         "器件": item.get("refdes"),
                         "摘要": item.get("detail") or ""})

    native_hits = len(rows)                     # 合并前: 从 DRC 报告筛出的条目
    res = Result(cap, INFO, "")
    if have_drc:
        res.metric("未连接 / 飞线条目", native_hits, "条",
                   f"导入的 DRC 报告 ({drc.get('source')})")
        res.source(f"Allegro DRC 报告: {drc.get('source')}")

    rows, note = _merge_into(res, ctx,
                             ("UNCONNECTED", "RATSNEST", "UNROUTED", "FLY LINE"),
                             ("UNCONNECTED", "RATSNEST"), cols, rows,
                             own_titles=("UNCONNECTED", "RATSNEST", "UNROUTED", "FLY LINE"))
    merged_hits = len(rows)
    if not rows:
        # 既没有 DRC 报告, 也没有用户导入的同类报告 -> 如实说"算不了"
        res = unavailable(cap, "未连接状态必须在带网络的数据库上判定, "
                              "当前既没有 DRC 报告也没有 Unconnected 报告",
                          ctx.howto_drc_report())
        res.need("Allegro DRC / Unconnected Pins 报告", ctx.howto_drc_report())
        return res

    if native_hits:
        res.status = FAIL
        # 这里重写 summary 会覆盖 _merge_into 追加的合并说明, 所以再拼一次
        res.summary = (f"从 DRC 报告筛出 {native_hits} 条未连接 / 飞线条目"
                       f"（{drc.get('source')}）" + (f"；{note}" if note else ""))
    elif have_drc and not merged_hits:
        # DRC 报告存在、且合并后确实没有未连接条目: 这是"已知为零", 不是"没有数据"
        rows.insert(0, {"类别": "（无）", "层": "", "网络": "", "位置": "", "器件": "",
                        "摘要": f"{drc.get('source')} 报告里没有未连接 / 飞线记录"})
        res.status = PASS
        res.summary = (f"导入的 DRC 报告里没有未连接 / 飞线记录（{drc.get('source')}）"
                       + (f"；{note}" if note else ""))
    elif have_drc:
        # DRC 本身没有未连接条目, 但用户另导入的 Unconnected/飞线报告里有
        res.status = WARN
        res.summary = (f"DRC 报告未列出未连接, 但导入的同类报告有 {merged_hits} 条记录, "
                       "需与原始报告核对" + (f"；{note}" if note else ""))
    else:
        res.status = INFO
        res.summary = "本工具算不了未连接; 以下明细全部来自用户导入的报告"
        if note:
            res.summary = f"{res.summary}；{note}"
        res.need("Allegro DRC / Unconnected Pins 报告", ctx.howto_drc_report())
        res.warn("未连接明细不是从 DRC 解析结果直读, 请核对")
    if have_drc:
        res.warn("未连接条目按关键字 UNCONNECTED/RATSNEST/UNROUTED/未连接/飞线 从 "
                 "DRC 报告筛选; 若 Allegro 用了别的类别名, 可能漏筛, 请对照原报告")

    notes = [n for n in (note,) if n]
    notes.append("未连接明细只能来自 Allegro 导出的 DRC / Unconnected 报告, "
                 "本工具不猜")
    return _emit(res, ctx, "Unconnected Report", "未连接网络", cols, rows,
                 "；".join(notes))


# ==========================================================================
# 5.9 DRC Report
# ==========================================================================
_DRC_COLUMNS = ["类别", "层", "网络", "位置", "器件", "实测", "规则", "摘要"]


def cap_drc_report(ctx: Context) -> Result:
    cap = CATALOG_MAP["report.drc"]
    drc = ctx.exports.get("drc")
    cols = list(_DRC_COLUMNS)
    rows: List[dict] = []
    have_drc = bool(drc and drc.get("ok"))
    if have_drc:
        for item in (drc.get("items") or []):
            rows.append({"类别": item.get("class"), "层": item.get("layer"),
                         "网络": item.get("net"), "位置": item.get("location"),
                         "器件": item.get("refdes"), "实测": item.get("actual"),
                         "规则": item.get("required"),
                         "摘要": item.get("detail") or ""})

    res = Result(cap, INFO, "")
    if have_drc:
        res.status = FAIL if drc.get("count") else PASS
        res.metric("违规总数", drc.get("count"), "条",
                   f"导入的 DRC 报告 ({drc.get('source')})")
        res.metric("违规类别数", len(drc.get("by_class") or {}), "类", "同上")
        for cls, count in list((drc.get("by_class") or {}).items())[:20]:
            res.metric(f"类别 {cls}", count, "条", "导入的 DRC 报告按类别汇总")
        res.extra["by_class"] = dict(drc.get("by_class") or {})
        res.summary = (f"DRC 违规 {drc.get('count')} 条, "
                       f"{len(drc.get('by_class') or {})} 个类别"
                       + (f"; 主要类别: "
                          + "、".join(f"{k} {v}" for k, v in
                                      list((drc.get("by_class") or {}).items())[:4])
                          if drc.get("by_class") else ""))
        res.source(f"Allegro DRC 报告: {drc.get('source')}")
        if drc.get("unrecognized_blocks"):
            res.warn(f"报告里有 {drc['unrecognized_blocks']} 段没能解析出字段, 可能少算违规")

    rows, note = _merge_into(res, ctx, ("DRC REPORT", "DESIGN RULE", "VIOLATION"),
                             ("DRC", "VIOLATION"), cols, rows,
                             own_titles=("DRC REPORT", "DESIGN RULE", "DRC"))
    if not rows:
        res = unavailable(cap, "没有导入的 DRC 报告, 违规明细无从生成",
                          ctx.howto_drc_report())
        res.need("Allegro DRC 报告（含类别/层/位置/实测值/规则值）", ctx.howto_drc_report())
        return res
    if not have_drc:
        res.summary = "本工具没有解析出 DRC 明细; 以下条目来自用户导入的报告"
        res.need("Allegro DRC 报告（含类别/层/位置/实测值/规则值）", ctx.howto_drc_report())
        res.warn("以下 DRC 明细来自用户导入的报告, 不是解析器识别的 DRC 报告格式")

    notes = [n for n in (note,) if n]
    if have_drc and drc.get("by_class"):
        notes.append("按类别汇总: " + "、".join(f"{k} {v}" for k, v in
                                            (drc.get("by_class") or {}).items()))
    return _emit(res, ctx, "DRC Report", "设计规则错误", cols, rows, "；".join(notes))


# ==========================================================================
# 5.10 Constraint Report
# ==========================================================================
_CONSTRAINT_COLUMNS = ["类别", "约束集", "约束", "值", "单位", "出处"]
# 约束名 -> design['sources'] 的键 (只有全局汇总值有出处)
_CONSTRAINT_SOURCE_KEY = {
    "min_line_width_mm": "min_line_width_mm",
    "min_neck_width_mm": "min_neck_width_mm",
    "LINE_TO_LINE_SPACING": "min_spacing_mm",
    "LINE_TO_THRUVIA_SPACING": "min_spacing_mm",
}


def cap_constraint_report(ctx: Context) -> Result:
    cap = CATALOG_MAP["report.constraint"]
    source_map = ctx.design.get("sources") or {}
    cols = list(_CONSTRAINT_COLUMNS)
    rows: List[dict] = []
    missing_values = 0
    for row in facts.constraint_rows(ctx):
        raw = row.get("值")
        if raw is None:
            value_text = "未取值"
            missing_values += 1
        elif isinstance(raw, (list, tuple, set)):
            value_text = "、".join(_fmt(v) for v in raw)
        else:
            value_text = _fmt(raw)
        constraint = str(row.get("约束") or "")
        key = _CONSTRAINT_SOURCE_KEY.get(constraint)
        entry = source_map.get(key) if (key and isinstance(source_map, dict)) else None
        if isinstance(entry, dict) and entry.get("source"):
            origin = (f"{entry['source']}"
                      + (f"（置信度 {entry['confidence']}）" if entry.get("confidence") else ""))
        else:
            origin = f"{row.get('类别')}CSet.xml 归档对象"
        rows.append({"类别": row.get("类别"), "约束集": row.get("约束集"),
                     "约束": constraint, "值": value_text,
                     "单位": row.get("单位") or "", "出处": origin})

    n_phys = len(ctx.design.get("physical_csets") or {})
    n_space = len(ctx.design.get("spacing_csets") or {})
    has_native = bool(rows)
    res = Result(cap, INFO, "")
    if has_native:
        res.metric("PHYSICAL 约束集", n_phys, "个", "objects/PhysicalCSet.xml")
        res.metric("SPACING 约束集", n_space, "个", "objects/SpacingCSet.xml")
        res.metric("约束条目", len(rows), "条", "约束集摊平")
        if missing_values:
            res.metric("取不到值的约束", missing_values, "条",
                       "对象里该约束没有数值, 已如实写「未取值」")
            res.status = WARN
            res.warn(f"有 {missing_values} 条约束在 .brd 对象里没有数值, "
                     f"已写「未取值」而不是当成 0")
        res.summary = (f"{n_phys} 个 Physical / {n_space} 个 Spacing 约束集, "
                       f"摊平 {len(rows)} 条约束"
                       + (f"（{missing_values} 条无值）" if missing_values else ""))
        res.source("objects/PhysicalCSet.xml + objects/SpacingCSet.xml + design['sources']")

    rows, note = _merge_into(res, ctx, ("CONSTRAINT REPORT", "CONSTRAINTS",
                                        "PHYSICAL CSET", "SPACING CSET"),
                             ("CONSTRAINT", "SPACING", "PHYSICAL"), cols, rows,
                             own_titles=("CONSTRAINT REPORT", "CONSTRAINTS",
                                         "PHYSICAL CSET", "SPACING CSET"))
    if not rows:
        res = unavailable(cap, "BRD 归档里没有 PhysicalCSet.xml / SpacingCSet.xml, "
                              "读不到任何约束",
                          "确认 .brd 是 17.x 及以后版本（16.x 纯二进制不带约束对象）")
        res.need("约束对象（PhysicalCSet / SpacingCSet）",
                 "确认 .brd 是 17.x 及以后版本; 或提供 Allegro 约束报告")
        return res
    if not has_native:
        res.need("约束对象（PhysicalCSet / SpacingCSet）",
                 "确认 .brd 是 17.x 及以后版本")
        res.summary = "本工具没读到约束对象; 以下约束明细来自用户导入的报告"
        res.warn("约束明细不是从 .brd 归档直读, 以导入报告为准")
    return _emit(res, ctx, "Constraint Report", "约束信息", cols, rows, note)


# ==========================================================================
# 5.11 Testpoint Report
# ==========================================================================
_TP_IPC_COLUMNS = ["序号", "网络", "类型", "位号", "坐标 / 原始记录"]
_TP_PAD_COLUMNS = ["项目", "值", "来源"]


def cap_testpoint_report(ctx: Context) -> Result:
    cap = CATALOG_MAP["report.testpoint"]
    ipc = ctx.exports.get("ipc356")
    rows: List[dict] = []
    cols: List[str] = []
    has_native = False
    res = Result(cap, INFO, "")

    if ipc and ipc.get("ok"):
        # 首选: 用户导入的 IPC-D-356 (测试厂实际使用的测试点表)
        cols = list(_TP_IPC_COLUMNS)
        points = ipc.get("points") or []
        for idx, point in enumerate(points, start=1):
            rows.append({"序号": idx, "网络": point.get("net"),
                         "类型": ("连通性测试点 (327)"
                                 if point.get("type") == "continuity"
                                 else "绝缘测试点 (367)"),
                         "位号": point.get("refdes"),
                         "坐标 / 原始记录": point.get("tail") or ""})
        if not points:
            rows.append({"序号": "", "网络": "", "类型": "",
                         "位号": "", "坐标 / 原始记录":
                         f"IPC-356 里只有网络定义, 没有 327/367 测试点记录"
                         f"（{ipc.get('source')}）"})
        has_native = True
        res.metric("测试点数", ipc.get("point_count"), "个",
                   f"导入的 IPC-356 ({ipc.get('source')})", "high")
        res.metric("IPC-356 网络数", ipc.get("count"), "个", "IPC-356 的 317 记录")
        res.summary = (f"IPC-356 共 {ipc.get('point_count')} 个测试点、"
                       f"{ipc.get('count')} 个网络（{ipc.get('source')}）")
        res.source(f"导入的 IPC-D-356 网表测试文件: {ipc.get('source')}")
    else:
        # 退路: 内嵌快照里的钢网层圆焊盘 (src.pads 的口径)
        pads_info: dict = {"ok": False, "reason": "未尝试"}
        tp: dict = {"ok": False, "count": 0}
        try:
            from src.pads import detect_test_points, extract_pads

            pads_info = extract_pads(ctx.snapshot, ctx.scale_mm_per_unit)
            tp = detect_test_points(pads_info)
        except Exception as exc:  # noqa: BLE001  焊盘解析失败不能拖垮报告
            pads_info = {"ok": False, "reason": f"焊盘提取失败: {exc}"}
            tp = {"ok": False, "count": 0, "reason": str(exc)}
        if pads_info.get("ok"):
            cols = list(_TP_PAD_COLUMNS)
            rows = [
                {"项目": "测试点数量", "值": tp.get("count"),
                 "来源": tp.get("note") or "钢网层圆形焊盘"},
                {"项目": "焊盘总数", "值": pads_info.get("pads"),
                 "来源": pads_info.get("source", "")},
                {"项目": "钢网层焊盘数", "值": pads_info.get("stencil_pads"),
                 "来源": "class/subclass 含 PASTEMASK/PASTE/PMASK"},
                {"项目": "最小焊盘直径mm", "值": pads_info.get("min_d_mm"),
                 "来源": pads_info.get("source", "")},
                {"项目": "最大焊盘直径mm", "值": pads_info.get("max_d_mm"),
                 "来源": pads_info.get("source", "")},
                {"项目": "焊盘所在层", "值": "、".join(pads_info.get("layers") or []),
                 "来源": "内嵌快照焊盘图形"},
            ]
            has_native = True
            res.metric("测试点数", tp.get("count"), "个", "钢网层圆形焊盘", "medium")
            res.metric("最小焊盘直径", pads_info.get("min_d_mm"), "mm",
                       "内嵌快照焊盘图形", "medium")
            res.summary = (f"按钢网层圆焊盘识别出 {tp.get('count')} 个测试点, "
                           f"最小焊盘直径 {pads_info.get('min_d_mm')}mm")
            res.source(pads_info.get("source", "内嵌快照焊盘图形"))
            res.warn("快照焊盘图形不保证覆盖全部钢网开窗; 精确测试点表请用 IPC-356")

    rows, note = _merge_into(res, ctx, ("TESTPOINT", "TEST POINT", "IPC-356", "IPC356"),
                             ("TESTPOINT", "TEST POINT"), cols, rows,
                             own_titles=("TESTPOINT REPORT", "TEST POINT REPORT",
                                         "TESTPOINT", "TEST POINT", "IPC-356"))
    if not rows:
        res = unavailable(
            cap, "测试点要钢网层(PASTEMASK)圆焊盘或 IPC-356 测试网表, 两者都没有",
            "Allegro: File > Export > IPC-D-356 测试网表 (.ipc); "
            "或提供含 PASTEMASK 圆焊盘的 Gerber / 焊盘坐标文件")
        res.need("测试点数据（钢网层圆焊盘或 IPC-356）",
                 "Allegro: File > Export > IPC-D-356 测试网表; "
                 "或提供含 PASTEMASK 圆焊盘的 Gerber / 焊盘坐标")
        return res
    if not has_native:
        res.need("测试点数据（钢网层圆焊盘或 IPC-356）",
                 "Allegro: File > Export > IPC-D-356 测试网表 (.ipc)")
        res.summary = "本工具没读到测试点数据; 以下明细来自用户导入的报告"
        res.warn("测试点明细不是本工具识别的数据源, 以导入报告为准")
    return _emit(res, ctx, "Testpoint Report", "测试点信息", cols, rows, note)


# ==========================================================================
CATALOG: List[Capability] = [
    Capability("report.board", "reports", "Board Report", "板级基本信息",
               "设计名/文件/层数/网络数/器件数/板尺寸/单位精度/钻孔跨度/表面处理/板厚/约束集",
               cap_board_report, "brd",
               ["BRD 归档对象", "二进制元数据", "内嵌快照板框"],
               ["RPT/CSV/HTML 板级信息表"]),
    Capability("report.component", "reports", "Component Report", "元件数量与位号",
               "器件总数、逐位号清单、按前缀与类别的汇总", cap_component_report, "brd",
               ["objects/Part.xml"], ["RPT/CSV/HTML 器件清单"]),
    Capability("report.pin", "reports", "Pin Report", "引脚信息",
               "每个网络的引脚数与引脚列表; 引脚级数据只存在于网表", cap_pin_report,
               "export", ["导入的网表 (pstxnet.dat)"], ["RPT/CSV/HTML 引脚明细"]),
    Capability("report.net", "reports", "Net Report", "网络名称与连接",
               "网络清单、电源/地、差分对; 有网表时给出每个网络的引脚数",
               cap_net_report, "brd", ["objects/Net.xml", "objects/DiffPair.xml",
                                       "导入的网表"],
               ["RPT/CSV/HTML 网络清单"]),
    Capability("report.via", "reports", "Via Report", "过孔数量与位置",
               "焊盘栈明细、钻孔孔径分布、孔位坐标", cap_via_report, "brd",
               ["PhysicalCSet VIA_LIST", "二进制钻孔表", "导入的钻孔/坐标文件"],
               ["RPT/CSV/HTML 过孔与孔位表"]),
    Capability("report.etch", "reports", "Etch Report", "走线信息",
               "每层铜箔图形数/走线总长/线宽分布 + 约束里的最小线宽线距",
               cap_etch_report, "snapshot", ["内嵌快照 ETCH 图形", "PhysicalCSet/SpacingCSet"],
               ["RPT/CSV/HTML 每层走线统计"]),
    Capability("report.layer", "reports", "Layer Report", "层信息",
               "层号/层名/用途/是否有铜箔图形", cap_layer_report, "brd",
               ["预览元数据", "objects/SubClass.xml", "导入的叠层报告"],
               ["RPT/CSV/HTML 层表"]),
    Capability("report.unconnected", "reports", "Unconnected Report", "未连接网络",
               "未连接/飞线条目; 只能来自导入的 DRC 或 Unconnected 报告",
               cap_unconnected_report, "export",
               ["Allegro DRC / Unconnected Pins 报告"], ["RPT/CSV/HTML 未连接明细"]),
    Capability("report.drc", "reports", "DRC Report", "设计规则错误",
               "全部 DRC 违规条目与按类别汇总; 需要导入 Allegro DRC 报告",
               cap_drc_report, "export", ["Allegro DRC 报告"],
               ["RPT/CSV/HTML 违规明细与类别汇总"]),
    Capability("report.constraint", "reports", "Constraint Report", "约束信息",
               "PhysicalCSet/SpacingCSet 摊平 + 取值出处", cap_constraint_report, "brd",
               ["objects/PhysicalCSet.xml", "objects/SpacingCSet.xml"],
               ["RPT/CSV/HTML 约束清单"]),
    Capability("report.testpoint", "reports", "Testpoint Report", "测试点信息",
               "IPC-356 测试点清单; 或钢网层圆焊盘识别的测试点数量与最小焊盘直径",
               cap_testpoint_report, "export",
               ["导入的 IPC-356", "内嵌快照钢网层焊盘"],
               ["RPT/CSV/HTML 测试点清单"]),
]
CATALOG_MAP: Dict[str, Capability] = {c.id: c for c in CATALOG}

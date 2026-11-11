"""Checklist 模板识别: 不依赖固定行列, 换模板也能读.

老做法把列号写死 (第 3 列序号 / 第 5 列条目 ...), 模板一改就错位。
这里改成:
    1. 先在前 N 行里找"表头行" —— 同一行能匹配到最多的已知列名;
    2. 再按列名别名(类别/编号/条目内容...)确定每一列的位置;
    3. 实在找不到表头, 才退回旧模板的固定列, 并给出警告。

列名别名与表头扫描范围都在 config/checklist_auto_rules.yaml 里, 不用改代码。
"""
import os
import re
import unicodedata

import openpyxl

# 配置随程序走: 源码运行时是 <项目根>/config, 打包后是 <_MEIPASS>/config
DEFAULT_CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "checklist_auto_rules.yaml")

# 旧模板 (Layout_Checklist_Rev1.0) 的固定列, 仅作兜底
LEGACY_COLUMNS = {
    "category": 1, "subcategory": 2, "no": 3, "grade": 4, "items": 5,
    "standard": 6, "note": 7, "check": 8, "approved": 9,
}
LEGACY_HEADER_ROW = 3

# 分配列时的优先级: 越具体的先分, 避免 "检查" 抢走 "检查项"
COLUMN_PRIORITY = ["items", "standard", "note", "check", "approved",
                   "no", "grade", "category", "subcategory"]

BUILTIN_COLUMN_ALIASES = {
    "category": ["类别", "category", "项目", "阶段"],
    "subcategory": ["子类别", "sub category", "子项", "分项"],
    "no": ["编号", "no.", "no", "序号", "项次"],
    "grade": ["级别", "grade", "等级", "重要度"],
    "items": ["条目内容", "items", "item", "检查项", "检查内容", "条目"],
    "standard": ["符合标准", "meeting standards", "meeting", "标准"],
    "note": ["原因说明", "note", "备注", "说明", "原因"],
    "check": ["检查", "check", "检查结果", "结果"],
    "approved": ["签核", "approved", "确认", "审批"],
}


def normalize(text) -> str:
    """统一大小写/全半角, 去掉所有空白, 便于比较表头与条目文字。"""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    s = s.replace("\u3000", " ").lower()
    return re.sub(r"\s+", "", s)


def config_candidates() -> list:
    """规则库候选路径, 先找用户可改的外部配置, 再找打包内的默认配置。

    打包成 exe 后, 把 config\\checklist_auto_rules.yaml 放到 exe 旁边即可覆盖内置规则,
    不用重新打包 (站点自己加判定规则时用得上)。
    """
    import sys

    bases = []
    if getattr(sys, "frozen", False):
        bases.append(os.path.dirname(os.path.abspath(sys.executable)))
        if getattr(sys, "_MEIPASS", None):
            bases.append(sys._MEIPASS)
    else:
        bases.append(os.getcwd())
        bases.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return [os.path.join(b, "config", "checklist_auto_rules.yaml") for b in bases]


def default_config_path() -> str:
    for candidate in config_candidates():
        if os.path.exists(candidate):
            return candidate
    return DEFAULT_CONFIG


def load_config(path: str = None) -> dict:
    """读 rules yaml; 失败时返回内置默认 (保证界面不因配置缺失报错)。"""
    import yaml

    conf_path = path or default_config_path()
    conf = {}
    if os.path.exists(conf_path):
        try:
            # utf-8-sig: 规则库可能被人用记事本/Excel 导出, 带 BOM 也要能读
            with open(conf_path, "r", encoding="utf-8-sig") as f:
                conf = yaml.safe_load(f) or {}
        except Exception:  # noqa: BLE001  配置写错也不能让界面崩
            conf = {}
    tmpl = conf.get("template") or {}
    columns = dict(BUILTIN_COLUMN_ALIASES)
    for key, aliases in (tmpl.get("columns") or {}).items():
        # yaml 里裸写的 no/yes/on/off 会被解析成布尔, 这里统一转回字符串
        key = str(key).lower()
        if isinstance(aliases, list) and aliases:
            columns[key] = [str(a) for a in aliases]
    return {
        "raw": conf,
        "config_path": conf_path,
        "rules": conf.get("rules") or [],
        "sheet_candidates": [str(s).lower() for s in
                             (tmpl.get("sheet_candidates") or ["check list", "checklist"])],
        "header_scan_rows": int(tmpl.get("header_scan_rows") or 15),
        "columns": columns,
        "required": [str(k) for k in (tmpl.get("required") or ["no", "items", "check"])],
    }


def detect_sheet(wb, candidates):
    lowered = {name.lower(): name for name in wb.sheetnames}
    for cand in candidates:
        if cand in lowered:
            return lowered[cand], None
    for cand in candidates:
        for low, real in lowered.items():
            if cand in low:
                return real, None
    return wb.sheetnames[-1], f"未找到常见的工作表名, 改用最后一个: {wb.sheetnames[-1]}"


def _score_header(text, aliases) -> int:
    t = normalize(text)
    if not t:
        return 0
    best = 0
    for alias in aliases:
        a = normalize(alias)
        if not a:
            continue
        if t == a:
            return 100 + len(a)
        if t.startswith(a):
            best = max(best, 50 + len(a))
        elif a in t:
            best = max(best, 10 + len(a))
    return best


def _map_columns(ws, header_row: int, columns_cfg: dict) -> tuple:
    """返回 ({logical: col}, {logical: 表头原文}, 总分)。一列只分配给一个逻辑列。"""
    max_col = min(ws.max_column or 1, 60)
    candidates = []
    for logical in COLUMN_PRIORITY:
        aliases = columns_cfg.get(logical) or []
        for col in range(1, max_col + 1):
            text = ws.cell(header_row, col).value
            score = _score_header(text, aliases)
            if score:
                candidates.append((score, logical, col, str(text or "").split("\n")[0].strip()))
    candidates.sort(key=lambda item: -item[0])

    mapping, headers, used_cols, used_logical = {}, {}, set(), set()
    for score, logical, col, text in candidates:
        if logical in used_logical or col in used_cols:
            continue
        mapping[logical] = col
        headers[logical] = text
        used_cols.add(col)
        used_logical.add(logical)
    total = sum(score for score, logical, col, _ in candidates
                if mapping.get(logical) == col)
    return mapping, headers, total


def detect_layout(ws, cfg: dict = None) -> dict:
    """识别表头行与列位置; 失败时退回旧模板固定列。"""
    cfg = cfg or load_config()
    scan = max(1, min(cfg["header_scan_rows"], ws.max_row or 1))
    best = {"row": None, "mapping": {}, "headers": {}, "score": 0}
    for row in range(1, scan + 1):
        mapping, headers, score = _map_columns(ws, row, cfg["columns"])
        if len(mapping) > len(best["mapping"]) or (
                len(mapping) == len(best["mapping"]) and score > best["score"]):
            best = {"row": row, "mapping": mapping, "headers": headers, "score": score}

    missing = [key for key in cfg["required"] if key not in best["mapping"]]
    if len(best["mapping"]) >= 3 and not missing:
        return {"header_row": best["row"], "columns": best["mapping"],
                "headers": best["headers"], "method": "表头识别", "warnings": []}

    warnings = [f"表头识别失败 (仅识别到 {sorted(best['mapping'])} ) , 已按旧模板固定列读取"]
    if missing:
        warnings.append(f"缺少必需列: {missing}")
    return {"header_row": LEGACY_HEADER_ROW, "columns": dict(LEGACY_COLUMNS),
            "headers": {}, "method": "固定列兜底", "warnings": warnings}


def _as_int(value):
    """把 '12' / 12.0 / 'No.12' / '12.' 统一转成 int, 失败返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    match = re.search(r"-?\d+", text)
    if not match:
        return None
    try:
        return int(match.group())
    except ValueError:
        return None


def _cell_text(ws, row: int, col) -> str:
    if not col:
        return ""
    value = ws.cell(row, col).value
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_items(ws, layout: dict, cfg: dict = None) -> list:
    """按识别到的列读取条目; 类别/子类别做向下填充 (合并单元格只有首行有值)。"""
    cfg = cfg or load_config()
    cols = layout["columns"]
    items = []
    category = subcategory = ""

    for row in range(layout["header_row"] + 1, (ws.max_row or 0) + 1):
        cat_text = _cell_text(ws, row, cols.get("category"))
        if cat_text:
            category = cat_text
        sub_text = _cell_text(ws, row, cols.get("subcategory"))
        if sub_text:
            subcategory = sub_text

        item_text = _cell_text(ws, row, cols.get("items"))
        no_text = _cell_text(ws, row, cols.get("no"))
        no = _as_int(no_text)

        # 既没有序号也没有条目内容 -> 空行或分节行, 跳过
        if not item_text and no is None:
            continue
        if not item_text:
            continue

        grade = _cell_text(ws, row, cols.get("grade"))
        grade_type = "mandatory"
        if "推荐" in grade or "recommend" in grade.lower():
            grade_type = "recommend"
        elif "提示" in grade or "remind" in grade.lower():
            grade_type = "remind"

        items.append({
            "row": row,
            "no": no,
            "no_text": no_text,
            "category": category,
            "subcategory": subcategory,
            "grade": grade,
            "grade_type": grade_type,
            "item": item_text,
            "standard": _cell_text(ws, row, cols.get("standard")),
            "note": _cell_text(ws, row, cols.get("note")),
            "check": _cell_text(ws, row, cols.get("check")),
            "approved": _cell_text(ws, row, cols.get("approved")),
        })
    return items


def read_template(path: str, cfg: dict = None) -> dict:
    """读取 checklist 模板: 结构 + 条目 + 识别警告。"""
    cfg = cfg or load_config()
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        sheet_name, sheet_warn = detect_sheet(wb, cfg["sheet_candidates"])
        ws = wb[sheet_name]
        layout = detect_layout(ws, cfg)
        items = read_items(ws, layout, cfg)
    finally:
        wb.close()

    warnings = list(layout["warnings"])
    if sheet_warn:
        warnings.append(sheet_warn)
    if not items:
        warnings.append("没有读到任何检查项: 请确认表头行与列的别名配置")

    numbers = [it["no"] for it in items if it["no"] is not None]
    return {
        "path": path,
        "config_path": cfg.get("config_path", ""),
        "rule_count": len(cfg.get("rules") or []),
        "sheet": sheet_name,
        "header_row": layout["header_row"],
        "columns": layout["columns"],
        "column_headers": layout["headers"],
        "method": layout["method"],
        "items": items,
        "count": len(items),
        "numbered": len(numbers),
        "warnings": warnings,
    }


DEMO_ITEMS = [
    ("输入阶段", "规定", "在流程上接收到的资料是否齐全（原理图、brd、叠层文件）"),
    ("输入阶段", "规定", "确认所有器件封装是否正确, 元器件是否 100% 放置"),
    ("布线", "规定", "最小线宽是否满足板厂制程能力"),
    ("布线", "规定", "过孔孔径不小于板厚的 1/10（aspect ratio）"),
    ("布线", "推荐", "过孔的排列不宜太密，避免电源/地平面大范围断裂"),
    ("约束", "规定", "是否所有仿真约束都已经正确加到 Constraint Manager 中"),
    ("约束", "规定", "是否正确设置物理和电气规则（电源网络和地网络）"),
    ("电源", "规定", "为降低边缘辐射，电源层与地层是否满足 20H 原则"),
    ("测试", "规定", "测试点直径大于等于 0.8mm"),
    ("测试", "提示", "测试点边缘距离板边缘最少 2.5mm 以上"),
    ("丝印", "推荐", "PCB 版本号、料号与丝印内容是否一致"),
    ("DRC", "规定", "确认 DRC 已经调整到最少，不能消除的要一一确认"),
]


def create_demo_template(path: str) -> str:
    """生成一份演示用 checklist (结构与真实模板一致), 供"演示运行"使用。"""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "check list"
    header_fill = PatternFill("solid", start_color="2F5496")
    header_font = Font(color="FFFFFF", bold=True, name="Microsoft YaHei")
    thin = Border(*[Side(style="thin")] * 4)
    headers = ["类别 Category", "子类", "编号 No.", "级别 Grade", "条目内容 Items",
               "符合标准 Meeting", "原因说明 Note", "检查 Check", "签核 Approved"]
    for col, text in enumerate(headers, 1):
        cell = ws.cell(1, col, text)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin
    for idx, (cat, grade, item) in enumerate(DEMO_ITEMS, 1):
        row = idx + 1
        ws.cell(row, 1, cat if idx == 1 or DEMO_ITEMS[idx - 2][0] != cat else None)
        ws.cell(row, 3, idx)
        ws.cell(row, 4, grade)
        ws.cell(row, 5, item)
        for col in range(1, 10):
            ws.cell(row, col).border = thin
            ws.cell(row, col).alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[row].height = 30
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["E"].width = 58
    ws.column_dimensions["G"].width = 34
    ws.column_dimensions["H"].width = 12
    ws.column_dimensions["I"].width = 16

    summary = wb.create_sheet("summary")
    summary["A1"] = "演示 Checklist (自动生成)"
    summary["A2"] = "用途: 演示 BRD → Checklist 自动填写流程"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    wb.save(path)
    wb.close()
    return path

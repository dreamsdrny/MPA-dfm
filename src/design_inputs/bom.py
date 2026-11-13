"""BOM 合同 v1: 一行一个位号, UTF-8 CSV 或 Excel (.xlsx), 同一套列别名与校验。

合同字段见 ``FIELDS``。ERP/PLM/Cadence 导出的表头通过 ``ALIASES`` 归一化,
例如 ``Part Reference`` -> refdes, ``PCB Footprint`` -> footprint, ``IPN`` -> part_number。

Excel 会先在表头区(前 60 行)找"含位号列"的那一行, 再逐行读取 —— Cadence 的
``Bill Of Materials`` 报表前面常有修订号、页码、横线分隔行, 也能直接导入。
CSV 保持严格口径(数量不符/位号重复直接报错); Excel 按真实表格的容忍度处理,
把数量不符、空位号、重复位号、未展开的位号范围收进 ``warnings`` 供核对报告使用。
"""
import csv
import io
import os
import re

FIELDS = ("refdes", "part_number", "value", "footprint", "description", "manufacturer",
          "manufacturer_part_number", "side", "dnp")
CONTRACT = "bom-csv-v1"

# 归一化后(小写/空白合并)的表头别名 -> 合同字段
ALIASES = {
    # 位号
    "refdes": "refdes", "ref des": "refdes", "ref": "refdes",
    "reference": "refdes", "references": "refdes",
    "reference designator": "refdes", "reference designators": "refdes",
    "designator": "refdes", "designators": "refdes",
    "part reference": "refdes", "part references": "refdes", "part ref": "refdes",
    "component": "refdes", "component name": "refdes", "symbol": "refdes",
    "位号": "refdes", "元件位号": "refdes", "元件编号": "refdes", "器件位号": "refdes",
    # 料号
    "part number": "part_number", "partnumber": "part_number", "part no": "part_number",
    "pn": "part_number", "ipn": "part_number", "internal part number": "part_number",
    "item part number": "part_number",
    "料号": "part_number", "物料编码": "part_number", "物料号": "part_number",
    "内部料号": "part_number", "零件号": "part_number", "物料编号": "part_number",
    # 值
    "value": "value", "val": "value", "参数值": "value", "规格值": "value", "值": "value",
    # 封装
    "footprint": "footprint", "pcb footprint": "footprint", "package": "footprint",
    "package name": "footprint", "封装": "footprint", "封装名": "footprint",
    "pcb封装": "footprint",
    # 厂商料号
    "mpn": "manufacturer_part_number", "mfr part number": "manufacturer_part_number",
    "mfr pn": "manufacturer_part_number", "manufacturer pn": "manufacturer_part_number",
    "manufacturer part number": "manufacturer_part_number",
    "厂商料号": "manufacturer_part_number", "制造商料号": "manufacturer_part_number",
    "规格型号": "manufacturer_part_number",
    # 厂商
    "manufacturer": "manufacturer", "mfr": "manufacturer", "vendor": "manufacturer",
    "厂商": "manufacturer", "制造商": "manufacturer", "品牌": "manufacturer",
    # 描述
    "description": "description", "desc": "description", "描述": "description",
    "说明": "description", "物料描述": "description",
    # 面别
    "side": "side", "layer": "side", "board side": "side", "面别": "side", "面": "side",
    "贴装面": "side",
    # 不贴
    "dnp": "dnp", "do not populate": "dnp", "do not fit": "dnp", "not fitted": "dnp",
    "no fit": "dnp", "不贴": "dnp", "不焊接": "dnp", "不上件": "dnp",
    # 数量
    "quantity": "quantity", "qty": "quantity", "count": "quantity", "数量": "quantity",
    "用量": "quantity",
}

# 已知但不需要保留的列: 不算"未知列", 避免报告里出现一堆无关提示
IGNORED = {
    "item", "item no", "no", "序号", "schematic page", "page", "sheet", "图纸",
    "原理图页", "remark", "remarks", "备注", "unit", "单位", "rohs", "packing",
    "包装", "供货形式", "designator prefix",
}

# 一个单元格里用逗号/分号/斜杠/竖线/空白列出多个位号
_REF_SPLIT_RE = re.compile(r"[,;/|\s]+")
# R1-R4 这类范围写法: 合同不展开, 报告里提示人工确认
_REF_RANGE_RE = re.compile(r"^[A-Za-z]{1,4}\d{1,5}\s*[-~]\s*[A-Za-z]{0,4}\d{1,5}$")
_TRIM_RE = re.compile(r"[\s_\-./()（）:：]+")


def _key(raw) -> str:
    """表头归一化: 去空白/下划线/标点, 合并空格, 转小写。"""
    return _TRIM_RE.sub(" ", str(raw or "")).strip().lower()


def _cell_text(value) -> str:
    """单元格 -> 文本; Excel 里数量常是 1.0, 转成 '1' 而不是 '1.0'。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Y" if value else "N"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _normalize_side(value):
    up = str(value or "").strip().upper()
    if up in ("T", "TOP", "TOP SIDE", "TOP_LAYER", "顶面"):
        return "TOP"
    if up in ("B", "BOTTOM", "BOT SIDE", "BOTTOM_LAYER", "底面"):
        return "BOTTOM"
    return up


def _normalize_dnp(value):
    if isinstance(value, bool):
        return value
    up = str(value or "").strip().upper()
    if up in ("1", "Y", "YES", "TRUE", "DNP", "不贴", "是"):
        return True
    if up in ("0", "N", "NO", "FALSE", "", "贴", "否"):
        return False
    return up


def _parse_quantity(text):
    """数量列 -> int; 空的或不是数字返回 None (由上层决定报错还是提示)。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return int(value) if value.is_integer() else None


def _split_refs(text):
    return [ref for ref in (part.strip() for part in _REF_SPLIT_RE.split(str(text or "")))
            if ref]


def _column_map(header_cells):
    """表头 -> ({列序号: 字段}, [未识别列原文], {原文: 字段})。"""
    mapping, unknown, columns = {}, [], {}
    for index, raw in enumerate(header_cells):
        text = _cell_text(raw)
        if not text:
            continue
        name = ALIASES.get(_key(text))
        if name is None:
            if _key(text) not in IGNORED:
                unknown.append(text)
            continue
        columns[text] = name
        if name not in mapping.values():
            mapping[index] = name
    return mapping, unknown, columns


def _detect_header(matrix, scan=60, min_score=1):
    """在表头区找最像表头的一行: 必须含位号列, 取识别字段最多的一行。"""
    best = None
    for index, cells in enumerate(matrix[:scan]):
        if not cells:
            continue
        mapping, unknown, columns = _column_map(cells)
        fields = set(mapping.values())
        if "refdes" not in fields or len(fields) < min_score:
            continue
        if best is None or len(fields) > len(best[1]):
            best = (index, mapping, unknown, columns)
    return best


def _cell(matrix, row_index, col_index):
    row = matrix[row_index]
    return _cell_text(row[col_index]) if col_index < len(row) else ""


def _extract_rows(matrix, header_index, mapping, strict, warnings, stats):
    """按表头映射把数据行展开成"一行一个位号"。"""
    by_field = {}
    for index, name in mapping.items():
        by_field.setdefault(name, index)
    rows, seen = [], set()
    range_seen = []
    for offset in range(header_index + 1, len(matrix)):
        cells = matrix[offset]
        if not cells or not any(_cell_text(cell) for cell in cells):
            stats["skipped_empty"] += 1
            continue
        values = {field: _cell(matrix, offset, index)
                  for field, index in by_field.items()}

        ref_cell = values.get("refdes", "")
        refs = _split_refs(ref_cell)
        if not refs:
            stats["skipped_no_refdes"] += 1
            if ref_cell:
                warnings.append(f"第 {offset + 1} 行位号为空, 已跳过")
            continue
        if any(_REF_RANGE_RE.match(ref) for ref in refs):
            range_seen.extend(ref for ref in refs if _REF_RANGE_RE.match(ref))
        quantity = _parse_quantity(values.get("quantity", ""))
        if quantity is not None and quantity != len(refs):
            message = f"第 {offset + 1} 行数量 {quantity} 与位号数 {len(refs)} 不一致"
            if strict:
                raise ValueError("BOM " + message + " (" + ref_cell + ")")
            warnings.append(message + ", 按位号列展开")
        for ref in refs:
            if ref.upper() in seen:
                message = f"第 {offset + 1} 行位号重复: {ref}"
                if strict:
                    raise ValueError("BOM 位号重复: " + ref)
                warnings.append(message + ", 已保留首次出现")
                stats.setdefault("duplicate_refdes", []).append(ref)
                continue
            seen.add(ref.upper())
            row = {key: (ref if key == "refdes" else values.get(key, "")) for key in FIELDS}
            row["side"] = _normalize_side(row["side"])
            row["dnp"] = _normalize_dnp(row["dnp"])
            rows.append(row)
    if range_seen:
        warnings.append("位号范围写法未展开(按单个位号处理): " + "、".join(range_seen[:5]))
    stats["refdes_count"] = len(rows)
    return rows


def _xlsx_matrix(path):
    """读 Excel 的所有工作表, 返回 [(工作表名, 矩阵)]。"""
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - 打包版已内置 openpyxl
        raise ValueError("读取 .xlsx 需要 openpyxl 库: " + str(exc)) from exc
    try:
        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:  # 坏文件/加密/非 zip 都归为可读的错误信息
        raise ValueError(f"无法打开 Excel ({os.path.basename(os.fspath(path))}): {exc}") from exc
    sheets = []
    try:
        for sheet in workbook.worksheets:
            matrix = [[cell for cell in row] for row in sheet.iter_rows(values_only=True)]
            sheets.append((sheet.title, matrix))
    finally:
        workbook.close()
    return sheets


def _csv_matrix(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        text = handle.read()
    sample = text[:4096]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else (
        "," if sample.count(",") >= sample.count(";") else ";")
    return [[cell for cell in row]
            for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def read_bom(path, strict=None):
    """读 BOM, 返回行、表头识别结果与数据质量提示。

    返回 ``{"contract", "format", "sheet", "header_row", "rows", "warnings",
    "columns", "unmapped", "stats", "part_numbers"}``;
    完全读不出位号时抛 ``ValueError``。``strict`` 为 None 时按格式取默认口径:
    CSV 严格(数量不符/位号重复报错), Excel 宽容(收进 warnings), 因为真实 Excel
    BOM 常带分隔行、汇总行和少量笔误, 这些正是核对要报告的内容。
    """
    target = os.fspath(path)
    suffix = os.path.splitext(target)[1].lower()
    warnings, stats = [], {"skipped_empty": 0, "skipped_no_refdes": 0,
                           "refdes_count": 0, "header_row": None}
    best = None
    if suffix in (".xlsx", ".xlsm"):
        for sheet_name, matrix in _xlsx_matrix(target):
            found = _detect_header(matrix, min_score=2)
            if found and (best is None or len(found[1]) > len(best[1][1])):
                best = (found, matrix, "xlsx", sheet_name)
    elif suffix in (".csv", ".tsv", ".txt"):
        matrix = _csv_matrix(target)
        found = _detect_header(matrix, min_score=1)
        if found:
            best = (found, matrix, "csv", "")
    else:
        raise ValueError("不支持的 BOM 格式: " + (suffix or target) + " (支持 .csv / .xlsx)")

    if not best:
        raise ValueError("BOM 必须包含 refdes/Reference/Designator/Part Reference/位号列")
    (header_index, mapping, unknown, columns), matrix, fmt, sheet = best
    stats["header_row"] = header_index + 1
    strict = (fmt == "csv") if strict is None else bool(strict)
    rows = _extract_rows(matrix, header_index, mapping, strict, warnings, stats)
    if not rows:
        raise ValueError("BOM 没有读到任何位号 (表头行 " + str(header_index + 1) + ")")
    part_numbers = sorted({str(row.get("part_number") or "").strip().upper()
                           for row in rows if str(row.get("part_number") or "").strip()})
    return {
        "contract": CONTRACT, "format": fmt, "sheet": sheet,
        "header_row": header_index + 1, "rows": rows, "warnings": warnings,
        "columns": columns, "unmapped": unknown, "stats": stats,
        "part_numbers": part_numbers,
    }


def import_bom(path, strict=None):
    """读取 BOM 行 (兼容旧接口); 需要质量提示时用 ``read_bom``。"""
    return read_bom(path, strict=strict)["rows"]


def export_bom(path, rows):
    rows = list(rows)
    seen = set()
    for row in rows:
        ref = str(row.get("refdes") or "").strip()
        if not ref or "," in ref or ";" in ref:
            raise ValueError("导出 BOM 的每行必须包含且仅包含一个位号")
        if ref.upper() in seen:
            raise ValueError("BOM 位号重复: " + ref)
        seen.add(ref.upper())
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

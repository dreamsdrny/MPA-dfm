"""
Allegro .brd 设计数据提取 (Evan_BRD)
=====================================================
Allegro 17.x 之后的 .brd 不是纯二进制, 而是「二进制体 + 若干 zip 归档」的容器。
zip 归档里用 XML 描述设计对象, 可以直接读出 DFM 需要的设计量:

    Header.xml                  设计名 / 修订号 / 单位精度
    objects/PhysicalCSet.xml    物理约束集: MIN_LINE_WIDTH / MIN_NECK_WIDTH / VIA_LIST
    objects/SpacingCSet.xml     间距约束集: LINE_TO_LINE_SPACING / LINE_TO_THRUVIA_SPACING ...
    objects/SameNetSpacingCSet.xml  同网络间距 (SN_*)
    objects/Net.xml             网络与连接
    objects/Part.xml            器件
    objects/DiffPair.xml        差分对
    objects/SubClass.xml        层与子类
    objects/Design.xml          设计级参数

同一 .brd 里会残留多次保存的归档 (修订号递增), 本模块按文件内出现顺序 "后者覆盖前者",
即取最后一次写入的定义。

重要说明
--------
这里得到的是 **设计端约束值** (Constraint Manager 里设定的规则), 不是实际布线的实测几何
—— .brd 的图形数据在二进制体里, 不通过 Allegro 无法可靠解析。
所以报告的措辞应理解为「这块板的设计规则 vs 板厂制程能力」。
"""
import os
import math
import re
import struct
import zlib
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET
from src.infrastructure.resources import (InputBudgetExceeded, MAX_BRD_BYTES, MAX_BRD_ENTRY_BYTES,
                                 MAX_BRD_UNPACKED_BYTES, MAX_BRD_ENTRIES, read_bounded)

# 1 mil = 0.0254 mm
_UNIT_TO_MM = {
    "mil": 0.0254, "mils": 0.0254,
    "mm": 1.0, "millimeter": 1.0, "millimeters": 1.0,
    "inch": 25.4, "in": 25.4,
    "um": 0.001, "micron": 0.001,
}


# --------------------------------------------------------------------------
# zip 归档解析
# --------------------------------------------------------------------------
def _carve_entries(raw: bytes) -> List[Tuple[int, str, bytes]]:
    """逐个扫描 zip 本地文件头, 把条目解出来 (不依赖中央目录, 容忍多个归档叠加)。

    返回 [(文件内偏移, 条目名, 内容), ...], 保持文件内出现顺序。
    """
    out: List[Tuple[int, str, bytes]] = []
    pos, n, total = 0, len(raw), 0
    if n > MAX_BRD_BYTES:
        raise InputBudgetExceeded("BRD 大小超过安全上限")
    while True:
        i = raw.find(b"PK\x03\x04", pos)
        if i < 0 or i + 30 > n:
            break
        pos = i + 4
        (ver, flags, method, mtime, mdate, crc, csize, usize,
         nlen, elen) = struct.unpack("<HHHHHIIIHH", raw[i + 4:i + 30])
        if nlen == 0 or nlen > 512 or method not in (0, 8) or (flags & 0x08):
            continue
        try:
            name = raw[i + 30:i + 30 + nlen].decode("utf-8")
        except UnicodeDecodeError:
            continue
        data_start = i + 30 + nlen + elen
        if csize <= 0 or data_start + csize > n:
            continue
        remaining = MAX_BRD_UNPACKED_BYTES - total
        if usize > MAX_BRD_ENTRY_BYTES or usize > remaining or len(out) >= MAX_BRD_ENTRIES:
            raise InputBudgetExceeded("BRD 内嵌归档超过解压预算，分析未完成")
        budget = min(MAX_BRD_ENTRY_BYTES, remaining)
        try:
            if method == 0:
                if csize > budget:
                    raise InputBudgetExceeded("BRD 未压缩条目超过解压预算，分析未完成")
                data = raw[data_start:data_start + csize]
            else:
                blob = memoryview(raw)[data_start:data_start + csize]
                decoder = zlib.decompressobj(-15)
                data = decoder.decompress(blob, budget + 1)
                if len(data) > budget or decoder.unconsumed_tail:
                    raise InputBudgetExceeded("BRD 内嵌归档超过解压预算，分析未完成")
                if not decoder.eof:
                    continue
        except zlib.error:
            continue
        if len(data) > MAX_BRD_ENTRY_BYTES or total + len(data) > MAX_BRD_UNPACKED_BYTES:
            raise InputBudgetExceeded("BRD 内嵌归档超过解压预算，分析未完成")
        if usize and len(data) != usize:
            continue
        out.append((i, name, data))
        total += len(data)
    return out


def read_brd_objects(path: str, raw: Optional[bytes] = None) -> Tuple[Dict[str, bytes], dict]:
    """读出 .brd 里所有 zip 条目 (同名取最后一次写入 = 最后一次保存的定义)。"""
    if raw is None:
        raw = read_bounded(path)
    items = _carve_entries(raw)
    if not items:
        raise ValueError(
            "该 .brd 内没有找到 zip/XML 归档 (可能是 Allegro 16.x 等更早的纯二进制格式), "
            "无法读取约束数据")

    objects: Dict[str, bytes] = {}
    copies: Dict[str, List[bytes]] = {}
    for _off, name, data in items:
        objects[name] = data          # 后者覆盖前者
        copies.setdefault(name, []).append(data)

    # A random ZIP renamed to .brd is not sufficient evidence. This minimum
    # structural check is not cryptographic proof of a genuine Cadence design.
    recognized = ("objects/PhysicalCSet.xml", "objects/SpacingCSet.xml",
                  "objects/SubClass.xml", "objects/Net.xml", "objects/Design.xml",
                  "objects/DiffPair.xml")
    try:
        header_root = ET.fromstring(objects["Header.xml"])
    except (KeyError, ET.ParseError) as exc:
        raise ValueError("BRD 缺少有效 Header.xml，无法确认设计结构") from exc
    if header_root.tag != "root":
        raise ValueError("BRD Header.xml 结构无效")
    valid_objects = 0
    for name in recognized:
        if name not in objects:
            continue
        try:
            root = ET.fromstring(objects[name])
        except ET.ParseError as exc:
            raise ValueError("BRD 设计对象 XML 损坏：" + name) from exc
        if root.tag != "root":
            raise ValueError("BRD 设计对象 XML 根节点无效：" + name)
        if any(node.get("N") for node in root.findall("o")):
            valid_objects += 1
    if not valid_objects:
        raise ValueError("BRD 缺少可验证的设计对象，不能作为正式板文件导入")

    revision = None
    header = objects.get("Header.xml")
    if header:
        m = re.search(r'physical="(\d+)"', header.decode("utf-8", "ignore"))
        if m:
            revision = int(m.group(1))

    return objects, {
        "entry_count": len(items),
        "physical_revision": revision,
        "physical_csets": copies.get("objects/PhysicalCSet.xml", []),
    }


def _default_min_line_g(data: bytes) -> Optional[str]:
    """取 PhysicalCSet.xml 里 DEFAULT 的 MIN_LINE_WIDTH G 值, 用于判断两份副本是否同一块板。"""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    for o in root.findall("o"):
        if o.get("N") != "DEFAULT":
            continue
        for a in o.findall("a"):
            if a.get("N") != "MIN_LINE_WIDTH":
                continue
            v = a.find("v")
            if v is not None:
                return v.get("G")
    return None


def _find_via_list(copies: List[bytes], current: Optional[bytes]) -> Optional[str]:
    """新版保存时常把未改动的 VIA_LIST 省略掉, 这里从旧副本里补。

    为避免张冠李戴, 只接受 "DEFAULT 的 MIN_LINE_WIDTH G 值与当前副本一致" 的旧副本
    —— 说明是同一块板的不同保存版本。
    """
    if not copies:
        return None
    want = _default_min_line_g(current) if current else None
    for data in reversed(copies):          # 从新到旧
        got = _default_min_line_g(data)
        if want is not None and got is not None and got != want:
            continue
        text = data.decode("utf-8", "ignore")
        m = re.search(r'<a N="VIA_LIST"><v V="([^"]+)"', text)
        if m:
            return m.group(1)
    return None



def _xml(objects: Dict[str, bytes], name: str) -> Optional[ET.Element]:
    data = objects.get(name)
    if not data:
        return None
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        return None


# --------------------------------------------------------------------------
# 数值工具
# --------------------------------------------------------------------------
def _floats(text: Optional[str]) -> List[float]:
    """'4.02:3.50:2.56' -> [4.02, 3.50, 2.56];  0 表示未设置, 丢弃。"""
    if not text:
        return []
    out = []
    for part in text.split(":"):
        part = part.strip()
        if not part:
            continue
        try:
            v = float(part)
        except ValueError:
            continue
        if v > 0:
            out.append(v)
    return out


def _attr_values(node: ET.Element) -> Dict[str, List[float]]:
    """<o> 下所有 <a N="X"><v V="..."/></a> -> {X: [数值]}"""
    vals: Dict[str, List[float]] = {}
    for a in node.findall("a"):
        key = a.get("N")
        if not key:
            continue
        v = a.find("v")
        if v is None:
            continue
        nums = _floats(v.get("V")) or _floats(v.get("G"))
        if nums:
            vals.setdefault(key, []).extend(nums)
    return vals


def _collect_length_values(objects: Dict[str, bytes]) -> List[float]:
    """只用有效线宽锚点判单位；不让比例/计数/哨兵值或重复 G/V 投票。

    V/G 的冒号分量沿用约束提取的最小正值口径，不假定它们代表实际铜层。
    没有线宽声明时才退到明确的长度类间距字段。
    """
    vals: List[float] = []
    physical = _xml(objects, "objects/PhysicalCSet.xml")
    if physical is not None:
        for node in physical.findall("o"):
            for attr in node.findall("a"):
                if attr.get("N") != "MIN_LINE_WIDTH":
                    continue
                value = attr.find("v")
                if value is not None:
                    nums = _floats(value.get("V")) or _floats(value.get("G"))
                    nums = [n for n in nums if math.isfinite(n) and n < 1000]
                    if nums:
                        vals.append(min(nums))
    if vals:
        return vals
    for name in ("objects/SpacingCSet.xml", "objects/SameNetSpacingCSet.xml"):
        root = _xml(objects, name)
        if root is None:
            continue
        for o in root.findall("o"):
            for a in o.findall("a"):
                if not (a.get("N") or "").endswith("_SPACING"):
                    continue
                v = a.find("v")
                if v is None:
                    continue
                nums = _floats(v.get("V")) or _floats(v.get("G"))
                nums = [n for n in nums if math.isfinite(n) and n < 1000]
                if nums:
                    vals.append(min(nums))
    return vals


# 各字段的合理量级 (mm), 超出即视为解析异常而不是真实设计值
_SANITY_MM = {
    "min_line_width_mm": (0.02, 1.0),
    "min_neck_width_mm": (0.02, 1.0),
    "min_spacing_mm": (0.02, 5.0),
    "min_pad_to_line_mm": (0.01, 5.0),
    "min_pad_to_shape_mm": (0.01, 5.0),
    "min_pad_to_pad_mm": (0.01, 5.0),
    "min_drill_mm": (0.05, 10.0),
    "min_laser_via_mm": (0.02, 1.0),
    "min_annular_ring_mm": (0.0, 1.0),
    "min_annular_ring_laser_mm": (0.0, 1.0),
    "board_thickness_mm": (0.2, 10.0),
}


def _resolve_units(objects: Dict[str, bytes], raw_scan: dict) -> dict:
    """决定约束 XML 数值的换算系数。

    Allegro 数据库单位写在二进制里 (ALL UNITS ARE IN MILS), 但约束对象可能按"用户单位"
    保存 —— 实测有板子按 mm 存 (数值 0.09), 按 mil 换算会得到 0.0023mm 这种不可能值。
    用明确的最小线宽锚点打分；没有线宽时才用长度类间距。
    返回值仅用于约束 XML，绝不能作为 Editor 的坐标单位。
    """
    banner_factor = raw_scan.get("units", {}).get("factor")
    vals = _collect_length_values(objects)
    candidates = []
    if banner_factor:
        candidates.append((banner_factor, raw_scan["units"].get("source", "数据库单位")))
    for f, src in ((0.0254, "按 mil 解释 (1mil=0.0254mm)"),
                   (1.0, "按 mm 解释 (数值已是毫米)")):
        if all(abs(f - c[0]) > 1e-9 for c in candidates):
            candidates.append((f, src))
    if not candidates:
        candidates = [(0.0254, "默认 mil")]

    def score(factor: float) -> float:
        if not vals:
            return 0.0
        hit = sum(1 for v in vals if 0.02 <= v * factor <= 1.0)
        return hit / len(vals)

    scored = [(score(f), f, src) for f, src in candidates]
    best = max(scored, key=lambda item: (item[0], item[1] == banner_factor))
    unit = next((unit for unit, factor in _UNIT_TO_MM.items()
                 if unit in ("mm", "mil", "inch", "um") and abs(best[1] - factor) < 1e-9), None)
    return {
        "factor": best[1],
        "unit": unit,
        "score": round(best[0], 2),
        "source": best[2] + (f" (量级匹配度 {best[0]:.0%})" if vals else ""),
        "banner_unit": raw_scan.get("units", {}).get("unit"),
        "inferred": bool(vals),
        "anchor_values": vals,
    }


def _design_units(raw_scan: dict) -> dict:
    """真实设计用户单位优先 QuickView；未知时不从约束量级猜定位单位。"""
    quickview = raw_scan.get("quickview") or {}
    if quickview.get("found") and quickview.get("unit") in _UNIT_TO_MM:
        unit = quickview["unit"]
        return dict(unit=unit, factor=_UNIT_TO_MM[unit], source="二进制 _QuickViewText / Units")
    declared = raw_scan.get("units") or {}
    unit = declared.get("unit")
    if unit in _UNIT_TO_MM:
        return dict(unit=unit, factor=_UNIT_TO_MM[unit], source=declared.get("source", "图纸单位声明"))
    return dict(unit=None, factor=None, source="未确认 BRD 设计单位；不生成猜测的定位命令")


def _constraint_declarations(objects: Dict[str, bytes]) -> List[dict]:
    """保留原始线宽声明，单位推断/范围检查不允许让输入无迹可查。"""
    rows = []
    root = _xml(objects, "objects/PhysicalCSet.xml")
    if root is not None:
        for node in root.findall("o"):
            for attr in node.findall("a"):
                if attr.get("N") not in ("MIN_LINE_WIDTH", "MIN_NECK_WIDTH"):
                    continue
                value = attr.find("v")
                if value is not None:
                    rows.append(dict(cset=node.get("N"), field=attr.get("N"),
                                     V=value.get("V"), G=value.get("G")))
    return rows


# --------------------------------------------------------------------------
# 主提取
# --------------------------------------------------------------------------
def _is_through_span(span: str) -> bool:
    """钻孔表标题是否表示通孔跨度 (TOP to BOTTOM)。"""
    parts = re.split(r"\s+to\s+", span or "", flags=re.I)
    if len(parts) != 2:
        return False
    return parts[0].strip().upper() == "TOP" and parts[1].strip().upper() == "BOTTOM"


def extract_brd_design(path: str, raw: Optional[bytes] = None) -> dict:
    """解析 .brd, 返回 {ok, error?, meta, design...}。

    数据来源分两层:
      1) 约束对象 (zip 归档里的 PhysicalCSet / SpacingCSet ...) —— 设计规则值
      2) 原始字节流 (brd_raw) —— 单位、钻孔表、层数、焊盘栈、制程说明
    每个字段都带 sources 里的出处与置信度, 供 DFM 报告说明"这个值哪来的"。
    """
    result: dict = {"ok": False, "path": path, "file_size_mb": 0.0}
    if not os.path.exists(path):
        result["error"] = f"文件不存在: {path}"
        return result
    result["file_size_mb"] = round(os.path.getsize(path) / 1024 / 1024, 2)
    result["board_name"] = os.path.splitext(os.path.basename(path))[0]

    try:
        if raw is None:
            raw = read_bounded(path)
        objects, info = read_brd_objects(path, raw)
    except InputBudgetExceeded as exc:
        result["error"] = str(exc)
        result["resource_limit_exceeded"] = True
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        return result

    from src.brd_components import extract_components
    from src.brd_raw import scan_brd_raw, parse_via_name

    raw_scan = scan_brd_raw(raw)
    units = _resolve_units(objects, raw_scan)
    factor = units["factor"]
    chart_factor = raw_scan.get("units", {}).get("factor") or 0.0254

    design_units = _design_units(raw_scan)
    result["units"] = design_units["unit"]  # GUI/定位链路只使用真实设计单位
    result["units_source"] = design_units["source"]
    result["design_units_factor"] = design_units["factor"]
    result["constraint_units"] = units["unit"]
    result["constraint_units_factor"] = factor
    result["constraint_units_source"] = units["source"]
    result["constraint_units_inferred"] = units["inferred"]
    result["constraint_units_score"] = units["score"]
    result["constraint_declarations"] = _constraint_declarations(objects)
    result["units_factor"] = factor  # 兼容旧调用：仅表示约束解析系数
    result["zip_entries"] = info["entry_count"]
    result["physical_revision"] = info["physical_revision"]

    warnings: List[str] = list(raw_scan.get("warnings", []))
    if design_units["unit"] != units["unit"]:
        warnings.append(f"约束 XML 按 {units['unit']} 解释；BRD 设计单位为 {design_units['unit'] or '未知'}。"
                        "两者已分开，定位只使用设计单位；约束推断须在 Constraint Manager 核对")
    if units["inferred"] and units["score"] < 1:
        warnings.append("约束单位锚点不完全一致；原始 V/G 声明已保留，需核对单位及各分量含义")
    sources: Dict[str, dict] = {}

    def to_mm(value: float) -> float:
        return round(value * factor, 4)

    def accept(field: str, value, source: str, confidence: str = "high"):
        """记录取值与出处; 超出合理量级的值丢弃并记警告 (避免 0.0023mm 这类假数据)。"""
        if value is None:
            return None
        lo, hi = _SANITY_MM.get(field, (None, None))
        if lo is not None and not (lo <= value <= hi):
            warnings.append(
                f"{field}={value} 超出合理范围 [{lo}, {hi}]mm, 已忽略 (来源: {source})")
            return None
        sources[field] = {"source": source, "confidence": confidence}
        return value

    # ---- 物理约束集 ----
    physical: Dict[str, dict] = {}
    root = _xml(objects, "objects/PhysicalCSet.xml")
    if root is not None:
        for o in root.findall("o"):
            name = o.get("N") or "?"
            vals = _attr_values(o)
            entry: dict = {}
            if "MIN_LINE_WIDTH" in vals:
                entry["min_line_width_mm"] = to_mm(min(vals["MIN_LINE_WIDTH"]))
            if "MIN_NECK_WIDTH" in vals:
                entry["min_neck_width_mm"] = to_mm(min(vals["MIN_NECK_WIDTH"]))
            if "MAX_LINE_WIDTH" in vals:
                entry["max_line_width_mm"] = to_mm(max(vals["MAX_LINE_WIDTH"]))
            if "DIFFP_PRIMARY_GAP" in vals:
                entry["diff_gap_mm"] = to_mm(min(vals["DIFFP_PRIMARY_GAP"]))
            via_text = None
            for a in o.findall("a"):
                if a.get("N") == "VIA_LIST":
                    v = a.find("v")
                    if v is not None:
                        via_text = v.get("V") or v.get("G")
            if via_text:
                entry["via_list"] = [v for v in via_text.split(":") if v]
            physical[name] = entry
    result["physical_csets"] = physical

    # ---- 间距约束集 ----
    # 除线线间距外, 也读"到焊盘/到铜皮"的间距 (Allegro 里 SMD PIN 就是器件焊盘,
    # SHAPE 就是铜皮/平面), 焊盘间距分析要拿它们当判定依据。
    _spacing_keys = ("LINE_TO_LINE_SPACING", "LINE_TO_THRUVIA_SPACING",
                     "LINE_TO_SMDPIN_SPACING", "LINE_TO_PIN_SPACING",
                     "THRUVIA_TO_THRUVIA_SPACING",
                     "SMD_PIN_TO_SMD_PIN_SPACING", "PIN_TO_PIN_SPACING",
                     "SHAPE_TO_SMDPIN_SPACING", "SHAPE_TO_PIN_SPACING",
                     "SHAPE_TO_THRUVIA_SPACING")
    spacing: Dict[str, dict] = {}
    root = _xml(objects, "objects/SpacingCSet.xml")
    if root is not None:
        for o in root.findall("o"):
            name = o.get("N") or "?"
            vals = _attr_values(o)
            entry = {}
            for key in _spacing_keys:
                if key in vals:
                    entry[key] = to_mm(min(vals[key]))
            spacing[name] = entry
    result["spacing_csets"] = spacing

    # ---- 层 ----
    layers: List[str] = []
    root = _xml(objects, "objects/SubClass.xml")
    if root is not None:
        seen = set()
        for o in root.findall("o"):
            name = o.get("N") or ""
            if "/" in name:
                cls, layer = name.split("/", 1)
                if cls == "Etch" and layer not in seen:
                    seen.add(layer)
                    layers.append(layer)

    layer_count = raw_scan.get("layer_count")
    layer_source = raw_scan.get("layer_count_source", "")
    layer_confidence = raw_scan.get("layer_count_confidence", "medium")
    if not layer_count and layers:
        layer_count, layer_source, layer_confidence = len(layers), "BRD SubClass.xml (Etch 层)", "high"
    elif layer_count and layers and len(layers) != layer_count:
        warnings.append(
            f"SubClass.xml 列出 {len(layers)} 个 Etch 层, 与预览元数据 {layer_count} 层不一致, 以元数据为准")
    result["layers"] = layers or raw_scan.get("layers", {}).get("names", [])
    result["layer_count"] = layer_count
    if layer_count:
        sources["layer_count"] = {"source": layer_source, "confidence": layer_confidence}
    if raw_scan.get("layers", {}).get("note"):
        result["layer_note"] = raw_scan["layers"]["note"]

    # ---- 网络 / 器件 / 差分对 ----
    root = _xml(objects, "objects/Net.xml")
    result["net_count"] = len(root.findall("o")) if root is not None else 0
    components = extract_components(objects)
    result["components"] = components
    result["part_count"] = components["count"] if components.get("ok") else 0
    root = _xml(objects, "objects/DiffPair.xml")
    result["diff_pair_count"] = len(root.findall("o")) if root is not None else 0

    # ---- 设计名 ----
    root = _xml(objects, "objects/Design.xml")
    if root is not None:
        first = root.find("o")
        if first is not None and first.get("N"):
            name = first.get("N")
            if not name.startswith("#"):      # #Taaaavs36040 这类是临时名
                result["design_name"] = name
    if not result.get("design_name"):
        qv_name = raw_scan.get("quickview", {}).get("name")
        if qv_name:
            result["design_name"] = qv_name
            sources["design_name"] = {"source": "二进制 _QuickViewText", "confidence": "medium"}

    # ---- 汇总: 板级最严值 ----
    line_widths = {k: v["min_line_width_mm"] for k, v in physical.items()
                   if "min_line_width_mm" in v}
    neck_widths = [c["min_neck_width_mm"] for c in physical.values()
                   if "min_neck_width_mm" in c]
    spacings = [s["LINE_TO_LINE_SPACING"] for s in spacing.values()
                if "LINE_TO_LINE_SPACING" in s]
    if line_widths:
        worst_cls = min(line_widths, key=line_widths.get)
        result["min_line_width_class"] = worst_cls
        result["min_line_width_mm"] = accept(
            "min_line_width_mm", line_widths[worst_cls],
            f"PhysicalCSet.xml / {worst_cls} 的 MIN_LINE_WIDTH")
    else:
        result["min_line_width_mm"] = None
    result["min_neck_width_mm"] = accept(
        "min_neck_width_mm", min(neck_widths) if neck_widths else None,
        "PhysicalCSet.xml / MIN_NECK_WIDTH")
    result["min_spacing_mm"] = accept(
        "min_spacing_mm", min(spacings) if spacings else None,
        "SpacingCSet.xml / LINE_TO_LINE_SPACING")

    # 焊盘相关间距约束: 走线到贴片焊盘 / 铜皮到焊盘 (焊盘间距分析的判定依据)
    def _min_key(keys) -> Optional[float]:
        got = [s[k] for s in spacing.values() for k in keys if s.get(k)]
        return min(got) if got else None

    pad_line = _min_key(("LINE_TO_SMDPIN_SPACING", "LINE_TO_PIN_SPACING"))
    pad_shape = _min_key(("SHAPE_TO_SMDPIN_SPACING", "SHAPE_TO_PIN_SPACING",
                          "SHAPE_TO_THRUVIA_SPACING"))
    pad_pad = _min_key(("SMD_PIN_TO_SMD_PIN_SPACING", "PIN_TO_PIN_SPACING"))
    result["min_pad_to_line_mm"] = accept(
        "min_pad_to_line_mm", pad_line,
        "SpacingCSet.xml / LINE_TO_SMDPIN_SPACING (走线到贴片焊盘)", "medium")
    result["min_pad_to_shape_mm"] = accept(
        "min_pad_to_shape_mm", pad_shape,
        "SpacingCSet.xml / SHAPE_TO_SMDPIN_SPACING (铜皮到贴片焊盘)", "medium")
    result["min_pad_to_pad_mm"] = accept(
        "min_pad_to_pad_mm", pad_pad,
        "SpacingCSet.xml / SMD_PIN_TO_SMD_PIN_SPACING (焊盘到焊盘)", "medium")

    # ---- 过孔 / 钻孔 ----
    via_names: List[str] = []
    for entry in physical.values():
        via_names.extend(entry.get("via_list", []))
    # Preserve nonnumeric VIA_LIST entries: missing dimensions are not a reason
    # to silently discard an unknown or typed padstack from the audit.
    unmeasured_names = []
    via_source = "当前保存"
    if not via_names:
        legacy = _find_via_list(info.get("physical_csets", []),
                                objects.get("objects/PhysicalCSet.xml"))
        if legacy:
            via_names = [v for v in legacy.split(":") if v]
            via_source = f"较早副本 (物理修订号 {info.get('physical_revision')} 之前)"

    padstacks: List[dict] = []
    seen_pads = set()
    for nm in via_names:
        parsed = parse_via_name(nm)
        if not parsed:
            if nm not in unmeasured_names:
                unmeasured_names.append(nm)
            continue
        # 焊盘栈 VIA20D10 的约定单位是 mil，与物理约束 XML 的单位推断无关。
        pad, drill = parsed["pad_mil"] * 0.0254, parsed["drill_mil"] * 0.0254
        # 按名字去重: 同一孔径可能既有通孔焊盘栈又有埋孔焊盘栈 (VIA8-16 / VIA14D8_3-10),
        # 按 (焊盘, 钻孔) 去重会把埋孔那个吞掉, 孔环就会算到机械孔头上
        base = nm.lower()
        for suffix in (".pad", ".dra", ".psm"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        key = (base, round(pad, 4), round(drill, 4))
        if key in seen_pads:
            continue
        seen_pads.add(key)
        anti = parsed["anti_mil"]
        padstacks.append({
            "name": nm,
            "drill_mm": round(drill, 4),
            "pad_mm": round(pad, 4),
            "antipad_mm": None if anti is None else round(anti * 0.0254, 4),
            "annular_ring_mm": round((pad - drill) / 2.0, 4),
            "span": parsed.get("span"),
        })
    if not padstacks:
        for v in raw_scan.get("padstacks", {}).get("vias", []):
            padstacks.append({
                "name": v["name"], "drill_mm": v["drill_mm"], "pad_mm": v["pad_mm"],
                "antipad_mm": v.get("anti_mm"), "annular_ring_mm": v["annular_mm"],
                "span": v.get("span"),
            })
        via_source = "二进制焊盘栈命名"
    padstacks.sort(key=lambda p: p["drill_mm"])
    result["via_padstacks"] = padstacks
    result["via_list_source"] = via_source

    # 钻孔表
    drill = raw_scan.get("drill", {})
    rows = drill.get("rows", [])
    through = [r for r in rows if r["plating"] == "PTH" and _is_through_span(r["span"])]
    npth = [r for r in rows if r["plating"] == "NPTH"]
    result["drill_spans"] = drill.get("spans", [])
    result["drill_rows"] = [
        {"span": r["span"], "size_mm": round(r["size_mil"] * chart_factor, 4),
         "plating": r["plating"], "qty": r["qty"]}
        for r in rows
    ]

    # 孔型以 Cadence 类型及明确跨层为准，不以孔径或焊盘大小猜测。
    from src.pcb_hole_types import classify_hole
    unknown_vias: List[dict] = []
    for p in padstacks:
        classification = classify_hole(name=p["name"])
        p["kind"] = classification["kind"]
        p["kind_reason"] = classification["reason"]

    for p in padstacks:
        if p["kind"] == "unknown":
            unknown_vias.append({
                "name": p["name"], "drill_mm": p["drill_mm"], "pad_mm": p["pad_mm"],
                "annular_ring_mm": p["annular_ring_mm"], "reason": p["kind_reason"],
            })
    for name in unmeasured_names:
        classification = classify_hole(name=name)
        unknown_vias.append({"name": name, "drill_mm": None, "pad_mm": None,
                             "annular_ring_mm": None,
                             "reason": classification["reason"] + "；焊盘/孔径无法从名称确认"})
    result["via_kind_unknown"] = unknown_vias
    if unknown_vias:
        names = "、".join(v["name"] for v in unknown_vias[:4])
        ring_txt = min((v["annular_ring_mm"] for v in unknown_vias if v["annular_ring_mm"] is not None),
                       default="未知")
        warnings.append(
            f"焊盘栈 {names} {unknown_vias[0]['reason']}, 无法确定孔环 {ring_txt}mm "
            f"的孔型或尺寸未确认, 已单列待人工确认, 不参与自动判定")


    # VIA_LIST 是允许使用的焊盘栈，不证明已放置；名称也不保证真实尺寸。
    # 只有明确贯通且镀孔的钻孔表可作为当前贯通孔的候选尺寸。
    chart_row = min(through, key=lambda row: row["size_mil"], default=None)
    chart_min = round(chart_row["size_mil"] * chart_factor, 4) if chart_row else None
    candidates = [(v, src) for v, src in (
        (chart_min, f"钻孔表 ({chart_row['span']}) 最小通孔孔径" if chart_row else None),
    ) if v is not None]
    if candidates:
        value, src = min(candidates, key=lambda item: item[0])
        result["min_drill_mm"] = None
        warnings.append(f"{src} {value}mm：只确认跨度，未确认机械/激光工艺，不用于机械孔硬判定")
    else:
        result["min_drill_mm"] = None

    # BBVia/Microvia 是结构分类，不能单凭名称/孔径证明采用激光钻孔工艺。
    result["min_laser_via_mm"] = None
    process_unknown = [p for p in padstacks if p["kind"] == "blind"]
    result["via_process_unknown"] = [dict(name=p["name"], drill_mm=p["drill_mm"],
                                          reason="盲/埋孔结构已识别；机械/激光工艺未确认")
                                     for p in process_unknown]
    if chart_row:
        result["via_process_unknown"].append(dict(name=chart_row["span"], drill_mm=chart_min,
                                                   reason="钻孔表只确认贯通跨度；钻孔工艺未确认"))
    result["via_process_unknown"].extend(
        dict(name=name, drill_mm=None, reason="盲/埋孔结构已识别；孔径和钻孔工艺未确认")
        for name in unmeasured_names if classify_hole(name=name)["kind"] == "blind")
    if process_unknown:
        warnings.append("BBVia/Microvia 已识别为盲/埋孔；激光或机械工艺未确认，不自动判激光孔能力")


    # 通孔/盲埋孔的镀覆分类 (PTH / NPTH) 汇总: 判定"NPTH 孔到成型线"要有孔坐标,
    # 而 .brd 快照只有孔径/跨度/数量, 没有坐标, 所以这里只做清楚的数据汇总。
    hole_rows = result["drill_rows"]
    pth_rows = [r for r in hole_rows if r["plating"] == "PTH"]
    npth_rows = [r for r in hole_rows if r["plating"] == "NPTH"]
    result["holes"] = {
        "pth_sizes_mm": sorted({r["size_mm"] for r in pth_rows}),
        "npth_sizes_mm": sorted({r["size_mm"] for r in npth_rows}),
        "pth_span_count": len(pth_rows),
        "npth_span_count": len(npth_rows),
        "pth_count": sum(r["qty"] for r in pth_rows if r.get("qty")) or None,
        "npth_count": sum(r["qty"] for r in npth_rows if r.get("qty")) or None,
        "npth_to_edge_mm": None,
        "npth_to_edge_reason": ("BRD 快照不含孔坐标, 只有孔径/跨度/数量; "
                                "算孔壁到成型线要钻孔坐标 (CAM/NC Drill)"),
    }

    # 名称推算的圆形孔环不是实际焊盘栈/偏心孔的测量值。
    result["min_annular_ring_mm"] = None
    result["min_annular_ring_laser_mm"] = None

    # 板厚 / 表面处理来自制程说明, 置信度低, 报告里需标注
    notes = raw_scan.get("notes", {})
    result["board_thickness_mm"] = accept(
        "board_thickness_mm", notes.get("board_thickness_mm"),
        "制程说明文本", "low")
    result["board_thickness_note"] = notes.get("board_thickness_note", "")
    result["surface_finishes"] = notes.get("surface_finishes", [])
    result["impedance"] = bool(notes.get("impedance"))

    # 板框: 从 .brd 内嵌设计快照里读 BOARD GEOMETRY/OUTLINE, 用 Draw Size 标定
    snapshot = None
    snapshot_loaded = False
    try:
        from src.design_geometry import _decode_snapshot, board_geometry

        qv = raw_scan.get("quickview") or {}
        snapshot = _decode_snapshot(raw)
        snapshot_loaded = True
        geo = (board_geometry(path, qv if qv.get("found") else None, snapshot=snapshot)
               if snapshot is not None else {"ok": False, "reason": "没有可解析的内嵌快照"})
    except Exception as exc:  # noqa: BLE001  几何解析失败不能影响主流程
        # One bounded failed attempt is enough; do not repeat expensive/unsafe
        # decompression for pad/silk extraction below.
        snapshot_loaded = True
        geo = {"ok": False, "reason": str(exc)}
        warnings.append("内嵌几何快照未完成：" + str(exc))
    result["board_geometry"] = geo
    try:
        from src.design_geometry import load_design_snapshot
        from src.pad_metrics import extract_pad_metrics
        from src.silkscreen import silkscreen_stats, silkscreen_to_outline
        scale = float((geo.get("outline") or {}).get("scale_mm_per_unit") or 0.0)
        doc = snapshot if snapshot_loaded else load_design_snapshot(path)
        result["pad_metrics"] = extract_pad_metrics(doc, scale)
        silk_info = silkscreen_stats(doc, scale)
        result["silkscreen"] = silk_info
        if silk_info.get("has_widths"):
            result["min_silkscreen_width_mm"] = accept(
                "min_silkscreen_width_mm", silk_info["min_width_mm"],
                silk_info["source"], "medium")
        silk_edge = silkscreen_to_outline(doc, scale)
        result["silkscreen_to_outline"] = silk_edge
        if silk_edge.get("ok"):
            result["silkscreen_to_outline_mm"] = accept(
                "silkscreen_to_outline_mm", silk_edge["min_mm"], silk_edge["source"], "medium")
        from src.pads import detect_test_points, extract_pads
        pads = extract_pads(doc, scale)
        result["pads"] = pads
        result["test_points"] = detect_test_points(pads)
        if pads.get("ok"):
            result["min_test_pad_mm"] = accept(
                "min_test_pad_mm", pads["min_d_mm"], pads["source"], "medium")
        from src.silkscreen import silk_to_component
        result["silk_to_component"] = silk_to_component(doc, scale)
    except Exception as exc:  # noqa: BLE001  几何失败不能拖垮主流程
        result["pad_metrics"] = {"ok": False, "reason": f"焊盘几何解析失败: {exc}", "pads": 0}
        result["silkscreen"] = {"ok": False, "reason": f"丝印解析失败: {exc}", "segments": 0}
        result["silkscreen_to_outline"] = {"ok": False, "reason": f"丝印到板框解析失败: {exc}"}
    outline = (geo or {}).get("outline") or {}
    if outline.get("ok"):
        result["board_size_mm"] = [outline["width_mm"], outline["height_mm"]]
        result["board_outline_bbox"] = outline.get("bbox_units")
        sources["board_size_mm"] = {
            "source": "内嵌设计快照 BOARD GEOMETRY/OUTLINE (Draw Size 标定)",
            "confidence": outline.get("confidence", "high"),
        }
    else:
        result["board_size_mm"] = None

    # 孔数: 只有所有行都带数量时才给总数, 否则标记为不完整
    complete = bool(rows) and all(r["qty"] is not None for r in rows)
    result["hole_count"] = sum(r["qty"] for r in rows) if complete else None
    result["hole_count_partial"] = sum(r["qty"] or 0 for r in rows)
    result["npth_holes"] = sum(r["qty"] or 0 for r in npth) if npth else None

    result["sources"] = sources
    result["warnings"] = warnings
    result["raw_note"] = {
        "quickview": raw_scan.get("quickview", {}),
        "units": raw_scan.get("units", {}),
        "layers": raw_scan.get("layers", {}),
        "padstacks": {
            "count": len(raw_scan.get("padstacks", {}).get("names", [])),
            "min_annular_mm": raw_scan.get("padstacks", {}).get("min_annular_mm"),
        },
        "drill": {
            "charts": drill.get("charts", []),
            "spans": drill.get("spans", []),
            "total_holes": drill.get("total_holes"),
            "npth_holes": drill.get("npth_holes"),
        },
        "notes": notes,
    }
    result["ok"] = True
    return result


def to_design_params(extracted: dict, copper_um: float = 35.0,
                     board_type: str = "rigid") -> dict:
    """把提取结果转成 dfm_engine.analyze_design() 需要的 design 参数 (长度单位 mm)。

    字段映射与界面共用 src/dfm_inputs.py, 避免"界面带入一套、CLI 算另一套"。
    """
    from src.dfm_inputs import brd_design_values

    values, sources = brd_design_values(extracted)
    design: dict = dict(values)
    design["copper_um"] = copper_um
    design["layers"] = list(extracted.get("layers") or [])
    design["stackup"] = extracted.get("stackup") or []
    design["board_name"] = extracted.get("design_name") or extracted.get("board_name", "")
    if extracted.get("surface_finishes"):
        design["surface_finish"] = extracted["surface_finishes"][0]
    # 供报告说明字段出处 (不参与比对)
    design["_sources"] = sources or extracted.get("sources", {})
    design["_warnings"] = extracted.get("warnings", [])
    design["_drill_spans"] = extracted.get("drill_spans", [])
    design["_via_kind_unknown"] = extracted.get("via_kind_unknown", [])
    design["_via_process_unknown"] = extracted.get("via_process_unknown", [])
    design["_components"] = extracted.get("components") or {}
    design["_pad_metrics"] = extracted.get("pad_metrics") or {}
    design["_silkscreen"] = extracted.get("silkscreen") or {}
    design["_silkscreen_to_outline"] = extracted.get("silkscreen_to_outline") or {}
    design["_pads"] = extracted.get("pads") or {}
    design["_test_points"] = extracted.get("test_points") or {}
    design["_silk_to_component"] = extracted.get("silk_to_component") or {}
    design["_holes"] = extracted.get("holes") or {}
    design["_data_origin"] = "BRD"
    return design


def field_sources(extracted: dict) -> List[Tuple[str, str, str]]:
    """返回 [(报告字段, 取值, 来源说明)], 用于在报告里标注数据出处。"""
    rows = []
    sources = extracted.get("sources") or {}

    def fmt(value, suffix=""):
        return "—" if value is None else f"{value}{suffix}"

    def src(key, default="未解析到"):
        item = sources.get(key) or {}
        text = item.get("source") or default
        conf = item.get("confidence")
        if conf == "low":
            text += "（低置信，建议核对）"
        return text

    rows.append(("设计名", extracted.get("design_name") or extracted.get("board_name") or "—",
                 "约束对象 / 预览元数据"))
    rows.append(("层数", fmt(extracted.get("layer_count")), src("layer_count")))
    rows.append(("最小线宽", fmt(extracted.get("min_line_width_mm"), " mm"), src("min_line_width_mm")))
    rows.append(("最小线距", fmt(extracted.get("min_spacing_mm"), " mm"), src("min_spacing_mm")))
    rows.append(("最小钻孔", fmt(extracted.get("min_drill_mm"), " mm"), src("min_drill_mm")))
    rows.append(("最小激光孔", fmt(extracted.get("min_laser_via_mm"), " mm"),
                 src("min_laser_via_mm")))
    rows.append(("单边孔环(机械孔)", fmt(extracted.get("min_annular_ring_mm"), " mm"),
                 src("min_annular_ring_mm")))
    rows.append(("单边孔环(激光/盲埋孔)", fmt(extracted.get("min_annular_ring_laser_mm"), " mm"),
                 src("min_annular_ring_laser_mm")))
    rows.append(("板厚", fmt(extracted.get("board_thickness_mm"), " mm"), src("board_thickness_mm")))
    comp = extracted.get("components") or {}
    if comp.get("ok"):
        rows.append(("器件数", f"{comp['count']} 个", "BRD objects/Part.xml 位号清单"))
        rows.append(("器件位号", "、".join(comp["refdes"][:10]) + ("…" if comp["count"] > 10 else ""),
                     "BRD objects/Part.xml"))
    else:
        rows.append(("器件数", "—", comp.get("reason", "BRD 无 Part.xml")))
    holes = extracted.get("holes") or {}
    if holes.get("pth_span_count") or holes.get("npth_span_count"):
        rows.append(("钻孔分类",
                     f"PTH {holes['pth_span_count']} 种 / NPTH {holes['npth_span_count']} 种",
                     "BRD 钻孔表 PTH/NPTH 列"))
    rows.append(("网络数", fmt(extracted.get("net_count")), "BRD Net.xml"))
    rows.append(("差分对", fmt(extracted.get("diff_pair_count")), "BRD DiffPair.xml"))
    rows.append(("设计单位", extracted.get("units") or "未知", extracted.get("units_source", "")))
    rows.append(("约束 XML 解释单位", extracted.get("constraint_units") or "未知",
                 extracted.get("constraint_units_source", "")))
    if extracted.get("drill_spans"):
        rows.append(("钻孔跨度", " / ".join(extracted["drill_spans"]), "二进制钻孔表"))
    return rows

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
import re
import struct
import zlib
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

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
    pos, n = 0, len(raw)
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
        blob = raw[data_start:data_start + csize]
        try:
            data = blob if method == 0 else zlib.decompressobj(-15).decompress(blob)
        except zlib.error:
            continue
        if usize and len(data) != usize:
            continue
        out.append((i, name, data))
    return out


def read_brd_objects(path: str, raw: Optional[bytes] = None) -> Tuple[Dict[str, bytes], dict]:
    """读出 .brd 里所有 zip 条目 (同名取最后一次写入 = 最后一次保存的定义)。"""
    if raw is None:
        with open(path, "rb") as f:
            raw = f.read()
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
    """收集约束 XML 里的长度类原始数值, 用于判断这些数值是按 mil 还是 mm 存放。"""
    vals: List[float] = []
    for name in ("objects/PhysicalCSet.xml", "objects/SpacingCSet.xml",
                 "objects/SameNetSpacingCSet.xml"):
        root = _xml(objects, name)
        if root is None:
            continue
        for o in root.findall("o"):
            for a in o.findall("a"):
                v = a.find("v")
                if v is None:
                    continue
                for attr in ("V", "G"):
                    for part in (v.get(attr) or "").split(":"):
                        try:
                            num = float(part)
                        except ValueError:
                            continue
                        if num > 0:
                            vals.append(num)
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
    这里用两个候选系数各自打分, 取能让线宽/线距落在合理区间的那个。
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
        hit = sum(1 for v in vals if 0.02 <= v * factor <= 0.8)
        return hit / len(vals)

    scored = [(score(f), f, src) for f, src in candidates]
    best = max(scored, key=lambda item: (item[0], item[1] == banner_factor))
    unit = "mm" if abs(best[1] - 1.0) < 1e-9 else "mil"
    return {
        "factor": best[1],
        "unit": unit,
        "score": round(best[0], 2),
        "source": best[2] + (f" (量级匹配度 {best[0]:.0%})" if vals else ""),
        "banner_unit": raw_scan.get("units", {}).get("unit"),
    }


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
            with open(path, "rb") as f:
                raw = f.read()
        objects, info = read_brd_objects(path, raw)
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        return result

    from src.brd_components import extract_components
    from src.brd_raw import scan_brd_raw, parse_via_name

    raw_scan = scan_brd_raw(raw)
    units = _resolve_units(objects, raw_scan)
    factor = units["factor"]
    chart_factor = raw_scan.get("units", {}).get("factor") or 0.0254

    result["units"] = units["unit"]
    result["units_factor"] = factor
    result["units_source"] = units["source"]
    result["zip_entries"] = info["entry_count"]
    result["physical_revision"] = info["physical_revision"]

    warnings: List[str] = list(raw_scan.get("warnings", []))
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
            continue
        pad, drill = parsed["pad_mil"] * factor, parsed["drill_mil"] * factor
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
            "antipad_mm": None if anti is None else round(anti * factor, 4),
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
    blind = [r for r in rows if r["plating"] == "PTH" and not _is_through_span(r["span"])]
    npth = [r for r in rows if r["plating"] == "NPTH"]
    result["drill_spans"] = drill.get("spans", [])
    result["drill_rows"] = [
        {"span": r["span"], "size_mm": round(r["size_mil"] * chart_factor, 4),
         "plating": r["plating"], "qty": r["qty"]}
        for r in rows
    ]

    # ---- 焊盘栈用途判定: 通孔 / 盲埋孔 / 待确认 ----
    # 只看孔径大小分类会把 8mil 的埋孔 (VIA14D8_3-10) 当成机械通孔, 孔环 0.0762
    # 拿去和"机械孔 6mil"比, 结论是错的。这里用三种证据:
    #   a) 焊盘栈名字里的跨层标记 (_3-10 / _L3-L10) -> 盲埋孔
    #   b) 孔径 < 0.15mm -> 激光孔
    #   c) 钻孔表里该孔径只出现在通孔跨度 / 只出现在盲埋跨度 -> 对应类型
    # 同孔径两种跨度都在 (本板 8mil 既有通孔又有埋孔) 时判不了, 单列"待确认",
    # 不参与自动判定, 也不硬塞给机械孔。
    size_kinds: Dict[float, set] = {}
    for row in result["drill_rows"]:
        # 大孔 (安装孔/定位孔) 不可能是激光盲孔; 钻孔表的跨度列有时是模板默认值, 不采信
        if row["size_mm"] >= 0.6:
            kind = "through"
        else:
            kind = "through" if _is_through_span(row["span"]) else "blind"
        size_kinds.setdefault(round(row["size_mm"], 3), set()).add(kind)

    layer_total = result.get("layer_count") or 0
    unknown_vias: List[dict] = []
    for p in padstacks:
        span = p.get("span")
        if span:
            a, b = span
            kind = "through" if (layer_total and a <= 1 and b >= layer_total) else "blind"
            why = f"焊盘栈名称标注跨层 {a}-{b}"
        elif p["drill_mm"] < 0.15:
            kind, why = "blind", "孔径 < 0.15mm, 按激光孔"
        else:
            kinds = size_kinds.get(round(p["drill_mm"], 3), set())
            if kinds == {"through"}:
                kind, why = "through", "该孔径只出现在通孔跨度"
            elif kinds == {"blind"}:
                kind, why = "blind", "该孔径只出现在盲/埋孔跨度"
            elif kinds:
                kind, why = "unknown", "该孔径同时出现在通孔和盲/埋孔跨度"
            else:
                # 钻孔表里没有这个孔径 (很多板子干脆没有钻孔表, 或表只列了部分孔):
                # 没有证据可依, 按业内口径归类, 来源里写明是"按孔径口径"
                if p["drill_mm"] >= 0.15:
                    kind, why = "through", "钻孔表无该孔径信息, 按孔径 ≥0.15mm 归为机械通孔"
                else:
                    kind, why = "blind", "钻孔表无该孔径信息, 按孔径 <0.15mm 归为激光孔"
        if kind == "blind" and p["drill_mm"] >= 0.6:
            kind, why = "through", "孔径 ≥0.6mm, 按安装/定位通孔"
        p["kind"] = kind
        p["kind_reason"] = why

    # 同一孔径既出现在通孔跨度又出现在盲/埋孔跨度时, 真正的打孔类型分不到焊盘栈头上。
    # 用焊盘直径兜底: 通孔要留更大的孔环 (Z 向对位), 同孔径下焊盘最大的按通孔算,
    # 其余按盲/埋孔算。这只是启发式, 来源里会写明, 报告里可人工复核。
    for size, kinds in size_kinds.items():
        if kinds != {"through", "blind"}:
            continue
        cands = [p for p in padstacks
                 if p["kind"] == "unknown" and not p.get("span")
                 and round(p["drill_mm"], 3) == size]
        pads = {p["pad_mm"] for p in cands}
        if len(cands) < 2 or len(pads) < 2:
            continue
        biggest = max(pads)
        for p in cands:
            p["kind"] = "through" if p["pad_mm"] == biggest else "blind"
            p["kind_reason"] = "同孔径下焊盘最大的按通孔, 其余按盲/埋孔 (启发性判据)"

    # 同一个焊盘栈同时被通孔和盲埋孔用 (或几何完全相同分不开): 它的孔环两类都要满足,
    # 两边都算上, 不能因为分不清就漏判。
    for p in padstacks:
        if p["kind"] != "unknown":
            continue
        kinds = size_kinds.get(round(p["drill_mm"], 3), set())
        if kinds == {"through", "blind"}:
            p["kind"] = "both"
            p["kind_reason"] = "该孔径既做通孔也做盲/埋孔, 孔环两类都要满足"

    for p in padstacks:
        if p["kind"] == "unknown":
            unknown_vias.append({
                "name": p["name"], "drill_mm": p["drill_mm"], "pad_mm": p["pad_mm"],
                "annular_ring_mm": p["annular_ring_mm"], "reason": p["kind_reason"],
            })
    result["via_kind_unknown"] = unknown_vias
    if unknown_vias:
        names = "、".join(v["name"] for v in unknown_vias[:4])
        ring_txt = min(v["annular_ring_mm"] for v in unknown_vias)
        warnings.append(
            f"焊盘栈 {names} {unknown_vias[0]['reason']}, 无法确定孔环 {ring_txt}mm "
            f"该按机械孔还是激光孔比, 已单列待人工确认, 不参与自动判定")


    # 最小钻孔: 钻孔表可能只列出部分孔 (漏表), 与焊盘栈命名取更小值更稳
    chart_min = round(min(r["size_mil"] for r in through) * chart_factor, 4) if through else None
    pad_min = min((p["drill_mm"] for p in padstacks if p.get("kind") == "through"), default=None)
    candidates = [(v, src) for v, src in (
        (chart_min, f"钻孔表 ({through[0]['span']}) 最小通孔孔径" if through else None),
        (pad_min, "焊盘栈命名推算"),
    ) if v is not None]
    if candidates:
        value, src = min(candidates, key=lambda item: item[0])
        if chart_min is not None and pad_min is not None and abs(chart_min - pad_min) > 0.01:
            warnings.append(
                f"钻孔表最小孔 {chart_min}mm 与焊盘栈 {pad_min}mm 不一致, 取更小值")
        result["min_drill_mm"] = accept("min_drill_mm", value, src)
    else:
        result["min_drill_mm"] = None

    # 最小激光孔: 先看钻孔表里的盲/埋孔孔径; 没有钻孔表时退回焊盘栈里
    # 真正的激光孔 (孔径 < 0.15mm 的盲孔), 两种来源都写清楚。
    laser_candidates = []
    if blind:
        chart_laser = round(min(r["size_mil"] for r in blind) * chart_factor, 4)
        laser_candidates.append((chart_laser, f"钻孔表 ({blind[0]['span']}) 盲/埋孔最小孔径"))
    pad_laser = [p for p in padstacks if p.get("kind") in ("blind", "both") and p["drill_mm"] < 0.15]
    if pad_laser:
        smallest = min(pad_laser, key=lambda p: p["drill_mm"])
        laser_candidates.append((smallest["drill_mm"], f"焊盘栈 ({smallest['name']}) 激光孔最小孔径"))
    if laser_candidates:
        value, src = min(laser_candidates, key=lambda item: item[0])
        if len(laser_candidates) > 1 and abs(laser_candidates[0][0] - laser_candidates[1][0]) > 0.01:
            warnings.append(
                f"最小激光孔: 钻孔表 {laser_candidates[0][0]}mm 与焊盘栈 {laser_candidates[1][0]}mm 不一致, 取更小值")
        result["min_laser_via_mm"] = accept("min_laser_via_mm", value, src, "medium")
        result["laser_via_candidates"] = [{"value_mm": v, "source": s} for v, s in laser_candidates]
    else:
        result["min_laser_via_mm"] = None


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

    # 孔环按孔的用途分开: 机械通孔与激光/盲埋孔的制程能力不同
    mech = [p for p in padstacks if p["kind"] in ("through", "both")]
    laser = [p for p in padstacks if p["kind"] in ("blind", "both")]
    mech_min = min(mech, key=lambda p: p["annular_ring_mm"], default=None)
    laser_min = min(laser, key=lambda p: p["annular_ring_mm"], default=None)
    result["min_annular_ring_mm"] = accept(
        "min_annular_ring_mm", mech_min["annular_ring_mm"] if mech_min else None,
        f"焊盘栈命名 (焊盘-钻孔)/2, 机械通孔 ({mech_min['name']})" if mech_min
        else "焊盘栈命名 (焊盘-钻孔)/2, 机械通孔", "medium")
    result["min_annular_ring_laser_mm"] = accept(
        "min_annular_ring_laser_mm", laser_min["annular_ring_mm"] if laser_min else None,
        f"焊盘栈命名 (焊盘-钻孔)/2, 激光/盲埋孔 ({laser_min['name']})" if laser_min
        else "焊盘栈命名 (焊盘-钻孔)/2, 激光/盲埋孔", "medium")

    # 板厚 / 表面处理来自制程说明, 置信度低, 报告里需标注
    notes = raw_scan.get("notes", {})
    result["board_thickness_mm"] = accept(
        "board_thickness_mm", notes.get("board_thickness_mm"),
        "制程说明文本", "low")
    result["board_thickness_note"] = notes.get("board_thickness_note", "")
    result["surface_finishes"] = notes.get("surface_finishes", [])
    result["impedance"] = bool(notes.get("impedance"))

    # 板框: 从 .brd 内嵌设计快照里读 BOARD GEOMETRY/OUTLINE, 用 Draw Size 标定
    try:
        from src.design_geometry import board_geometry

        qv = raw_scan.get("quickview") or {}
        geo = board_geometry(path, qv if qv.get("found") else None) if path else {"ok": False}
    except Exception:  # noqa: BLE001  几何解析失败不能影响主流程
        geo = {"ok": False}
    result["board_geometry"] = geo
    try:
        from src.design_geometry import load_design_snapshot
        from src.pad_metrics import extract_pad_metrics
        from src.silkscreen import silkscreen_stats, silkscreen_to_outline
        scale = float((geo.get("outline") or {}).get("scale_mm_per_unit") or 0.0)
        doc = load_design_snapshot(path)
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
    design["board_name"] = extracted.get("design_name") or extracted.get("board_name", "")
    if extracted.get("surface_finishes"):
        design["surface_finish"] = extracted["surface_finishes"][0]
    # 供报告说明字段出处 (不参与比对)
    design["_sources"] = sources or extracted.get("sources", {})
    design["_warnings"] = extracted.get("warnings", [])
    design["_drill_spans"] = extracted.get("drill_spans", [])
    design["_via_kind_unknown"] = extracted.get("via_kind_unknown", [])
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
    rows.append(("设计单位", extracted.get("units", "mil"), extracted.get("units_source", "")))
    if extracted.get("drill_spans"):
        rows.append(("钻孔跨度", " / ".join(extracted["drill_spans"]), "二进制钻孔表"))
    return rows

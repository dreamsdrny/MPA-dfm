"""
Evan_BRD: .brd 原始字节流提取 (Raw Binary Scanner)
=====================================================
Allegro 17.x 的 .brd 由「二进制数据库 + zip 归档」组成。zip 归档里只有约束对象
(PhysicalCSet / SpacingCSet ...), 而钻孔表、层叠命名、单位等信息以文本对象的形式
存在二进制体里。本模块直接扫描二进制体, 抽取不依赖 Allegro 也能拿到的设计量:

    units          图纸单位 (ALL UNITS ARE IN MILS / MILLIMETERS)
    drill_chart    钻孔表 (DRILL CHART: TOP to BOTTOM 等), 每个钻孔跨度的
                   孔径 / 公差 / 镀铜 / 数量
    padstacks      过孔焊盘栈命名 (VIA20D10A30 -> 焊盘20mil 钻孔10mil 反焊盘30mil)
    layers         层名 (TOP / BOTTOM / L2_GND ...), 用于推算层数
    notes          制程说明 (板厚 / 铜厚 / 表面处理 / 阻抗要求)

这里得到的是「设计声明值」, 不是实测几何: 图形数据在数据库里, 不做几何反算。
每个结果都带 source / confidence, 供上层决定能不能直接用于 DFM 判定。
"""
# 导入正则表达式模块
import re
# 导入类型注解相关类型
from typing import Dict, List, Optional, Tuple

# 模块公开接口，供外部调用
__all__ = ["scan_brd_raw", "parse_via_name"]

# 单位换算系数表：键为 brd 文件中的单位字符串，值为(换算为 mm 的乘数, 标准化单位名)
_UNIT_FACTOR = {
    "MILS": (0.0254, "mil"),
    "MIL": (0.0254, "mil"),
    "MILLIMETERS": (1.0, "mm"),
    "MILLIMETER": (1.0, "mm"),
    "MM": (1.0, "mm"),
    "INCHES": (25.4, "inch"),
    "INCH": (25.4, "inch"),
}

# 匹配 brd 文件中单位声明行，如 "ALL UNITS ARE IN MILLIMETERS"
_UNIT_RE = re.compile(
    rb"ALL UNITS ARE IN\s+(MILLIMETERS|MILLIMETER|INCHES|INCH|MILS|MIL|MM)\b")
# 匹配钻孔图表标题，提取图表名称（2~40字符）
_DRILL_TITLE_RE = re.compile(rb"DRILL CHART:\s*([A-Za-z0-9_ ]{2,40})")
# 匹配可打印 ASCII 文本（2~80字符），用于提取字符串内容
_TEXT_RE = re.compile(rb"[\x20-\x7e]{2,80}")

# 匹配公差格式，如 "+0.1/-0.1"
_TOL_RE = re.compile(r"^[+-]\d+(?:\.\d+)?/[+-]\d+(?:\.\d+)?$")
# 匹配小数格式，如 "12.34"
_DEC_RE = re.compile(r"^\d{1,4}\.\d{1,4}$")
# 匹配整数格式，2~6位数字
_INT_RE = re.compile(r"^\d{2,6}$")
# 匹配电镀类型标识，如 "PLAT"、"NON-PLAT"、"NPTH"、"PTH"（不区分大小写）
_PLATE_RE = re.compile(r"^(?:PLAT|NON[-_ ]?PLAT|NPTH|PTH)", re.I)

# 匹配层名，如 "L1_GND"、"L2_SIG" 等，提取层号
_LAYER_RE = re.compile(
    rb"\bL(\d{1,2})_(?:GND|PWR|SIG|VCC|PLANE|INNER|SIGNAL|GROUND|POWER)\d*\b")
# 匹配 QuickView 区域信息，提取边界坐标、名称、单位、精度、层数
_QUICKVIEW_RE = re.compile(
    rb"MINX=(-?\d+)\|MINY=(-?\d+)\|MAXX=(-?\d+)\|MAXY=(-?\d+)[^\x00]{0,400}?"
    rb"(?:STRING=Name:\s*([^\r\n\x00|]{1,80}))?[^\x00]{0,400}?"
    rb"Units:\s*([A-Za-z]+)[^\x00]{0,200}?Accuracy:\s*(\d+)[^\x00]{0,200}?Layers:\s*(\d+)")
# 匹配过孔命名格式：VIA<直径>D<钻孔>[A<公差>]，如 "VIA10D5A2"
_VIA_RE = re.compile(
    r"\bVIA(\d+(?:\.\d+)?)D(\d+(?:\.\d+)?)(?:A(\d+(?:\.\d+)?))?", re.I)
# 匹配任意过孔标记，如 "VIA10_20"、"VIA8MIL" 等
_VIA_ANY_RE = re.compile(rb"\bVIA[0-9][0-9A-Za-z_.\-]{1,30}", re.I)
# 另一种常见命名: VIA10-20 / VIA8_14 -> 钻孔-焊盘 (单位 mil)
_VIA_DASH_RE = re.compile(r"^VIA(\d+(?:\.\d+)?)[-_](\d+(?:\.\d+)?)$", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# 单位
# --------------------------------------------------------------------------
def _scan_units(raw: bytes) -> dict:
    # 在原始二进制数据中搜索单位声明（如 "ALL UNITS ARE IN ..."）
    m = _UNIT_RE.search(raw)
    
    # 如果未匹配到任何单位声明，返回默认结果
    if not m:
        return {
            "unit": None,           # 未识别到单位
            "factor": None,         # 未识别到换算因子
            "source": "未找到单位声明",  # 数据来源说明
            "confidence": "low"     # 置信度低
        }
    
    # 提取匹配到的单位关键字，并转换为大写字符串
    key = m.group(1).decode("ascii", "ignore").upper()
    
    # 根据单位关键字查找对应的换算因子和标准单位名称
    # 若未找到，则使用 (None, 原单位小写) 作为默认值
    factor, unit = _UNIT_FACTOR.get(key, (None, key.lower()))
    
    # 返回解析后的单位信息字典
    return {
        "unit": unit,               # 标准单位名称
        "factor": factor,           # 单位换算因子（如需要）
        "raw": m.group(0).decode("latin-1"),  # 原始匹配文本
        "source": "二进制文本 'ALL UNITS ARE IN ...'",  # 数据来源
        "confidence": "high",       # 置信度高
    }


# --------------------------------------------------------------------------
# 钻孔表
# --------------------------------------------------------------------------
def _chart_windows(raw: bytes) -> List[Tuple[int, int, str]]:
    """返回 [(start, end, 标题)], 每个 DRILL CHART 到下一个 DRILL CHART 之间。"""
    
    # 在原始二进制数据中查找所有 DRILL CHART 标题的起始位置
    starts = [m.start() for m in _DRILL_TITLE_RE.finditer(raw)]
    
    # 如果未找到任何 DRILL CHART 标题，返回空列表
    if not starts:
        return []
    
    # 初始化结果列表
    out = []
    
    # 遍历每个 DRILL CHART 标题的起始偏移量
    for i, off in enumerate(starts):
        # 确定当前 DRILL CHART 的结束位置：
        # 如果有下一个 DRILL CHART，则结束位置为下一个的起始位置；
        # 否则，限制最大窗口大小为起始位置 + 40000 字节
        end = min(starts[i + 1] if i + 1 < len(starts) else off + 40000, off + 40000)
        
        # 从当前偏移量开始匹配 DRILL CHART 标题正则
        m = _DRILL_TITLE_RE.match(raw, off)
        
        # 提取并清理标题文本；若匹配失败则使用空字符串
        title = _clean(m.group(1).decode("latin-1")) if m else ""
        
        # 将 (起始位置, 结束位置, 标题) 三元组加入结果列表
        out.append((off, end, title))
    
    # 返回所有 DRILL CHART 窗口信息
    return out


def _parse_span(title: str) -> Tuple[str, str]:
    """将 "TOP to BOTTOM" 解析为 ("TOP", "BOTTOM")"""
    
    # 使用不区分大小写的正则表达式，按 "to" 关键字分割标题字符串
    parts = re.split(r"\s+to\s+", title, flags=re.I)
    
    # 如果成功分割为两部分，分别去除首尾空白后返回
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    
    # 否则，将整个标题作为第一部分返回，第二部分为空字符串
    return title.strip(), ""


def _scan_drill(raw: bytes) -> dict:
    charts = _chart_windows(raw)
    if not charts:
        return {"rows": [], "charts": [], "source": "未找到钻孔表", "confidence": "low"}

    rows: List[dict] = []
    chart_titles = []
    for start, end, title in charts:
        chart_titles.append(title)
        seg = raw[start:end]
        toks = [(start + m.start(), m.group(0).decode("latin-1"))
                for m in _TEXT_RE.finditer(seg)]
        for i, (off, txt) in enumerate(toks):
            if not _PLATE_RE.match(txt):
                continue
            plating = "NPTH" if txt.upper().startswith(("NON", "NPTH")) else "PTH"

            tol = None
            for j in range(i - 1, max(-1, i - 25), -1):
                t_off, t_txt = toks[j]
                if off - t_off > 900:
                    break
                if _TOL_RE.match(t_txt):
                    tol = t_txt
                    break

            size = None
            for j in range(i - 1, max(-1, i - 30), -1):
                t_off, t_txt = toks[j]
                if off - t_off > 1400:
                    break
                if _DEC_RE.match(t_txt):
                    size = t_txt
                    break

            qty = None
            for j in range(i + 1, min(len(toks), i + 25)):
                t_off, t_txt = toks[j]
                if t_off - off > 900:
                    break
                if _INT_RE.match(t_txt):
                    qty = int(t_txt)
                    break

            if size is None:
                continue
            try:
                size_mil = float(size)
            except ValueError:
                continue
            if not (0.5 <= size_mil <= 600):
                continue
            if qty is not None and not (1 <= qty <= 2000000):
                continue
            rows.append({
                "span": title,
                "size_mil": size_mil,
                "tol": tol or "",
                "plating": plating,
                "qty": qty,
            })

    # 同一跨度内按 (孔径, 镀铜) 去重, 数量取最大值(同一张表可能重复出现)
    merged: Dict[Tuple[str, float, str], dict] = {}
    for r in rows:
        key = (r["span"], round(r["size_mil"], 3), r["plating"])
        cur = merged.get(key)
        if cur is None or (r["qty"] or 0) > (cur["qty"] or 0):
            merged[key] = r
    rows = sorted(merged.values(), key=lambda r: (r["size_mil"], r["plating"]))

    pth = [r for r in rows if r["plating"] == "PTH"]
    npth = [r for r in rows if r["plating"] == "NPTH"]
    return {
        "rows": rows,
        "charts": sorted(set(chart_titles)),
        "spans": sorted({r["span"] for r in rows}),
        "min_mil": min((r["size_mil"] for r in pth), default=None),
        "max_mil": max((r["size_mil"] for r in pth), default=None),
        "npth_min_mil": min((r["size_mil"] for r in npth), default=None),
        "total_holes": sum(r["qty"] or 0 for r in rows),
        "npth_holes": sum(r["qty"] or 0 for r in npth),
        "has_blind": any(split[0].upper() != "TOP" for split in
                         (_parse_span(r["span"]) for r in rows)),
        "source": "二进制图纸 'DRILL CHART' 表",
        "confidence": "medium",
    }


# --------------------------------------------------------------------------
# 过孔 / 焊盘栈
# 预编译一个正则表达式对象，用于从字符串末尾提取过孔（VIA）连接的起止层编号
# 匹配模式示例：VIA_L12-13、VIA_5_6、TOP_VIA-L3_L8 等
# 捕获组1：起始层号（1~2位数字）
# 捕获组2：结束层号（1~2位数字）
# [_\-]          匹配一个下划线或连字符作为前缀分隔符
# (?:L)?         可选的非捕获字母 L（常见于层标识）
# (\d{1,2})      捕获 1~2 位数字作为层号
# [-_]           匹配中间的分隔符（连字符或下划线）
# (?:L)?         可选的非捕获字母 L
# (\d{1,2})      捕获第二个层号
# $              锚定字符串结尾，确保匹配发生在末尾
# re.I           忽略大小写（对当前模式影响有限，但保持健壮性）
# --------------------------------------------------------------------------

_VIA_SPAN_RE = re.compile(r"[_\-](?:L)?(\d{1,2})[-_](?:L)?(\d{1,2})$", re.I)


def parse_via_name(name: str) -> Optional[dict]:
    """VIA20D10A28 -> {pad: 20, drill: 10, anti: 28} (单位 mil)。

    名字尾部带跨层标记时一并取出, 例如 VIA14D8_3-10 表示只连 L3~L10 的埋孔;
    这类焊盘栈不是通孔, 不能拿去和"机械通孔"的孔环能力比。
    """
    m = _VIA_RE.search(name)
    if m:
        pad = float(m.group(1))
        drill = float(m.group(2))
        anti = float(m.group(3)) if m.group(3) else None
    else:
        # VIA10-20 这种"钻孔-焊盘"命名
        m2 = _VIA_DASH_RE.match(name.strip())
        if not m2:
            return None
        drill = float(m2.group(1))
        pad = float(m2.group(2))
        anti = None
        if pad <= drill:      # VIA8-1 这类被截断的名字, 不能当焊盘栈用
            return None
    if pad <= 0 or drill <= 0 or pad <= drill:
        return None
    span = None
    ms = _VIA_SPAN_RE.search(name.strip())
    if ms:
        try:
            a, b = int(ms.group(1)), int(ms.group(2))
            if 0 < a < b <= 99:
                span = (a, b)
        except ValueError:
            span = None
    return {"pad_mil": pad, "drill_mil": drill, "anti_mil": anti,
            "annular_mil": round((pad - drill) / 2.0, 3), "span": span}


def _scan_padstacks(raw: bytes, factor: float) -> dict:
    names = []
    seen = set()
    for m in _VIA_ANY_RE.finditer(raw):
        n = m.group(0).decode("latin-1")
        if n not in seen:
            seen.add(n)
            names.append(n)
    vias = []
    for n in names:
        parsed = parse_via_name(n)
        if not parsed:
            continue
        vias.append({
            "name": n,
            "drill_mm": round(parsed["drill_mil"] * factor, 4),
            "pad_mm": round(parsed["pad_mil"] * factor, 4),
            "anti_mm": None if parsed["anti_mil"] is None else round(parsed["anti_mil"] * factor, 4),
            "annular_mm": round(parsed["annular_mil"] * factor, 4),
            "span": parsed.get("span"),
        })
    vias.sort(key=lambda v: v["drill_mm"])
    return {
        "names": names,
        "vias": vias,
        "min_drill_mm": vias[0]["drill_mm"] if vias else None,
        "min_annular_mm": min((v["annular_mm"] for v in vias), default=None),
        "source": "二进制焊盘栈命名 (VIA<pad>D<drill>A<anti>)",
        "confidence": "medium",
    }


# --------------------------------------------------------------------------
# 层
# --------------------------------------------------------------------------
def _scan_quickview(raw: bytes) -> dict:
    """设计预览元数据: 单位 / 精度 / 层数 / 图纸范围。"""
    m = _QUICKVIEW_RE.search(raw)
    if not m:
        return {"found": False, "source": "未找到 _QuickViewText 元数据", "confidence": "low"}
    minx, miny, maxx, maxy = (int(m.group(i)) for i in range(1, 5))
    name = (m.group(5) or b"").decode("latin-1").strip() or None
    unit = m.group(6).decode("latin-1").lower()
    factor = 0.0254 if unit.startswith("mil") else 1.0
    return {
        "found": True,
        "name": name,
        "minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy,
        "drawing_w_mm": round((maxx - minx) * factor, 2),
        "drawing_h_mm": round((maxy - miny) * factor, 2),
        "unit": "mil" if factor == 0.0254 else "mm",
        "accuracy": int(m.group(7)),
        "layer_count": int(m.group(8)),
        "source": "二进制 _QuickViewText 元数据",
        "confidence": "high",
    }


def _scan_layers(raw: bytes) -> dict:
    idx = set()
    names = set()
    for m in _LAYER_RE.finditer(raw):
        idx.add(int(m.group(1)))
        names.add(m.group(0).decode("latin-1"))
    note = ""
    by_name = None
    if idx and min(idx) <= 2:
        by_name = max(idx) + 1          # TOP=1, L2..Ln, BOTTOM=n+1
        gaps = [i for i in range(2, max(idx) + 1) if i not in idx]
        if gaps:
            note = f"层名缺号: {gaps}"
    return {
        "names": sorted(names),
        "inner_index": sorted(idx),
        "count_by_name": by_name,
        "note": note,
        "source": "二进制层名 L<n>_<TYPE>",
        "confidence": "low" if by_name is None else "medium",
    }


# --------------------------------------------------------------------------
# 制程说明
# --------------------------------------------------------------------------
_THICK_RE = re.compile(
    r"(?:OVERALL\s+)?(?:PCB\s+)?THICKNESS[^.]{0,40}?([0-9]*\.?[0-9]+)\s*(MM|MIL|INCH|\")?",
    re.I)
_OZ_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:OZ|OUNCE)", re.I)
_FINISH = [
    ("ENEPIG", "ENEPIG"), ("ENIG", "ENIG"), ("OSP", "OSP"),
    ("HARD GOLD", "HARD_GOLD"), ("IMMERSION SILVER", "IMMERSION_SILVER"),
    ("IMMERSION TIN", "IMMERSION_TIN"), ("HASL", "HASL"),
]


def _scan_notes(raw: bytes) -> dict:
    texts = []
    for m in re.finditer(rb"[\x20-\x7e]{25,400}", raw):
        s = _clean(m.group(0).decode("latin-1"))
        up = s.upper()
        if s.startswith("<") or "DEFAULTTHICKNESS" in up:
            continue                      # 材料库 XML, 不是制程说明
        if not any(k in up for k in ("THICKNESS", "SHALL", "MUST", "IMPEDANCE", "IPC-",
                                     "COPPER WEIGHT", "SURFACE FINISH", "FINISH")):
            continue
        texts.append(s)
        if len(texts) >= 80:
            break

    thickness_mm = None
    thickness_src = ""
    for t in texts:
        up = t.upper()
        if "DEFAULTTHICKNESS" in up or '="' in t or "NAME=" in up:
            continue                      # 材料库属性, 不是板厚说明
        if "PCB" not in up and "OVERALL" not in up:
            continue
        m = _THICK_RE.search(t)
        if not m:
            continue
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        unit = (m.group(2) or "").upper()
        if unit == "MM":
            mm = val
        elif unit == "MIL":
            mm = val * 0.0254
        elif unit in ("INCH", '"'):
            mm = val * 25.4
        else:
            # 说明里常写 "ALL DIMENSIONS ARE IN INCHES"; .063 这类按英寸, 大于 10 的按 mil
            mm = val * 25.4 if val < 1 else (val * 0.0254 if val > 10 else val)
        if 0.3 <= mm <= 10:
            thickness_mm = round(mm, 3)
            thickness_src = t[:120]
            break

    copper_oz = None
    for t in texts:
        m = _OZ_RE.search(t)
        if m:
            try:
                copper_oz = float(m.group(1))
                break
            except ValueError:
                pass

    joined = " ".join(texts).upper()
    finishes = sorted({label for key, label in _FINISH if key in joined})
    impedance = ("CONTROLLED IMPEDANCE" in joined or "IMPEDANCE TABLE" in joined)
    return {
        "texts": texts[:20],
        "board_thickness_mm": thickness_mm,
        "board_thickness_note": thickness_src,
        "copper_oz": copper_oz,
        "surface_finishes": finishes,
        "impedance": impedance,
        "source": "二进制制程说明文本",
        "confidence": "low",
    }


# --------------------------------------------------------------------------
# scan_brd_raw() 负责扫描 Allegro .brd 二进制文件
# 提取图纸级设计信息（单位、层数、钻孔表、焊盘栈、备注等），并返回一份结构化的“设计元数据报告”
# 未解析设计的 不解析走线、坐标、图形、反算尺寸 
# --------------------------------------------------------------------------
def scan_brd_raw(raw: bytes) -> dict:
    """扫描 .brd 二进制体, 返回原始设计量 (不含几何反算)。"""
    quickview = _scan_quickview(raw)
    units = _scan_units(raw)
    if not units.get("factor") and quickview.get("found"):
        units["factor"] = 0.0254 if quickview["unit"] == "mil" else 1.0
        units["unit"] = quickview["unit"]
        units["source"] = "由 _QuickViewText 的 Units 推断"
    factor = units.get("factor") or 0.0254

    drill = _scan_drill(raw)
    padstacks = _scan_padstacks(raw, factor)
    layers = _scan_layers(raw)
    notes = _scan_notes(raw)

    # 层数优先取预览元数据 (权威), 层名只用于展示与异常提示
    layer_count = quickview.get("layer_count") if quickview.get("found") else None
    layer_source = quickview.get("source") if layer_count else layers["source"]
    layer_confidence = "high" if layer_count else layers["confidence"]
    if layer_count and layers["count_by_name"] and layer_count != layers["count_by_name"]:
        layers["note"] = (layers.get("note") + "; " if layers.get("note") else "") + \
            f"层名推断 {layers['count_by_name']} 层, 与预览元数据 {layer_count} 层不一致, 以元数据为准"

    warnings = []
    if not units.get("unit"):
        warnings.append("未找到图纸单位声明, 按 mil 处理")
    if not drill["rows"]:
        warnings.append("未找到钻孔表, 孔径数据需手工补充")
    if not layer_count:
        warnings.append("未解析到层数, 需手工补充")

    return {
        "quickview": quickview,
        "units": units,
        "drill": drill,
        "padstacks": padstacks,
        "layers": layers,
        "layer_count": layer_count,
        "layer_count_source": layer_source,
        "layer_count_confidence": layer_confidence,
        "notes": notes,
        "warnings": warnings,
    }

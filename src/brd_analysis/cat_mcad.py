"""第 7 类: 机械结构与 ECAD/MCAD 数据交换。

对应 PCB Editor 的: DXF Export / IDF Export / IDX Export / STEP Export / Creo View。

这一类的口径是「能真生成的必须真生成, 生成不了的必须说清缺什么」:

  * DXF —— 快照几何 (板框 / 禁布 / 丝印 / 装配) 直接写成 DXF R12 ASCII 的
    机械二维图, 机械工程师能用 AutoCAD / LibreCAD 打开, 这是本类唯一能
    独立交付的产物;
  * IDF / IDX / STEP / Creo View —— 都依赖设计数据库里的器件坐标、旋转角、
    封装占位外形、器件高度和封装级三维模型映射, 这些**不在 .brd 内嵌快照里**,
    一律如实 UNAVAILABLE, 同时给出一份「导出准备度清单 CSV」,
    让人知道还差哪几项、在 Allegro 里具体怎么补。

设计纪律 (与 model.py 一致): **缺数据 ≠ 通过**。
DXF 在快照坐标用经验系数标定时状态降为 WARN, 并在结论里写明
「DXF 尺寸可能不准, 需核对」; 其余四项绝不伪造成 pass。
"""
import csv
import math
import os
import re
import tempfile
from typing import Dict, List, Optional, Tuple

from src.brd_analysis import facts
from src.brd_analysis.context import Context
from src.brd_analysis.model import (INFO, PASS, UNAVAILABLE, WARN, Capability,
                                    Result, unavailable)

# --------------------------------------------------------------------------
# 输出目录 / 文件名
# --------------------------------------------------------------------------
_FILE_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _safe_file(name: str, fallback: str = "board") -> str:
    """文件名安全化 (Windows 不允许的字符换成下划线)。"""
    text = _FILE_BAD_CHARS.sub("_", str(name or "").strip()).strip(" .")
    return text or fallback


def _mcad_dir(ctx: Context) -> str:
    """本类所有产物的落地目录 <out_dir>/mcad/。

    输出目录优先级: ctx.out_dir -> .brd 所在目录 -> 系统临时目录
    (三者都取不到时也不能把文件写到当前工作目录里, 那会污染仓库)。
    """
    base = ctx.out_dir or ""
    if not base and ctx.brd_path:
        base = os.path.dirname(ctx.brd_path)
    if not base:
        base = tempfile.gettempdir()
    path = os.path.join(base, "mcad")
    os.makedirs(path, exist_ok=True)
    return path


def _write_csv(path: str, columns: List[str], rows: List[dict]) -> str:
    """写明细 CSV (与本包 writers.write_csv 同口径)。

    编码用 utf-8-sig: 保留 BOM, 中文 Windows 上双击用 Excel 打开不乱码;
    纯 utf-8 的 CSV 在 Excel 里中文会变成乱码, 这份清单是给人看的, 必须能直接开。
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: ("" if row.get(col) is None else row.get(col))
                             for col in columns})
    return path


def _user_files(ctx: Context, suffixes) -> List[str]:
    """在用户给的文件/目录里找指定后缀的文件 (IDF/IDX/STEP/Creo View)。

    .brd 解析不出来的东西只能靠用户提供, 所以这里会翻:
      * --export-files 显式列出的文件
      * .brd 同目录 (只在 .brd 真存在时才翻, 避免对不存在的路径去列盘符根目录)
      * ctx.extra_dirs / ctx.out_dir
    只扫一层, 不做递归 —— 递归翻整个工程目录会把机械图、旧版本全扫进来。
    """
    wanted = tuple(s.lower() for s in suffixes)
    found: List[str] = []
    for path in ctx.export_files:
        if isinstance(path, str) and os.path.isfile(path) \
                and path.lower().endswith(wanted):
            ap = os.path.abspath(path)
            if ap not in found:
                found.append(ap)
    dirs: List[str] = []
    if ctx.brd_path and os.path.isfile(ctx.brd_path):
        dirs.append(os.path.dirname(os.path.abspath(ctx.brd_path)))
    dirs.extend(d for d in ctx.extra_dirs if d)
    if ctx.out_dir:
        dirs.append(ctx.out_dir)
    for directory in dirs:
        if not directory or not os.path.isdir(directory):
            continue
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            if not name.lower().endswith(wanted):
                continue
            full = os.path.join(directory, name)
            if os.path.isfile(full):
                ap = os.path.abspath(full)
                if ap not in found:
                    found.append(ap)
    return found


def _file_brief(paths: List[str]) -> str:
    """把文件列表写成一行人类可读的说明 (带大小)。"""
    parts = []
    for path in paths:
        try:
            kb = os.path.getsize(path) / 1024.0
        except OSError:
            kb = 0.0
        parts.append(f"{os.path.basename(path)} ({kb:.0f}KB)")
    return "、".join(parts)


# ==========================================================================
# 7.1 DXF Export —— 本类唯一能真生成的产物
# ==========================================================================
# DXF 图层: 标准层名 -> 颜色号 (R12 用 ACI 颜色索引)
_DXF_LAYER_ORDER: List[str] = ["OUTLINE", "KEEPIN", "SILKSCREEN_TOP",
                               "SILKSCREEN_BOTTOM", "ASSEMBLY_TOP",
                               "ASSEMBLY_BOTTOM"]
_DXF_LAYER_COLORS: Dict[str, int] = {"OUTLINE": 7, "KEEPIN": 1,
                                     "SILKSCREEN_TOP": 3, "SILKSCREEN_BOTTOM": 4,
                                     "ASSEMBLY_TOP": 5, "ASSEMBLY_BOTTOM": 6}
_DXF_LAYER_LABELS: Dict[str, str] = {
    "OUTLINE": "板框 / 板边",
    "KEEPIN": "禁布区 (ROUTE KEEPIN)",
    "SILKSCREEN_TOP": "顶层丝印",
    "SILKSCREEN_BOTTOM": "底层丝印",
    "ASSEMBLY_TOP": "顶层装配图",
    "ASSEMBLY_BOTTOM": "底层装配图",
}
# 只有这四类图形进机械二维图: 铜箔/阻焊/锡膏/文字不属于机械图内容
_DXF_KINDS: Tuple[str, ...] = ("outline", "keepin", "silk", "assembly")

# DXF 组码里不能出现的字符: 空格、斜杠、引号、逗号等在 R12 图层名里非法
_DXF_BAD_CHARS = re.compile(r'[<>/\\":;?*|=,\s]+')
_DXF_LAYER_MAX = 31      # DXF R12 图层名长度上限


def _dxf_layer_name(raw: str, fallback: str = "OTHER") -> str:
    """把快照里的 layer 原文规范化成合法的 DXF 图层名。

    DXF 图层名不能含空格和 / \\ " : ; ? * | = , 这类字符, 且 R12 上限 31 字符,
    所以统一转大写 + 非法字符换下划线 + 截断。空名退回 fallback。
    """
    name = _DXF_BAD_CHARS.sub("_", str(raw or "").strip().upper()).strip("_")
    if not name:
        name = fallback
    return name[:_DXF_LAYER_MAX]


def _side_of(text: str) -> str:
    """从 class/subclass 里判断面别 (TOP / BOTTOM)。"""
    up = str(text or "").upper()
    if "BOTTOM" in up or up.endswith("_B"):
        return "BOTTOM"
    if "TOP" in up or up.endswith("_T"):
        return "TOP"
    return ""


def _dxf_layer_of(seg: dict) -> str:
    """一条快照图形 -> DXF 图层名。

    规范化规则 (图层名从 seg['layer'] 的语义来, 而不是把 "BOARD GEOMETRY/OUTLINE"
    整串塞进去 —— 那样机械工程师在 CAD 里看到的是没法用的长名字):
        kind=outline   -> OUTLINE
        kind=keepin    -> KEEPIN
        kind=silk      -> SILKSCREEN_TOP / SILKSCREEN_BOTTOM (无面别时 SILKSCREEN)
        kind=assembly  -> ASSEMBLY_TOP / ASSEMBLY_BOTTOM   (无面别时 ASSEMBLY)
        其它           -> 对 seg['layer'] 原文做字符规范化
    """
    kind = str(seg.get("kind") or "")
    raw = str(seg.get("layer") or "")
    side = _side_of(raw) or _side_of(seg.get("subclass") or "")
    if kind == "outline":
        return "OUTLINE"
    if kind == "keepin":
        return "KEEPIN"
    if kind == "silk":
        return f"SILKSCREEN_{side}" if side else "SILKSCREEN"
    if kind == "assembly":
        return f"ASSEMBLY_{side}" if side else "ASSEMBLY"
    return _dxf_layer_name(raw, kind.upper() if kind else "OTHER")


def _c4(value) -> str:
    """坐标一律 %.4f (mm)。"""
    return f"{float(value):.4f}"


def _a6(value) -> str:
    """角度/半径用 %.6f。"""
    return f"{float(value):.6f}"


def _pair(code, value) -> List[str]:
    """DXF 组码: 一行组码 + 一行值, 永远是成对的。"""
    return [str(code), str(value)]


def _is_closed(pts: List[Tuple[float, float]], tol: float = 0.01) -> bool:
    if len(pts) < 3:
        return False
    return math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) <= tol


def _circle_from_3pts(p1, p2, p3) -> Optional[Tuple[float, float]]:
    """三点定圆 -> 圆心; 三点共线时返回 None。"""
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return (ux, uy)


def _norm_deg(rad: float) -> float:
    """弧度 -> DXF 角度 [0, 360) 度。

    浮点噪声 (例如 -7e-6 度) 会被归一化成 359.99999 度, 机械工程师读起来莫名其妙,
    所以 1e-3 度以内的偏差直接吸附到 0 度 —— 对 50mm 半径只有 0.9µm, 无实际影响。
    """
    deg = math.degrees(rad) % 360.0
    if deg > 359.999 or deg < 0.001:
        return 0.0
    return round(deg, 6)


def _arc_entity(layer: str, pts: List[Tuple[float, float]],
                radius_mm: float) -> Optional[dict]:
    """把圆弧的采样折线还原成 DXF 的 ARC / CIRCLE 实体。

    快照的圆弧在 geom.normalize 里已被采样成 9 点折线 (pts), 但首点、中点、
    末点都落在真实圆弧上, 三点定圆即可还原圆心与半径。
    DXF 的 ARC 语义是「从起始角逆时针扫到终止角」, **没有**方向字段, 所以必须
    用采样中点判断原始方向: 中点在逆时针弧上就照原样, 否则交换起止角。
    还原不出来 (共线/退化) 时返回 None, 由调用方退化成折线, 绝不让图形消失。
    """
    if len(pts) < 3:
        return None
    p0, p1 = pts[0], pts[-1]
    pm = pts[len(pts) // 2]
    third = pts[len(pts) // 3]
    two_third = pts[(2 * len(pts)) // 3]
    center = _circle_from_3pts(p0, pm, p1) or _circle_from_3pts(p0, third, two_third)
    if not center:
        return None
    radius = math.hypot(p0[0] - center[0], p0[1] - center[1])
    if radius <= 1e-6:
        return None
    if math.hypot(p1[0] - p0[0], p1[1] - p0[1]) < 1e-6:
        # 首末点重合 = 整圆
        return {"type": "circle", "layer": layer, "center": center,
                "radius": round(radius, 5)}
    a0 = math.atan2(p0[1] - center[1], p0[0] - center[0])
    a1 = math.atan2(p1[1] - center[1], p1[0] - center[0])
    am = math.atan2(pm[1] - center[1], pm[0] - center[0])
    span = (a1 - a0) % (2.0 * math.pi)
    if span <= 1e-9:
        return {"type": "circle", "layer": layer, "center": center,
                "radius": round(radius, 5)}
    mid = (am - a0) % (2.0 * math.pi)
    if mid <= span + 1e-9:
        start, end = a0, a1
    else:
        start, end = a1, a0
    start_deg, end_deg = _norm_deg(start), _norm_deg(end)
    if abs(end_deg - start_deg) < 1e-6:
        # 起止角归一化后重合: 画整圆而不是一段零长度弧
        return {"type": "circle", "layer": layer, "center": center,
                "radius": round(radius, 5)}
    return {"type": "arc", "layer": layer, "center": center,
            "radius": round(radius, 5),
            "start_deg": start_deg, "end_deg": end_deg}


def _point_polyline_dist(pt: Tuple[float, float], line) -> float:
    """点到折线的最短距离 (用于判断某段图形是不是板边的一部分)。"""
    px, py = pt
    best = None
    for i in range(len(line) - 1):
        ax, ay = line[i]
        bx, by = line[i + 1]
        dx, dy = bx - ax, by - ay
        den = dx * dx + dy * dy
        if den <= 1e-12:
            dist = math.hypot(px - ax, py - ay)
        else:
            t = ((px - ax) * dx + (py - ay) * dy) / den
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            dist = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        if best is None or dist < best:
            best = dist
    return float("inf") if best is None else best


def _board_edge_segments(segs: List[dict], edge_lines,
                         tol: float = 0.1) -> List[dict]:
    """从快照的 OUTLINE 图形里挑出真正属于板边的那几段。

    为什么不能直接导 ctx.segments(["outline"]): 快照里 PACKAGE GEOMETRY/OUTLINE
    (器件外框) 也会被 geom.classify 归成 outline, 那些不是板边。
    facts.outline_polylines 给出的是可信的板边折线, 但它把圆弧采样成折线了;
    这里拿原始图形按"每个采样点到板边折线的距离"做匹配, 匹配上的原始图形还带
    arc/radius_mm 信息, 于是板边圆弧能以真 ARC 实体写进 DXF, 而不是 8 段小直线。
    """
    if not edge_lines or not segs or len(segs) > 2000:
        return []
    kept: List[dict] = []
    for seg in segs:
        pts = seg.get("pts") or []
        if len(pts) < 2:
            continue
        probes = [pts[0], pts[len(pts) // 2], pts[-1]]
        if all(any(_point_polyline_dist(pt, line) <= tol for line in edge_lines)
               for pt in probes):
            kept.append(seg)
    return kept


def _polyline_length_mm(line) -> float:
    total = 0.0
    for i in range(len(line) - 1):
        total += math.hypot(line[i + 1][0] - line[i][0], line[i + 1][1] - line[i][1])
    return total


def _seg_length_mm(seg: dict) -> float:
    """一段图形的长度: 优先用 normalize 给的 length_mm, 没有就按折线算。"""
    try:
        value = float(seg.get("length_mm") or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    if value > 0:
        return value
    return _polyline_length_mm(seg.get("pts") or [])


def _outline_entity(line) -> Optional[dict]:
    """板边折线 -> 实体: 两点用 LINE, 多点用闭合 POLYLINE。"""
    pts: List[Tuple[float, float]] = []
    for pair in line or []:
        try:
            pts.append((float(pair[0]), float(pair[1])))
        except (TypeError, ValueError, IndexError):
            continue
    if len(pts) < 2:
        return None
    if len(pts) == 2:
        return {"type": "line", "layer": "OUTLINE", "a": pts[0], "b": pts[1]}
    return _polyline_entity("OUTLINE", pts, closed=True)


def _polyline_entity(layer: str, pts, closed: Optional[bool] = None) -> Optional[dict]:
    """折线实体; closed=True 时丢掉重复的首末点 (闭合由组码 70 表达)。"""
    clean: List[Tuple[float, float]] = []
    for pair in pts or []:
        try:
            x, y = float(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if not clean or abs(x - clean[-1][0]) > 1e-9 or abs(y - clean[-1][1]) > 1e-9:
            clean.append((x, y))
    if len(clean) < 2:
        return None
    is_closed = _is_closed(clean) if closed is None else bool(closed)
    if is_closed and len(clean) >= 3:
        clean = clean[:-1]
    if len(clean) < 2:
        return None
    return {"type": "polyline", "layer": layer, "pts": clean,
            "closed": is_closed and len(clean) >= 3}


def _dxf_entities_for_seg(seg: dict) -> List[dict]:
    """一段快照图形 -> DXF 实体列表。

    实体类型的选择理由 (要求里点名要写明):
      * 圆弧 (seg['arc'] 且能定出圆心) -> **ARC** 实体。机械工程师要能量半径/
        拾取圆心, 用折线近似会让他误以为板边是 8 段小直线。
      * 两点直线 -> **LINE** 实体。所有 CAD (含 R12 时代的老工具) 都认, 也
        便于逐段标注/测量。
      * 3 点以上的多段线 (板框轮廓、闭合禁布框) -> **POLYLINE/VERTEX/SEQEND**。
        DXF R12 (AC1009) **没有** LWPOLYLINE (那是 AC1015/R2000 才引入的),
        写 LWPOLYLINE 会被严格的 R12 阅读器丢掉整段; 所以这里用 R12 唯一合法的
        老式多段线写法。板框闭合时用组码 70=1 表达闭合, 不再重复首点。
    """
    pts: List[Tuple[float, float]] = []
    for pair in seg.get("pts") or []:
        try:
            pts.append((float(pair[0]), float(pair[1])))
        except (TypeError, ValueError, IndexError):
            continue
    dedup: List[Tuple[float, float]] = []
    for pt in pts:
        if not dedup or abs(pt[0] - dedup[-1][0]) > 1e-9 or abs(pt[1] - dedup[-1][1]) > 1e-9:
            dedup.append(pt)
    if len(dedup) < 2:
        return []
    layer = _dxf_layer_of(seg)
    if seg.get("arc"):
        try:
            radius_mm = float(seg.get("radius_mm") or 0.0)
        except (TypeError, ValueError):
            radius_mm = 0.0
        arc = _arc_entity(layer, dedup, radius_mm)
        if arc:
            return [arc]
        # 定不出圆心 (共线/退化) 时不丢图形: 落到下面的 LINE / POLYLINE 分支
    if len(dedup) == 2:
        return [{"type": "line", "layer": layer, "a": dedup[0], "b": dedup[1]}]
    ent = _polyline_entity(layer, dedup)
    return [ent] if ent else []


def _entity_points(entity: dict) -> List[Tuple[float, float]]:
    """一个实体占用的点 (圆弧用外接方框的两个角点), 用于算 bbox。"""
    typ = entity.get("type")
    if typ == "line":
        return [entity["a"], entity["b"]]
    if typ == "polyline":
        return list(entity.get("pts") or [])
    if typ in ("arc", "circle"):
        cx, cy = entity["center"]
        radius = float(entity.get("radius") or 0.0)
        return [(cx - radius, cy - radius), (cx + radius, cy + radius)]
    return []


def _dxf_extents(entities: List[dict]) -> Tuple[float, float, float, float]:
    """$EXTMIN/$EXTMAX: 优先用板框 (OUTLINE 层) 的 bbox, 没有板框才退回全部实体。"""
    def box_of(subset) -> Optional[Tuple[float, float, float, float]]:
        pts = [pt for ent in subset for pt in _entity_points(ent)]
        if not pts:
            return None
        xs = [pt[0] for pt in pts]
        ys = [pt[1] for pt in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    return (box_of([e for e in entities if e.get("layer") == "OUTLINE"])
            or box_of(entities) or (0.0, 0.0, 0.0, 0.0))


def _entity_length_mm(entity: dict) -> float:
    typ = entity.get("type")
    if typ == "line":
        a, b = entity["a"], entity["b"]
        return math.hypot(b[0] - a[0], b[1] - a[1])
    if typ == "polyline":
        pts = entity.get("pts") or []
        total = sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                    for i in range(len(pts) - 1))
        if entity.get("closed") and len(pts) > 2:
            total += math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])
        return total
    if typ == "circle":
        return 2.0 * math.pi * float(entity.get("radius") or 0.0)
    if typ == "arc":
        span = (float(entity["end_deg"]) - float(entity["start_deg"])) % 360.0
        return math.radians(span) * float(entity.get("radius") or 0.0)
    return 0.0


def _entity_sort_key(entity: dict):
    layer = entity.get("layer") or ""
    layer_rank = (_DXF_LAYER_ORDER.index(layer) if layer in _DXF_LAYER_ORDER
                  else len(_DXF_LAYER_ORDER))
    type_rank = {"polyline": 0, "line": 1, "arc": 2, "circle": 3}.get(
        entity.get("type"), 9)
    pts = _entity_points(entity)
    first = pts[0] if pts else (0.0, 0.0)
    return (layer_rank, type_rank, round(first[1], 4), round(first[0], 4))


def _used_layers(entities: List[dict]) -> List[str]:
    seen: List[str] = []
    for entity in entities:
        layer = entity.get("layer") or "?"
        if layer not in seen:
            seen.append(layer)
    return sorted(seen, key=lambda name: (
        _DXF_LAYER_ORDER.index(name) if name in _DXF_LAYER_ORDER
        else len(_DXF_LAYER_ORDER), name))


def _entity_lines(entity: dict) -> List[str]:
    """一个 DXF 实体的组码行 (每行一组: 组码 / 值)。"""
    layer = entity["layer"]
    typ = entity["type"]
    out: List[str] = []
    if typ == "line":
        out += _pair(0, "LINE") + _pair(8, layer)
        out += _pair(10, _c4(entity["a"][0])) + _pair(20, _c4(entity["a"][1]))
        out += _pair(30, _c4(0.0))
        out += _pair(11, _c4(entity["b"][0])) + _pair(21, _c4(entity["b"][1]))
        out += _pair(31, _c4(0.0))
    elif typ == "polyline":
        out += _pair(0, "POLYLINE") + _pair(8, layer)
        out += _pair(66, 1)                      # 66=1: 后面跟着 VERTEX
        out += _pair(70, 1 if entity.get("closed") else 0)
        out += _pair(10, _c4(0.0)) + _pair(20, _c4(0.0)) + _pair(30, _c4(0.0))
        for x, y in entity.get("pts") or []:
            out += _pair(0, "VERTEX") + _pair(8, layer)
            out += _pair(10, _c4(x)) + _pair(20, _c4(y)) + _pair(30, _c4(0.0))
        out += _pair(0, "SEQEND") + _pair(8, layer)
    elif typ == "arc":
        cx, cy = entity["center"]
        out += _pair(0, "ARC") + _pair(8, layer)
        out += _pair(10, _c4(cx)) + _pair(20, _c4(cy)) + _pair(30, _c4(0.0))
        out += _pair(40, _a6(entity["radius"]))
        out += _pair(50, _a6(entity["start_deg"]))
        out += _pair(51, _a6(entity["end_deg"]))
    elif typ == "circle":
        cx, cy = entity["center"]
        out += _pair(0, "CIRCLE") + _pair(8, layer)
        out += _pair(10, _c4(cx)) + _pair(20, _c4(cy)) + _pair(30, _c4(0.0))
        out += _pair(40, _a6(entity["radius"]))
    return out


def _build_dxf(entities: List[dict],
               extents: Optional[Tuple[float, float, float, float]] = None) -> str:
    """生成 DXF R12 ASCII (AC1009) 文本。

    结构: HEADER (版本/单位/范围) -> TABLES (LAYER 表) -> ENTITIES -> EOF。
    $INSUNITS 是 R2000 才有的头变量, R12 阅读器会忽略未知变量 (无害);
    同时写 R12 本来就有的 $MEASUREMENT=1(公制) 与 $LUNITS=2(十进制), 双保险。
    所有坐标是毫米, 直接以 %.4f 写盘, 不做任何缩放 —— 机械侧打开就是真实尺寸。
    """
    ext = tuple(extents) if extents else _dxf_extents(entities)
    layers = _used_layers(entities)

    out: List[str] = []
    out += _pair(0, "SECTION") + _pair(2, "HEADER")
    out += _pair(9, "$ACADVER") + _pair(1, "AC1009")
    out += _pair(9, "$INSUNITS") + _pair(70, 4)          # 4 = 毫米
    out += _pair(9, "$MEASUREMENT") + _pair(70, 1)       # 1 = 公制
    out += _pair(9, "$LUNITS") + _pair(70, 2)            # 2 = 十进制
    out += _pair(9, "$EXTMIN")
    out += _pair(10, _c4(ext[0])) + _pair(20, _c4(ext[1])) + _pair(30, _c4(0.0))
    out += _pair(9, "$EXTMAX")
    out += _pair(10, _c4(ext[2])) + _pair(20, _c4(ext[3])) + _pair(30, _c4(0.0))
    out += _pair(0, "ENDSEC")

    out += _pair(0, "SECTION") + _pair(2, "TABLES")
    out += _pair(0, "TABLE") + _pair(2, "LAYER") + _pair(70, len(layers))
    for name in layers:
        out += _pair(0, "LAYER") + _pair(2, name)
        out += _pair(70, 0)                               # 0 = 图层打开且未冻结
        out += _pair(62, int(_DXF_LAYER_COLORS.get(name, 7)))
        out += _pair(6, "CONTINUOUS")
    out += _pair(0, "ENDTAB")
    out += _pair(0, "ENDSEC")

    out += _pair(0, "SECTION") + _pair(2, "ENTITIES")
    for entity in entities:
        out += _entity_lines(entity)
    out += _pair(0, "ENDSEC")
    out += _pair(0, "EOF")
    return "\n".join(out) + "\n"


def _layer_stats(entities: List[dict]) -> List[dict]:
    """每层实体数量统计 (报告里的明细表, 也是"这图里到底有什么"的凭据)。"""
    order = {name: idx for idx, name in enumerate(_DXF_LAYER_ORDER)}
    agg: Dict[str, dict] = {}
    for entity in entities:
        layer = entity.get("layer") or "?"
        row = agg.setdefault(layer, {
            "DXF 图层": layer,
            "来源类别": _DXF_LAYER_LABELS.get(layer, "其它图形 (按快照 class/subclass 规范化)"),
            "实体数": 0, "LINE 数": 0, "ARC 数": 0, "POLYLINE 数": 0, "长度mm": 0.0,
        })
        row["实体数"] += 1
        typ = entity.get("type")
        if typ == "line":
            row["LINE 数"] += 1
        elif typ in ("arc", "circle"):
            row["ARC 数"] += 1
        elif typ == "polyline":
            row["POLYLINE 数"] += 1
        row["长度mm"] = round(row["长度mm"] + _entity_length_mm(entity), 3)
    return [agg[name] for name in sorted(agg, key=lambda n: (order.get(n, 99), n))]


def _count_closed_loops(segs: List[dict], tol: float = 0.02) -> int:
    """数一数这些图形里有多少个闭合环 (判定口径与 cat_physical 的闭合环一致)。

    端点吸附 (0.02mm 容差) 建图, 只把"每个点度数正好是 2"的连通片算作闭合环 ——
    板框的最外圈是一个环, 多出来的环就是开槽/内孔; 零散线段不会构成环。
    """
    def key(pt):
        return (round(pt[0] / tol), round(pt[1] / tol))

    adj: Dict[tuple, set] = {}
    for seg in segs:
        pts = seg.get("pts") or []
        for i in range(len(pts) - 1):
            a, b = key(pts[i]), key(pts[i + 1])
            if a == b:
                continue
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)

    seen = set()
    loops = 0
    for start in adj:
        if start in seen:
            continue
        stack = [start]
        comp = []
        closed = True
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            comp.append(cur)
            if len(adj.get(cur, ())) != 2:
                closed = False
            for nxt in adj.get(cur, ()):
                if nxt not in seen:
                    stack.append(nxt)
        if closed and len(comp) >= 3:
            loops += 1
    return loops


def _board_edge_segs(ctx: Context, outline_lines=None) -> List[dict]:
    """只属于板边的那几段原始图形 (cap_dxf 与 IDF 准备度共用)。

    返回 [] 表示"匹配不可靠, 请调用方改用 facts.outline_polylines 的折线"。
    """
    outline_segs = ctx.segments(["outline"])
    lines = outline_lines if outline_lines is not None else facts.outline_polylines(ctx)
    if not lines:
        return []
    kept = _board_edge_segments(outline_segs, lines)
    edge_len = sum(_polyline_length_mm(line) for line in lines)
    kept_len = sum(_seg_length_mm(seg) for seg in kept)
    if kept and (edge_len <= 0 or kept_len >= edge_len * 0.95):
        return kept
    return []


def cap_dxf(ctx: Context) -> Result:
    cap = CATALOG_MAP["mcad.dxf"]
    warn = facts.geometry_warning(ctx)

    # 板框: 先用 facts.outline_polylines (design_geometry 的"取最外一圈"算法) 定位
    # 板边, 再把原始图形按距离匹配回来 —— 这样板边圆弧能写成真 ARC 实体。
    # 匹配不上时退回折线, 保证板框一定画得出来。
    entities: List[dict] = []
    outline_segs = ctx.segments(["outline"])
    outline_lines = facts.outline_polylines(ctx)
    outline_source = "内嵌快照 BOARD GEOMETRY/OUTLINE (取最外一圈, 折线)"
    if outline_lines:
        # 匹配回来的段总长必须基本等于板边折线总长, 否则说明有板边段没匹配上
        # —— 机械图缺一段板边比圆弧退化成折线严重得多, 这时整体退回折线。
        kept = _board_edge_segs(ctx, outline_lines)
        if kept:
            for seg in kept:
                entities.extend(_dxf_entities_for_seg(seg))
            outline_source = "内嵌快照 BOARD GEOMETRY/OUTLINE (板边逐段, 圆弧用 ARC)"
        else:
            for line in outline_lines:
                ent = _outline_entity(line)
                if ent:
                    entities.append(ent)
    else:
        for seg in outline_segs:
            entities.extend(_dxf_entities_for_seg(seg))
        outline_source = "内嵌快照 BOARD GEOMETRY/OUTLINE (逐段)"
    for seg in ctx.segments(["keepin", "silk", "assembly"]):   # _DXF_KINDS 去掉 outline
        entities.extend(_dxf_entities_for_seg(seg))

    if not entities:
        res = unavailable(
            cap, "没有可导出的二维几何 (板框/禁布/丝印/装配 一类都没有)",
            "确认 .brd 有内嵌设计快照且板框标定成功 (Draw Size); "
            "或直接用 Allegro: File > Export > DXF 导出机械图")
        if warn:
            res.warn(warn)
        return res

    entities.sort(key=_entity_sort_key)
    ext = _dxf_extents(entities)
    text = _build_dxf(entities, ext)
    path = os.path.join(_mcad_dir(ctx), f"{_safe_file(ctx.board_name)}.dxf")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    rows = _layer_stats(entities)
    layers = [row["DXF 图层"] for row in rows]
    total_len = round(sum(_entity_length_mm(e) for e in entities), 3)
    area = facts.region_areas(ctx)

    res = Result(cap, WARN if not ctx.scale_confident else PASS, "")
    res.metric("DXF 版本", "R12 ASCII (AC1009)", "", "本工具生成")
    res.metric("单位", "mm ($INSUNITS=4 / $MEASUREMENT=1)", "", "本工具生成")
    res.metric("DXF 图层数", len(layers), "层", "按图形类别归层")
    res.metric("DXF 实体数", len(entities), "个", "LINE + ARC + POLYLINE")
    res.metric("导出图形总长", total_len, "mm", "按实体几何累加",
               "high" if ctx.scale_confident else "low")
    if area.get("ok"):
        res.metric("板框尺寸", f"{area['width_mm']} × {area['height_mm']}", "mm",
                   outline_source, "high" if ctx.scale_confident else "low")
    res.metric("导出范围", "板框 / 禁布 / 丝印 / 装配 (不含铜箔、阻焊、锡膏、文字)",
               "", "本工具口径")
    res.add_all(rows, ["DXF 图层", "来源类别", "实体数", "LINE 数", "ARC 数",
                       "POLYLINE 数", "长度mm"])
    res.summary = (f"已生成 DXF R12 (AC1009, 单位 mm): {len(entities)} 个实体, "
                   f"{len(layers)} 个图层 ({'、'.join(layers)}); "
                   f"文件 {os.path.basename(path)}")
    if not ctx.scale_confident:
        res.summary += " —— 快照坐标是经验系数换算的, DXF 尺寸可能不准, 需核对"
    res.artifact("dxf", path, "DXF R12 ASCII 机械二维图 (单位 mm)")
    res.source("内嵌设计快照 BOARD GEOMETRY/OUTLINE、ROUTE KEEPIN、"
               "SILKSCREEN、ASSEMBLY")
    res.extra.update({"dxf_path": path, "layers": layers,
                      "entity_count": len(entities), "layer_stats": rows,
                      "not_exported_kinds": ["etch", "mask", "paste", "pad", "text"]})
    if not ctx.scale_confident:
        res.warn("快照坐标是用经验系数换算的 (未取到 .brd 的 Draw Size): "
                 "**这份 DXF 的线性尺寸可能不准, 需人工核对** "
                 "(拿板框长宽跟机械图/板厂资料对一下再发给结构)")
    return res


# ==========================================================================
# 7.2 IDF Export
# ==========================================================================
_IDF_HOWTO = ("Allegro: File > Export > IDF (输出 .emn 板 + .emp 器件), "
              "导出前必须给每个器件定义高度属性 PLACE_BOUND / "
              "PACKAGE_HEIGHT_MIN/MAX, 否则 IDF 里器件是零高度的扁片")

_IDF_COLUMNS = ["项目", "状态", "现有数据 / 来源", "缺什么", "在 Allegro 里怎么补"]


def _idf_readiness_rows(ctx: Context) -> List[dict]:
    """IDF 导出准备度清单: 逐项说清「有 / 部分有 / 没有」。"""
    rows: List[dict] = []
    area = facts.region_areas(ctx)
    comp = facts.components(ctx)
    comps = ctx.exports.get("components") or []
    asm = ctx.segments(["assembly"])
    keepin = ctx.segments(["keepin"])
    holes = ctx.exports.get("holes") or []
    drills = facts.drill_by_size(ctx)
    thickness = ctx.design.get("board_thickness_mm")

    def add(item: str, status: str, have: str, lack: str, howto: str) -> None:
        rows.append({"项目": item, "状态": status, "现有数据 / 来源": have,
                     "缺什么": lack, "在 Allegro 里怎么补": howto})

    if area.get("ok"):
        add("板外形 (Board Outline)", "✔ 有",
            f"{area['width_mm']} × {area['height_mm']} mm, "
            f"{area['outline_count']} 组折线 (内嵌快照 BOARD GEOMETRY/OUTLINE)",
            "", "无需补充; 若要更精确, 直接用 Allegro 导出的 .emn BOARD_OUTLINE 段")
    else:
        add("板外形 (Board Outline)", "✘ 缺",
            area.get("reason", "没有可用板框折线"), "闭合的板外形",
            "确认 BOARD GEOMETRY/OUTLINE 完整, 或在 Allegro 里 File > Export > IDF 时指定板外形层")

    if thickness:
        add("板厚 (IDF 头里要写)", "△ 部分", f"{thickness} mm (制程说明文本)",
            "叠层/板厚的权威值 (当前置信度低)", ctx.howto_stackup())
    else:
        add("板厚 (IDF 头里要写)", "✘ 缺", "没有取到板厚", "成品板厚",
            ctx.howto_stackup())

    if comp.get("ok"):
        add("器件位号清单 (refdes)", "✔ 有",
            f"{comp['count']} 个 (objects/Part.xml)", "", "无需补充")
    else:
        add("器件位号清单 (refdes)", "✘ 缺",
            comp.get("reason", "BRD 无 objects/Part.xml"), "器件位号清单",
            "确认 .brd 的 objects/Part.xml 完整")

    if comps:
        add("器件 XY 坐标", "✔ 有",
            f"{len(comps)} 个 (导入的 Placement/SKILL 坐标文件)", "",
            "无需补充; IDF 会沿用 Allegro 导出时的坐标")
    else:
        add("器件 XY 坐标", "✘ 缺", "占位清单里没有坐标",
            "每个器件的 x/y (mm)", ctx.howto_components_xy())

    with_rot = sum(1 for c in comps if c.get("rotation") is not None)
    if comps and with_rot:
        add("器件旋转角 / 面别", "✔ 有" if with_rot == len(comps) else "△ 部分",
            f"{with_rot}/{len(comps)} 条带旋转角 (导入的坐标文件)",
            "" if with_rot == len(comps) else f"{len(comps) - with_rot} 条缺旋转角",
            "Allegro: File > Export > Placement 会带 Rotation 与 Layer")
    else:
        add("器件旋转角 / 面别", "✘ 缺", "坐标文件里没有旋转角/面别",
            "每个器件的旋转角与 TOP/BOTTOM", ctx.howto_components_xy())

    add("器件高度 (Height)", "✘ 缺",
        "快照只有二维图形, 没有任何高度字段",
        "每个器件的 PACKAGE_HEIGHT_MIN/MAX 或 PLACE_BOUND 拉伸高度",
        "Allegro: 在封装里定义 PLACE_BOUND 高度 (Package Geometry > Place_Bound_Top "
        "的 Height 属性); 没有它 File > Export > IDF 导出的器件是零厚度")

    if asm:
        add("封装占位外形 (Place Bound)", "△ 部分",
            f"快照里有 {len(asm)} 条 PLACE_BOUND/ASSEMBLY 图形 (mm 坐标)",
            "图形与位号的对应关系 (快照不带器件坐标, 无法归属到具体器件)",
            "Allegro: File > Export > IDF 会直接输出 .emp 的占位外框; "
            "或提供 Placement 后由工具按坐标就近匹配")
    else:
        add("封装占位外形 (Place Bound)", "✘ 缺", "快照里没有 PLACE_BOUND/ASSEMBLY 图形",
            "每个封装的占位外框", "Allegro: 用 Place_Bound 定义封装占位区")

    if holes:
        add("安装孔 / 定位孔坐标", "✔ 有",
            f"{len(holes)} 个孔 (导入的 Excellon/SKILL 钻孔文件)", "",
            "无需补充")
    else:
        add("安装孔 / 定位孔坐标", "✘ 缺",
            (f"钻孔表有 {len(drills)} 种孔径, 但没有坐标" if drills
             else "既没有钻孔坐标也没有钻孔表"),
            "每个孔的 x/y 与孔径", ctx.howto_holes_xy())

    if keepin:
        add("禁布区 / 限高区 (Keepin)", "△ 部分",
            f"快照里有 {len(keepin)} 条 ROUTE KEEPIN 图形 (二维轮廓)",
            "限高值 (IDF 的 PLACE_KEEPOUT 需要 Z 向高度)",
            "Allegro: 在约束管理器/区域里定义限高, Export IDF 时勾选 keepout")
    else:
        add("禁布区 / 限高区 (Keepin)", "✘ 缺", "没有 ROUTE KEEPIN 图形",
            "禁布/限高区域", "Allegro: 用 Route Keepin / Package Keepin 定义后再导出")

    # 开槽/内孔: 数板边图形里的闭合环, 最外一圈是外形, 多出来的环才是开槽。
    # 不能用 region_areas 的 outline_count (那是折线组数, 一块矩形板拆成 4 段就是 4)。
    edge_segs = _board_edge_segs(ctx)
    loops = _count_closed_loops(edge_segs) if edge_segs else 0
    if not area.get("ok"):
        add("板边开槽 / 内孔", "✘ 缺", area.get("reason", "没有可用板框折线"),
            "板外形与开槽轮廓", "先修好 BOARD GEOMETRY/OUTLINE")
    elif loops >= 2:
        add("板边开槽 / 内孔", "✔ 有",
            f"板框闭合环 {loops} 个 (外环 1 + 内环 {loops - 1}, 疑似开槽/内孔)",
            "", "无需补充; Allegro 导出 IDF 时开槽会写进 OTHER_OUTLINE 段")
    elif loops == 1:
        add("板边开槽 / 内孔", "✔ 有",
            "板框是 1 个闭合环, 未发现内环 (本板无开槽)", "",
            "无需补充; 若结构侧另有开槽要求, 需在 Allegro 的板框里先画出来")
    else:
        add("板边开槽 / 内孔", "△ 部分",
            "板框图形没有构成闭合环 (可能有断点/缺口)",
            "闭合的板外形 (开槽更无从判断)",
            "Allegro: 检查 BOARD GEOMETRY/OUTLINE 是否闭合 (有断点时 IDF 会报错)")

    objects = ctx.objects
    idf_entries = [name for name in objects
                   if any(k in name.upper() for k in ("IDF", "IDX"))]
    if idf_entries:
        add(".brd 归档里的 IDF/IDX 数据段", "✔ 有",
            "、".join(sorted(idf_entries)[:5]), "", "无需补充")
    else:
        add(".brd 归档里的 IDF/IDX 数据段", "✘ 缺",
            "归档条目里没有 IDF/IDX 相关数据", "IDF 数据本身",
            "Allegro: File > Export > IDF（本工具不生成 .emn/.emp）")
    return rows


def cap_idf(ctx: Context) -> Result:
    cap = CATALOG_MAP["mcad.idf"]
    rows = _idf_readiness_rows(ctx)
    res = unavailable(
        cap,
        "IDF (.emn/.emp) 需要器件 XY 坐标、旋转角、封装外形尺寸与器件高度, "
        "这些数据不在 .brd 内嵌快照里, 本工具无法生成合规的 IDF",
        _IDF_HOWTO)
    res.add_all(rows, list(_IDF_COLUMNS))

    ok = sum(1 for r in rows if str(r["状态"]).startswith("✔"))
    part = sum(1 for r in rows if str(r["状态"]).startswith("△"))
    miss = sum(1 for r in rows if str(r["状态"]).startswith("✘"))
    res.metric("准备度项数", len(rows), "项", "按 IDF 导出所需数据逐项核对")
    res.metric("已具备", ok, "项", "同上")
    res.metric("部分具备", part, "项", "同上")
    res.metric("缺失", miss, "项", "同上")
    comp = facts.components(ctx)
    res.metric("器件位号数", comp.get("count") if comp.get("ok") else None, "个",
               "objects/Part.xml", "high" if comp.get("ok") else "")
    res.metric("有坐标的器件", len(ctx.exports.get("components") or []), "个",
               "导入的 Placement/SKILL 坐标文件", "high")
    res.metric("器件高度数据", "无", "", "快照无高度字段", "high")
    lacks = [str(r["项目"]) for r in rows if str(r["状态"]).startswith("✘")]
    res.summary = (f"IDF 导出条件: {ok} 项具备 / {part} 项部分 / {miss} 项缺失"
                   + ("; 仍缺 " + "、".join(lacks[:4]) + (" 等" if len(lacks) > 4 else "")
                      if lacks else "")
                   + "; 本工具不生成 .emn/.emp, 必须用 Allegro: File > Export > IDF 导出")
    if not ctx.has_components_xy:
        res.need("器件 XY 坐标 / 旋转角 / 面别", ctx.howto_components_xy())
    res.need("器件高度 (PACKAGE_HEIGHT_MIN/MAX 或 PLACE_BOUND 拉伸高度)",
             "Allegro: 在封装里定义 Place_Bound 与高度属性, 否则 IDF 里的器件是零厚度")
    res.need("安装孔 / 定位孔坐标", ctx.howto_holes_xy())

    path = os.path.join(_mcad_dir(ctx),
                        f"{_safe_file(ctx.board_name)}_IDF导出准备度.csv")
    _write_csv(path, list(_IDF_COLUMNS), rows)
    res.artifact("csv", path, "IDF 导出准备度清单 (逐项: 有/部分/缺 + 怎么补)")
    res.source("内嵌快照几何 + objects/Part.xml + 导入的坐标/钻孔文件")
    res.extra["readiness"] = rows
    res.extra["idf_parse_plan"] = {
        "如果用户把 Allegro 导出的 IDF 交给本工具, 工具能做什么": [
            "解析 .emn 的 BOARD_OUTLINE 段 (mm), 与快照板框 bbox 逐边比对, "
            "报出偏差 > 0.1mm 的边 (核对两边板外形是否同一版)",
            "解析 .emn 的 DRILLED_HOLES / OTHER_OUTLINE, 与导入的 Excellon 钻孔、"
            "板框开槽核对孔位/孔径/槽位",
            "解析 .emp 的 PLACEMENT 段 (refdes/x/y/rot/side/height), 与导入的 "
            "Placement 坐标核对: 位移 > 0.05mm、旋转不一致、面别翻转、器件增删",
            "用 .emp 的 PLACE_REGION 外形与快照 PLACE_BOUND 图形按坐标就近匹配, "
            "核对封装占位外框尺寸",
            "输出差异清单 CSV (器件 / 字段 / ECAD 值 / MCAD 值 / 差值) 供结构工程师确认",
        ],
        "注意": "本轮 .brd 里既没有器件坐标也没有高度, 所以 IDF 本体必须由 Allegro "
                "导出; 本工具只做导出准备度核对与后续的 IDF 解析比对",
    }
    return res


# ==========================================================================
# 7.3 IDX Export
# ==========================================================================
_IDX_SUFFIXES = (".idx", ".idz", ".emn", ".emp", ".idf")

_IDX_COLUMNS = ["比对面", "所需数据", "判据 / 阈值", "输出", "实现思路"]

_IDX_PLAN: List[dict] = [
    {"比对面": "器件位移", "所需数据": "两侧的 PLACEMENT (refdes, x, y, rot, side)",
     "判据 / 阈值": "|Δx| 或 |Δy| > 0.05mm, 或 |Δrot| > 0.1°",
     "输出": "位移器件清单 (refdes/ECAD 值/MCAD 值/差值)",
     "实现思路": "按 refdes 建索引逐字段比对; 先按 UNITS 段把单位统一成 mm "
                 "(IDF 是 mm, 部分 IDX 可能是 mil)"},
    {"比对面": "器件增删", "所需数据": "两侧的 refdes 集合",
     "判据 / 阈值": "只在单侧出现的 refdes",
     "输出": "新增 / 删除器件清单",
     "实现思路": "集合差集; 增删要排除位号重命名 (x/y 相同但位号不同时提示疑似重命名)"},
    {"比对面": "器件面别翻转", "所需数据": "两侧的 side (TOP/BOTTOM)",
     "判据 / 阈值": "同一位号两侧面别不同",
     "输出": "面别翻转清单",
     "实现思路": "面别翻转会让镜像关系整体反掉, 必须单列高亮 (结构干涉的高风险项)"},
    {"比对面": "板外形变更", "所需数据": "两侧的 BOARD_OUTLINE 折线 (mm)",
     "判据 / 阈值": "顶点最近点距离 > 0.1mm, 或折线段数变化",
     "输出": "外形差异边清单",
     "实现思路": "把折线按周长参数化做最近点匹配 (近似 Hausdorff 距离), "
                 "段数不同时先按周长比例粗对齐再比"},
    {"比对面": "封装占位 / 限高区变更", "所需数据": "两侧的 PLACE_REGION / PLACE_KEEPOUT 外框",
     "判据 / 阈值": "外框尺寸差 > 0.1mm 或限高 Z 差 > 0.05mm",
     "输出": "占位外框与限高变更清单",
     "实现思路": "按 refdes 匹配外框, 比对 bbox 长宽与 Z 值"},
    {"比对面": "安装孔 / 开槽变更", "所需数据": "两侧的 DRILLED_HOLES / OTHER_OUTLINE",
     "判据 / 阈值": "孔径或孔位差 > 0.1mm",
     "输出": "孔位/孔径/开槽差异清单",
     "实现思路": "按 (孔径, 坐标) 就近配对; 配不上的算增删"},
    {"比对面": "板厚 / 层数变更", "所需数据": ".emn HEADER 与 IDX 的 BOARD 段",
     "判据 / 阈值": "厚度差 > 0.02mm 或层数不同",
     "输出": "叠层差异说明",
     "实现思路": "读 IDX/IDF 头段的 thickness / layer count 字段直接对比"},
]


def cap_idx(ctx: Context) -> Result:
    cap = CATALOG_MAP["mcad.idx"]
    found = _user_files(ctx, _IDX_SUFFIXES)
    res = unavailable(
        cap,
        "IDX 是 ECAD/MCAD 之间的增量变更交换格式, 必须有 ECAD 侧与 MCAD 侧"
        "各自导出的基准文件才能比对, 本轮两侧基准都没有",
        "Allegro: File > Export > IDX 导出 ECAD 侧基准; MCAD 侧 (Creo/SolidWorks/UG) "
        "用对应的 ECAD-MCAD 协作插件导出 MCAD 侧基准, 两份都要")
    res.add_all(_IDX_PLAN, list(_IDX_COLUMNS))
    res.metric("ECAD 侧基准 (IDX/IDF)", "有" if found else "缺", "",
               "用户提供的文件" if found else "需 Allegro File > Export > IDX")
    res.metric("MCAD 侧基准 (IDX/IDF)", "缺", "", "需 MCAD 侧的协作插件导出")
    res.metric("可比对的面", len(_IDX_PLAN), "项", "本工具设计的比对口径")
    if found:
        res.summary = (f"发现 {len(found)} 个候选 IDF/IDX 文件 "
                       f"({_file_brief(found)}), 但当前版本还没有实现 IDX 解析; "
                       f"比对口径与实现思路已列在明细里")
        res.warn("检测到的候选文件只是「看到了」, 本工具**尚未实现** IDX/IDF 解析与比对, "
                 "不要把它当成已经比对过")
    else:
        res.summary = (f"两侧基准文件都没有, 无法比对; 已给出 {len(_IDX_PLAN)} 个"
                       f"比对面 (位移/增删/面别/外形/占位/孔/叠层) 的判据与实现思路")
    res.need("ECAD 侧基准 IDX (或 IDF)",
             "Allegro: File > Export > IDX（IDF 也可作为基准的替代输入）")
    res.need("MCAD 侧基准 IDX",
             "在 Creo/SolidWorks/UG 里用 ECAD-MCAD 协作插件导入 ECAD 基准后导出 MCAD 侧 IDX")

    path = os.path.join(_mcad_dir(ctx),
                        f"{_safe_file(ctx.board_name)}_IDX比对方案.csv")
    _write_csv(path, list(_IDX_COLUMNS), _IDX_PLAN)
    res.artifact("csv", path, "IDX 双侧比对方案 (比对面 / 数据 / 判据 / 实现思路)")
    res.source("Cadence Allegro IDX 导出流程 + 本工具设计的比对口径")
    res.extra["compare_plan"] = _IDX_PLAN
    res.extra["found_files"] = found
    res.extra["implementation_note"] = (
        "实现 IDX 解析需要: 1) 读 .idx/.idf 的头段 (UNITS/THICKNESS) 统一单位; "
        "2) 按段名 (BOARD_OUTLINE / PLACEMENT / PLACE_REGION / DRILLED_HOLES) "
        "切块解析; 3) 以 refdes 为主键做双侧匹配, 输出差异表。")
    return res


# ==========================================================================
# 7.4 STEP Export
# ==========================================================================
_STEP_SUFFIXES = (".stp", ".step", ".stl", ".sat", ".x_t", ".x_b", ".jt",
                  ".igs", ".iges")
# 归档条目名里出现这些词才可能代表 .brd 自带三维数据
_MODEL_HINTS = ("3D", "STEP", "MODEL", "PVZ", "MCAD", "SOLID")

_3D_COLUMNS = ["项目", "状态", "说明", "在 Allegro / MCAD 侧怎么做"]


def _three_d_evidence(ctx: Context) -> dict:
    """在 .brd 归档条目、内嵌快照、文件名里找三维数据的证据。

    保守口径: 只有归档条目名/用户提供的三维文件/快照里明确的三维字段才算证据;
    文件名里带个 "3D" 只是命名习惯, 单独出现不足以证明设计里有三维模型。
    """
    objects = sorted(name for name in ctx.objects
                     if any(k in name.upper() for k in _MODEL_HINTS))
    snapshot_hits: List[str] = []
    if ctx.has_snapshot:
        try:
            for entry in ctx.raw_entries:
                if not isinstance(entry, dict):
                    continue
                blob = f"{entry.get('class') or ''} {entry.get('subclass') or ''}".upper()
                if any(k in blob for k in _MODEL_HINTS):
                    snapshot_hits.append(blob.strip())
                else:
                    keys = {str(k).lower() for k in entry.keys()}
                    if {"z", "height", "thickness", "model"} & keys:
                        snapshot_hits.append("字段: " + "、".join(sorted(keys)))
                if len(snapshot_hits) >= 5:
                    break
        except Exception:  # noqa: BLE001  证据收集失败不能拖垮能力
            snapshot_hits = []
    name_text = f"{ctx.board_name} {os.path.basename(ctx.brd_path or '')}".lower()
    return {
        "objects": objects[:10],
        "objects_total": len(objects),
        "files": _user_files(ctx, _STEP_SUFFIXES),
        "snapshot": snapshot_hits,
        "name_hint": "3d" in name_text,
    }


def _three_d_rows(ctx: Context, evidence: dict) -> List[dict]:
    """STEP / Creo View 的三维发布准备度清单。"""
    rows: List[dict] = []
    area = facts.region_areas(ctx)
    thickness = ctx.design.get("board_thickness_mm")

    def add(item: str, status: str, note: str, howto: str) -> None:
        rows.append({"项目": item, "状态": status, "说明": note,
                     "在 Allegro / MCAD 侧怎么做": howto})

    add("封装级三维模型映射 (每个封装 -> .stp/.sat)",
        "△ 部分" if evidence["files"] else "✘ 缺",
        "快照里只有二维 class/subclass, 没有任何三维模型引用; "
        + (f"工作目录里发现 {len(evidence['files'])} 个三维文件"
           if evidence["files"] else "工作目录里也没有三维文件"),
        "Allegro: 在封装里 Package Geometry > 3D, 或用 3D Canvas 给封装绑定 STEP 模型")
    add("器件高度属性 (PLACE_BOUND / PACKAGE_HEIGHT)",
        "✘ 缺", "没有高度就拉伸不出器件体, 三维模型只能是一块光板",
        "在封装里定义 Place_Bound 高度 / PACKAGE_HEIGHT_MIN/MAX 属性")
    if area.get("ok"):
        add("板外形 (闭合轮廓)", "✔ 有",
            f"{area['width_mm']} × {area['height_mm']} mm, {area['outline_count']} 组折线",
            "无需补充; 导出 STEP 时板体由外形拉伸")
    else:
        add("板外形 (闭合轮廓)", "✘ 缺", area.get("reason", "没有可用板框"),
            "先修好 BOARD GEOMETRY/OUTLINE")
    add("板厚", ("△ 部分" if thickness else "✘ 缺"),
        (f"{thickness} mm (制程说明文本, 置信度低)" if thickness else "没有取到板厚"),
        ctx.howto_stackup())
    add("Allegro STEP 导出能力 (选项/授权)",
        "✘ 缺", "File > Export > STEP 依赖设计里的三维数据与相应导出选项",
        "Allegro: File > Export > STEP（需要 3D 模型已映射到封装上）")
    add("Creo View 发布 (3D 流程)",
        "✘ 缺", "Creo View 是 Allegro 三维流程发布出来的查看格式, 不是 .brd 自带的",
        "Allegro: File > Export > Creo View（或通过 Allegro 3D Canvas 发布）")
    add("用户已有的三维文件",
        "✔ 有" if evidence["files"] else "✘ 缺",
        (_file_brief(evidence["files"]) if evidence["files"]
         else "没有在 .brd 同目录 / extra_dirs 里找到 .stp/.step/.sat 等文件"),
        "把三维文件放在 .brd 同目录, 或用 --export-files 显式给出, 工具会登记")
    add(".brd 归档条目里的三维数据",
        "✔ 有" if evidence["objects"] else "✘ 缺",
        ("、".join(evidence["objects"]) if evidence["objects"]
         else "归档条目名里没有 3D/STEP/MODEL 之类的数据段"),
        "有的话请连同 .brd 一起提供, 便于核对来源")
    add("内嵌快照里的三维字段",
        "△ 部分" if evidence["snapshot"] else "✘ 缺",
        ("；".join(evidence["snapshot"][:3]) if evidence["snapshot"]
         else "快照条目里没有 z/height/model 字段"),
        "快照是二维图形导出, 三维数据只能靠 Allegro 侧导出")
    return rows


def cap_step(ctx: Context) -> Result:
    cap = CATALOG_MAP["mcad.step"]
    evidence = _three_d_evidence(ctx)
    rows = _three_d_rows(ctx, evidence)
    strong = bool(evidence["objects"] or evidence["files"] or evidence["snapshot"])

    if evidence["files"]:
        res = Result(cap, INFO, "")
        res.summary = (f"未由本工具生成 STEP; 发现用户已有的三维文件 "
                       f"{len(evidence['files'])} 个 ({_file_brief(evidence['files'])})")
        res.warn("这些文件是**用户已有的**, 不是本工具从 .brd 生成的; "
                 "本工具不做三维数据 -> STEP 的转换")
        res.metric("已有三维文件", len(evidence["files"]), "个",
                   "用户提供的 .stp/.step/.sat 等", "high")
    elif strong:
        res = Result(cap, INFO, "")
        res.summary = ("未由本工具生成 STEP; 但在 "
                       + ("归档条目 " + "、".join(evidence["objects"]) if evidence["objects"]
                          else "内嵌快照 " + "；".join(evidence["snapshot"][:2]))
                       + " 里找到了三维相关证据")
        res.warn("证据只说明设计里可能有三维数据, 本工具**没有**实现三维数据 -> STEP "
                 "的转换, 必须用 Allegro 的导出功能")
        res.need("把三维数据导出成 STEP 的工具链",
                 "Allegro: File > Export > STEP（需要 3D 模型已映射到封装上）")
    else:
        res = unavailable(
            cap, "没有找到 .brd 自带三维模型的任何证据 (归档条目、快照字段、文件都没有)",
            "Allegro: File > Export > STEP（需要 3D 模型已映射到封装上）")
        if evidence["name_hint"]:
            res.warn("文件名/设计名里带「3D」只是命名习惯, 不能证明设计里有三维模型, "
                     "所以本项仍按数据不足处理")

    res.add_all(rows, list(_3D_COLUMNS))
    ok = sum(1 for r in rows if str(r["状态"]).startswith("✔"))
    miss = sum(1 for r in rows if str(r["状态"]).startswith("✘"))
    res.metric("三维证据条数", len(evidence["objects"]) + len(evidence["files"])
               + len(evidence["snapshot"]), "条", "归档条目 / 三维文件 / 快照字段")
    res.metric("准备度已具备", ok, "项", "按三维发布所需条件核对")
    res.metric("准备度缺失", miss, "项", "同上")

    if not evidence["files"]:
        res.need("PCB 三维模型",
                 "Allegro: File > Export > STEP（需要 3D 模型已映射到封装上）")
    path = os.path.join(_mcad_dir(ctx),
                        f"{_safe_file(ctx.board_name)}_3D发布准备度.csv")
    _write_csv(path, list(_3D_COLUMNS), rows)
    res.artifact("csv", path, "三维发布准备度清单 (STEP / Creo View 共用)")
    res.source(".brd 归档条目名 + 内嵌快照字段 + 用户提供的三维文件")
    res.extra["evidence"] = evidence
    res.extra["readiness"] = rows
    res.extra["export_note"] = {
        "为什么不生成 STEP": "STEP 需要封装级三维模型映射, .brd 的二进制与内嵌快照里"
                              "只有二维图形与器件位号, 没有可转换的三维实体",
        "Allegro 路径": "File > Export > STEP（前置: 3D 模型已映射到封装上）",
    }
    return res


# ==========================================================================
# 7.5 Creo View
# ==========================================================================
_CREO_SUFFIXES = (".pvz", ".ol", ".edz", ".ed", ".pvs", ".svf", ".cgm")

_CREO_COLUMNS = ["前置条件", "状态", "说明"]


def cap_creo(ctx: Context) -> Result:
    cap = CATALOG_MAP["mcad.creo"]
    found = _user_files(ctx, _CREO_SUFFIXES)
    evidence = _three_d_evidence(ctx)
    area = facts.region_areas(ctx)
    thickness = ctx.design.get("board_thickness_mm")

    rows: List[dict] = []

    def add(item: str, status: str, note: str) -> None:
        rows.append({"前置条件": item, "状态": status, "说明": note})

    add("封装级三维模型映射",
        "△ 部分" if evidence["objects"] or evidence["files"] else "✘ 缺",
        "Creo View 发布的是三维装配体, 没有 3D 封装映射就发布不出器件")
    add("STEP / 三维数据已生成",
        "✔ 有" if evidence["files"] else "✘ 缺",
        "工作目录里" + (f"有 {len(evidence['files'])} 个三维文件"
                     if evidence["files"] else "没有 .stp/.step 等三维文件"))
    add("Allegro 3D Canvas / Creo View 授权",
        "✘ 缺", "发布 .pvz 需要 Cadence 的三维流程与 Creo View 授权, 本工具无法核实授权状态")
    add("板外形与板厚",
        "△ 部分" if area.get("ok") else "✘ 缺",
        (f"外形 {area['width_mm']} × {area['height_mm']} mm; "
         f"板厚 {thickness or '缺'} (制程说明, 置信度低)" if area.get("ok")
         else area.get("reason", "没有可用板框")))
    add("用户已有的 Creo View 发布文件",
        "✔ 有" if found else "✘ 缺",
        (_file_brief(found) if found
         else "没有在 .brd 同目录 / extra_dirs 里找到 .pvz/.ol 等文件"))

    if found:
        res = Result(cap, INFO, "")
        res.summary = (f"未由本工具生成; 发现用户已有的 Creo View 发布文件 "
                       f"{len(found)} 个 ({_file_brief(found)})")
        res.warn("这些是**用户已有**的发布产物, 不是本工具从 .brd 生成的; "
                 "本工具不发布 Creo View 格式")
        res.metric("已有发布文件", len(found), "个", "用户提供的 .pvz/.ol 等", "high")
    else:
        res = unavailable(
            cap,
            "Creo View (.pvz/.ol 等) 是 Allegro 三维流程发布出来的查看/发布格式, "
            ".brd 里没有它, 本工具也无法发布",
            "Allegro: File > Export > Creo View（或通过 Allegro 3D Canvas 发布）")

    res.add_all(rows, list(_CREO_COLUMNS))
    res.metric("前置条件项数", len(rows), "项", "按 Creo View 发布所需条件核对")
    res.metric("已具备", sum(1 for r in rows if str(r["状态"]).startswith("✔")), "项",
               "同上")
    res.need("三维封装映射与 STEP/三维数据",
             "先完成 3D 封装映射并用 Allegro: File > Export > STEP 生成三维模型")
    res.need("Creo View 发布授权与流程",
             "Allegro: File > Export > Creo View（或通过 Allegro 3D Canvas 发布）")
    res.source("Cadence Allegro 三维发布流程 (3D Canvas / Creo View)")
    res.extra["prerequisites"] = rows
    res.extra["found_files"] = found
    res.extra["note"] = ("Creo View 是查看器/发布格式, 属于 Allegro 三维流程的下游产物; "
                         "本工具既不发布也不解析 .pvz, 只能登记用户已有的文件")
    return res


CATALOG: List[Capability] = [
    Capability("mcad.dxf", "mcad", "DXF Export", "DXF 机械二维图导出",
               "把快照几何导成 DXF R12 ASCII (AC1009, mm) 的板框/禁布/丝印/装配二维图",
               cap_dxf, "snapshot",
               ["内嵌快照 BOARD GEOMETRY/OUTLINE", "ROUTE KEEPIN",
                "SILKSCREEN", "ASSEMBLY"],
               ["<board>.dxf", "每层实体数量统计"]),
    Capability("mcad.idf", "mcad", "IDF Export", "IDF 导出 (PCB 与结构协同)",
               "IDF (.emn/.emp) 需要器件坐标/旋转/封装外形/器件高度; .brd 里没有, "
               "只出导出准备度清单",
               cap_idf, "external",
               ["器件 XY/旋转/面别", "器件高度属性", "封装占位外形", "板外形"],
               ["IDF 导出准备度清单 CSV", "Allegro 导出路径说明"]),
    Capability("mcad.idx", "mcad", "IDX Export", "IDX 导出 (ECAD/MCAD 变更交换)",
               "IDX 是双侧增量交换格式, 需要 ECAD 与 MCAD 各自的基准文件才能比对",
               cap_idx, "external",
               ["ECAD 侧基准 IDX/IDF", "MCAD 侧基准 IDX/IDF"],
               ["IDX 双侧比对方案 CSV", "比对实现思路"]),
    Capability("mcad.step", "mcad", "STEP Export", "STEP 三维模型导出",
               "STEP 需要封装级三维模型映射与 Allegro 三维导出; "
               "本工具只核实 .brd 里有没有三维证据",
               cap_step, "external",
               [".brd 归档/快照里的三维数据", "封装三维模型映射",
                "Allegro STEP 导出"],
               ["3D 发布准备度清单 CSV", "已发现的三维证据"]),
    Capability("mcad.creo", "mcad", "Creo View", "Creo View 三维结构查看",
               "Creo View (.pvz/.ol) 由 Cadence 三维流程发布, .brd 里没有; "
               "需要三维映射与发布授权",
               cap_creo, "external",
               ["三维封装映射", "STEP/三维数据已生成", "Creo View 发布授权"],
               ["前置条件清单", "Allegro 导出路径说明"]),
]
CATALOG_MAP: Dict[str, Capability] = {c.id: c for c in CATALOG}

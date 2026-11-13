"""从 .brd 内嵌的设计快照里读几何信息 (不含 Allegro 依赖)。

Allegro 17.x 会把一份设计快照 (JSON) 压进 .brd 尾部, 内容是全板的
线段/圆弧/文字图形 (含 BOARD GEOMETRY/OUTLINE 板框), 但**没有**器件位号、
没有钻孔坐标、没有焊盘。这里只做它能做到的事:

    board_outline()   板框 bbox -> 板尺寸 (mm), 标定用 .brd 里的
                      "Draw Size" (QuickViewText, mil), 没有标定时退回 µm 假设
器件坐标 (X/Y) 与钻孔坐标不在这个快照里, 见 src/pick.py + src/allegro_skill.py:
需要用 PCB Editor 的 SKILL 导出一份坐标清单, 或导入钻孔 (Excellon) 文件。
"""
import io
import json
import math
import os
from functools import lru_cache
import zipfile
from typing import Dict, List, Optional, Tuple

# 快照里代表板框的子类名
_OUTLINE_SUBCLASSES = ("OUTLINE", "DESIGN_OUTLINE")

# 布线允许区 (ROUTE KEEPIN) 的 class 名; 它的子类决定它管哪些层
_KEEPIN_CLASS = "ROUTE KEEPIN"
_INNER_KEYS = ("INNER", "INNER_LAYER", "INT")
_OUTER_KEYS = ("OUTER", "OUTER_LAYER", "TOP", "BOTTOM", "EXT")


def load_design_snapshot(brd_path: str) -> Optional[dict]:
    """读出 .brd 内嵌的设计快照 JSON; 没有或格式不符时返回 None。

    同一块板在"板框/焊盘间距"等处会重复请求快照, 按 (路径, 修改时间, 大小)
    缓存一份, 避免反复解 2~3MB JSON。
    """
    try:
        stat = os.stat(brd_path)
        key = (brd_path, stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None
    return _load_snapshot_cached(key)


@lru_cache(maxsize=4)
def _load_snapshot_cached(key):
    brd_path = key[0]
    try:
        with open(brd_path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    idx = raw.find(b"PK\x03\x04")
    if idx < 0:
        return None
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw[idx:]))
        names = zf.namelist()
        name = next((n for n in names if n.lower().endswith(".json")
                     and "design" in n.lower()), names[0] if names else "")
        if not name:
            return None
        text = zf.read(name).decode("utf-8", "replace")
        doc = json.loads(text)
    except (zipfile.BadZipFile, IndexError, ValueError, OSError):
        return None
    return doc if isinstance(doc, dict) else None


def _entries(doc: dict) -> List[dict]:
    design = (doc or {}).get("design") or {}
    comps = design.get("comps")
    return comps if isinstance(comps, list) else []


def _bbox(points) -> Optional[Tuple[float, float, float, float]]:
    xs = [p.get("x") for p in points if isinstance(p, dict) and p.get("x") is not None]
    ys = [p.get("y") for p in points if isinstance(p, dict) and p.get("y") is not None]
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def outline_segments(entries: List[dict]) -> List[dict]:
    return [c for c in entries
            if str(c.get("subclass") or "").upper() in _OUTLINE_SUBCLASSES]


def _polyline(seg: dict) -> List[Tuple[float, float]]:
    """把快照里的一段图形近似成折线。

    快照约定: 直线(type=2) 的 points 是两端点; 圆弧(type=6) 的 points 是**圆心**,
    endpts 是两端点, start/end 是起止角 (弧度)。圆弧按 8 段折线取样。
    """
    pts = [(float(p["x"]), float(p["y"]))
           for p in (seg.get("points") or [])
            if isinstance(p, dict) and p.get("x") is not None and p.get("y") is not None]
    end = [(float(p["x"]), float(p["y"]))
           for p in (seg.get("endpts") or [])
            if isinstance(p, dict) and p.get("x") is not None and p.get("y") is not None]
    if len(end) == 2 and pts:      # 圆弧: 绕圆心从起点扫到终点
        cx, cy = pts[0]
        (x0, y0), (x1, y1) = end
        radius = math.hypot(x0 - cx, y0 - cy)
        if radius <= 0:
            return [end[0], end[1]]
        a0 = seg.get("start")
        a1 = seg.get("end")
        try:
            a0 = float(a0) if a0 is not None else math.atan2(y0 - cy, x0 - cx)
            a1 = float(a1) if a1 is not None else math.atan2(y1 - cy, x1 - cx)
        except (TypeError, ValueError):
            a0 = math.atan2(y0 - cy, x0 - cx)
            a1 = math.atan2(y1 - cy, x1 - cx)
        delta = a1 - a0
        while delta > math.pi:      # 取短弧, 避免绕远路
            delta -= 2 * math.pi
        while delta < -math.pi:
            delta += 2 * math.pi
        if abs(delta) < 1e-9:
            return [end[0], end[1]]
        steps = 8
        return [(cx + radius * math.cos(a0 + delta * i / steps),
                 cy + radius * math.sin(a0 + delta * i / steps))
                for i in range(steps + 1)]
    return pts


def _pt_seg_dist(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    den = dx * dx + dy * dy
    if den <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / den
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _seg_seg_dist(a1, a2, b1, b2) -> float:
    """两线段的最小距离 (相交 = 0)。"""
    d1, d2 = _cross(b1, b2, a1), _cross(b1, b2, a2)
    d3, d4 = _cross(a1, a2, b1), _cross(a1, a2, b2)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return min(_pt_seg_dist(a1, b1, b2), _pt_seg_dist(a2, b1, b2),
               _pt_seg_dist(b1, a1, a2), _pt_seg_dist(b2, a1, a2))


def _poly_dist(a: List[Tuple[float, float]], b: List[Tuple[float, float]]) -> float:
    best = None
    for i in range(len(a) - 1):
        for j in range(len(b) - 1):
            d = _seg_seg_dist(a[i], a[i + 1], b[j], b[j + 1])
            if best is None or d < best:
                best = d
                if best <= 0:
                    return 0.0
    return float("inf") if best is None else best


def _endpoints(seg: dict) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """取一段图形的两个端点 (圆弧用 endpts, 直线用 points 首尾), 取不到返回 None。"""
    pts = [(float(p["x"]), float(p["y"]))
           for p in (seg.get("points") or [])
            if isinstance(p, dict) and p.get("x") is not None and p.get("y") is not None]
    end = [(float(p["x"]), float(p["y"]))
           for p in (seg.get("endpts") or [])
            if isinstance(p, dict) and p.get("x") is not None and p.get("y") is not None]
    if len(end) == 2:
        return end[0], end[1]
    if len(pts) >= 2:
        return pts[0], pts[-1]
    return None


def board_edge_lines(entries: List[dict]) -> List[List[Tuple[float, float]]]:
    """只取"板边"那圈线段, 排除板框子类里混着的文字/尺寸小图形。

    做法: 按端点相连把 OUTLINE 子类的段分组 (并查集), 取包围盒面积最大的一组
    —— 板边一定是最外那圈, 说明用的小图形各自成一组且包围盒很小。
    """
    segs = outline_segments(entries)
    pairs = []
    for seg in segs:
        ends = _endpoints(seg)
        if ends:
            pairs.append((seg, ends))
    if not pairs:
        return []

    def key(pt):
        return (round(pt[0], 1), round(pt[1], 1))

    parent = list(range(len(pairs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: Dict[Tuple[float, float], int] = {}
    for idx, (_seg, (a, b)) in enumerate(pairs):
        for pt in (a, b):
            k = key(pt)
            if k in owner:
                ra, rb = find(idx), find(owner[k])
                if ra != rb:
                    parent[ra] = rb
            else:
                owner[k] = idx

    groups: Dict[int, list] = {}
    for idx, (seg, _ends) in enumerate(pairs):
        groups.setdefault(find(idx), []).append(seg)

    best, best_area = None, -1.0
    for group in groups.values():
        pts = [pt for seg in group for pt in _polyline(seg)]
        if len(pts) < 2:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if area > best_area:
            best, best_area = group, area

    if not best:
        return []
    all_pts = [pt for seg in segs for pt in _polyline(seg)]
    if all_pts:
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        total = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if total > 0 and best_area < total * 0.5:
            # 端点没接上 (例如板边被文字打断): 退回使用全部板框段
            return [_polyline(seg) for seg in segs]
    return [_polyline(seg) for seg in best]


def route_keepin_clearance(doc: dict, scale_mm: float) -> Dict[str, object]:
    """ROUTE KEEPIN 到板框的最小距离 (mm) = 设计允许的布线到板边余量。

    注意: 这是**设计规则边界**(允许布线的区域), 不是实测铜箔位置 ——
    实际铜箔只会更靠内, 不会更靠外 (除非设计违反 keepin)。
    子类为 ALL/空 时内外层同值; 若设计分了 INNER/OUTER 子类则分别给值。
    """
    entries = _entries(doc)
    outline_lines = [p for p in board_edge_lines(entries) if len(p) >= 2]
    keep = [e for e in entries if str(e.get("class") or "").upper() == _KEEPIN_CLASS]
    if not keep or not outline_lines:
        reason = ("内嵌快照里没有 ROUTE KEEPIN" if not keep
                  else "内嵌快照里没有板框, 无法量取相对距离")
        return {"ok": False, "reason": reason}

    groups: Dict[str, list] = {}
    for seg in keep:
        sub = str(seg.get("subclass") or "ALL").upper()
        if any(k in sub for k in _INNER_KEYS):
            key = "inner"
        elif any(k in sub for k in _OUTER_KEYS):
            key = "outer"
        else:
            key = "all"
        groups.setdefault(key, []).append(_polyline(seg))

    def gap(lines) -> Optional[float]:
        best = None
        for lp in lines:
            if len(lp) < 2:
                continue
            for op in outline_lines:
                d = _poly_dist(lp, op)
                if best is None or d < best:
                    best = d
        return None if best is None or best == float("inf") else round(best * scale_mm, 4)

    result: Dict[str, object] = {
        "ok": True,
        "subclasses": sorted({str(s.get("subclass") or "ALL") for s in keep}),
        "segment_count": len(keep),
        "scale_mm_per_unit": scale_mm,
    }
    if "all" in groups:
        value = gap(groups["all"])
        result["all_mm"] = value
        result["inner_mm"] = value
        result["outer_mm"] = value
        result["layered"] = False
    else:
        result["inner_mm"] = gap(groups.get("inner") or [])
        result["outer_mm"] = gap(groups.get("outer") or [])
        result["layered"] = True
    for k in ("inner_mm", "outer_mm"):
        if result.get(k) is not None and result[k] <= 0:
            result.setdefault("warnings", []).append(
                f"ROUTE KEEPIN 与板框重合 ({k}=0), 该值不能当作线到边余量")
    return result


def board_outline(doc: dict, draw_extents_mil: Optional[dict] = None) -> Dict[str, object]:
    """计算板框 bbox 与板尺寸 (mm)。

    draw_extents_mil: .brd 文本里的 Draw Size (mil), 形如
        {"minx":0,"miny":0,"maxx":14084,"maxy":6643}
    有它就能标定快照坐标的换算系数; 没有时按经验假设 1 单位 = 1/583.3 mm
    (即 0.0017145mm), 并把 method 标成 assumed, 提示需要人工核对。
    """
    entries = _entries(doc)
    segs = outline_segments(entries)
    pts = [p for seg in segs for p in (seg.get("points") or [])]
    box = _bbox(pts)
    if not box or not segs:
        return {"ok": False, "reason": "内嵌快照里没有 BOARD GEOMETRY/OUTLINE"}

    x0, y0, x1, y1 = box
    span_x, span_y = x1 - x0, y1 - y0
    if span_x <= 0 or span_y <= 0:
        return {"ok": False, "reason": "板框几何无效"}

    result: Dict[str, object] = {
        "ok": True,
        "segment_count": len(segs),
        "bbox_units": [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)],
        "span_units": [round(span_x, 3), round(span_y, 3)],
    }

    method = "assumed"
    scale_x = scale_y = None
    if draw_extents_mil:
        try:
            dx = float(draw_extents_mil["maxx"]) - float(draw_extents_mil["minx"])
            dy = float(draw_extents_mil["maxy"]) - float(draw_extents_mil["miny"])
            if dx > 0 and dy > 0:
                unit = str(draw_extents_mil.get("unit") or "mil").lower()
                if unit.startswith("inch"):
                    factor = 25.4
                elif unit.startswith("mm") or unit.startswith("millimeter"):
                    factor = 1.0
                else:
                    factor = 0.0254
                scale_x = dx * factor / span_x
                scale_y = dy * factor / span_y
                method = "draw_size"
        except (KeyError, TypeError, ValueError):
            scale_x = scale_y = None

    if scale_x and scale_y and abs(scale_x - scale_y) / max(scale_x, scale_y) > 0.02:
        # 两个方向换算系数差太多, 说明标定不可靠, 退回假设值
        scale_x = scale_y = None
        method = "assumed_conflict"

    if scale_x is None:
        scale_x = scale_y = 0.0017145  # 经验值: 该快照坐标 ≈ 1/583.3 mm

    w = span_x * scale_x
    h = span_y * scale_y
    result.update({
        "width_mm": round(w, 2),
        "height_mm": round(h, 2),
        "scale_mm_per_unit": round((scale_x + scale_y) / 2.0, 8),
        "method": method,
        "confidence": "high" if method == "draw_size" else "low",
    })
    return result


def board_geometry(brd_path: str, draw_extents_mil: Optional[dict] = None) -> Dict[str, object]:
    """一站式: 读快照 -> 板框 bbox + 板尺寸 (mm)。"""
    doc = load_design_snapshot(brd_path)
    if not doc:
        return {"ok": False, "reason": "该 .brd 没有可解析的内嵌设计快照"}
    outline = board_outline(doc, draw_extents_mil)
    result = {"ok": bool(outline.get("ok")), "outline": outline,
              "entry_count": len(_entries(doc))}
    if outline.get("ok"):
        try:
            result["keepin"] = route_keepin_clearance(
                doc, float(outline.get("scale_mm_per_unit") or 0.0))
        except Exception as exc:  # noqa: BLE001  几何失败不能拖垮主流程
            result["keepin"] = {"ok": False, "reason": f"ROUTE KEEPIN 解析失败: {exc}"}
    return result

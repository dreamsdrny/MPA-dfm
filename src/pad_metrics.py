"""焊盘几何指标: 两个相邻焊盘的**引脚间距** (中心距) 与**焊盘间距** (边缘距)。

计算口径
--------
    引脚间距 = 两个焊盘中心之间的距离 (pitch, 中心到中心)
    焊盘间距 = 两个焊盘边缘之间的最短距离 (gap, 负值表示重叠)

焊盘形状支持:
    {"x", "y", "d"}                 圆形焊盘, d = 直径
    {"x", "y", "w", "h"}            矩形焊盘, w/h = 宽/高
    {"x", "y", "w", "h", "angle"}   旋转矩形焊盘, angle 为角度(度)

数据来源
--------
`.brd` 的内嵌设计快照里**通常没有焊盘几何**（多数板只有丝印、装配、ETCH、板框、
ROUTE KEEPIN 等图形），钻孔表也只有孔径/跨度/数量，没有焊盘坐标。所以本模块在快照里
找不到焊盘时如实返回 ok=False 并说明缺哪种数据；一旦拿到焊盘坐标（内嵌快照里的
`PAD/PIN`·`PASTEMASK` 图形 / SKILL 导出的 `evan_pads.csv` / Allegro pad 报告），
同一套函数可以直接算。

逐器件口径（焊盘尺寸、每颗器件的引脚间距、到铜皮/走线距离）见 `src/pad_geometry.py`。
"""
import math
from typing import Iterable, List, Optional, Tuple

# 快照里可能装焊盘的 (class, subclass) 关键字；命中才当焊盘几何用
_PAD_CLASS_HINTS = ("PAD",)
_PAD_SUBCLASS_HINTS = ("PAD",)

_EPS = 1e-9


def _seg_point_dist(a: Tuple[float, float], b: Tuple[float, float],
                    p: Tuple[float, float]) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    if seg_len2 < _EPS:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _seg_seg_dist(a1, a2, b1, b2) -> float:
    """两条线段的最近距离 (相交时为 0)。"""
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])

    d1, d2 = cross(a1, a2, b1), cross(a1, a2, b2)
    d3, d4 = cross(b1, b2, a1), cross(b1, b2, a2)
    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return 0.0
    return min(
        _seg_point_dist(a1, a2, b1), _seg_point_dist(a1, a2, b2),
        _seg_point_dist(b1, b2, a1), _seg_point_dist(b1, b2, a2),
    )


def _point_in_poly(p, poly) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > p[1]) != (y2 > p[1]):
            xin = (x2 - x1) * (p[1] - y1) / ((y2 - y1) or _EPS) + x1
            if p[0] < xin:
                inside = not inside
    return inside


def pad_outline(pad: dict) -> dict:
    """把焊盘规整成 {"kind": "circle"/"polygon", ...}，便于统一算距离。"""
    x, y = float(pad.get("x", 0.0)), float(pad.get("y", 0.0))
    if pad.get("d"):
        return {"kind": "circle", "c": (x, y), "r": float(pad["d"]) / 2.0}
    w, h = float(pad.get("w") or 0.0), float(pad.get("h") or 0.0)
    if w <= 0 or h <= 0:
        raise ValueError(f"焊盘尺寸缺失: {pad}")
    angle = math.radians(float(pad.get("angle") or 0.0))
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    corners = []
    for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        corners.append((x + dx * cos_a - dy * sin_a, y + dx * sin_a + dy * cos_a))
    return {"kind": "polygon", "poly": corners, "c": (x, y)}


def pad_center(pad: dict) -> Tuple[float, float]:
    return float(pad.get("x", 0.0)), float(pad.get("y", 0.0))


def shape_gap(a: dict, b: dict) -> float:
    """两个焊盘边缘间的最短距离; 重叠返回负值。"""
    oa, ob = pad_outline(a), pad_outline(b)
    if oa["kind"] == "circle" and ob["kind"] == "circle":
        return math.hypot(oa["c"][0] - ob["c"][0], oa["c"][1] - ob["c"][1]) - oa["r"] - ob["r"]
    if oa["kind"] == "circle" or ob["kind"] == "circle":
        circle, poly = (oa, ob["poly"]) if oa["kind"] == "circle" else (ob, oa["poly"])
        cx, cy = circle["c"]
        if _point_in_poly((cx, cy), poly):
            return -circle["r"]
        dist = min(_seg_point_dist(poly[i], poly[(i + 1) % len(poly)], (cx, cy))
                   for i in range(len(poly)))
        return dist - circle["r"]
    pa, pb = oa["poly"], ob["poly"]
    if any(_point_in_poly(p, pb) for p in pa) or any(_point_in_poly(p, pa) for p in pb):
        return 0.0
    best = float("inf")
    for i in range(len(pa)):
        a1, a2 = pa[i], pa[(i + 1) % len(pa)]
        for j in range(len(pb)):
            b1, b2 = pb[j], pb[(j + 1) % len(pb)]
            best = min(best, _seg_seg_dist(a1, a2, b1, b2))
    return best


def pad_metrics(pads: Iterable[dict], eps: float = 1e-6) -> dict:
    """算最小引脚间距(中心距)与最小焊盘间距(边缘距)。

    pads: 焊盘字典序列, 每项 {"x","y", 以及 "d" 或 "w"/"h"/"angle", 可带 "ref"}
    返回 {"ok", "count", "min_pitch_mm", "min_gap_mm", "pitch_pair", "gap_pair"}
    """
    pads = [p for p in pads if p]
    if len(pads) < 2:
        return {"ok": False, "count": len(pads), "reason": "焊盘少于 2 个, 算不了间距",
                "pads": len(pads)}

    centers = [pad_center(p) for p in pads]
    best_pitch = float("inf")
    pitch_pair = None
    for i in range(len(pads)):
        for j in range(i + 1, len(pads)):
            dist = math.hypot(centers[i][0] - centers[j][0], centers[i][1] - centers[j][1])
            if dist > eps and dist < best_pitch:
                best_pitch = dist
                pitch_pair = (pads[i].get("ref", f"#{i + 1}"), pads[j].get("ref", f"#{j + 1}"))

    best_gap = float("inf")
    gap_pair = None
    outlines = [pad_outline(p) for p in pads]
    for i in range(len(pads)):
        for j in range(i + 1, len(pads)):
            gap = shape_gap(pads[i], pads[j])
            if gap < best_gap:
                best_gap = gap
                gap_pair = (pads[i].get("ref", f"#{i + 1}"), pads[j].get("ref", f"#{j + 1}"))

    return {
        "ok": True,
        "count": len(pads),
        "min_pitch_mm": round(best_pitch, 4) if best_pitch < float("inf") else None,
        "min_gap_mm": round(best_gap, 4) if best_gap < float("inf") else None,
        "pitch_pair": pitch_pair,
        "gap_pair": gap_pair,
        "method": "焊盘中心距(引脚间距) / 边缘最短距(焊盘间距)",
        "outline_count": len(outlines),
    }


def _looks_like_pad(entry: dict) -> bool:
    cls = (entry.get("class") or "").upper()
    sub = (entry.get("subclass") or "").upper()
    if any(hint in cls for hint in _PAD_CLASS_HINTS) and entry.get("type") in (2, 3, 6):
        # 注意: Allegro 的 PIN 类是丝印上的引脚标记, 不是焊盘, 这里不当焊盘用
        return True
    return any(hint in sub for hint in _PAD_SUBCLASS_HINTS)


def extract_pad_metrics(doc: Optional[dict], scale_mm_per_unit: float) -> dict:
    """从内嵌快照里找焊盘几何并算间距; 快照没有焊盘时如实说明。

    优先走 `src.pad_geometry` (识别矩形焊盘 + 圆形焊盘, 并给出逐器件分组),
    拿不到时退回本模块早期的"只认圆形焊盘"实现。
    """
    if not doc:
        return {"ok": False, "reason": "该 .brd 没有可解析的内嵌设计快照", "pads": 0}
    if not scale_mm_per_unit:
        return {"ok": False, "reason": "板框标定失败, 无法换算焊盘尺寸", "pads": 0}
    comps = ((doc.get("design") or {}).get("comps")) or []

    try:
        from src.pad_geometry import group_components, snapshot_pads

        snap = snapshot_pads(doc, scale_mm_per_unit)
    except Exception:  # noqa: BLE001  新模块出问题不能拖垮旧能力
        snap = None
    if snap and snap.get("ok") and len(snap.get("pads") or []) >= 2:
        # 用器件/簇名做焊盘的 ref, 这样 pitch_pair 直接显示是哪颗器件的哪两个焊盘
        try:
            groups = group_components(snap["pads"])
            label = {id(p): g["name"] for g in groups for p in g["pads"]}
        except Exception:  # noqa: BLE001
            label = {}
        pads = [{"x": p["x"], "y": p["y"], "w": p["w"], "h": p["h"],
                 "ref": label.get(id(p)) or p.get("refdes") or p.get("layer") or ""}
                for p in snap["pads"]]
        metrics = pad_metrics(pads)
        metrics["source"] = ("、".join(snap.get("sources") or [])
                             + "; 矩形焊盘按闭合轮廓求长宽")
        metrics["pads_detail"] = pads
        metrics["method"] = ("焊盘中心距(引脚间距) / 边缘最短距(焊盘间距); "
                             "逐器件尺寸与间距明细见 physical.pad_geometry")
        if label:
            metrics["grouped_by"] = "几何聚类 (焊盘边缘间距 <= 5mm, 不是真实位号)"
        return metrics

    pads: List[dict] = []
    for entry in comps:
        if not _looks_like_pad(entry):
            continue
        pts = entry.get("points") or []
        if len(pts) != 1 or not isinstance(pts[0], dict):
            continue
        radius = float(entry.get("radius") or 0.0)
        if radius <= 0:
            continue
        pads.append({
            "x": pts[0].get("x", 0.0) * scale_mm_per_unit,
            "y": pts[0].get("y", 0.0) * scale_mm_per_unit,
            "d": radius * 2.0 * scale_mm_per_unit,
            "ref": (entry.get("class") or ""),
        })
    if len(pads) < 2:
        return {
            "ok": False,
            "pads": len(pads),
            "reason": ("BRD 内嵌快照只有丝印/装配/ETCH/板框图形, 没有焊盘坐标; "
                       "引脚间距与焊盘间距要用 Gerber / Allegro 焊盘报告, "
                       "或用 SKILL 导出 evan_pads.csv (每个引脚的焊盘外框)"),
            "snapshot_entries": len(comps),
        }
    metrics = pad_metrics(pads)
    metrics["source"] = "内嵌快照焊盘图形 (只认圆形焊盘)"
    return metrics

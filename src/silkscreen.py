"""丝印图形提取: 丝印线宽 + 丝印到板框成型线的距离。

数据位置
--------
丝印图形在 `.brd` 内嵌快照的 `design.comps` 里, class/subclass 形如:

    PACKAGE GEOMETRY / SILKSCREEN_TOP        (器件丝印)
    PACKAGE GEOMETRY / SILKSCREEN_BOTTOM
    BOARD GEOMETRY   / SILKSCREEN_TOP        (板级丝印/文字框)

每条图形带 `width` 字段 (设计单位)。多数 FAB 图纸导出时 `width` 全是 0
(线宽没写进快照), 这时本模块如实返回 `has_widths=False`, 不拿 0 当"0.000mm 线宽"
去判 FAIL; 有非 0 宽度时取最小值, 换算成 mm。

丝印到板框的距离用几何计算: 丝印线段到 `BOARD GEOMETRY/OUTLINE` 各段的最短距离
(圆弧按折线近似, 与板框标定共用同一套换算)。

丝印到器件本体: 快照里没有焊盘, 用 `PACKAGE GEOMETRY/PLACE_BOUND_*` 与
`ASSEMBLY_*` 作为"器件本体/占位框"的替代轮廓。组件自己的丝印天然贴着自己的
本体 (距离恒为 0, 没有意义), 所以只算**板级丝印** (`BOARD GEOMETRY/SILKSCREEN_*`)
到器件本体的距离, 这样量到的是"板级丝印/文字有没有压到器件"。
"""
from collections import OrderedDict
from typing import List, Optional

from src.design_geometry import _entries, _polyline, _seg_seg_dist

_SILK_HINTS = ("SILKSCREEN", "SILK")


def is_silk_entry(entry: dict) -> bool:
    """class 或 subclass 里带 SILKSCREEN 的图形都算丝印。"""
    cls = (entry.get("class") or "").upper()
    sub = (entry.get("subclass") or "").upper()
    return any(h in cls for h in _SILK_HINTS) or any(h in sub for h in _SILK_HINTS)


def _pieces(entry: dict) -> List[tuple]:
    """把一条图形拆成线段 (圆弧按折线近似)。"""
    try:
        pts = _polyline(entry)
    except Exception:  # noqa: BLE001  单条图形坏了不影响整体
        return []
    return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


def _bbox_of(pieces: List[tuple]):
    xs = [p[0] for seg in pieces for p in seg]
    ys = [p[1] for seg in pieces for p in seg]
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_gap(a, b) -> float:
    """两个轴对齐包围盒之间的距离 (重叠为 0), 用作精确距离的快速下界。"""
    dx = max(0.0, max(a[0] - b[2], b[0] - a[2]))
    dy = max(0.0, max(a[1] - b[3], b[1] - a[3]))
    return (dx * dx + dy * dy) ** 0.5


def silkscreen_stats(doc: Optional[dict], scale_mm_per_unit: float) -> dict:
    """统计丝印图形数量与线宽 (只认非 0 宽度)。"""
    if not doc:
        return {"ok": False, "reason": "该 .brd 没有可解析的内嵌设计快照", "segments": 0}
    entries = _entries(doc)
    silk = [e for e in entries if is_silk_entry(e)]
    by_class: "OrderedDict[str, int]" = OrderedDict()
    for e in silk:
        key = f"{e.get('class')} / {e.get('subclass')}"
        by_class[key] = by_class.get(key, 0) + 1
    widths = sorted({round((e.get("width") or 0.0) * scale_mm_per_unit, 4)
                     for e in silk if (e.get("width") or 0.0) > 0})
    return {
        "ok": bool(silk),
        "segments": len(silk),
        "by_class": dict(by_class),
        "has_widths": bool(widths),
        "widths_mm": widths[:8],
        "min_width_mm": widths[0] if widths else None,
        "max_width_mm": widths[-1] if widths else None,
        "source": "内嵌快照 class/subclass 含 SILKSCREEN 的图形 width 字段",
        "reason": "" if widths else "丝印图形的 width 字段全是 0 (快照未记录线宽)",
    }


def silkscreen_to_outline(doc: Optional[dict], scale_mm_per_unit: float) -> dict:
    """丝印到板框成型线的最短距离 (mm)。

    用线段到线段的最短距离算; 先按包围盒做下界剪枝, 避免上万段丝印全量两两比较。
    """
    if not doc:
        return {"ok": False, "reason": "该 .brd 没有可解析的内嵌设计快照"}
    if not scale_mm_per_unit:
        return {"ok": False, "reason": "板框标定失败, 无法换算距离"}
    entries = _entries(doc)
    outline_pieces: List[tuple] = []
    for e in entries:
        if (e.get("class") or "").upper() == "BOARD GEOMETRY" and \
                (e.get("subclass") or "").upper() == "OUTLINE":
            outline_pieces.extend(_pieces(e))
    if not outline_pieces:
        return {"ok": False, "reason": "没找到 BOARD GEOMETRY/OUTLINE 板框"}
    silk_pieces: List[tuple] = []
    for e in entries:
        if is_silk_entry(e):
            silk_pieces.extend(_pieces(e))
    if not silk_pieces:
        return {"ok": False, "reason": "没找到丝印图形 (class/subclass 含 SILKSCREEN)"}

    outline_bbox = _bbox_of(outline_pieces)
    best = float("inf")
    sample = None
    for sp in silk_pieces:
        piece_bbox = (min(sp[0][0], sp[1][0]), min(sp[0][1], sp[1][1]),
                      max(sp[0][0], sp[1][0]), max(sp[0][1], sp[1][1]))
        if _bbox_gap(piece_bbox, outline_bbox) > best:
            continue
        for op in outline_pieces:
            dist = _seg_seg_dist(sp[0], sp[1], op[0], op[1])
            if dist < best:
                best = dist
                sample = sp
                if best <= 0:
                    break
        if best <= 0:
            break
    if best == float("inf"):
        return {"ok": False, "reason": "丝印与板框距离计算失败"}
    return {
        "ok": True,
        "min_mm": round(best * scale_mm_per_unit, 4),
        "silk_segments": len(silk_pieces),
        "outline_segments": len(outline_pieces),
        "sample_unit": sample[0] if sample else None,
        "source": "丝印图形到 BOARD GEOMETRY/OUTLINE 的几何最短距离",
    }


_BODY_SUBCLASSES = ("ASSEMBLY_TOP", "ASSEMBLY_BOTTOM",
                    "PLACE_BOUND_TOP", "PLACE_BOUND_BOTTOM")


def silk_to_component(doc: Optional[dict], scale_mm_per_unit: float) -> dict:
    """板级丝印到器件本体 (PLACE_BOUND / ASSEMBLY 轮廓) 的最短距离 (mm)。

    快照没有焊盘, 器件本体用占位框/装配轮廓代替; 组件自身丝印与本体天然相接,
    所以只量板级丝印 (`BOARD GEOMETRY/SILKSCREEN_*`), 避免恒为 0。
    """
    if not doc:
        return {"ok": False, "reason": "该 .brd 没有可解析的内嵌设计快照"}
    if not scale_mm_per_unit:
        return {"ok": False, "reason": "板框标定失败, 无法换算距离"}
    entries = _entries(doc)
    bodies: List[tuple] = []
    for e in entries:
        if (e.get("class") or "").upper() != "PACKAGE GEOMETRY":
            continue
        if (e.get("subclass") or "").upper() not in _BODY_SUBCLASSES:
            continue
        pts = _polyline(e)
        if len(pts) < 2:
            continue
        body_bbox = (min(p[0] for p in pts), min(p[1] for p in pts),
                     max(p[0] for p in pts), max(p[1] for p in pts))
        for i in range(len(pts) - 1):
            bodies.append((pts[i], pts[i + 1], body_bbox))
    if not bodies:
        return {"ok": False,
                "reason": "快照里没有器件本体轮廓 (PACKAGE GEOMETRY/PLACE_BOUND 或 ASSEMBLY)"}

    silk_pieces: List[tuple] = []
    for e in entries:
        if (e.get("class") or "").upper() != "BOARD GEOMETRY":
            continue
        if "SILKSCREEN" not in (e.get("subclass") or "").upper():
            continue
        silk_pieces.extend(_pieces(e))
    if not silk_pieces:
        return {"ok": False, "reason": "这块板没有板级丝印 (BOARD GEOMETRY/SILKSCREEN)"}

    best = float("inf")
    sample = None
    for sp in silk_pieces:
        piece_bbox = (min(sp[0][0], sp[1][0]), min(sp[0][1], sp[1][1]),
                      max(sp[0][0], sp[1][0]), max(sp[0][1], sp[1][1]))
        for a, b, body_bbox in bodies:
            if _bbox_gap(piece_bbox, body_bbox) > best:
                continue
            dist = _seg_seg_dist(sp[0], sp[1], a, b)
            if dist < best:
                best = dist
                sample = sp[0]
    if best == float("inf"):
        return {"ok": False, "reason": "丝印与器件本体距离计算失败"}
    return {
        "ok": True,
        "min_mm": round(best * scale_mm_per_unit, 4),
        "silk_segments": len(silk_pieces),
        "body_segments": len(bodies),
        "sample_unit": sample,
        "source": "板级丝印到 PACKAGE GEOMETRY/PLACE_BOUND·ASSEMBLY 本体的几何最短距离",
        "note": "快照无焊盘, 器件本体用占位框/装配轮廓代替; 组件自身丝印未计入",
    }

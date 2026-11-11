"""几何工具: 把 .brd 内嵌快照里的图形规整成「毫米坐标的线段」, 并做空间索引。

数据现实 (为什么这里要写得这么啰嗦)
--------------------------------
`.brd` 内嵌快照 (design.comps) 里只有图形本身: class / subclass / type / width /
points / endpts, **没有** 网络号, **没有** 焊盘定义, **没有** 器件位号坐标。
所以本模块严格只做「图形 -> 毫米」的换算与归类, 不替上层猜网络归属:

    entries 里的 class/subclass 决定图形属于哪一层、什么用途
    width 是设计单位, 要乘 scale_mm_per_unit 才是 mm
    type=2 直线 (points 两端点), type=6 圆弧 (points 是圆心, endpts 是端点)

上层 (cat_drc / cat_physical) 用 `same_layer_gaps()` 找间距违规, 用
`length_mm()` 统计铜箔长度; 这些结论都会在报告里注明"不含网络信息"。
"""
import math
from typing import Dict, Iterable, List, Optional, Tuple

from src.design_geometry import _polyline

# --------------------------------------------------------------------------
# 分类关键字: 快照里的 class/subclass 写法在不同版本/不同 CAD 流程下会变,
# 这里用"包含"匹配, 宁可多认几种写法也不要漏掉整层图形。
# --------------------------------------------------------------------------
_OUTLINE_HINTS = ("OUTLINE", "DESIGN_OUTLINE")
_ETCH_HINTS = ("ETCH", "CONDUCTOR", "ROUTE")
_SILK_HINTS = ("SILKSCREEN", "SILK", "SS_")
_ASSEMBLY_HINTS = ("ASSEMBLY", "PLACE_BOUND", "PLACEBOUND")
_PASTE_HINTS = ("PASTEMASK", "PASTE", "PMASK", "STENCIL")
_MASK_HINTS = ("SOLDERMASK", "SMASK", "SOLDER_MASK")
_KEEPIN_HINTS = ("KEEPIN", "KEEP_IN", "KEEPOUT")
_PAD_HINTS = ("PAD", "PIN")
_TEXT_HINTS = ("TEXT", "REFDES", "TITLE", "DIMENSION")


def _has(text: str, hints) -> bool:
    return any(h in text for h in hints)


def classify(entry: dict) -> str:
    """把一条快照图形归到用途类别: outline/etch/silk/assembly/paste/mask/keepin/pad/other。"""
    cls = str(entry.get("class") or "").upper()
    sub = str(entry.get("subclass") or "").upper()
    blob = f"{cls} {sub}"
    # 顺序有讲究: OUTLINE 可能出现在 BOARD GEOMETRY/OUTLINE, 也可能在
    # PACKAGE GEOMETRY/OUTLINE (器件外框), 后者不算板框 —— 由 class 决定。
    if _has(cls, _KEEPIN_HINTS) or _has(sub, _KEEPIN_HINTS):
        return "keepin"
    if _has(sub, _OUTLINE_HINTS) and not _has(cls, _ASSEMBLY_HINTS):
        return "outline"
    if _has(cls, ("BOARD GEOMETRY",)) and _has(sub, _OUTLINE_HINTS):
        return "outline"
    if _has(cls, _ETCH_HINTS) or _has(sub, ("ETCH",)):
        return "etch"
    if _has(sub, _PASTE_HINTS) or _has(cls, _PASTE_HINTS):
        return "paste"
    if _has(sub, _MASK_HINTS) or _has(cls, _MASK_HINTS):
        return "mask"
    if _has(sub, _SILK_HINTS) or _has(cls, _SILK_HINTS):
        return "silk"
    if _has(sub, _ASSEMBLY_HINTS) or _has(cls, _ASSEMBLY_HINTS):
        return "assembly"
    if _has(sub, _PAD_HINTS) or _has(cls, _PAD_HINTS):
        return "pad"
    if _has(sub, _TEXT_HINTS) or _has(cls, _TEXT_HINTS):
        return "text"
    if blob.strip():
        return "other"
    return "unknown"


def layer_of(entry: dict) -> str:
    """层名: ETCH 用 subclass 当层名, 其它类用 class/subclass 组合。"""
    cls = str(entry.get("class") or "").strip()
    sub = str(entry.get("subclass") or "").strip()
    kind = classify(entry)
    if kind == "etch":
        return sub or "ETCH"
    if cls and sub:
        return f"{cls}/{sub}"
    return cls or sub or "?"


def is_outer_layer(name: str, layer_count: Optional[int] = None) -> bool:
    up = (name or "").upper()
    if up in ("TOP", "BOTTOM"):
        return True
    return any(k in up for k in ("_TOP", "_BOTTOM"))


def layer_index(name: str) -> Optional[int]:
    """把层名映射成序号: TOP=1, BOTTOM=层数(未知时给 -1), L2_GND=2。"""
    up = (name or "").upper().strip()
    if up == "TOP":
        return 1
    if up == "BOTTOM":
        return -1
    m = None
    if up.startswith("L") and len(up) > 1 and up[1].isdigit():
        j = 1
        while j < len(up) and up[j].isdigit():
            j += 1
        m = up[1:j]
    if m:
        try:
            return int(m)
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------
# 图形 -> 毫米线段
# --------------------------------------------------------------------------
def _points(raw_pts) -> List[Tuple[float, float]]:
    out = []
    for p in raw_pts or []:
        if isinstance(p, dict) and p.get("x") is not None:
            try:
                out.append((float(p["x"]), float(p["y"])))
            except (TypeError, ValueError):
                continue
    return out


def _is_arc(entry: dict) -> bool:
    end = _points(entry.get("endpts"))
    return len(end) == 2 and bool(_points(entry.get("points")))


def arc_length(entry: dict) -> Optional[float]:
    """圆弧的真实弧长 (设计单位); 取不到返回 None (调用方退回折线近似)。"""
    end = _points(entry.get("endpts"))
    pts = _points(entry.get("points"))
    if len(end) != 2 or not pts:
        return None
    cx, cy = pts[0]
    (x0, y0), (x1, y1) = end
    r = math.hypot(x0 - cx, y0 - cy)
    if r <= 0:
        return None
    a0, a1 = entry.get("start"), entry.get("end")
    try:
        a0 = float(a0) if a0 is not None else math.atan2(y0 - cy, x0 - cx)
        a1 = float(a1) if a1 is not None else math.atan2(y1 - cy, x1 - cx)
    except (TypeError, ValueError):
        a0 = math.atan2(y0 - cy, x0 - cx)
        a1 = math.atan2(y1 - cy, x1 - cx)
    delta = a1 - a0
    while delta > math.pi:
        delta -= 2 * math.pi
    while delta < -math.pi:
        delta += 2 * math.pi
    return abs(delta) * r


def length_units(entry: dict, pts: List[Tuple[float, float]]) -> float:
    """一段图形的长度 (设计单位): 圆弧用精确弧长, 直线用两点距离。"""
    exact = arc_length(entry)
    if exact is not None:
        return exact
    total = 0.0
    for i in range(len(pts) - 1):
        total += math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
    return total


def bbox_of(pts) -> Optional[Tuple[float, float, float, float]]:
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def normalize(entries: Iterable[dict], scale_mm_per_unit: float,
              kinds: Optional[Iterable[str]] = None) -> List[dict]:
    """把快照图形规整成 [{kind, layer, width_mm, pts, bbox, length_mm, ...}]。

    pts 已换算成 mm。width 为 0 表示快照没写线宽 (FAB 图纸导出常见),
    此时 width_mm = 0.0 且 has_width = False, 调用方不能拿 0 当"0 线宽"。
    """
    scale = float(scale_mm_per_unit or 0.0)
    want = set(kinds) if kinds else None
    out: List[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        kind = classify(entry)
        if want is not None and kind not in want:
            continue
        raw_pts = _points(entry.get("points"))
        if not raw_pts:
            continue
        is_arc = _is_arc(entry)
        if not is_arc and len(raw_pts) == 1:
            # 圆形图案 (圆焊盘 / 圆盘): 只有圆心 + radius, 没有第二个点。
            # ★ 以前这里按 "点数<2" 直接丢掉, 后果是阻焊层/钢网层**一个焊盘都不剩**,
            #   Gerber 里连 SOLDERMASK 都不会生成 (实测踩过)。
            try:
                radius = float(entry.get("radius") or 0.0)
            except (TypeError, ValueError):
                radius = 0.0
            if radius > 0:
                cx, cy = raw_pts[0]
                center = (round(cx * scale, 5), round(cy * scale, 5))
                diameter = round(2.0 * radius * scale, 5)
                out.append({
                    "kind": kind,
                    "layer": layer_of(entry),
                    "subclass": str(entry.get("subclass") or ""),
                    # 快照的 width 字段对圆形图案没有意义 (那是线宽), 保持 0/False,
                    # 免得焊盘直径混进"线宽分布"里把线宽检查带偏。
                    "width_mm": round(float(entry.get("width") or 0.0) * scale, 5),
                    "has_width": False,
                    "pts": [center, center],
                    "bbox": (center[0], center[1], center[0], center[1]),
                    "length_mm": 0.0,
                    "arc": False,
                    "radius_mm": round(radius * scale, 5),
                    "flash": True,
                    "diameter_mm": diameter,
                })
                continue
        pts = _polyline(entry) if is_arc else raw_pts
        if len(pts) < 2:
            continue
        pts_mm = [(round(x * scale, 5), round(y * scale, 5)) for x, y in pts]
        try:
            width = float(entry.get("width") or 0.0)
        except (TypeError, ValueError):
            width = 0.0
        box = bbox_of(pts_mm)
        out.append({
            "kind": kind,
            "layer": layer_of(entry),
            "subclass": str(entry.get("subclass") or ""),
            "width_mm": round(width * scale, 5),
            "has_width": width > 0,
            "pts": pts_mm,
            "bbox": box,
            "length_mm": round(length_units(entry, pts) * scale, 5),
            "arc": is_arc,
            "radius_mm": round(float(entry.get("radius") or 0.0) * scale, 5),
            "flash": False,
        })
    return out


def total_length_mm(segs: Iterable[dict]) -> float:
    return round(sum(s["length_mm"] for s in segs), 3)


def width_summary(segs: Iterable[dict]) -> dict:
    """线宽分布统计; 没有任何线宽信息时 has_widths=False (不把 0 当线宽)。"""
    widths = [s["width_mm"] for s in segs if s.get("has_width") and s["width_mm"] > 0]
    if not widths:
        return {"has_widths": False, "count": 0}
    widths_sorted = sorted(widths)
    n = len(widths_sorted)

    def pick(q):
        return widths_sorted[min(n - 1, int(q * n))]

    return {
        "has_widths": True,
        "count": n,
        "min_mm": round(widths_sorted[0], 4),
        "max_mm": round(widths_sorted[-1], 4),
        "median_mm": round(widths_sorted[n // 2], 4),
        "p10_mm": round(pick(0.10), 4),
        "p90_mm": round(pick(0.90), 4),
        "distinct": sorted({round(w, 3) for w in widths_sorted})[:40],
    }


# --------------------------------------------------------------------------
# 空间索引: 找同一层上小于阈值的线段对
# --------------------------------------------------------------------------
def _seg_seg_dist(a1, a2, b1, b2) -> float:
    from src.design_geometry import _seg_seg_dist as dist

    return dist(a1, a2, b1, b2)


def _poly_dist(a: List[Tuple[float, float]], b: List[Tuple[float, float]]) -> float:
    from src.design_geometry import _poly_dist as dist

    return dist(a, b)


def _bbox_close(a, b, gap: float) -> bool:
    return not (a[2] + gap < b[0] or b[2] + gap < a[0]
                or a[3] + gap < b[1] or b[3] + gap < a[1])


def copper_half_width(seg: dict) -> float:
    """图形在间距计算里占掉的半宽。

    走线用线宽的一半; 圆形图案 (焊盘/圆盘) 用直径的一半 —— 它的 width_mm 是
    线宽字段 (对圆盘无意义, 保持 0), 直接拿 0 会把焊盘当成一个点, 低估间距。
    """
    if seg.get("flash"):
        return float(seg.get("diameter_mm") or 0.0) / 2.0
    return float(seg.get("width_mm") or 0.0) / 2.0


def same_layer_gaps(segs: List[dict], threshold_mm: float,
                    max_pairs: int = 200000,
                    max_items: int = 2000) -> dict:
    """同一层内, 间距小于 threshold_mm 的线段对。

    返回 {pairs: [...], min_gap_mm, examined, truncated, item_truncated}。
    用均匀网格做粗筛, 只对包围盒接近的对做精确距离 —— 真实板子 ETCH 段上万条,
    不做索引就是 O(n^2), 跑不动。

    注意口径: 快照几何**不带网络号**, 所以同网络本来就连在一起的铜也被算进来,
    结论必须写清"含同网络间距, 需用 DRC 报告复核"。
    """
    if not segs or threshold_mm <= 0:
        return {"pairs": [], "min_gap_mm": None, "examined": 0, "truncated": False,
                "item_truncated": False, "segment_count": len(segs)}

    cell = max(threshold_mm, 0.05)
    grid: Dict[Tuple[int, int, str], List[int]] = {}
    for idx, seg in enumerate(segs):
        box = seg.get("bbox")
        if not box:
            continue
        layer = seg["layer"]
        # ★ 必须把包围盒向外扩一个阈值再入格: 否则"刚好落在相邻格子里"的一对
        #   永远分不到同一个桶里, 阈值内的间距就被整片漏掉 (实测踩过这个坑)。
        x0 = int(math.floor((box[0] - threshold_mm) / cell))
        y0 = int(math.floor((box[1] - threshold_mm) / cell))
        x1 = int(math.floor((box[2] + threshold_mm) / cell))
        y1 = int(math.floor((box[3] + threshold_mm) / cell))
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                grid.setdefault((gx, gy, layer), []).append(idx)

    examined = 0
    truncated = False
    seen = set()
    pairs: List[dict] = []
    touching: List[dict] = []
    min_gap = None
    # 中心线距离小于这个值 = 两段图形相接/相交。相接的绝大多数是**同一网络的
    # 连接点**(拐角、T 型接头), 不是间距不足; 相交才可能是短路, 但判短路需要
    # 网络信息。这里把两者单独归到 touching, 由上层如实说明, 不计入间距违规。
    touch_tol = 0.002
    for key, bucket in grid.items():
        if truncated:
            break
        if len(bucket) < 2:
            continue
        for i in range(len(bucket)):
            a = segs[bucket[i]]
            for j in range(i + 1, len(bucket)):
                b = segs[bucket[j]]
                pid = (bucket[i], bucket[j])
                if pid in seen:
                    continue
                seen.add(pid)
                examined += 1
                if examined > max_pairs:
                    truncated = True
                    break
                if not _bbox_close(a["bbox"], b["bbox"], threshold_mm):
                    continue
                gap = _poly_dist(a["pts"], b["pts"])
                if gap <= touch_tol:
                    if len(touching) < max_items:
                        touching.append({"a": bucket[i], "b": bucket[j],
                                         "layer": a["layer"],
                                         "a_width_mm": a["width_mm"],
                                         "b_width_mm": b["width_mm"],
                                         "a_bbox": a["bbox"], "b_bbox": b["bbox"]})
                    continue
                # 铜宽占掉一部分间距: 中心线距离 - 两边半宽 = 铜到铜净间距
                # (圆形焊盘用直径的一半, 见 copper_half_width)
                edge = gap - copper_half_width(a) - copper_half_width(b)
                if edge > threshold_mm:
                    continue
                if min_gap is None or edge < min_gap:
                    min_gap = edge
                if len(pairs) < max_items:
                    pairs.append({"a": bucket[i], "b": bucket[j],
                                  "layer": a["layer"], "gap_mm": round(edge, 4),
                                  "center_mm": round(gap, 4),
                                  "a_width_mm": a["width_mm"],
                                  "b_width_mm": b["width_mm"],
                                  "a_flash": bool(a.get("flash")),
                                  "b_flash": bool(b.get("flash")),
                                  "a_bbox": a["bbox"], "b_bbox": b["bbox"]})
    pairs.sort(key=lambda p: p["gap_mm"])
    return {
        "pairs": pairs,
        "touching": touching,
        "min_gap_mm": None if min_gap is None else round(min_gap, 4),
        "examined": examined,
        "truncated": truncated,
        "item_truncated": len(pairs) >= max_items,
        "segment_count": len(segs),
    }


def mid_point(seg: dict) -> Tuple[float, float]:
    box = seg.get("bbox")
    if not box:
        return (0.0, 0.0)
    return (round((box[0] + box[2]) / 2.0, 4), round((box[1] + box[3]) / 2.0, 4))


def nearest_segment(segs: List[dict], point: Tuple[float, float],
                    kind: Optional[str] = None) -> Optional[dict]:
    """离给定点最近的线段 (用于把违规位置对到板边/器件)."""
    best, best_d = None, None
    for seg in segs:
        if kind and seg["kind"] != kind:
            continue
        box = seg.get("bbox")
        if not box:
            continue
        d = _poly_dist([point], seg["pts"]) if len(seg["pts"]) > 1 else None
        if d is None:
            continue
        if best_d is None or d < best_d:
            best, best_d = seg, d
    return best

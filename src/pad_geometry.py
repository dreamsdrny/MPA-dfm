"""器件焊盘几何: 焊盘尺寸 / 引脚间距, 以及焊盘到铜皮(平面)与到走线(线路)的距离。

用户问的三件事, 本模块一件一件回答
----------------------------------
    1. 每颗器件的焊盘**尺寸**是多少 (长×宽 / 直径);
    2. 每颗器件的**引脚间距**是多少 (焊盘中心距, pitch);
    3. 每颗器件的焊盘**离铜皮多远、离走线多远** (mm)。

数据从哪来 (按可信度排序, 都拿不到就如实说缺什么)
------------------------------------------------
    a. Allegro SKILL 导出 `evan_pads.csv` (推荐)
       每个引脚的焊盘外框 W/H + 位号 + 焊盘栈 + 层 —— 这是设计数据库里的
       真实焊盘尺寸, 逐器件统计与判定都以它为准;
    b. `.brd` 内嵌快照里的焊盘图形
       钢网层 (PASTEMASK) / PAD / PIN 层的圆盘 (type=3 flash) 或闭合矩形轮廓。
       快照里**没有位号**, 用"几何聚类"把挨在一起的焊盘合成一颗器件, 命名成
       `簇#n`, 并明确标注是聚类结果而不是真实位号;
    c. 缺 b 时退回 `evan_holes.csv` 里 KIND=PIN 的行 (有 REFDES, 焊盘尺寸只能
       从焊盘栈名里解析, 解析不出就不给尺寸, 绝不猜)。

铜箔几何只有快照里那一份 (ETCH 图形 = 走线 + 铜皮/平面**轮廓**), 所以:
    * 线宽 < 0.5mm 的 ETCH 图形按**走线**看; >= 0.5mm 或首尾闭合的按**铜皮**看;
    * 快照只有铜皮轮廓, 没有填充面。焊盘落在闭合铜皮轮廓**内部**时, 说明它
      已经把焊盘包进去了 (通常是同网络的平面/铜皮), 此时不给"间距"数字, 而是
      标成"在铜皮内 (含相连, 需网络信息复核)" —— 拿轮廓距离当间距会骗人。

坐标系
------
快照坐标是设计单位 (mil), 乘 scale_mm_per_unit 得 mm, 但原点与设计原点未必重合;
SKILL / Placement 导出的是设计坐标系 mm。两套坐标一起用时, 先用 .brd 的
Draw Size (MINX/MINY) 做原点对齐, 再**校验焊盘是否都落在图纸范围内**:
校验不过就不量距离, 只报"坐标对不上, 需人工核对", 不输出假数字。
"""
import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from src.design_geometry import _seg_seg_dist
from src.pad_metrics import _point_in_poly, _seg_point_dist, pad_outline, shape_gap
from src.pad_metrics import pad_metrics as _pair_metrics

__all__ = [
    "collect_pads", "snapshot_pads", "inventory_pads", "pad_size_from_padstack",
    "group_components", "component_rows", "copper_features",
    "measure_pad_clearance", "clearance_rows", "align_offset", "analyze",
]

# 快照里的焊盘层 -> 可信度: 钢网层(PASTEMASK)最接近真实焊盘尺寸,
# PAD/PIN 是焊盘本体, 阻焊(SOLDERMASK)是焊盘 + 开窗余量 (只能作参考)
_PAD_KIND_PRIORITY = {"paste": 0, "pad": 1, "mask": 2}
_PAD_KINDS: Tuple[str, ...] = tuple(_PAD_KIND_PRIORITY)
_KIND_LABEL = {"paste": "钢网层(PASTEMASK)", "pad": "焊盘层(PAD/PIN)",
               "mask": "阻焊层(SOLDERMASK, 含开窗余量)"}

_POUR_WIDTH_MM = 0.5        # 线宽 >= 0.5mm 的 ETCH 图形按铜皮/平面轮廓看
_LOOP_TOL_MM = 0.02         # 端点吸附容差 (闭合环识别 / 同一焊盘去重)
_TOUCH_TOL_MM = 0.001       # 小于该值视为相接/相交
_DEDUP_TOL_MM = 0.05        # 同一位置多层焊盘去重半径


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def side_of_layer(layer: str) -> str:
    """从层名判断面别: 'PACKAGE GEOMETRY/PASTEMASK_TOP' -> 'TOP'。"""
    up = (layer or "").upper()
    if "BOTTOM" in up:
        return "BOTTOM"
    if "TOP" in up:
        return "TOP"
    return ""


def _bbox_of(pts: Sequence[Tuple[float, float]]):
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _pt_key(pt, tol: float = _LOOP_TOL_MM):
    return (round(pt[0] / tol), round(pt[1] / tol))


def _poly_len(pts: Sequence[Tuple[float, float]]) -> float:
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))


def _edge_key(a, b, tol: float = _LOOP_TOL_MM):
    ka, kb = _pt_key(a, tol), _pt_key(b, tol)
    return (ka, kb) if ka <= kb else (kb, ka)


def _closed_loops(seg_pts: List[List[Tuple[float, float]]],
                  tol: float = _LOOP_TOL_MM) -> List[dict]:
    """把一组折线里首尾相连的闭合环挑出来 (每个点恰好接两条边)。

    焊盘的矩形轮廓在快照里就是 4 条独立线段, 只有拼成环才能算出焊盘长宽;
    拼不上的 (孤立的线/文字) 不算焊盘, 由调用方计入"未识别"。

    返回 [{"poly": 闭合折线, "edges": 用到的边(端点对)}, ...]: 调用方要用 edges
    把这些线段从"独立走线"里剔掉, 否则同一个铜皮会被算两遍。
    """
    adj: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    coords: Dict[Tuple[int, int], Tuple[float, float]] = {}
    for pts in seg_pts:
        for i in range(len(pts) - 1):
            a, b = _pt_key(pts[i], tol), _pt_key(pts[i + 1], tol)
            if a == b:
                continue
            coords.setdefault(a, pts[i])
            coords.setdefault(b, pts[i + 1])
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)

    seen = set()
    loops: List[dict] = []
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
            nbrs = adj.get(cur, [])
            if len(nbrs) != 2:
                closed = False
            for nxt in nbrs:
                if nxt not in seen:
                    stack.append(nxt)
        if not closed or len(comp) < 3:
            continue
        # 沿邻接走一圈, 顺序还原成闭合折线
        order = [comp[0]]
        prev = None
        cur = comp[0]
        guard = 0
        while guard < len(comp) + 2:
            guard += 1
            nxts = [n for n in adj.get(cur, []) if n != prev]
            if not nxts:
                break
            nxt = nxts[0]
            if nxt == comp[0]:
                break
            order.append(nxt)
            prev, cur = cur, nxt
        poly = [coords[k] for k in order]
        if len(poly) >= 3:
            poly = poly + [poly[0]]
            edges = {(order[i], order[(i + 1) % len(order)]) for i in range(len(order))}
            loops.append({"poly": poly,
                          "edges": {_edge_key(coords[a], coords[b]) for a, b in edges}})
    return loops


# --------------------------------------------------------------------------
# 焊盘来源 1: SKILL / 导出文件
# --------------------------------------------------------------------------
def pad_size_from_padstack(name: str) -> Optional[Tuple[float, float]]:
    """从焊盘栈名里解析焊盘尺寸 (mm)。解析不出返回 None —— 绝不猜。

    常见写法: `PAD60X30` / `SMD_0.6X1.05` / `R0603_1`(解析不出)。
    名字里没有单位的按 mil 处理 (Allegro 焊盘栈名习惯用 mil), 并做合理性检查。
    """
    import re

    text = (name or "").strip().upper()
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*[X*]\s*(\d+(?:\.\d+)?)", text)
    if not m:
        return None
    try:
        a, b = float(m.group(1)), float(m.group(2))
    except ValueError:
        return None
    if a <= 0 or b <= 0:
        return None
    # 明显是"局部尺寸/内径"的小数 (如 0.5X1.05 mm) 与 mil 数值 (60X30) 都支持:
    # mil 数值乘 0.0254; 已经带小数点且 <= 20 的按 mm 直接看。
    if a <= 20 and b <= 20 and ("." in m.group(1) or "." in m.group(2)):
        w_mm, h_mm = a, b
    else:
        w_mm, h_mm = a * 0.0254, b * 0.0254
    if not (0.05 <= w_mm <= 30 and 0.05 <= h_mm <= 30):
        return None
    return (round(max(w_mm, h_mm), 4), round(min(w_mm, h_mm), 4))


def inventory_pads(inventory: Optional[dict]) -> dict:
    """从导入的坐标清单里取焊盘几何 (evan_pads.csv 优先, PIN 孔位行兜底)。"""
    if not inventory:
        return {"ok": False, "pads": [], "sources": [],
                "reason": "没有导入坐标文件"}
    pads: List[dict] = []
    sources: List[str] = []

    for row in inventory.get("pads") or []:
        w, h = row.get("w_mm"), row.get("h_mm")
        if not w or not h:
            continue
        pads.append({
            "x": float(row["x_mm"]), "y": float(row["y_mm"]),
            "w": float(w), "h": float(h),
            "shape": "rect" if abs(float(w) - float(h)) > 1e-6 else "square",
            "refdes": (row.get("refdes") or "").strip(),
            "pin": (row.get("pin") or row.get("padstack") or "").strip(),
            "padstack": (row.get("padstack") or "").strip(),
            "net": (row.get("net") or "").strip(),
            "layer": (row.get("layer") or row.get("side") or "").strip(),
            "side": (row.get("side") or side_of_layer(row.get("layer") or "")).strip().upper(),
            "source": row.get("source") or "SKILL 导出 (焊盘外框)",
            "kind": "export",
        })
    if pads:
        sources.append(f"SKILL 导出焊盘外框 ({len(pads)} 个)")

    if not pads:
        # 兜底: evan_holes.csv 里的 PIN 行 (有 REFDES, 焊盘尺寸看焊盘栈名)
        for row in inventory.get("holes") or []:
            if (row.get("kind") or "").upper() != "PIN":
                continue
            w = row.get("pad_w_mm")
            h = row.get("pad_h_mm")
            if not w or not h:
                size = pad_size_from_padstack(row.get("padstack") or "")
                if not size:
                    continue
                w, h = size
            pads.append({
                "x": float(row["x_mm"]), "y": float(row["y_mm"]),
                "w": float(w), "h": float(h),
                "shape": "rect", "refdes": (row.get("refdes") or "").strip(),
                "pin": (row.get("pin") or "").strip(),
                "padstack": (row.get("padstack") or "").strip(),
                "net": (row.get("net") or "").strip(),
                "layer": "", "side": (row.get("side") or "").strip().upper(),
                "source": "SKILL 导出引脚孔位 + 焊盘栈名解析",
                "kind": "export",
            })
        if pads:
            sources.append(f"SKILL 导出 PIN 孔位 + 焊盘栈名 ({len(pads)} 个)")

    if not pads:
        return {"ok": False, "pads": [], "sources": [],
                "reason": ("导入的文件里没有焊盘几何: 需要 evan_pads.csv "
                           "(SKILL 导出焊盘外框), 或 evan_holes.csv 里能解析出尺寸的 "
                           "KIND=PIN 行")}
    return {"ok": True, "pads": pads, "sources": sources,
            "count": len(pads)}


# --------------------------------------------------------------------------
# 焊盘来源 2: .brd 内嵌快照的焊盘图形
# --------------------------------------------------------------------------
def snapshot_pads(doc: Optional[dict], scale_mm_per_unit: float) -> dict:
    """从内嵌快照里取焊盘图形 (圆盘 flash / 闭合矩形轮廓)。"""
    if not doc:
        return {"ok": False, "pads": [], "reason": "该 .brd 没有可解析的内嵌设计快照"}
    if not scale_mm_per_unit:
        return {"ok": False, "pads": [], "reason": "板框标定失败, 无法换算焊盘尺寸"}
    try:
        from src.brd_analysis import geom
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "pads": [], "reason": f"几何模块不可用: {exc}"}

    entries = ((doc.get("design") or {}).get("comps")) or []
    segs = geom.normalize(entries, scale_mm_per_unit, _PAD_KINDS)
    pads: List[dict] = []
    skipped = 0

    # 1) 圆盘: 单点 + radius (焊盘 flash)
    loops_input: Dict[Tuple[str, str], List[List[Tuple[float, float]]]] = {}
    for seg in segs:
        kind, layer = seg["kind"], seg["layer"]
        if seg.get("flash"):
            d = float(seg.get("diameter_mm") or 0.0)
            if d <= 0:
                skipped += 1
                continue
            cx, cy = seg["pts"][0]
            pads.append({
                "x": cx, "y": cy, "w": d, "h": d, "d": d, "shape": "circle",
                "refdes": "", "pin": "", "padstack": "", "net": "",
                "layer": layer, "side": side_of_layer(layer), "source": "快照焊盘圆盘",
                "kind": kind, "flash": True,
            })
        else:
            loops_input.setdefault((kind, layer), []).append(list(seg["pts"]))

    # 2) 矩形焊盘: 同一 (层, 类别) 下首尾相连的闭合轮廓
    for (kind, layer), chunk in loops_input.items():
        loops = _closed_loops(chunk)
        for loop in loops:
            poly = loop["poly"]
            box = _bbox_of(poly)
            if not box:
                continue
            w, h = box[2] - box[0], box[3] - box[1]
            if w <= 0 or h <= 0 or w > 60 or h > 60:
                skipped += 1
                continue
            pads.append({
                "x": (box[0] + box[2]) / 2.0, "y": (box[1] + box[3]) / 2.0,
                "w": w, "h": h, "shape": "rect",
                "refdes": "", "pin": "", "padstack": "", "net": "",
                "layer": layer, "side": side_of_layer(layer),
                "source": "快照焊盘矩形轮廓", "kind": kind, "flash": False,
                "angle": 0.0,
            })
        skipped += len(chunk) - sum(len(p["poly"]) - 1 for p in loops)

    if not pads:
        return {
            "ok": False, "pads": [], "skipped": skipped,
            "reason": ("内嵌快照里没有焊盘图形 (PASTEMASK/PAD/PIN 层的圆盘或闭合矩形); "
                       "用 --export-skill 生成 SKILL 脚本导出 evan_pads.csv, "
                       "或提供 Allegro 的焊盘栈/焊盘报告"),
        }

    # 3) 同一位置多层重复 (钢网 / 阻焊 / 焊盘本体重叠): 只留最可信的一层
    pads.sort(key=lambda p: (_PAD_KIND_PRIORITY.get(p.get("kind", ""), 9), p["x"], p["y"]))
    kept: List[dict] = []
    for pad in pads:
        dup = None
        for other in kept:
            if (_pt_key((pad["x"], pad["y"]), _DEDUP_TOL_MM)
                    == _pt_key((other["x"], other["y"]), _DEDUP_TOL_MM)
                    and abs(pad["w"] - other["w"]) < _DEDUP_TOL_MM * 4
                    and abs(pad["h"] - other["h"]) < _DEDUP_TOL_MM * 4):
                dup = other
                break
        if dup is None:
            kept.append(pad)
        else:
            dup.setdefault("also_on", []).append(
                f"{_KIND_LABEL.get(pad.get('kind', ''), pad.get('kind'))}"
                f" {pad['w']:.3f}x{pad['h']:.3f}mm")
    kept.sort(key=lambda p: (p["y"], p["x"]))
    kinds = sorted({p.get("kind") for p in kept})
    return {
        "ok": True,
        "pads": kept,
        "count": len(kept),
        "skipped": skipped,
        "layers": sorted({p["layer"] for p in kept}),
        "sources": [f"内嵌快照焊盘图形 ({_KIND_LABEL.get(k, k)}, {len(kept)} 个)" for k in kinds],
        "note": ("同一位置的钢网/阻焊/焊盘图形已去重, 保留最可信的一层 "
                 "(钢网层 > 焊盘层 > 阻焊层)"),
    }


# --------------------------------------------------------------------------
# 汇总所有来源
# --------------------------------------------------------------------------
def collect_pads(doc: Optional[dict] = None, scale_mm_per_unit: float = 0.0,
                 inventory: Optional[dict] = None) -> dict:
    """把两路焊盘来源合起来: 导出文件优先 (有位号/真实尺寸), 快照图形补充。"""
    sources: List[str] = []
    pads: List[dict] = []
    reasons: List[str] = []

    exp = inventory_pads(inventory)
    if exp.get("ok"):
        pads.extend(exp["pads"])
        sources.extend(exp["sources"])
    else:
        reasons.append(f"导出文件: {exp.get('reason')}")

    snap = snapshot_pads(doc, scale_mm_per_unit)
    if snap.get("ok"):
        # 导出文件已经覆盖的位置不再重复计入 (导出的是真实焊盘尺寸)
        added = 0
        for pad in snap["pads"]:
            if any(_pt_key((pad["x"], pad["y"]), _DEDUP_TOL_MM)
                   == _pt_key((o["x"], o["y"]), _DEDUP_TOL_MM) for o in pads):
                continue
            pads.append(pad)
            added += 1
        if added:
            sources.extend(snap.get("sources") or [])
            if added < snap["count"]:
                sources.append(f"快照焊盘与导出焊盘重叠 {snap['count'] - added} 个, 已按导出值计")
        if not added:
            sources.append("内嵌快照焊盘图形与导出焊盘完全重叠, 未重复计入")
    else:
        reasons.append(f"内嵌快照: {snap.get('reason')}")

    if not pads:
        return {"ok": False, "pads": [], "sources": [], "reasons": reasons,
                "reason": "；".join(r for r in reasons if r) or "没有焊盘数据"}
    return {"ok": True, "pads": pads, "count": len(pads), "sources": sources,
            "reasons": reasons,
            "named": sum(1 for p in pads if p.get("refdes")),
            "shapes": sorted({p.get("shape") for p in pads})}


# --------------------------------------------------------------------------
# 器件分组: 有位号按位号, 没有位号按几何聚类
# --------------------------------------------------------------------------
def _edge_gap(a: dict, b: dict) -> float:
    try:
        return shape_gap(a, b)
    except ValueError:
        dx = abs(a["x"] - b["x"]) - (a["w"] + b["w"]) / 2.0
        dy = abs(a["y"] - b["y"]) - (a["h"] + b["h"]) / 2.0
        return max(dx, dy)


def group_components(pads: List[dict], gap_mm: float = 5.0) -> List[dict]:
    """把焊盘归到器件上。

    有位号 (SKILL 导出) -> 直接按位号分;
    没有位号 (快照图形)  -> 按"焊盘边缘间距 <= gap_mm"做并查集聚类, 命名 簇#n,
                            并在来源里写明这是聚类结果, 不是真实位号。
    """
    named: Dict[str, List[dict]] = {}
    unnamed: List[dict] = []
    for pad in pads:
        ref = (pad.get("refdes") or "").strip()
        if ref:
            named.setdefault(ref, []).append(pad)
        else:
            unnamed.append(pad)

    groups: List[dict] = []
    for ref in sorted(named, key=_natural_key):
        groups.append({"name": ref, "refdes": ref, "pads": named[ref],
                       "source": "SKILL 导出位号", "clustered": False})

    if unnamed:
        parent = list(range(len(unnamed)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(unnamed)):
            for j in range(i + 1, len(unnamed)):
                if _edge_gap(unnamed[i], unnamed[j]) <= gap_mm:
                    ra, rb = find(i), find(j)
                    if ra != rb:
                        parent[ra] = rb

        buckets: Dict[int, List[dict]] = {}
        for idx, pad in enumerate(unnamed):
            buckets.setdefault(find(idx), []).append(pad)
        ordered = sorted(buckets.values(),
                         key=lambda b: (_bbox_of([(p["x"], p["y"]) for p in b]) or (0, 0, 0, 0))[1::-1])
        for n, bucket in enumerate(ordered, start=1):
            bucket.sort(key=lambda p: (p["y"], p["x"]))
            groups.append({"name": f"簇#{n}", "refdes": "", "pads": bucket,
                           "source": f"几何聚类 (焊盘边缘间距 <= {gap_mm}mm)",
                           "clustered": True})
    return groups


def _natural_key(refdes: str):
    import re

    parts = re.split(r"(\d+)", refdes or "")
    return [(1, int(p), "") if p.isdigit() else (0, 0, p.upper()) for p in parts if p]


def component_rows(groups: List[dict]) -> List[dict]:
    """每颗器件的焊盘尺寸 / 引脚间距 / 焊盘间距。"""
    rows: List[dict] = []
    for group in groups:
        pads = group["pads"]
        if not pads:
            continue
        # 统一成 pad_metrics 认识的形状, 复用同一套间距算法
        shaped = [_as_metric_pad(p) for p in pads]
        metrics = _pair_metrics(shaped)
        ws = [p["w"] for p in pads]
        hs = [p["h"] for p in pads]
        sizes = sorted({(round(p["w"], 3), round(p["h"], 3)) for p in pads})
        pitches = _pitches(pads)
        box = _bbox_of([(p["x"], p["y"]) for p in pads])
        rows.append({
            "器件": group["name"],
            "焊盘数": len(pads),
            "焊盘尺寸mm": " / ".join(f"{w:g}×{h:g}" for w, h in sizes[:4])
                          + (" 等" if len(sizes) > 4 else ""),
            "最小焊盘mm": round(min(min(ws), min(hs)), 3),
            "最大焊盘mm": round(max(max(ws), max(hs)), 3),
            "引脚间距mm": metrics.get("min_pitch_mm"),
            "主间距mm": pitches.get("mode_pitch_mm"),
            "焊盘间距mm": metrics.get("min_gap_mm"),
            "层/面": "、".join(sorted({(p.get("layer") or p.get("side") or "?")
                                      for p in pads}))[:60],
            "分组依据": group["source"],
            "跨度mm": (f"{box[2] - box[0]:.2f}×{box[3] - box[1]:.2f}" if box else ""),
        })
    return rows


def _as_metric_pad(pad: dict) -> dict:
    """转成 src.pad_metrics 认识的焊盘字典。"""
    out = {"x": pad["x"], "y": pad["y"], "ref": pad.get("pin") or pad.get("refdes") or ""}
    if pad.get("shape") == "circle" or (pad.get("d") and abs(pad["w"] - pad["h"]) < 1e-9):
        out["d"] = pad.get("d") or pad["w"]
    else:
        out["w"] = pad["w"]
        out["h"] = pad["h"]
        out["angle"] = pad.get("angle") or 0.0
    return out


def _pitches(pads: List[dict]) -> dict:
    """焊盘中心距统计: 最小间距与"主间距"(出现最多的那种间距, 即封装 pitch)。"""
    dists: List[float] = []
    for i in range(len(pads)):
        for j in range(i + 1, len(pads)):
            d = math.hypot(pads[i]["x"] - pads[j]["x"], pads[i]["y"] - pads[j]["y"])
            if d > 1e-6:
                dists.append(d)
    if not dists:
        return {"min_pitch_mm": None, "mode_pitch_mm": None, "count": 0}
    buckets: Dict[float, int] = {}
    for d in dists:
        key = round(d, 2)
        buckets[key] = buckets.get(key, 0) + 1
    mode = max(buckets.items(), key=lambda kv: (kv[1], -kv[0]))[0]
    return {"min_pitch_mm": round(min(dists), 4), "mode_pitch_mm": mode,
            "count": len(dists), "distinct": sorted(buckets)[:12]}


# --------------------------------------------------------------------------
# 铜箔几何: 走线 / 铜皮轮廓
# --------------------------------------------------------------------------
def copper_features(doc: Optional[dict], scale_mm_per_unit: float,
                    pour_width_mm: float = _POUR_WIDTH_MM) -> dict:
    """把快照 ETCH 图形分成走线与铜皮轮廓 (含铜层上的圆盘 flash)。"""
    if not doc:
        return {"ok": False, "by_layer": {}, "reason": "该 .brd 没有可解析的内嵌设计快照"}
    if not scale_mm_per_unit:
        return {"ok": False, "by_layer": {}, "reason": "板框标定失败, 无法换算铜箔尺寸"}
    try:
        from src.brd_analysis import geom
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "by_layer": {}, "reason": f"几何模块不可用: {exc}"}

    entries = ((doc.get("design") or {}).get("comps")) or []
    segs = geom.normalize(entries, scale_mm_per_unit, ["etch"])
    if not segs:
        return {"ok": False, "by_layer": {},
                "reason": "内嵌快照里没有 ETCH 铜箔图形, 量不了焊盘到铜皮/走线的距离"}

    by_layer: Dict[str, List[dict]] = {}
    # 先把非 flash 的按层收起来, 用于找铜皮的闭合轮廓
    raw: Dict[str, List[dict]] = {}
    for seg in segs:
        if seg.get("flash"):
            d = float(seg.get("diameter_mm") or 0.0)
            if d <= 0:
                continue
            cx, cy = seg["pts"][0]
            by_layer.setdefault(seg["layer"], []).append({
                "kind": "flash", "layer": seg["layer"], "width_mm": 0.0,
                "half_mm": d / 2.0, "diameter_mm": round(d, 4),
                "pts": [(cx, cy)], "closed": False, "poly": None,
                "closed_pour": False,
                "length_mm": 0.0, "bbox": (cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2),
            })
        else:
            raw.setdefault(seg["layer"], []).append(seg)

    closed_edges = set()
    for layer, layer_segs in raw.items():
        loops = _closed_loops([list(s["pts"]) for s in layer_segs])
        by_edge = {}
        for seg in layer_segs:
            for i in range(len(seg["pts"]) - 1):
                by_edge[_edge_key(seg["pts"][i], seg["pts"][i + 1])] = seg
        for loop in loops:
            poly = loop["poly"]
            box = _bbox_of(poly)
            if not box:
                continue
            closed_edges |= loop["edges"]
            # 铜皮轮廓的线宽: 取构成这个环的线段里最大的线宽 (保守: 铜皮更宽)
            widths = [float(by_edge[e].get("width_mm") or 0.0) for e in loop["edges"]
                      if e in by_edge and by_edge[e].get("has_width")]
            width = max(widths) if widths else 0.0
            by_layer.setdefault(layer, []).append({
                "kind": "pour", "layer": layer, "width_mm": round(width, 4),
                "half_mm": width / 2.0,
                "pts": poly, "poly": poly, "closed": True, "closed_pour": True,
                "length_mm": round(_poly_len(poly), 4), "bbox": box,
                "note": "闭合铜皮轮廓 (内部视为铜)",
            })
        # 闭合环用掉的线段不再当独立走线/独立铜皮, 否则同一个铜皮会被算两遍
        for seg in layer_segs:
            used = any(_edge_key(seg["pts"][i], seg["pts"][i + 1]) in closed_edges
                       for i in range(len(seg["pts"]) - 1))
            if used:
                continue
            width = float(seg.get("width_mm") or 0.0)
            has_width = bool(seg.get("has_width"))
            is_pour = has_width and width >= pour_width_mm
            box = seg.get("bbox")
            by_layer.setdefault(layer, []).append({
                "kind": "pour" if is_pour else "trace", "layer": layer,
                "width_mm": round(width, 4), "half_mm": width / 2.0,
                "pts": list(seg["pts"]), "poly": None, "closed": False,
                "closed_pour": False,
                "length_mm": round(float(seg.get("length_mm") or 0.0), 4),
                "bbox": box, "has_width": has_width,
                "note": "线宽 >= %gmm, 按铜皮/平面轮廓看" % pour_width_mm if is_pour else "",
            })

    counts = {"trace": 0, "pour": 0, "flash": 0}
    for items in by_layer.values():
        for item in items:
            counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return {
        "ok": True, "by_layer": by_layer, "counts": counts,
        "layers": sorted(by_layer),
        "pour_width_mm": pour_width_mm,
        "source": "内嵌快照 ETCH 图形 (走线 + 铜皮轮廓) + 铜层圆盘",
    }


# --------------------------------------------------------------------------
# 焊盘 -> 铜皮 / 走线 的距离
# --------------------------------------------------------------------------
def _grid_index(items: List[dict], cell: float) -> Dict[Tuple[int, int], List[int]]:
    grid: Dict[Tuple[int, int], List[int]] = {}
    for idx, item in enumerate(items):
        box = item.get("bbox")
        if not box:
            continue
        half = float(item.get("half_mm") or 0.0)
        x0 = int(math.floor((box[0] - half) / cell))
        y0 = int(math.floor((box[1] - half) / cell))
        x1 = int(math.floor((box[2] + half) / cell))
        y1 = int(math.floor((box[3] + half) / cell))
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                grid.setdefault((gx, gy), []).append(idx)
    return grid


def _pad_bbox(pad: dict):
    x, y, w, h = pad["x"], pad["y"], pad["w"], pad["h"]
    return (x - w / 2.0, y - h / 2.0, x + w / 2.0, y + h / 2.0)


def _clearance_to_item(pad: dict, item: dict, pad_box) -> Tuple[float, bool]:
    """焊盘边缘到铜箔边缘的距离; 返回 (距离mm, 是否落在闭合铜皮内部)。"""
    outline = pad_outline(_as_metric_pad(pad))
    pts = item["pts"]
    if item["kind"] == "flash":
        cx, cy = pts[0]
        dist = math.hypot(pad["x"] - cx, pad["y"] - cy) - item["half_mm"]
        return dist - max(pad["w"], pad["h"]) / 2.0, False
    if outline["kind"] == "circle":
        c, r = outline["c"], outline["r"]
        if item.get("closed_pour") and _point_in_poly(c, pts):
            return -r, True
        if len(pts) == 1:
            d = math.hypot(c[0] - pts[0][0], c[1] - pts[0][1])
        else:
            d = min(_seg_point_dist(pts[i], pts[i + 1], c) for i in range(len(pts) - 1))
        return d - r - float(item.get("half_mm") or 0.0), False
    poly = outline["poly"]
    if len(pts) == 1:
        cx, cy = pts[0]
        d = min(math.hypot(p[0] - cx, p[1] - cy) for p in poly)
    else:
        best = float("inf")
        for i in range(len(poly)):
            a1, a2 = poly[i], poly[(i + 1) % len(poly)]
            for j in range(len(pts) - 1):
                best = min(best, _seg_seg_dist(a1, a2, pts[j], pts[j + 1]))
        d = best
    if item.get("closed_pour") and any(_point_in_poly(p, pts) for p in poly):
        return -float(item.get("half_mm") or 0.0), True
    return d - float(item.get("half_mm") or 0.0), False


def _etch_candidates(by_layer: Dict[str, List[dict]], side: str) -> List[str]:
    keys = list(by_layer)
    if not side:
        return keys
    want = [k for k in keys if k.upper() == side or k.upper().endswith("_" + side)]
    return want or keys


def measure_pad_clearance(pads: List[dict], copper: dict,
                          search_mm: float = 5.0,
                          offset: Tuple[float, float] = (0.0, 0.0),
                          max_pads: int = 20000) -> dict:
    """逐个焊盘量到最近铜皮与最近走线的距离 (mm, 边缘到边缘)。"""
    if not copper.get("ok"):
        return {"ok": False, "reason": copper.get("reason", "没有铜箔几何"), "pads": []}
    cell = max(search_mm, 0.5)
    indexes = {layer: _grid_index(items, cell) for layer, items in copper["by_layer"].items()}
    dx, dy = offset
    out: List[dict] = []
    measured_pour = measured_trace = 0
    min_pour = min_trace = None
    min_trace_info = None
    min_pour_info = None

    for pad in pads[:max_pads]:
        p = dict(pad)
        p["x"] = pad["x"] + dx
        p["y"] = pad["y"] + dy
        box = _pad_bbox(p)
        side = (p.get("side") or side_of_layer(p.get("layer") or "")).upper()
        row = {"pad": p, "pour_mm": None, "trace_mm": None, "pour": None,
               "trace": None, "inside_pour": None, "touch": [], "searched": False}
        for layer in _etch_candidates(copper["by_layer"], side):
            items = copper["by_layer"][layer]
            grid = indexes.get(layer) or {}
            x0 = int(math.floor((box[0] - search_mm) / cell))
            y0 = int(math.floor((box[1] - search_mm) / cell))
            x1 = int(math.floor((box[2] + search_mm) / cell))
            y1 = int(math.floor((box[3] + search_mm) / cell))
            seen = set()
            for gx in range(x0, x1 + 1):
                for gy in range(y0, y1 + 1):
                    for idx in grid.get((gx, gy), []):
                        if idx in seen:
                            continue
                        seen.add(idx)
                        item = items[idx]
                        gap, inside = _clearance_to_item(p, item, box)
                        if gap > search_mm and not inside:
                            continue
                        row["searched"] = True
                        if inside:
                            # 焊盘落在铜皮轮廓内部: 通常是同网络的平面/覆铜,
                            # 此时"到铜皮的距离"没有意义, 单列说明
                            row["inside_pour"] = {
                                "layer": layer, "width_mm": item.get("width_mm"),
                                "note": item.get("note") or "闭合铜皮轮廓内部",
                            }
                            continue
                        if gap <= _TOUCH_TOL_MM:
                            # 相接/相交: 绝大多数是同一网络的连线/热焊盘, 不计入间距
                            row["touch"].append({
                                "layer": layer, "kind": item["kind"],
                                "gap_mm": round(gap, 4),
                                "pos": "%0.3f, %0.3f" % tuple(item["pts"][0]),
                            })
                            continue
                        bucket = "pour" if item["kind"] in ("pour", "flash") else "trace"
                        if bucket == "pour":
                            if row["pour_mm"] is None or gap < row["pour_mm"]:
                                row["pour_mm"] = round(gap, 4)
                                row["pour"] = {
                                    "layer": layer, "kind": item["kind"],
                                    "width_mm": item.get("width_mm") or item.get("diameter_mm"),
                                    "length_mm": item.get("length_mm"),
                                    "pos": "%0.3f, %0.3f" % tuple(item["pts"][0]),
                                }
                        else:
                            if row["trace_mm"] is None or gap < row["trace_mm"]:
                                row["trace_mm"] = round(gap, 4)
                                row["trace"] = {
                                    "layer": layer, "kind": item["kind"],
                                    "width_mm": item.get("width_mm"),
                                    "length_mm": item.get("length_mm"),
                                    "pos": "%0.3f, %0.3f" % tuple(item["pts"][0]),
                                }
        if row["pour_mm"] is not None:
            measured_pour += 1
            if min_pour is None or row["pour_mm"] < min_pour:
                min_pour, min_pour_info = row["pour_mm"], {"pad": p, "info": row["pour"]}
        if row["trace_mm"] is not None:
            measured_trace += 1
            if min_trace is None or row["trace_mm"] < min_trace:
                min_trace, min_trace_info = row["trace_mm"], {"pad": p, "info": row["trace"]}
        out.append(row)

    return {
        "ok": True, "pads": out, "search_mm": search_mm, "offset_mm": [dx, dy],
        "measured_pour": measured_pour, "measured_trace": measured_trace,
        "touching_pads": sum(1 for r in out if r["touch"]),
        "inside_pour_pads": sum(1 for r in out if r["inside_pour"]),
        "min_pour_mm": min_pour, "min_trace_mm": min_trace,
        "min_pour": min_pour_info, "min_trace": min_trace_info,
        "note": ("距离都是「焊盘边缘 -> 铜箔边缘」, 且只算**未相连**的铜箔: "
                 "相接/相交的铜箔已单列(通常是同网络连线); 快照只有铜皮轮廓, "
                 "没有填充面, 焊盘落在闭合铜皮轮廓内部时按「在铜皮内」标注, 不给数字"),
    }


def clearance_rows(groups: List[dict], clearance: dict,
                   limit: int = 400) -> Tuple[List[dict], dict]:
    """按器件汇总焊盘到铜皮/走线的距离, 返回 (明细行, 汇总)。"""
    if not clearance.get("ok"):
        return [], {"ok": False, "reason": clearance.get("reason", "")}
    by_pos = {}
    for row in clearance["pads"]:
        by_pos[(row["pad"].get("refdes", ""), round(row["pad"]["x"], 4),
                round(row["pad"]["y"], 4))] = row
    rows: List[dict] = []
    summary = {"ok": True, "components": 0, "min_pour_mm": None, "min_trace_mm": None,
               "worst_pour": None, "worst_trace": None, "inside_pour_pads": 0,
               "touching_pads": 0, "no_copper_in_range": 0,
               "search_mm": clearance.get("search_mm")}
    for group in groups:
        best_pour = best_trace = None
        pour_info = trace_info = None
        inside = touch = 0
        for pad in group["pads"]:
            key = (pad.get("refdes", ""), round(pad["x"] + clearance["offset_mm"][0], 4),
                   round(pad["y"] + clearance["offset_mm"][1], 4))
            row = by_pos.get(key)
            if row is None:
                row = by_pos.get((pad.get("refdes", ""), round(pad["x"], 4),
                                  round(pad["y"], 4)))
            if row is None:
                continue
            if row.get("inside_pour"):
                inside += 1
            touch += len(row.get("touch") or [])
            if row.get("pour_mm") is not None and (best_pour is None or row["pour_mm"] < best_pour):
                best_pour, pour_info = row["pour_mm"], row["pour"]
            if row.get("trace_mm") is not None and (best_trace is None or row["trace_mm"] < best_trace):
                best_trace, trace_info = row["trace_mm"], row["trace"]
        summary["components"] += 1
        summary["inside_pour_pads"] += inside
        summary["touching_pads"] += touch
        if best_pour is None and best_trace is None:
            summary["no_copper_in_range"] += 1
        if best_pour is not None and (summary["min_pour_mm"] is None
                                      or best_pour < summary["min_pour_mm"]):
            summary["min_pour_mm"] = best_pour
            summary["worst_pour"] = {"component": group["name"], "info": pour_info}
        if best_trace is not None and (summary["min_trace_mm"] is None
                                       or best_trace < summary["min_trace_mm"]):
            summary["min_trace_mm"] = best_trace
            summary["worst_trace"] = {"component": group["name"], "info": trace_info}
        if len(rows) < limit:
            rows.append({
                "器件": group["name"],
                "焊盘数": len(group["pads"]),
                "到铜皮mm": (best_pour if best_pour is not None
                             else ">%g" % clearance["search_mm"]),
                "铜皮层": (pour_info or {}).get("layer", ""),
                "铜皮线宽mm": (pour_info or {}).get("width_mm"),
                "到走线mm": (best_trace if best_trace is not None
                             else ">%g" % clearance["search_mm"]),
                "走线层": (trace_info or {}).get("layer", ""),
                "走线宽mm": (trace_info or {}).get("width_mm"),
                "走线长mm": (trace_info or {}).get("length_mm"),
                "在铜皮内焊盘": inside or "",
                "相接铜箔数": touch or "",
                "数据来源": group["source"],
            })
    return rows, summary


# --------------------------------------------------------------------------
# 坐标系对齐
# --------------------------------------------------------------------------
def _draw_extent_mm(quickview: Optional[dict]):
    if not quickview or not quickview.get("found"):
        return None
    try:
        factor = 0.0254 if str(quickview.get("unit") or "").startswith("mil") else 1.0
        return (float(quickview["minx"]) * factor, float(quickview["miny"]) * factor,
                float(quickview["maxx"]) * factor, float(quickview["maxy"]) * factor)
    except (KeyError, TypeError, ValueError):
        return None


def align_offset(doc: Optional[dict], scale_mm_per_unit: float,
                 quickview: Optional[dict], pads: List[dict],
                 tolerance_mm: float = 2.0) -> dict:
    """把快照坐标换算到导出坐标 (设计 mm) 需要的平移量, 并校验。

    只有"两套坐标都在用"时才需要: 焊盘来自导出文件 (设计 mm) 而铜箔来自快照
    (设计单位 × scale)。做法: 用 .brd 的 Draw Size 原点 (MINX/MINY) 对齐快照
    板框左下角, 然后检查焊盘是否都落在图纸范围内 —— 落不进去就报"对不上",
    不硬算距离。
    """
    result = {"ok": True, "offset_mm": [0.0, 0.0], "method": "same_coords",
              "verified": None, "reason": ""}
    if not pads:
        return result
    extent = _draw_extent_mm(quickview)
    if not extent:
        result["method"] = "no_draw_size"
        result["reason"] = "BRD 里没有 Draw Size (MINX/MINY), 无法核对快照与导出坐标的原点"
        return result

    snap_box = None
    if doc:
        try:
            from src.design_geometry import _entries, outline_segments, _bbox

            box = _bbox([p for seg in outline_segments(_entries(doc))
                         for p in (seg.get("points") or [])])
            if box:
                snap_box = tuple(v * scale_mm_per_unit for v in box)
        except Exception:  # noqa: BLE001
            snap_box = None
    if not snap_box:
        result["method"] = "no_outline"
        result["reason"] = "快照里没有板框, 无法做原点对齐"
        return result

    # 快照 bbox 与 Draw Size 都是"图纸范围", 左下角对齐即得平移量
    dx = extent[0] - snap_box[0]
    dy = extent[1] - snap_box[1]
    if abs(dx) > 1000 or abs(dy) > 1000:
        return {"ok": False, "offset_mm": [dx, dy], "method": "draw_size",
                "verified": False,
                "reason": f"按 Draw Size 对齐得到 {dx:.1f}, {dy:.1f}mm 的平移量, 明显不合理"}
    inside = 0
    for pad in pads:
        x, y = pad["x"] + dx, pad["y"] + dy
        if (extent[0] - tolerance_mm <= x <= extent[2] + tolerance_mm
                and extent[1] - tolerance_mm <= y <= extent[3] + tolerance_mm):
            inside += 1
    ratio = inside / max(1, len(pads))
    result.update({"offset_mm": [round(dx, 5), round(dy, 5)], "method": "draw_size",
                   "verified": ratio >= 0.99, "inside_ratio": round(ratio, 4),
                   "draw_extent_mm": [round(v, 3) for v in extent]})
    if ratio < 0.99:
        result["ok"] = False
        result["reason"] = (f"只有 {ratio:.0%} 的焊盘落在 .brd 的图纸范围里, 快照坐标与"
                            f"导出坐标可能不是同一原点; 已放弃量距离, 请人工核对")
    return result


# --------------------------------------------------------------------------
# 一站式
# --------------------------------------------------------------------------
def analyze(doc: Optional[dict] = None, scale_mm_per_unit: float = 0.0,
            inventory: Optional[dict] = None, quickview: Optional[dict] = None,
            search_mm: float = 5.0, cluster_gap_mm: float = 5.0,
            with_clearance: bool = True) -> dict:
    """焊盘尺寸/间距 + 焊盘到铜皮·走线距离, 一次算完。"""
    collected = collect_pads(doc, scale_mm_per_unit, inventory)
    result: dict = {
        "ok": bool(collected.get("ok")),
        "pads": collected,
        "components": [],
        "rows": [],
        "clearance": {},
        "clearance_rows": [],
        "sources": collected.get("sources") or [],
        "reasons": collected.get("reasons") or [],
        "cluster_gap_mm": cluster_gap_mm,
    }
    if not collected.get("ok"):
        result["reason"] = collected.get("reason")
        return result

    pads = collected["pads"]
    groups = group_components(pads, gap_mm=cluster_gap_mm)
    result["components"] = [{"name": g["name"], "refdes": g["refdes"],
                             "pad_count": len(g["pads"]), "source": g["source"],
                             "clustered": g["clustered"],
                             "bbox": _bbox_of([(p["x"], p["y"]) for p in g["pads"]])}
                            for g in groups]
    result["rows"] = component_rows(groups)
    result["grouped"] = len(groups)

    if not with_clearance:
        return result

    copper = copper_features(doc, scale_mm_per_unit)
    result["copper"] = {k: v for k, v in copper.items() if k != "by_layer"}
    if not copper.get("ok"):
        result["clearance"] = {"ok": False, "reason": copper.get("reason")}
        result["reasons"].append(f"铜箔几何: {copper.get('reason')}")
        return result

    # 焊盘来自导出 (设计 mm)、铜箔来自快照时, 先对齐坐标系
    from_export = any(p.get("kind") == "export" for p in pads)
    align = {"ok": True, "offset_mm": [0.0, 0.0], "method": "snapshot_only"}
    if from_export:
        align = align_offset(doc, scale_mm_per_unit, quickview, pads)
    result["align"] = align
    if not align.get("ok"):
        result["clearance"] = {"ok": False, "reason": align.get("reason")}
        result["reasons"].append(f"坐标对齐: {align.get('reason')}")
        return result

    clearance = measure_pad_clearance(pads, copper, search_mm=search_mm,
                                      offset=tuple(align.get("offset_mm") or (0.0, 0.0)))
    rows, summary = clearance_rows(groups, clearance)
    result["clearance"] = {k: v for k, v in clearance.items() if k != "pads"}
    result["clearance_rows"] = rows
    result["clearance_summary"] = summary
    return result

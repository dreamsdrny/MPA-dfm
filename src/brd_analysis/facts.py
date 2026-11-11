"""跨类别共用的派生事实: 网络、器件、过孔、钻孔、约束、叠层、板框折线。

把这些从各 cat_*.py 里抽出来, 是为了让"同一个量在 DRC、报告、装配、导出里
口径完全一致"。任何一项口径改动只改这里一处。
"""
from typing import Dict, List, Optional, Tuple

from src.brd_analysis import geom
from src.brd_analysis.context import Context


# --------------------------------------------------------------------------
# 网络
# --------------------------------------------------------------------------
def net_names(ctx: Context) -> List[str]:
    """网络名。优先从归档 objects/Net.xml 的节点名读 (可信); 读不到时返回空表
    —— 不用二进制文本正则去猜, 那会把器件名、层名也当成网络。"""
    return sorted(set(ctx.xml_nodes("objects/Net.xml")))


def diff_pair_names(ctx: Context) -> List[str]:
    """差分对名。优先 DiffPair.xml 节点名; 没有节点名时退回文本里的 DIFF_PAIR 记录。"""
    names = ctx.xml_nodes("objects/DiffPair.xml")
    if names:
        return sorted(set(names))
    got = []
    try:
        from src.pcb_reader import extract_brd_info

        info = extract_brd_info(ctx.brd_path)
        for item in info.get("diff_pairs") or []:
            if isinstance(item, dict) and item.get("name"):
                got.append(item["name"])
    except Exception:  # noqa: BLE001
        pass
    return sorted(set(got))


def power_ground_nets(names: List[str]) -> List[str]:
    import re

    pat = re.compile(r"^(GND|AGND|PGND|DGND|SGND|VCC|VDD|VIN|VBUS|[0-9]+V|V\d)",
                     re.I)
    return [n for n in names if pat.match(n)]


def diff_pair_guess(names: List[str]) -> List[Tuple[str, str]]:
    """没有 DiffPair.xml 时, 按 _P/_N 后缀配对, 只作提示用 (报告里注明是推断)。"""
    by_base: Dict[str, Dict[str, str]] = {}
    for name in names:
        for suffix, tag in (("_P", "p"), ("_N", "n"), ("+", "p"), ("-", "n")):
            if name.upper().endswith(suffix):
                base = name[: -len(suffix)]
                by_base.setdefault(base, {})[tag] = name
                break
    return [(v["p"], v["n"]) for v in by_base.values() if "p" in v and "n" in v]


# --------------------------------------------------------------------------
# 器件
# --------------------------------------------------------------------------
def components(ctx: Context) -> dict:
    comp = ctx.design.get("components") or {}
    if comp.get("ok"):
        return comp
    return {"ok": False, "count": 0, "refdes": [], "by_prefix": {}, "by_category": {},
            "reason": comp.get("reason", "BRD 无 objects/Part.xml")}


# --------------------------------------------------------------------------
# 过孔 / 钻孔
# --------------------------------------------------------------------------
def via_rows(ctx: Context) -> List[dict]:
    rows = []
    doc = ctx.design
    for p in doc.get("via_padstacks") or []:
        rows.append({
            "name": p.get("name", ""),
            "drill_mm": p.get("drill_mm"),
            "pad_mm": p.get("pad_mm"),
            "antipad_mm": p.get("antipad_mm"),
            "annular_mm": p.get("annular_ring_mm"),
            "kind": p.get("kind", "unknown"),
            "kind_reason": p.get("kind_reason", ""),
            "span": p.get("span"),
        })
    return rows


def hole_kind_label(kind: str) -> str:
    return {"through": "机械通孔", "blind": "盲/埋孔", "both": "通孔+盲埋共用",
            "unknown": "待确认"}.get(kind, kind or "?")


def drill_rows(ctx: Context) -> List[dict]:
    return list(ctx.design.get("drill_rows") or [])


def drill_by_size(ctx: Context) -> List[dict]:
    """按孔径聚合钻孔 (跨跨度合并), 给出每种孔径的数量与镀覆。"""
    agg: Dict[Tuple[float, str], dict] = {}
    for row in drill_rows(ctx):
        size = row.get("size_mm")
        if size is None:
            continue
        key = (round(float(size), 3), row.get("plating") or "PTH")
        entry = agg.setdefault(key, {"size_mm": round(float(size), 3),
                                     "plating": key[1], "qty": 0,
                                     "spans": set()})
        entry["qty"] += int(row.get("qty") or 0)
        if row.get("span"):
            entry["spans"].add(row["span"])
    out = []
    for entry in sorted(agg.values(), key=lambda e: (e["size_mm"], e["plating"])):
        out.append({"size_mm": entry["size_mm"], "plating": entry["plating"],
                    "qty": entry["qty"], "spans": " / ".join(sorted(entry["spans"]))})
    return out


# --------------------------------------------------------------------------
# 约束
# --------------------------------------------------------------------------
def constraint_rows(ctx: Context) -> List[dict]:
    """把 PhysicalCSet / SpacingCSet 摊平成一行一个约束, 便于报告与比对。"""
    rows: List[dict] = []
    for cset, entry in (ctx.design.get("physical_csets") or {}).items():
        for key, value in entry.items():
            if key == "via_list":
                rows.append({"类别": "Physical", "约束集": cset, "约束": "VIA_LIST",
                             "值": "、".join(value), "单位": ""})
            else:
                rows.append({"类别": "Physical", "约束集": cset, "约束": key,
                             "值": value, "单位": "mm"})
    for cset, entry in (ctx.design.get("spacing_csets") or {}).items():
        for key, value in entry.items():
            rows.append({"类别": "Spacing", "约束集": cset, "约束": key,
                         "值": value, "单位": "mm"})
    return rows


def design_values(ctx: Context) -> Dict[str, object]:
    """板级关键设计值 (取各约束集最严值), key 与 dfm_inputs 的口径一致。"""
    d = ctx.design
    return {
        "min_line_width_mm": d.get("min_line_width_mm"),
        "min_neck_width_mm": d.get("min_neck_width_mm"),
        "min_spacing_mm": d.get("min_spacing_mm"),
        "min_drill_mm": d.get("min_drill_mm"),
        "min_laser_via_mm": d.get("min_laser_via_mm"),
        "min_annular_ring_mm": d.get("min_annular_ring_mm"),
        "min_annular_ring_laser_mm": d.get("min_annular_ring_laser_mm"),
        "board_thickness_mm": d.get("board_thickness_mm"),
        "copper_oz": (d.get("raw_note") or {}).get("notes", {}).get("copper_oz"),
    }


# --------------------------------------------------------------------------
# 叠层 / 板框 / 丝印
# --------------------------------------------------------------------------
def stackup_rows(ctx: Context) -> List[dict]:
    """叠层表: 层号 + 层名 + 类型 + 是否有铜箔图形。"""
    layers = ctx.layers
    count = ctx.layer_count
    etch_layers = set(ctx.etch_layers)
    rows: List[dict] = []

    def kind_of(name: str) -> str:
        up = name.upper()
        if up == "TOP":
            return "外层信号"
        if up == "BOTTOM":
            return "外层信号"
        if any(k in up for k in ("GND", "PWR", "PLANE", "VCC")):
            return "内层平面"
        if any(k in up for k in ("SIG", "SIGNAL")):
            return "内层信号"
        return "未标注"

    if layers:
        for idx, name in enumerate(layers, start=1):
            rows.append({"层号": idx, "层名": name, "用途": kind_of(name),
                         "有铜箔图形": "是" if name in etch_layers else "—",
                         "来源": "BRD SubClass.xml / 二进制层名"})
    elif count:
        for idx in range(1, int(count) + 1):
            name = "TOP" if idx == 1 else ("BOTTOM" if idx == int(count) else f"L{idx}")
            rows.append({"层号": idx, "层名": name, "用途": kind_of(name),
                         "有铜箔图形": "是" if name in etch_layers else "—",
                         "来源": "层数推算 (无层名)"})
    return rows


def outline_polylines(ctx: Context) -> List[List[Tuple[float, float]]]:
    """板框折线 (mm)。复用 design_geometry 的"取最外一圈"算法。"""
    if not ctx.has_snapshot or not ctx.has_scale:
        return []
    try:
        from src.design_geometry import board_edge_lines

        scale = ctx.scale_mm_per_unit
        lines = board_edge_lines(ctx.raw_entries)
        return [[(round(x * scale, 5), round(y * scale, 5)) for x, y in line]
                for line in lines if len(line) >= 2]
    except Exception:  # noqa: BLE001
        return []


def region_areas(ctx: Context) -> dict:
    """板框面积 (外接矩形面积 + 多边形面积估算)。"""
    lines = outline_polylines(ctx)
    if not lines:
        return {"ok": False, "reason": "没有可用的板框折线"}
    xs = [p[0] for line in lines for p in line]
    ys = [p[1] for line in lines for p in line]
    if not xs or not ys:
        return {"ok": False, "reason": "板框几何为空"}
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    # 多边形面积 (鞋带公式) 只对闭合的那圈有意义, 取面积最大的折线
    best_area = 0.0
    for line in lines:
        if len(line) < 3:
            continue
        area = 0.0
        for i in range(len(line) - 1):
            x1, y1 = line[i]
            x2, y2 = line[i + 1]
            area += x1 * y2 - x2 * y1
        area = abs(area) / 2.0
        best_area = max(best_area, area)
    return {"ok": True, "bbox_mm": [round(min(xs), 3), round(min(ys), 3),
                                    round(max(xs), 3), round(max(ys), 3)],
            "width_mm": round(w, 3), "height_mm": round(h, 3),
            "bbox_area_mm2": round(w * h, 2),
            "polygon_area_mm2": round(best_area, 2),
            "outline_count": len(lines)}


def copper_stats(ctx: Context) -> List[dict]:
    """每层铜箔统计: 线段数 / 折线长度 / 线宽分布 / 面积占比估算。"""
    area = region_areas(ctx)
    board_area = area.get("bbox_area_mm2") if area.get("ok") else None
    rows = []
    for layer, segs in sorted(ctx.segments_by_layer(["etch"]).items()):
        ws = geom.width_summary(segs)
        length = geom.total_length_mm(segs)
        avg_w = (sum(s["width_mm"] for s in segs if s["has_width"])
                 / max(1, sum(1 for s in segs if s["has_width"]))) if ws["has_widths"] else None
        copper_area = round(length * (avg_w or 0.0), 2) if avg_w else None
        rows.append({
            "层": layer,
            "图形数": len(segs),
            "走线总长mm": length,
            "最小线宽mm": ws.get("min_mm"),
            "中位线宽mm": ws.get("median_mm"),
            "最大线宽mm": ws.get("max_mm"),
            "铜面积估算mm2": copper_area,
            "铜占比": (round(copper_area / board_area * 100, 1)
                     if copper_area and board_area else None),
        })
    return rows


def has_any_geometry(ctx: Context) -> bool:
    return bool(ctx.segments()) if (ctx.has_snapshot and ctx.has_scale) else False


def geometry_warning(ctx: Context) -> Optional[str]:
    """几何类能力统一的前置提示: 标定不可信时必须说清楚。"""
    if not ctx.has_snapshot:
        return "该 .brd 没有可解析的内嵌设计快照, 几何类分析无法进行"
    if not ctx.has_scale:
        return "板框标定失败 (快照坐标无法换算成 mm), 几何类分析无法进行"
    if not ctx.scale_confident:
        return ("快照坐标是用经验系数换算的 (未取到 .brd 的 Draw Size), "
                "尺寸类结论需人工核对")
    return None

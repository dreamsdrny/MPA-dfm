"""302-report lessons: mechanical/assembly geometry with explicit identities."""
import math
from collections import defaultdict

from shapely.geometry import Point, box
from shapely.ops import nearest_points
from shapely.strtree import STRtree

CHECKS = {
    "pth_to_opposite_smd": ("PTH焊接盘到反面SMT盘", "明确PTH元件/跨度/焊接面、SMT身份、铜盘和 pth_to_opposite_smd_mm"),
    "mounting_clearance": ("螺丝孔到器件净空", "明确螺丝孔用途/真实孔壁或螺钉头净空区、本体及 mounting_clearance_mm"),
    "heel_length": ("实体引脚后跟余量", "确认实体引脚/铜盘/前趾轴向和 heel_min_mm；不按CAD原点猜本体"),
    "pad_copper_thermal": ("SMD焊盘周围铜连接/热不平衡", "同面最终铜、完整焊盘配对、明确 thermal_halo_mm 与 thermal_copper_ratio_max；仅热风险复核"),
    "panel_support_rib": ("拼板支撑肋", "明确支撑肋轮廓或已确认长度/宽度、量测口径和 panel_rib_min_mm"),
    "rigid_flex_separation": ("软硬结合板分板设备/工艺", "明确板型与Punch/激光等工艺适用性、已确认设备/产线评审"),
}
NUMERIC_RULES = {"pth_to_opposite_smd_mm", "mounting_clearance_mm", "heel_min_mm", "thermal_halo_mm",
                 "thermal_copper_ratio_max", "thermal_imbalance_max", "panel_rib_min_mm"}


def scan_mechanical(data, films, rules, emit, consume, checked, missing, poly, tolerance):
    """Report distances by the stated physical boundary; never infer screw purpose."""
    dnp = {row["refdes"].upper() for row in data.get("dnp_components", [])}
    components = {row["refdes"].upper(): row for row in data.get("components", [])
                  if row.get("refdes") and row["refdes"].upper() not in dnp}
    if any(c.get("pth") is True and c.get("smd") is True for c in components.values()):
        raise ValueError("器件SMD/PTH身份冲突，不能执行对应机械装配检查")
    pads = defaultdict(list)
    for obj in data.get("pads", []):
        if obj.get("confirmed") is True and obj.get("outline") and obj.get("side") in ("TOP", "BOTTOM"):
            pads[(str(obj.get("refdes", "")).upper(), obj["side"])].append((obj, poly(obj)))

    def minimum(key, first, second, rule_key, reason, hard=True, **attrs):
        limit = rules.get(rule_key)
        if limit is None:
            missing[key] += 1
            return
        checked[key] += 1
        actual = first.distance(second)
        if actual < limit + tolerance:
            p, q = nearest_points(first, second)
            emit(key, reason, Point((p.x+q.x)/2, (p.y+q.y)/2),
                 "fail" if hard and actual < limit-tolerance else "review", round(actual, 6), limit, **attrs)

    # Compare the PTH pads on the actual opposite assembly/solder side to SMD
    # pads on that SAME copper side. Body projection != toeprint spacing.
    for ref, comp in components.items():
        if comp.get("pth") is not True:
            continue
        side = comp.get("side")
        if side not in ("TOP", "BOTTOM"):
            missing["pth_to_opposite_smd"] += 1
            continue
        opposite = "BOTTOM" if side == "TOP" else "TOP"
        anchors = pads[(ref, opposite)]
        if not anchors:
            missing["pth_to_opposite_smd"] += 1
            continue
        for other_ref, other in components.items():
            if other_ref == ref or other.get("smd") is not True or other.get("side") != opposite:
                continue
            peers = pads[(other_ref, opposite)]
            if not peers:
                missing["pth_to_opposite_smd"] += 1
            for a_obj, a in anchors:
                for b_obj, b in peers:
                    if not consume():
                        return
                    minimum("pth_to_opposite_smd", a, b, "pth_to_opposite_smd_mm",
                            "PTH元件焊接面铜盘边到同面反面SMT铜盘边；不使用本体投影或中心距替代",
                            refdes=comp["refdes"], other_refdes=other["refdes"], pin=a_obj.get("pin"),
                            other_pin=b_obj.get("pin"), side=opposite)

    for hole in data.get("mounting_holes", []):
        if hole.get("mounting_confirmed") is not True:
            missing["mounting_clearance"] += 1
            continue
        if not str(hole.get("identity_source") or hole.get("source") or data.get("_source", "")).strip():
            missing["mounting_clearance"] += 1
            continue
        if hole.get("keepout_confirmed") is True and hole.get("keepout_outline"):
            boundary = poly(dict(outline=hole["keepout_outline"]))
            boundary_label = "已确认螺钉头/机械净空区外边缘"
        elif hole.get("diameter_mm") is not None:
            diameter = float(hole["diameter_mm"])
            if not math.isfinite(diameter) or diameter <= 0 or isinstance(hole["diameter_mm"], bool):
                raise ValueError("螺丝孔直径必须为有限正数")
            coords = [float(hole[k]) for k in ("x_mm", "y_mm")]
            if not all(math.isfinite(v) for v in coords):
                raise ValueError("螺丝孔坐标必须为有限数")
            boundary = Point(coords).buffer(diameter/2)
            boundary_label = "真实孔壁边缘（不是螺钉头半径）"
        else:
            missing["mounting_clearance"] += 1
            continue
        sides = hole.get("assembly_sides")
        if not isinstance(sides, list) or not sides or any(s not in ("TOP", "BOTTOM") for s in sides):
            missing["mounting_clearance"] += 1
            continue
        hole_ref = str(hole.get("refdes") or hole.get("id", ""))
        for ref, comp in components.items():
            if ref == hole_ref.upper() or comp.get("side") not in sides:
                continue
            if not comp.get("outline") or comp.get("boundary") not in ("body", "place_bound"):
                missing["mounting_clearance"] += 1
                continue
            if not consume():
                return
            body = poly(comp)
            minimum("mounting_clearance", boundary, body, "mounting_clearance_mm",
                    boundary_label + "到器件" + ("本体" if comp["boundary"] == "body" else "占位边界；本体需确认"),
                    hard=comp["boundary"] == "body", refdes=hole_ref, other_refdes=comp["refdes"], side=comp["side"],
                    boundary_basis="hole_wall" if boundary_label.startswith("真实") else "confirmed_keepout")

    for ref, comp in components.items():
        leads = comp.get("physical_leads", [])
        if not leads:
            missing["heel_length"] += 1
        for lead in leads:
            if not consume():
                return
            if lead.get("no_solder") is True:
                continue
            direction = lead.get("toe_direction")
            limit = rules.get("heel_min_mm")
            matching = [shape for obj, shape in pads[(ref, comp.get("side"))]
                        if str(obj.get("pin")) == str(lead.get("pin"))]
            if lead.get("confirmed") is not True or not lead.get("outline") or direction is None or limit is None or len(matching) != 1:
                missing["heel_length"] += 1
                continue
            if not isinstance(direction, (list, tuple)) or len(direction) != 2 or any(isinstance(v, bool) for v in direction):
                raise ValueError("后跟量测需要有效 toe_direction 二维轴向")
            dx, dy = float(direction[0]), float(direction[1])
            norm = math.hypot(dx, dy)
            if not math.isfinite(norm) or norm <= 0:
                raise ValueError("后跟量测轴向无效")
            pin, pad = poly(lead), matching[0]
            # toe points outward; heel is the inward/minimum projection end.
            actual = min(x*dx/norm+y*dy/norm for x, y in pin.exterior.coords) - min(x*dx/norm+y*dy/norm for x, y in pad.exterior.coords)
            checked["heel_length"] += 1
            if actual < limit+tolerance:
                emit("heel_length", "沿明确焊端轴向的后跟延伸量；不足/负值不是前趾检查", pin,
                     "fail" if actual < limit-tolerance and lead.get("no_solder") is False else "review",
                     round(actual, 6), limit, refdes=comp["refdes"], pin=lead.get("pin"), side=comp.get("side"))

    # Copper already covers a copper pad; intersection(pad,copper)/pad.area is
    # always ~100% and cannot mean thermal connection. Measure OUTSIDE the pad.
    for side in ("TOP", "BOTTOM"):
        copper = films.get("copper_" + side.lower())
        halo, maximum = rules.get("thermal_halo_mm"), rules.get("thermal_copper_ratio_max")
        if not copper or halo is None or halo <= 0 or maximum is None:
            missing["pad_copper_thermal"] += 1
            continue
        from src.domain.layout_geometry import polygons
        copper_parts = polygons(copper["geometry"])
        copper_index = STRtree(copper_parts)
        for ref, comp in components.items():
            if comp.get("smd") is not True or comp.get("side") != side:
                continue
            pad_rows = pads[(ref, side)]
            ratios = []
            for obj, shape in pad_rows:
                if not consume():
                    return
                from shapely.ops import unary_union
                x0, y0, x1, y1 = shape.bounds
                window = box(x0-halo-.004, y0-halo-.004, x1+halo+.004, y1+halo+.004)
                local_copper = unary_union([copper_parts[int(i)].intersection(window) for i in copper_index.query(window)])
                if not local_copper.buffer(.002).covers(shape):
                    missing["pad_copper_thermal"] += 1
                    continue
                ring = shape.buffer(halo).difference(shape)
                ratio = ring.intersection(local_copper).area/ring.area if ring.area else 0
                ratios.append((obj, shape, ratio))
                checked["pad_copper_thermal"] += 1
                if ratio > maximum:
                    emit("pad_copper_thermal", "实际焊盘外邻域铜占比偏高；热连接风险候选，不是阻焊覆盖率/热仿真", shape,
                         actual=round(ratio, 6), limit=maximum, refdes=comp["refdes"], pin=obj.get("pin"), side=side,
                         unit_override="ratio", measurement_basis="outside_pad_halo", halo_mm=halo)
            imbalance = rules.get("thermal_imbalance_max")
            if comp.get("thermal_pair_confirmed") is True and len({str(row[0].get("pin")) for row in ratios}) == 2 and len(ratios) == 2 and imbalance is not None:
                diff = abs(ratios[0][2]-ratios[1][2])
                checked["pad_copper_thermal"] += 1
                if diff > imbalance:
                    emit("pad_copper_thermal", "已确认两焊端外邻域铜占比不平衡；墓碑/温差风险需工艺验证", ratios[0][1],
                         actual=round(diff, 6), limit=imbalance, refdes=comp["refdes"], side=side, unit_override="ratio")

    for rib in data.get("panel_support_ribs", []):
        if not consume():
            return
        limit = rules.get("panel_rib_min_mm")
        if rib.get("confirmed") is not True or rib.get("dimension_basis") not in ("length", "width") or rib.get("measured_mm") is None or limit is None:
            missing["panel_support_rib"] += 1
            continue
        value = float(rib["measured_mm"])
        if isinstance(rib["measured_mm"], bool) or not math.isfinite(value) or value <= 0 or not rib.get("source"):
            raise ValueError("支撑肋需要确认来源及有限正尺寸")
        checked["panel_support_rib"] += 1
        shape = poly(rib) if rib.get("outline") else None
        if value < limit+tolerance:
            emit("panel_support_rib", "拼板支撑肋已确认" + rib["dimension_basis"] + "不足；不能将长度和宽度互换", shape,
                 "fail" if value < limit-tolerance else "review", value, limit, refdes=rib.get("id", ""),
                 dimension_basis=rib["dimension_basis"], evidence_source=rib["source"])
    if data.get("board_type_confirmed") is True and data.get("board_type") in ("rigid_flex", "flex"):
        review = data.get("engineering_review", {}).get("rigid_flex_separation", {})
        if review.get("status") in ("pass", "not_applicable") and review.get("source") and review.get("equipment_confirmed") is True and review.get("method") in ("punch", "laser", "other_confirmed"):
            checked["rigid_flex_separation"] += 1
        elif review.get("status") == "fail" and review.get("source"):
            checked["rigid_flex_separation"] += 1
            emit("rigid_flex_separation", "已确认分板设备/工艺不满足该板；不得仅由软板层名猜设备", status="fail", evidence_source=review["source"])
        else:
            missing["rigid_flex_separation"] += 1
    elif data.get("board_type_confirmed") is True and data.get("board_type") == "rigid":
        checked["rigid_flex_separation"] += 1
    else:
        missing["rigid_flex_separation"] += 1

"""Additional assembly checks with explicit identities, process exceptions and scope.

The external DFM report is a checklist, never a source of silently selected limits.
"""
import math
from collections import defaultdict

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree
from src.domain.smt_mechanical import CHECKS as MECHANICAL_CHECKS, NUMERIC_RULES, scan_mechanical
from src.domain.smt_multireport import CHECKS as MULTIREPORT_CHECKS, NUMERIC_RULES as MULTIREPORT_RULES, OTHER_RULES, scan_multireport
from src.domain.smt_pad_integrity import CHECKS as PAD_CHECKS, NUMERIC_RULES as PAD_RULES, scan_pad_integrity
from src.domain.smt_report_extensions import CHECKS as EXT_CHECKS, NUMERIC_RULES as EXT_RULES, OTHER_RULES as EXT_OTHER, scan_report_extensions

TOLERANCE = .004
MAX_CANDIDATES = 200000
MAX_FINDINGS = 5000
CHECKS = {
    "via_in_pad": ("SMD焊盘内孔", "明确 SMD 铜盘、钻孔跨度和填孔/盖铜策略"),
    "smd_direct_connection": ("SMD焊盘直接互连", "同面铜盘、网络及真实走线；需要确认焊点/出线策略"),
    "pin_copper_coverage": ("引脚下铜盘覆盖", "实体引脚轮廓/原点、贴装面和已对齐铜层；原点检查非VPL本体证明"),
    "pin_count": ("CAD/实体模型引脚数量", "明确 expected_pin_count 与完整 CAD 引脚清单"),
    "pin_pitch": ("CAD引脚原点Pitch统计", "完整同器件引脚原点；不是器件数据手册焊端pitch认证"),
    "toe_length": ("引脚前趾余量", "实际引脚/铜盘轮廓、toe_direction 和 toe_max_mm"),
    "paste_outside_smd": ("焊膏非SMD开窗", "实际焊膏、完整 SMD 焊盘身份；排除允许开窗"),
    "paste_spacing": ("焊膏开窗间距", "同面焊膏和已确认 paste_spacing_mm"),
    "test_under_body": ("测试点被本体/泡棉遮挡", "明确测试点、本体/泡棉覆盖区及贴装面"),
    "test_to_silk": ("测试点到丝印", "同面测试点/丝印和 test_to_silk_mm"),
    "mark_like_pad": ("Mark相似焊盘干扰", "明确全局Mark和同面铜盘；mark_like_pad_mm及尺寸识别策略"),
    "mark_mask_clearance": ("Mark阻焊开窗余量", "明确Mark、同面阻焊与 mark_mask_clearance_mm"),
    "opposite_side_clearance": ("双面器件/焊盘治具避让", "明确波峰焊适用性、本体/另一面焊盘及 opposite_side_clearance_mm"),
    "reference_designator": ("器件丝印位号", "已确认贴装清单、丝印文字对象完整性"),
    "board_transport": ("单板传送宽度/内凹", "实际封闭板框、传送方向、board_width_min/max_mm和edge_gap_max_mm"),
    "laser_area": ("镭雕区域/白油", "明确镭雕区域和工艺白油身份；不能靠矩形图形猜用途"),
    "flow_direction": ("拼板流板方向", "工程师确认拼板/面别与流板标识记录"),
    "tooling_poka_yoke": ("定位孔防呆", "工程师确认孔清单/治具方向，不能以孔数量代替防呆"),
}
CHECKS.update(MECHANICAL_CHECKS)
CHECKS.update(MULTIREPORT_CHECKS)
CHECKS.update(PAD_CHECKS)
CHECKS.update(EXT_CHECKS)


def validate_rules(raw):
    if not isinstance(raw, dict):
        raise ValueError("assembly_rules 必须为对象")
    numeric = {"toe_max_mm", "paste_spacing_mm", "test_to_silk_mm", "mark_like_pad_mm",
               "mark_mask_clearance_mm", "opposite_side_clearance_mm", "board_width_min_mm", "board_width_max_mm", "edge_gap_max_mm"}
    numeric |= NUMERIC_RULES
    numeric |= MULTIREPORT_RULES
    numeric |= PAD_RULES
    numeric |= EXT_RULES
    if set(raw) - numeric - OTHER_RULES - EXT_OTHER - {"source", "revision", "via_in_pad_policy", "wave_solder_applicable", "transport_axis"}:
        raise ValueError("装配补充规则存在未知字段")
    rules = dict(raw)
    if rules.get("source") is not None and not isinstance(rules["source"], str):
        raise ValueError("确认来源 source 必须为字符串")
    for key in numeric & rules.keys():
        if rules[key] is None:
            continue
        if isinstance(rules[key], bool):
            raise ValueError(key + " 不能为布尔值")
        value = float(rules[key])
        if not math.isfinite(value) or value < 0:
            raise ValueError(key + " 必须为非负有限毫米值")
        rules[key] = value
    for key in ("thermal_copper_ratio_max", "thermal_imbalance_max", "pad_area_imbalance_max", "thermal_void_ratio_max"):
        if rules.get(key) is not None and rules[key] > 1:
            raise ValueError(key + " 必须为0..1比例，不能将百分数直接输入")
    if rules.get("panel_mark_count_min") is not None and (not rules["panel_mark_count_min"].is_integer() or rules["panel_mark_count_min"]<1):
        raise ValueError("panel_mark_count_min 必须为正整数")
    if any(rules.get(key) is not None for key in numeric) and not str(rules.get("source") or "").strip():
        raise ValueError("装配阈值必须填写确认来源 source")
    if rules.get("via_in_pad_policy") not in (None, "review", "forbidden", "filled_capped_only"):
        raise ValueError("via_in_pad_policy 不支持")
    if rules.get("via_in_pad_policy") in ("forbidden", "filled_capped_only") and not rules.get("source"):
        raise ValueError("焊盘内孔策略必须注明确认来源")
    if rules.get("wave_solder_applicable") not in (None, True, False):
        raise ValueError("wave_solder_applicable 必须为 true/false/null")
    if rules.get("transport_axis") not in (None, "X", "Y"):
        raise ValueError("transport_axis 必须为 X/Y/null")
    if rules.get("laser_no_via") is not None and not isinstance(rules["laser_no_via"], bool):
        raise ValueError("laser_no_via 必须为 true/false/null")
    if rules.get("laser_no_trace") is not None and not isinstance(rules["laser_no_trace"],bool):raise ValueError("laser_no_trace 必须为true/false/null")
    text=rules.get("expected_board_text")
    if text is not None and (not isinstance(text,list) or not text or any(not isinstance(t,str) or not t.strip() for t in text)):
        raise ValueError("expected_board_text 必须为非空字符串列表或null")
    if (text or rules.get("laser_no_via") is not None or rules.get("laser_no_trace") is not None) and not str(rules.get("source") or "").strip():
        raise ValueError("丝印/镭雕策略必须填写确认来源")
    for lower,upper in (("toe_min_mm","toe_max_mm"),("heel_min_mm","heel_max_mm")):
        if rules.get(lower) is not None and rules.get(upper) is not None and rules[lower]>rules[upper]:
            raise ValueError("焊端尺寸下限不能高于上限")
    lo, hi = rules.get("board_width_min_mm"), rules.get("board_width_max_mm")
    if lo is not None and hi is not None and lo > hi:
        raise ValueError("板宽下限不能高于上限")
    return rules


def scan_assembly(data, films, input_errors=()):
    rules = validate_rules(data.get("assembly_rules", {}))
    findings, coverage = [], []
    checked, missing = defaultdict(int), defaultdict(int)
    source = rules.get("source") or data.get("_source", "BRD原生对象")
    budget = 0
    budget_exhausted = False
    identities = {c["refdes"].upper(): c for c in data.get("components", []) if c.get("refdes")}
    dnp = {c["refdes"].upper() for c in data.get("dnp_components", [])}
    def emit(key, reason, shape=None, status="review", actual=None, limit=None, **attrs):
        if len(findings) >= MAX_FINDINGS:
            return
        unit=attrs.pop("unit_override","mm")
        row = dict(id="ASM-"+key, field=key, rule=key, label=CHECKS[key][0], reason=reason,
                   status=status, actual=actual, requirement=limit, unit=unit, severity="high" if status == "fail" else "medium",
                   source=source, advice="核对当前工艺及资料；不要将候选当成完整放行", **attrs)
        if shape is not None and not shape.is_empty:
            p = shape if isinstance(shape, Point) else shape.representative_point()
            row.update(x_mm=round(p.x,6), y_mm=round(p.y,6))
        findings.append(row)
    def consume():
        nonlocal budget, budget_exhausted
        budget += 1
        if budget > MAX_CANDIDATES:
            budget_exhausted = True
            return False
        return True
    def near_limit(key, shape, actual, limit_key, reason, **attrs):
        limit = rules.get(limit_key)
        if limit is None:
            missing[key] += 1
            return
        checked[key] += 1
        if actual < limit + TOLERANCE:
            emit(key, reason, shape, "fail" if actual < limit-TOLERANCE else "review", round(actual,6), limit, **attrs)
    def poly(obj):
        if any(len(p)!=2 or any(isinstance(v,bool) or not math.isfinite(float(v)) for v in p) for p in obj["outline"]):
            raise ValueError("对象坐标必须为有限毫米数")
        shape = Polygon(obj["outline"])
        if shape.is_empty or not shape.is_valid or shape.area <= 0:
            raise ValueError("补充对象轮廓无效")
        return shape
    # Copper overlap and mask integrity precede review-only pitch statistics.
    scan_pad_integrity(data,films,rules,emit,consume,checked,missing,poly,TOLERANCE)
    if not budget_exhausted:scan_report_extensions(data,films,rules,emit,consume,checked,missing,poly,TOLERANCE)
    pads_by_side = defaultdict(list)
    for obj in data.get("pads", []):
        if obj.get("confirmed") is True and obj.get("side") in ("TOP","BOTTOM") and obj.get("outline"):
            pads_by_side[obj["side"]].append((obj,poly(obj)))
    holes = data.get("native_holes", [])
    pin_map = defaultdict(list)
    for pin in data.get("native_pins", []):
        pin_map[str(pin.get("refdes", "")).upper()].append(pin)
    traces = data.get("native_traces", [])
    for side, pads in pads_by_side.items():
        shapes = [shape for _, shape in pads]
        tree = STRtree(shapes)
        layer = 1 if side == "TOP" else data.get("layer_count")
        for hole in holes:
            if not consume(): break
            start, end = hole.get("start_layer"), hole.get("end_layer")
            names = data.get("layer_names", [])
            if start not in names or end not in names or layer is None:
                missing["via_in_pad"] += 1
                continue
            if not names.index(start)+1 <= layer <= names.index(end)+1:
                continue
            if hole.get("drill_mm") is None:
                missing["via_in_pad"] += 1
                continue
            hshape = Point(hole["x_mm"],hole["y_mm"]).buffer(hole["drill_mm"]/2)
            for index in tree.query(hshape,predicate="intersects"):
                if not consume(): break
                obj, shape = pads[int(index)]
                comp = identities.get(str(obj.get("refdes", "")).upper(), {})
                if comp.get("smd") is not True or str(obj.get("refdes", "")).upper() in dnp:
                    continue
                checked["via_in_pad"] += 1
                policy = comp.get("via_in_pad_policy") or rules.get("via_in_pad_policy", "review")
                if policy not in ("review","forbidden","filled_capped_only"):
                    raise ValueError("器件via-in-pad策略无效")
                if policy in ("forbidden","filled_capped_only") and not (comp.get("via_in_pad_policy_source") or rules.get("source")):
                    raise ValueError("器件via-in-pad硬性策略缺确认来源")
                treatment = hole.get("filled_capped") is True
                if policy == "filled_capped_only" and treatment:
                    continue
                emit("via_in_pad", "实际孔壁与SMD盘相交；填孔/盖铜和用途按明确策略核对", hshape.intersection(shape),
                     "fail" if policy == "forbidden" or policy == "filled_capped_only" and hole.get("filled_capped") is False else "review",
                     actual=hole["drill_mm"], refdes=obj.get("refdes", ""), side=side)
        paste = films.get("paste_"+side.lower())
        smd_pads = [(obj,shape) for obj,shape in pads if identities.get(str(obj.get("refdes", "")).upper(),{}).get("smd") is True]
        if paste:
            paste_parts = list(paste["geometry"].geoms) if hasattr(paste["geometry"],"geoms") else [paste["geometry"]]
            smd_union = unary_union([shape for _,shape in smd_pads])
            for opening in paste_parts:
                if opening.is_empty or not consume(): continue
                checked["paste_outside_smd"] += 1
                uncovered = opening.difference(smd_union.buffer(.002))
                if uncovered.area > .004**2:
                    emit("paste_outside_smd", "焊膏超出已确认SMD铜盘；可能为Mark/过孔/有意钢网扩展，需工艺复核", uncovered,
                         side=side, actual=round(uncovered.area,6), unit_override="mm2")
            limit=rules.get("paste_spacing_mm")
            if limit is not None:
                ptree=STRtree(paste_parts)
                for i,a in enumerate(paste_parts):
                    for j in ptree.query(a,predicate="dwithin",distance=limit+TOLERANCE):
                        j=int(j)
                        if j<=i: continue
                        if not consume(): break
                        p,q=nearest_points(a,paste_parts[j])
                        near_limit("paste_spacing",Point((p.x+q.x)/2,(p.y+q.y)/2),a.distance(paste_parts[j]),"paste_spacing_mm","独立焊膏开窗边到边距离",side=side)
            else: missing["paste_spacing"] += 1
        else:
            missing["paste_outside_smd"] += 1
            missing["paste_spacing"] += 1
        # Trace intersects two distinct same-net pads; always process review,
        # not an electrical short (same-net connection may be intentional).
        for trace in traces:
            if trace.get("layer", "").split("/")[-1] != ("TOP" if side=="TOP" else "BOTTOM") or not trace.get("points"):
                continue
            if not consume(): break
            line=LineString(trace["points"]).buffer(trace.get("width_mm",0)/2)
            hits=[pads[int(i)] for i in tree.query(line,predicate="intersects")]
            by_ref=defaultdict(list)
            for obj,shape in hits:
                comp=identities.get(str(obj.get("refdes","")).upper(),{})
                if comp.get("smd") is True and trace.get("net") and obj.get("net")==trace["net"]:
                    by_ref[obj["refdes"]].append((obj,shape))
            for ref,objects in by_ref.items():
                if len({obj.get("pin") for obj,_ in objects})>1:
                    checked["smd_direct_connection"] += 1
                    emit("smd_direct_connection","同网络走线直接相交多个SMD盘；仅出线工艺候选，不是短路证明",line,refdes=ref,side=side)
    # Physical pin origins alone cannot certify VPL footprint integrity.
    for ref,comp in identities.items():
        if ref in dnp: continue
        side=comp.get("side")
        observed={str(pin.get("pin")) for pin in pin_map[ref] if pin.get("pin")}
        pins=pin_map[ref]
        nearest=None
        points=[]
        for pin in pins:
            if not consume():break
            points.append(Point(pin["x_mm"],pin["y_mm"]))
        if len(points)>1:
            _,distances=STRtree(points).query_nearest(points,exclusive=True,return_distance=True)
            nearest=min((float(d) for d in distances if d>.000001),default=None)
        if nearest is not None:
            checked["pin_pitch"]+=1
            # A measurement/statistic is not automatically a capability violation.
            emit("pin_pitch","同器件CAD引脚原点最小中心距统计；需核对封装脚排/热焊盘等语义",Point(pins[0]["x_mm"],pins[0]["y_mm"]),
                 actual=round(nearest,6),refdes=ref,side=side)
        else:missing["pin_pitch"]+=1
        expected=comp.get("expected_pin_count")
        if expected is not None:
            if isinstance(expected,bool) or int(expected)!=float(expected) or int(expected)<1:
                raise ValueError("expected_pin_count 必须为正整数")
            if data.get("complete",{}).get("native_pins") is True:
                checked["pin_count"]+=1
                if len(observed)!=int(expected):
                    emit("pin_count","CAD引脚数与已确认实体模型不一致",Point(comp["x_mm"],comp["y_mm"]) if comp.get("x_mm") is not None else None,
                         "fail",len(observed),int(expected),refdes=ref,side=side)
            else: missing["pin_count"]+=1
        else: missing["pin_count"]+=1
        film=films.get("copper_"+str(side).lower())
        for pin in pin_map[ref] if comp.get("smd") is True else []:
            if not consume(): break
            if not film:
                missing["pin_copper_coverage"]+=1
                continue
            p=Point(pin["x_mm"],pin["y_mm"])
            checked["pin_copper_coverage"]+=1
            if not film["geometry"].buffer(.002).covers(p):
                emit("pin_copper_coverage","SMD引脚原点未落在实际铜上；非实体引脚全面积/VPL证明",p,refdes=ref,pin=pin.get("pin"),side=side)
        for lead in comp.get("physical_leads", []):
            if lead.get("no_solder") is True:
                continue
            if lead.get("confirmed") is True and lead.get("outline") and film:
                lead_shape=poly(lead)
                checked["pin_copper_coverage"]+=1
                if lead_shape.intersection(film["geometry"]).area <= .002**2:
                    emit("pin_copper_coverage","已确认实体引脚轮廓下没有铜盘；仍需排除明确不焊引脚",lead_shape,
                         "review" if lead.get("no_solder") is not False else "fail",refdes=ref,pin=lead.get("pin"),side=side)
            if lead.get("confirmed") is not True or not lead.get("outline") or not lead.get("toe_direction"):
                missing["toe_length"]+=1;continue
            direction=lead["toe_direction"]
            dx,dy=float(direction[0]),float(direction[1]); norm=math.hypot(dx,dy)
            if not math.isfinite(norm) or norm<=0: raise ValueError("toe_direction 无效")
            matching=[shape for obj,shape in pads_by_side.get(side,[]) if str(obj.get("refdes","")).upper()==ref and str(obj.get("pin"))==str(lead.get("pin"))]
            limit=rules.get("toe_max_mm")
            if len(matching)!=1 or limit is None:
                missing["toe_length"]+=1;continue
            shape=poly(lead);pad=matching[0]
            actual=max(x*dx/norm+y*dy/norm for x,y in pad.exterior.coords)-max(x*dx/norm+y*dy/norm for x,y in shape.exterior.coords)
            checked["toe_length"]+=1
            if actual>limit+TOLERANCE:
                emit("toe_length","沿已确认前趾方向的铜盘延伸量超上限",shape,"fail",round(actual,6),limit,refdes=ref,side=side)
    for test in data.get("test_pads", []):
        if test.get("confirmed") is not True or not test.get("outline"):
            missing["test_under_body"]+=1;continue
        shape=poly(test);side=test.get("side")
        for body in data.get("components", [])+data.get("foam_regions", []):
            if body.get("side")!=side or not body.get("outline"):continue
            if body.get("boundary")!="body" and body.get("confirmed") is not True:continue
            if not consume():break
            checked["test_under_body"]+=1
            overlap=shape.intersection(poly(body))
            if overlap.area>.004**2:
                emit("test_under_body","同面已确认本体/泡棉覆盖测试点，需治具/测试方向复核",overlap,refdes=test.get("refdes", ""),other_refdes=body.get("refdes") or body.get("id",""),side=side)
        silk=films.get("silkscreen_"+str(side).lower())
        if silk and not silk["geometry"].is_empty:
            near_limit("test_to_silk",shape,shape.distance(silk["geometry"]),"test_to_silk_mm","测试点铜边到丝印距离",refdes=test.get("refdes",""),side=side)
        else:missing["test_to_silk"]+=1
    for mark in data.get("marks", []):
        if mark.get("global_confirmed") is not True or mark.get("diameter_mm") is None:
            missing["mark_mask_clearance"]+=1;missing["mark_like_pad"]+=1;continue
        side=mark.get("side");point=Point(mark["x_mm"],mark["y_mm"])
        shape=point.buffer(mark["diameter_mm"]/2)
        mask=films.get("mask_"+str(side).lower())
        if mask:
            actual=shape.distance(mask["geometry"].boundary) if mask["geometry"].covers(shape) else 0
            near_limit("mark_mask_clearance",point,actual,"mark_mask_clearance_mm","Mark铜边到开窗边界余量",refdes=mark.get("id",""),side=side)
        else:missing["mark_mask_clearance"]+=1
        for obj,pad in pads_by_side.get(side,[]):
            if not consume():break
            if pad.buffer(.002).covers(point):continue
            if abs(pad.area-shape.area)>shape.area*.25:continue
            before=len(findings)
            near_limit("mark_like_pad",point,shape.distance(pad),"mark_like_pad_mm","Mark附近面积相似铜盘候选，形状识别需设备确认",refdes=mark.get("id",""),other_refdes=obj.get("refdes",""),side=side)
            if len(findings)>before:findings[-1]["status"]="review"
    if rules.get("wave_solder_applicable") is True:
        for comp in data.get("components",[]):
            if comp.get("boundary")!="body" or not comp.get("outline"):continue
            side="BOTTOM" if comp.get("side")=="TOP" else "TOP"
            for obj,pad in pads_by_side.get(side,[]):
                if not consume():break
                near_limit("opposite_side_clearance",poly(comp),poly(comp).distance(pad),"opposite_side_clearance_mm",
                           "波峰焊明确适用；本体投影到另一面焊盘距离",refdes=comp.get("refdes",""),other_refdes=obj.get("refdes",""),side=comp.get("side"))
    else:missing["opposite_side_clearance"]+=1
    # Explicit transport axis gives board width. Concave exterior gaps are
    # bounded projected runs, not a universal transportability certification.
    if data.get("board_outline") and rules.get("transport_axis"):
        outline=poly(dict(outline=data["board_outline"]))
        x0,y0,x1,y1=outline.bounds
        width=(y1-y0) if rules["transport_axis"]=="X" else (x1-x0)
        lo,hi=rules.get("board_width_min_mm"),rules.get("board_width_max_mm")
        if lo is not None or hi is not None:
            checked["board_transport"]+=1
            if lo is not None and width<lo or hi is not None and width>hi:
                emit("board_transport","按明确传送方向检查板宽上下限；单板/拼板需区分",outline,"fail",width,f"{lo}..{hi}")
        gap_limit=rules.get("edge_gap_max_mm")
        if gap_limit is not None:
            # Report-only projection candidates retain review semantics.
            coords=list(outline.exterior.coords)
            axis=0 if rules["transport_axis"]=="X" else 1
            perp=1-axis
            lo_p,hi_p=(y0,y1) if axis==0 else (x0,x1)
            for a,b in zip(coords,coords[1:]):
                if min(abs(a[perp]-lo_p),abs(a[perp]-hi_p))>.01 and min(abs(b[perp]-lo_p),abs(b[perp]-hi_p))>.01:
                    gap=abs(b[axis]-a[axis]);checked["board_transport"]+=1
                    if gap>gap_limit:
                        emit("board_transport","非最外侧板边的投影跨度超限；轨道承靠方向/轮廓分段需工程复核",LineString([a,b]),actual=gap,limit=gap_limit)
    else:missing["board_transport"]+=1
    for key in ("laser_area","flow_direction","tooling_poka_yoke"):
        record=data.get("engineering_review",{}).get(key)
        if not isinstance(record,dict) or not record.get("source") or record.get("status") not in ("pass","fail","review","not_applicable"):
            missing[key]+=1
        else:
            checked[key]+=1
            if record["status"] in ("fail","review"):
                emit(key,record.get("note", "工程评审记录"),status=record["status"],review_source=record["source"])
    silk_refs=data.get("silkscreen_refdes", {})
    if data.get("complete",{}).get("silkscreen_refdes") is True:
        for ref,comp in identities.items():
            checked["reference_designator"]+=1
            if ref not in {str(x).upper() for x in silk_refs.get(comp.get("side"), [])}:
                emit("reference_designator","贴装器件未找到同面明确丝印位号文字，需核对空间/客户规则",Point(comp["x_mm"],comp["y_mm"]) if comp.get("x_mm") is not None else None,refdes=ref,side=comp.get("side"))
    else:missing["reference_designator"]+=1
    scan_mechanical(data,films,rules,emit,consume,checked,missing,poly,TOLERANCE)
    if not budget_exhausted:
        scan_multireport(data,films,rules,emit,consume,checked,missing,poly,TOLERANCE)
    full=data.get("complete",{})
    for key,condition in (("physical_pin_mask",full.get("components") and full.get("physical_leads")),
                          ("laser_trace_test_clearance",full.get("laser_regions") and full.get("test_pads") and full.get("native_traces")),
                          ("mark_to_silkscreen",full.get("marks"))):
        if not condition:missing[key]+=1
    for key,condition in (("smd_pin_spacing",full.get("components") and full.get("pads_top") and full.get("pads_bottom")),
                          ("smd_mask_opening",full.get("components") and full.get("mask_openings")),
                          ("smd_mask_bridge",full.get("components") and full.get("mask_openings")),
                          ("mark_shield_visibility",full.get("marks") and full.get("shields")),
                          ("laser_frame_size",full.get("laser_regions")),
                          ("thermal_void_evidence",full.get("thermal_void_records"))):
        if not condition:missing[key]+=1
    for key,condition in (("via_in_pad",full.get("native_holes") and full.get("components")),
                          ("smd_direct_connection",full.get("native_traces") and full.get("components")),
                          ("pin_copper_coverage",full.get("native_pins") and full.get("components")),
                          ("paste_outside_smd",full.get("components") and full.get("pads_top") and full.get("pads_bottom")),
                          ("test_under_body",full.get("test_pads") and full.get("components") and full.get("foam_regions")),
                          ("test_to_silk",full.get("test_pads")),
                          ("mark_like_pad",full.get("marks") and full.get("pads_top") and full.get("pads_bottom"))):
        if not condition:missing[key]+=1
    for key,condition in (("lead_geometry",full.get("components") and full.get("physical_leads")),
                          ("pad_size_balance",full.get("components") and full.get("pads_top") and full.get("pads_bottom")),
                          ("component_shield",full.get("components") and full.get("shields")),
                          ("side_height",full.get("components")),
                          ("conveyor_clearance",full.get("components") and full.get("conveyed_edges")),
                          ("test_mark",full.get("test_pads") and full.get("marks")),
                          ("test_pad_clearance",full.get("test_pads") and full.get("pads_top") and full.get("pads_bottom")),
                          ("via_pad_clearance",full.get("via_pads") and full.get("pads_top") and full.get("pads_bottom")),
                          ("assembly_pad_spacing",full.get("components") and full.get("pads_top") and full.get("pads_bottom")),
                          ("trace_pad_width",full.get("native_traces") and full.get("pads_top") and full.get("pads_bottom")),
                          ("laser_via",full.get("native_holes") and full.get("laser_regions")),
                          ("thermal_via_policy",full.get("native_holes") and full.get("thermal_pads")),
                          ("part_model",full.get("components") and full.get("part_models"))):
        if not condition:missing[key]+=1
    for key,condition in (("pth_to_opposite_smd",full.get("components") and full.get("pads_top") and full.get("pads_bottom")),
                          ("mounting_clearance",full.get("mounting_holes") and full.get("components")),
                          ("pad_copper_thermal",full.get("components") and full.get("pads_top") and full.get("pads_bottom")),
                          ("panel_support_rib",full.get("panel_support_ribs"))):
        if not condition:missing[key]+=1
    for key,(label,need) in CHECKS.items():
        if checked[key]==0:missing[key]+=1
        coverage.append(dict(check_key=key,label=label,phase="partial" if checked[key] and (missing[key] or budget_exhausted) else "checked" if checked[key] else "unavailable",
            verdict="fail" if any(f["field"]==key and f["status"]=="fail" for f in findings) else
            "within_scope" if key=="rigid_flex_separation" and checked[key] and not missing[key] and not budget_exhausted else
            "within_scope" if key in ("laser_area","flow_direction","tooling_poka_yoke","panel_corner_support") and checked[key] and not missing[key]
            and data.get("engineering_review",{}).get(key,{}).get("status") in ("pass","not_applicable") else "review",
            checked=checked[key],missing=missing[key],required_data=need,source=source,full_board_complete=False))
    errors=list(input_errors)
    if budget_exhausted or len(findings)>=MAX_FINDINGS:errors.append("装配补充检查预算耗尽，未完成，不放行")
    return dict(findings=findings,coverage=coverage,errors=errors,candidates=budget)

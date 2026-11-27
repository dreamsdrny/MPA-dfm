"""Nine-report lessons; threshold source and actual side/geometry are mandatory."""
import math
from collections import defaultdict

from shapely.geometry import LineString, Point
from shapely.ops import nearest_points
from shapely.strtree import STRtree

CHECKS = {
    "lead_geometry": ("前趾下限/后跟上限/引脚横向宽差", "实体引脚、匹配铜盘和toe轴向；toe_min_mm/heel_max_mm/lead_width_excess_max_mm"),
    "pad_size_balance": ("两焊端铜盘面积不一致", "已确认两端配对；pad_area_imbalance_max，差值定义为(max-min)/max"),
    "component_shield": ("器件到屏蔽罩避让", "明确屏蔽罩外形/焊接裙边及同面本体；component_shield_mm"),
    "side_height": ("分面器件高度/设备上限", "明确TOP/BOTTOM高度与component_height_top/bottom_max_mm"),
    "conveyor_clearance": ("分面器件到真实传送边", "已确认conveyed_edges线段、面别、工艺边；conveyor_clearance_top/bottom_mm"),
    "test_mark": ("测试点到Mark距离", "明确测试点/全局Mark，同面真实铜边；test_to_mark_mm，不混中心距"),
    "test_pad_clearance": ("测试点到同/异网器件焊盘", "已确认测试点和网络、同面铜盘；test_to_pad_same_net_mm/different_net_mm"),
    "via_pad_clearance": ("过孔铜盘到同/异网器件焊盘", "真实过孔铜盘（非孔径）/网络/面；via_to_pad_same_net_mm/different_net_mm"),
    "assembly_pad_spacing": ("不同器件SMD焊盘边缘间距", "同面完整SMD铜盘身份及assembly_pad_spacing_mm；不是本体距离"),
    "exposed_copper": ("裸露铜/过孔到其它裸铜", "真实铜与阻焊交集；exposed_copper_spacing_mm，仅几何候选，非短路认证"),
    "exposed_to_covered": ("裸铜到阻焊下其它铜", "实际铜连通块/阻焊开窗；exposed_to_covered_mm，排除同一铜块正常出线"),
    "trace_pad_width": ("铜线宽于焊盘横向宽度", "真实LINE/铜盘相交、网络/方向；trace_pad_width_excess_mm，圆弧需复核"),
    "silkscreen_revision": ("丝印板号/版本一致性", "完整丝印文字及有来源的expected_board_text，不能以文件名猜板号"),
    "laser_via": ("镭雕区内孔/过孔", "已确认镭雕区域/检查面和真实孔位，laser_no_via策略"),
    "thermal_via_policy": ("散热焊盘排气Via/填孔策略", "确认thermal_pads及当前元件工艺via_policy；不能将所有via-in-pad统一禁止"),
    "part_model": ("MPN实体模型/第一脚/封装匹配", "明确MPN绑定模型、CAD焊盘封装代码、pin1及实体引脚，不按MPN字符串猜尺寸"),
}
NUMERIC_RULES = {"toe_min_mm", "heel_max_mm", "lead_width_excess_max_mm", "pad_area_imbalance_max",
                 "component_shield_mm", "component_height_top_max_mm", "component_height_bottom_max_mm",
                 "conveyor_clearance_top_mm", "conveyor_clearance_bottom_mm", "test_to_mark_mm",
                 "test_to_pad_same_net_mm", "test_to_pad_different_net_mm", "via_to_pad_same_net_mm",
                 "via_to_pad_different_net_mm", "exposed_copper_spacing_mm", "exposed_to_covered_mm", "assembly_pad_spacing_mm", "trace_pad_width_excess_mm"}
OTHER_RULES = {"expected_board_text", "laser_no_via"}


def scan_multireport(data, films, rules, emit, consume, checked, missing, poly, tolerance):
    dnp = {r["refdes"].upper() for r in data.get("dnp_components", [])}
    comps = {c["refdes"].upper(): c for c in data.get("components", []) if c.get("refdes") and c["refdes"].upper() not in dnp}
    pads = defaultdict(list)
    for p in data.get("pads", []):
        if p.get("confirmed") is True and p.get("outline") and p.get("side") in ("TOP", "BOTTOM"):
            pads[p["side"]].append((p, poly(p)))
    assembly_pads = {side:[(p,s) for p,s in pads[side] if comps.get(str(p.get("refdes", "")).upper(), {}).get("smd") is True] for side in ("TOP","BOTTOM")}
    pad_indexes = {side:STRtree([s for _,s in rows]) for side,rows in assembly_pads.items()}
    def minimum(key, a, b, rule, reason, hard=True, **attrs):
        limit = rules.get(rule)
        if limit is None:
            missing[key] += 1
            return
        checked[key] += 1
        dist = a.distance(b)
        if dist < limit+tolerance:
            x,y = nearest_points(a,b)
            emit(key, reason, Point((x.x+y.x)/2,(x.y+y.y)/2), "fail" if hard and dist < limit-tolerance else "review",
                 round(dist,6),limit,**attrs)
    for ref,c in comps.items():
        side = c.get("side")
        own = [(p,s) for p,s in pads[side] if str(p.get("refdes", "")).upper()==ref]
        for lead in c.get("physical_leads", []):
            if not consume():return
            if lead.get("no_solder") is True:continue
            axis = lead.get("toe_direction")
            matching = [s for p,s in own if str(p.get("pin"))==str(lead.get("pin"))]
            if lead.get("confirmed") is not True or not lead.get("outline") or axis is None or len(matching)!=1:
                missing["lead_geometry"]+=1;continue
            if not isinstance(axis,(list,tuple)) or len(axis)!=2 or any(isinstance(v,bool) for v in axis):raise ValueError("实体焊端轴向无效")
            dx,dy=map(float,axis);norm=math.hypot(dx,dy)
            if not math.isfinite(norm) or norm<=0:raise ValueError("实体焊端轴向无效")
            dx,dy=dx/norm,dy/norm
            pin,pad=poly(lead),matching[0]
            pp=[x*dx+y*dy for x,y in pin.exterior.coords]; cp=[x*dx+y*dy for x,y in pad.exterior.coords]
            pin_w=[-x*dy+y*dx for x,y in pin.exterior.coords];pad_w=[-x*dy+y*dx for x,y in pad.exterior.coords]
            measurements=(("toe_min_mm",max(cp)-max(pp),"min","前趾铜延伸量低于确认下限"),
                          ("heel_max_mm",min(pp)-min(cp),"max","后跟铜延伸量高于确认上限"),
                          ("lead_width_excess_max_mm",max(pin_w)-min(pin_w)-(max(pad_w)-min(pad_w)),"max","实体引脚横向宽度超过铜盘宽度"))
            for key,value,direction,reason in measurements:
                limit=rules.get(key)
                if limit is None:missing["lead_geometry"]+=1;continue
                checked["lead_geometry"]+=1
                violation=value<limit+tolerance if direction=="min" else value>limit-tolerance
                if violation:
                    hard=value<limit-tolerance if direction=="min" else value>limit+tolerance
                    emit("lead_geometry",reason,pin,"fail" if hard and lead.get("no_solder") is False else "review",round(value,6),limit,
                         refdes=c["refdes"],pin=lead.get("pin"),side=side,measurement=key)
        if not c.get("physical_leads"):missing["lead_geometry"]+=1
        balance=rules.get("pad_area_imbalance_max")
        if c.get("thermal_pair_confirmed") is True and len(own)==2 and len({str(p.get('pin')) for p,_ in own})==2 and balance is not None:
            areas=[s.area for _,s in own];value=(max(areas)-min(areas))/max(areas)
            checked["pad_size_balance"]+=1
            if value>balance:emit("pad_size_balance","确认两焊端铜盘面积差/max面积；不代表铺铜热容差",own[0][1],actual=round(value,6),limit=balance,
                                  refdes=c["refdes"],side=side,unit_override="ratio")
        else:missing["pad_size_balance"]+=1
        height=c.get("height_mm");height_key="component_height_"+str(side).lower()+"_max_mm"
        height_limit=rules.get(height_key)
        if side in ("TOP","BOTTOM") and height is not None and height_limit is not None:
            value=float(height)
            if isinstance(height,bool) or not math.isfinite(value) or value<=0:raise ValueError("器件高度无效")
            checked["side_height"]+=1
            if value>height_limit+tolerance:emit("side_height","该贴装面高度超过已确认设备上限；不自动声称回流炉中央支撑已避让",
                poly(c) if c.get("outline") else Point(c["x_mm"],c["y_mm"]) if c.get("x_mm") is not None else None,
                "fail" if c.get("height_source") and not c["height_source"].startswith("BRD") else "review",value,height_limit,refdes=c["refdes"],side=side)
        else:missing["side_height"]+=1
        if c.get("outline") and c.get("boundary") in ("body","place_bound"):
            body=poly(c)
            for shield in data.get("shields", []):
                if not consume():return
                if shield.get("side")!=side:continue
                if shield.get("confirmed") is not True or not shield.get("outline"):
                    missing["component_shield"]+=1;continue
                if str(shield.get("refdes") or shield.get("id", "")).upper()==ref:continue
                minimum("component_shield",body,poly(shield),"component_shield_mm","同面器件到屏蔽罩轮廓；整罩投影非裙边需复核",hard=c["boundary"]=="body" and shield.get("boundary") in ("wall","skirt"),
                        refdes=c["refdes"],other_refdes=shield.get("refdes") or shield.get("id"),side=side)
            for edge in data.get("conveyed_edges", []):
                if not consume():return
                if side not in edge.get("assembly_sides", []):continue
                if edge.get("confirmed") is not True or not edge.get("source"):
                    missing["conveyor_clearance"]+=1;continue
                coords=edge.get("points")
                if not isinstance(coords,list) or len(coords)!=2 or any(len(p)!=2 or any(isinstance(v,bool) or not math.isfinite(float(v)) for v in p) for p in coords):
                    raise ValueError("传送边需要实际毫米二维线段，不推测包围盒边")
                line=LineString(coords)
                if line.length<=0:raise ValueError("传送边长度无效")
                minimum("conveyor_clearance",body,line,"conveyor_clearance_"+side.lower()+"_mm","器件到确认的实际传送/工艺边线段距离",
                        hard=c["boundary"]=="body",refdes=c["refdes"],side=side,evidence_source=edge["source"])
        else:missing["component_shield"]+=1;missing["conveyor_clearance"]+=1
        model=c.get("part_model", {})
        if model.get("confirmed") is True and model.get("source") and model.get("mpn") and c.get("manufacturer_part_number"):
            if str(model["mpn"]).upper()!=str(c["manufacturer_part_number"]).upper():raise ValueError("器件模型MPN不匹配，拒绝实体判定")
            expected=model.get("package_code");actual=c.get("cad_package_code")
            if expected and actual:
                checked["part_model"]+=1
                if str(expected).upper()!=str(actual).upper():emit("part_model","数据手册确认封装代码与明确CAD焊盘代码不符，不按MPN字符串推断",poly(c) if c.get("outline") else None,"fail",refdes=c["refdes"],side=side)
            else:missing["part_model"]+=1
            pin1=c.get("cad_pin1")
            if pin1 and model.get("pin1"):
                checked["part_model"]+=1
                if str(pin1)!=str(model["pin1"]):emit("part_model","明确实体模型第一脚与CAD映射第一脚不一致，需核对旋转/镜像",poly(c) if c.get("outline") else None,"fail",refdes=c["refdes"],side=side)
            else:missing["part_model"]+=1
        else:missing["part_model"]+=1
    for test in data.get("test_pads", []):
        if test.get("confirmed") is not True or not test.get("outline") or test.get("side") not in ("TOP","BOTTOM"):
            missing["test_mark"]+=1;missing["test_pad_clearance"]+=1;continue
        shape=poly(test);side=test.get("side")
        for mark in data.get("marks", []):
            if not consume():return
            if mark.get("side")!=side:continue
            if mark.get("global_confirmed") is not True or not mark.get("diameter_mm"):
                missing["test_mark"]+=1;continue
            diameter=mark["diameter_mm"]
            if isinstance(diameter,bool) or not math.isfinite(float(diameter)) or float(diameter)<=0:raise ValueError("Mark铜盘直径无效")
            mark_shape=Point(mark["x_mm"],mark["y_mm"]).buffer(float(diameter)/2)
            minimum("test_mark",shape,mark_shape,"test_to_mark_mm","同面测试点铜边到明确Mark铜边；不是中心距",
                    refdes=test.get("refdes", ""),other_refdes=mark.get("id", ""),side=side)
        for pad_obj,pad_shape in assembly_pads[side]:
            if not consume():return
            if pad_obj.get("refdes")==test.get("refdes") and str(pad_obj.get("pin"))==str(test.get("pin")):continue
            if not test.get("net") or not pad_obj.get("net"):
                missing["test_pad_clearance"]+=1;continue
            same=test["net"]==pad_obj["net"]
            minimum("test_pad_clearance",shape,pad_shape,"test_to_pad_same_net_mm" if same else "test_to_pad_different_net_mm",
                    "同面测试点到器件焊盘铜边；按明确网络分别采用阈值",refdes=test.get("refdes", ""),other_refdes=pad_obj.get("refdes", ""),side=side,net_relation="same" if same else "different")
    for via in data.get("via_pads", []):
        if via.get("confirmed") is not True or not via.get("outline") or via.get("side") not in ("TOP","BOTTOM"):
            missing["via_pad_clearance"]+=1;continue
        side=via["side"];vshape=poly(via)
        limits=[rules[k] for k in ("via_to_pad_same_net_mm","via_to_pad_different_net_mm") if rules.get(k) is not None]
        if not limits:missing["via_pad_clearance"]+=1;continue
        checked["via_pad_clearance"]+=1
        if len(limits)!=2:missing["via_pad_clearance"]+=1
        if not via.get("net"):missing["via_pad_clearance"]+=1;continue
        for i in pad_indexes[side].query(vshape,predicate="dwithin",distance=max(limits)+tolerance):
            obj,shape=assembly_pads[side][int(i)]
            if not consume():return
            if not via.get("net") or not obj.get("net"):
                missing["via_pad_clearance"]+=1;continue
            same=via["net"]==obj["net"]
            minimum("via_pad_clearance",vshape,shape,"via_to_pad_same_net_mm" if same else "via_to_pad_different_net_mm",
                    "过孔实际铜盘到器件焊盘；不是钻孔孔径间距",refdes=via.get("id", ""),other_refdes=obj.get("refdes", ""),side=side,net_relation="same" if same else "different")
    for side in ("TOP","BOTTOM"):
        limit=rules.get("assembly_pad_spacing_mm")
        if limit is not None:
            checked["assembly_pad_spacing"]+=1
            for i,(obj,a) in enumerate(assembly_pads[side]):
                for j in pad_indexes[side].query(a,predicate="dwithin",distance=limit+tolerance):
                    if not consume():return
                    j=int(j)
                    if j<=i:continue
                    other,b=assembly_pads[side][j]
                    if str(obj.get("refdes", "")).upper()==str(other.get("refdes", "")).upper():continue
                    minimum("assembly_pad_spacing",a,b,"assembly_pad_spacing_mm","不同器件同面已确认SMD铜盘边缘间距",refdes=obj.get("refdes", ""),other_refdes=other.get("refdes", ""),side=side)
        else:missing["assembly_pad_spacing"]+=1
        copper,mask=films.get("copper_"+side.lower()),films.get("mask_"+side.lower())
        limit=rules.get("exposed_copper_spacing_mm")
        if copper and mask and limit is not None:
            from src.domain.layout_geometry import polygons
            pieces=polygons(copper["geometry"].intersection(mask["geometry"]))
            checked["exposed_copper"]+=1
            tree=STRtree(pieces)
            for i,a in enumerate(pieces):
                for j in tree.query(a,predicate="dwithin",distance=limit+tolerance):
                    if not consume():return
                    j=int(j)
                    if j<=i:continue
                    minimum("exposed_copper",a,pieces[j],"exposed_copper_spacing_mm","实际裸铜连通块边缘距离；缺网络/net-tie语义，不声称短路",hard=False,side=side)
        else:missing["exposed_copper"]+=1
        limit=rules.get("exposed_to_covered_mm")
        if copper and mask and limit is not None:
            from src.domain.layout_geometry import polygons
            all_copper=polygons(copper["geometry"])
            all_index=STRtree(all_copper)
            checked["exposed_to_covered"]+=1
            for i,part in enumerate(all_copper):
                exposed=part.intersection(mask["geometry"])
                if exposed.is_empty:continue
                for j in all_index.query(exposed,predicate="dwithin",distance=limit+tolerance):
                    if not consume():return
                    j=int(j)
                    if i==j:continue  # Normal pad-to-trace continuity is not a violation.
                    covered=all_copper[j].difference(mask["geometry"])
                    if not covered.is_empty:
                        minimum("exposed_to_covered",exposed,covered,"exposed_to_covered_mm","裸铜到其它独立铜块阻焊覆盖部分；排除正常同铜块出线，无网络时仅候选",hard=False,side=side)
        else:missing["exposed_to_covered"]+=1
    for trace in data.get("native_traces", []):
        side="TOP" if trace.get("layer", "").endswith("/TOP") else "BOTTOM" if trace.get("layer", "").endswith("/BOTTOM") else ""
        if not side:continue
        if trace.get("kind")!="LINE" or not trace.get("endpoints_mm"):
            missing["trace_pad_width"]+=1;continue
        limit=rules.get("trace_pad_width_excess_mm")
        if limit is None:missing["trace_pad_width"]+=1;continue
        a,b=trace["endpoints_mm"];dx,dy=b[0]-a[0],b[1]-a[1];norm=math.hypot(dx,dy)
        if norm<=0:missing["trace_pad_width"]+=1;continue
        stroke=LineString([a,b]).buffer(trace["width_mm"]/2)
        for obj,shape in assembly_pads[side]:
            if not consume():return
            if not shape.intersects(stroke) or not trace.get("net") or obj.get("net")!=trace["net"]:continue
            widths=[(-x*dy+y*dx)/norm for x,y in shape.exterior.coords]
            value=trace["width_mm"]-(max(widths)-min(widths));checked["trace_pad_width"]+=1
            if value>limit+tolerance:emit("trace_pad_width","实际直线走线宽度大于铜盘沿该走线法向投影宽度，出线工艺风险",shape,actual=round(value,6),limit=limit,refdes=obj.get("refdes", ""),side=side)
    expected=rules.get("expected_board_text")
    if expected and data.get("complete", {}).get("silkscreen_text_records") is True:
        records=data.get("silkscreen_text_records", []);texts={str(r.get("text", "")).strip() for r in records}
        for token in expected:
            checked["silkscreen_revision"]+=1
            if token not in texts:emit("silkscreen_revision","完整丝印文字未找到明确板号/版本字符串；严格匹配，不猜文件名",status="review",expected_text=token)
    else:missing["silkscreen_revision"]+=1
    for region in data.get("laser_regions", []):
        if region.get("confirmed") is not True or not region.get("outline") or not region.get("source") or region.get("side") not in ("TOP","BOTTOM"):
            missing["laser_via"]+=1;continue
        area=poly(region)
        if rules.get("laser_no_via") is not True:missing["laser_via"]+=1;continue
        for hole in data.get("native_holes", []):
            if not consume():return
            names=data.get("layer_names",[]);start,end=hole.get("start_layer"),hole.get("end_layer")
            if start not in names or end not in names:missing["laser_via"]+=1;continue
            index=1 if region["side"]=="TOP" else len(names)
            if not names.index(start)+1<=index<=names.index(end)+1:continue
            checked["laser_via"]+=1
            h=Point(hole["x_mm"],hole["y_mm"])
            if hole.get("drill_mm"):h=h.buffer(hole["drill_mm"]/2)
            if area.intersects(h):emit("laser_via","确认镭雕区域与同跨度实际孔/过孔相交；不包含扫码等级/白油厚度认证",h,"fail",refdes=region.get("id", ""),side=region["side"])
    for target in data.get("thermal_pads", []):
        if target.get("confirmed") is not True or not target.get("outline") or not target.get("source") or target.get("side") not in ("TOP","BOTTOM"):
            missing["thermal_via_policy"]+=1;continue
        policy=target.get("via_policy")
        if policy not in ("required_open", "required_filled_capped", "forbidden"):
            missing["thermal_via_policy"]+=1;continue
        area=poly(target);hits=[];unknown=False
        if str(target.get("refdes", "")).upper() in dnp:continue
        for h in data.get("native_holes", []):
            if not consume():return
            if area.covers(Point(h["x_mm"],h["y_mm"])):
                if h.get("kind")!="VIA" or h.get("plated") is not True or h.get("drill_mm") is None:
                    unknown=True;continue
                names=data.get("layer_names",[]);start,end=h.get("start_layer"),h.get("end_layer")
                if start not in names or end not in names:unknown=True;continue
                idx=1 if target["side"]=="TOP" else len(names)
                if names.index(start)+1<=idx<=names.index(end)+1:hits.append(h)
        checked["thermal_via_policy"]+=1
        if unknown:missing["thermal_via_policy"]+=1
        if policy=="forbidden" and hits:emit("thermal_via_policy","该明确热盘工艺禁止内孔（不推广为所有器件通用禁止）",area,"fail",len(hits),0,refdes=target.get("refdes", ""),side=target["side"],unit_override="count")
        elif policy.startswith("required"):
            complete=data.get("complete",{}).get("native_holes") is True and not unknown
            if not hits:emit("thermal_via_policy","该明确热盘工艺需要排气/散热孔但未取得；不能将未知清单当无孔",area,"fail" if complete else "review",0,1,refdes=target.get("refdes", ""),side=target["side"],unit_override="count")
            for h in hits:
                allowed=h.get("filled_capped") is True if policy=="required_filled_capped" else h.get("filled_capped") is False
                if not allowed:emit("thermal_via_policy","热盘Via填孔/开孔策略未满足或未确认（历史报告与不同工艺可能相反）",area,"review" if h.get("filled_capped") is None else "fail",refdes=target.get("refdes", ""),side=target["side"])

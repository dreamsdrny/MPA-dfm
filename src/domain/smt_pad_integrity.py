"""R12 intra-package pins, native mask apertures and fiducial process scope."""
import math
from collections import defaultdict

from shapely.geometry import Point
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree

CHECKS={
    "smd_pin_spacing":("SMD Pin to SMD Pin Spacing（同器件不同脚）","同面真实铜盘及SMD身份；smd_pin_spacing_mm；不同器件沿用assembly_pad_spacing，重叠不猜默认门槛"),
    "smd_mask_opening":("SMD阻焊漏开/覆盖/扩窗","明确NSMD/SMD开窗策略；同面最终阻焊或逐盘原生开窗，smd_mask_margin_mm"),
    "smd_mask_bridge":("SMD焊盘开窗相连/阻焊桥","逐盘开窗或最终阻焊；smd_mask_bridge_mm，允许共窗需明确例外"),
    "mark_shield_visibility":("Mark被屏蔽罩遮挡/装配次序","明确Mark面别/罩轮廓及识别在罩装配前或后"),
    "panel_mark_layout":("Panel Mark数量/不对称/板边","明确panel板框/Mark属于panel，不用单板Mark代替"),
    "laser_frame_size":("二维码镭雕框尺寸","明确真实框轮廓和laser_frame_min_mm，不等同扫码等级"),
    "thermal_void_evidence":("热焊盘空洞率/X-ray证据","当前器件已确认X-ray测量、来源及thermal_void_ratio_max；设计有Via不证明空洞合格"),
}
NUMERIC_RULES={"smd_pin_spacing_mm","smd_mask_margin_mm","smd_mask_bridge_mm","panel_mark_count_min","panel_mark_edge_mm","laser_frame_min_mm","thermal_void_ratio_max"}


def scan_pad_integrity(data, films, rules, emit, consume, checked, missing, poly, tolerance):
    dnp={r["refdes"].upper() for r in data.get("dnp_components",[])}
    comps={c["refdes"].upper():c for c in data.get("components",[]) if c.get("refdes") and c["refdes"].upper() not in dnp}
    opening_map=defaultdict(list)
    for o in data.get("mask_openings",[]):
        if o.get("confirmed") is True and o.get("outline"):
            opening_map[(str(o.get("refdes","")).upper(),str(o.get("pin","")),o.get("side"))].append(poly(o))
    for side in ("TOP","BOTTOM"):
        rows=[];seen=set()
        for pad in data.get("pads",[]):
            ref=str(pad.get("refdes","")).upper()
            if pad.get("side")!=side or pad.get("confirmed") is not True or not pad.get("outline"):continue
            if comps.get(ref,{}).get("smd") is not True:continue
            shape=poly(pad);identity=(ref,str(pad.get("pin","")),shape.normalize().wkb)
            if identity in seen:continue
            seen.add(identity);rows.append((pad,shape))
        index=STRtree([s for _,s in rows]);limit=rules.get("smd_pin_spacing_mm")
        checked["smd_pin_spacing"]+=len(rows)
        if limit is None:missing["smd_pin_spacing"]+=1
        for i,(a,sa) in enumerate(rows):
            for j in index.query(sa,predicate="dwithin",distance=(limit or 0)+tolerance):
                if not consume():return
                j=int(j)
                if j<=i:continue
                b,sb=rows[j]
                if str(a.get("refdes","")).upper()!=str(b.get("refdes","")).upper():continue
                if (str(a.get("refdes","")).upper(),str(a.get("pin")))==(str(b.get("refdes","")).upper(),str(b.get("pin"))):continue
                overlap=sa.intersection(sb);actual=sa.distance(sb)
                if overlap.area<=.002**2 and (limit is None or actual>=limit+tolerance):continue
                exception=a.get("shared_copper_confirmed") is True and b.get("shared_copper_confirmed") is True and a.get("exception_source") and b.get("exception_source")
                if exception:continue
                p,q=nearest_points(sa,sb)
                shape=overlap if overlap.area>.002**2 else Point((p.x+q.x)/2,(p.y+q.y)/2)
                emit("smd_pin_spacing","SMD铜盘重叠（OVERLAP）；含同一器件不同脚，不能因同网默认忽略" if overlap.area>.002**2 else "同面SMD引脚铜盘边缘间距不足；含同器件不同脚",
                     shape,"fail" if overlap.area>.002**2 or limit is not None and actual<limit-tolerance else "review",
                     round(actual,6),limit,refdes=a["refdes"],pin=a.get("pin"),other_refdes=b["refdes"],other_pin=b.get("pin"),side=side,
                     overlap_area_mm2=round(overlap.area,8),net=a.get("net"),other_net=b.get("net"),measurement_basis="copper_pad_edge")
        final_mask=films.get("mask_"+side.lower())
        mask_parts=[];mask_index=None
        if final_mask:
            from src.domain.layout_geometry import polygons
            mask_parts=polygons(final_mask["geometry"]);mask_index=STRtree(mask_parts)
        assigned=[]
        for a,sa in rows:
            if not consume():return
            identity=(a["refdes"].upper(),str(a.get("pin","")),side)
            native=opening_map[identity]
            opening=unary_union(native) if native else None
            basis="native_padstack_opening"
            if final_mask:
                opening=unary_union([mask_parts[int(i)] for i in mask_index.query(sa,predicate="intersects") if mask_parts[int(i)].intersection(sa).area>.002**2])
                basis="final_mask_film"
            if opening is None:
                missing["smd_mask_opening"]+=1;continue  # Empty native list is not proof of no aperture.
            checked["smd_mask_opening"]+=1
            uncovered=sa.difference(opening.buffer(.002))
            policy=a.get("mask_policy")
            if uncovered.area>.002**2:
                if policy!="smd" or opening.intersection(sa).area<=.002**2:
                    emit("smd_mask_opening","SMD铜盘未取得完整开窗；NSMD完全开窗策略未知时只给复核，不将有意SMD覆盖硬判",
                         uncovered,"fail" if policy=="open" else "review",round(uncovered.area/sa.area,6),0,
                         refdes=a["refdes"],pin=a.get("pin"),side=side,unit_override="ratio",measurement_basis=basis)
            margin=rules.get("smd_mask_margin_mm")
            if margin is not None and policy=="open":
                actual=sa.distance(opening.boundary) if opening.covers(sa) else 0
                if actual<margin+tolerance:emit("smd_mask_opening","明确NSMD铜边到阻焊开窗边的扩窗余量不足",sa,
                    "fail" if actual<margin-tolerance else "review",round(actual,6),margin,refdes=a["refdes"],pin=a.get("pin"),side=side,measurement_basis=basis)
            # Use the actual per-pad native aperture; a connected final mask
            # block may extend far beyond this pad. Final connectivity below.
            assigned.append((a,sa,unary_union(native) if native and not final_mask else opening,basis))
        limit=rules.get("smd_mask_bridge_mm")
        if assigned:
            tree=STRtree([s for _,s,_,_ in assigned])
            aperture_tree=STRtree([o for _,_,o,_ in assigned])
            for i,(a,sa,oa,basis) in enumerate(assigned):
                query=tree.query(sa,predicate="dwithin",distance=max(.5,limit or 0)) if final_mask else aperture_tree.query(oa,predicate="dwithin",distance=(limit or 0)+tolerance)
                for j in query:
                    if not consume():return
                    j=int(j)
                    if j<=i:continue
                    b,sb,ob,_=assigned[j]
                    if oa.is_empty or ob.is_empty:continue
                    if (a["refdes"].upper(),str(a.get("pin")))==(b["refdes"].upper(),str(b.get("pin"))):continue
                    if a.get("shared_mask_confirmed") is True and b.get("shared_mask_confirmed") is True and a.get("exception_source") and b.get("exception_source"):continue
                    actual=oa.distance(ob);connected=oa.intersection(ob).area>.002**2
                    checked["smd_mask_bridge"]+=1
                    if connected or limit is not None and actual<limit+tolerance:
                        p,q=nearest_points(sa,sb)
                        emit("smd_mask_bridge","不同SMD引脚开窗相连/重叠；可能有意共窗，需确认，不能把间隙为零当阻焊桥合格" if connected else "两SMD盘阻焊开窗边间桥宽不足",
                             Point((p.x+q.x)/2,(p.y+q.y)/2),"review",round(actual,6),limit,refdes=a["refdes"],pin=a.get("pin"),other_refdes=b["refdes"],other_pin=b.get("pin"),side=side,measurement_basis=basis)
        else:missing["smd_mask_bridge"]+=1
        if limit is None:missing["smd_mask_bridge"]+=1
    for mark in data.get("marks",[]):
        if mark.get("global_confirmed") is not True:missing["mark_shield_visibility"]+=1;continue
        for shield in data.get("shields",[]):
            if not consume():return
            if shield.get("side")!=mark.get("side"):continue
            if shield.get("confirmed") is not True or not shield.get("outline"):missing["mark_shield_visibility"]+=1;continue
            point=Point(mark["x_mm"],mark["y_mm"])
            checked["mark_shield_visibility"]+=1
            if poly(shield).covers(point) and mark.get("recognition_stage")!="before_shield":
                emit("mark_shield_visibility","Mark位于同面屏蔽罩投影内，需确认装罩前/后识别及罩开口；缺罩图不能断言不可识别",point,"review",refdes=mark.get("id"),other_refdes=shield.get("refdes") or shield.get("id"),side=mark.get("side"))
    for region in data.get("laser_regions",[]):
        limit=rules.get("laser_frame_min_mm")
        if region.get("confirmed") is not True or not region.get("source") or not region.get("outline") or limit is None:missing["laser_frame_size"]+=1;continue
        rect=poly(region).minimum_rotated_rectangle
        if abs(rect.area-poly(region).area)>tolerance**2:missing["laser_frame_size"]+=1;continue
        coords=list(rect.exterior.coords);actual=min(math.dist(a,b) for a,b in zip(coords,coords[1:]))
        checked["laser_frame_size"]+=1
        if actual<limit-tolerance:emit("laser_frame_size","明确矩形镭雕框短边小于当前确认尺寸；6/8mm不得混用不同客户要求",rect,"fail",round(actual,6),limit,refdes=region.get("id"),side=region.get("side"))
    panel=data.get("panel_outline")
    marks=[m for m in data.get("marks",[]) if m.get("scope")=="panel" and m.get("global_confirmed") is True]
    if panel and data.get("panel_alignment_confirmed") is True and data.get("complete",{}).get("panel_marks") is True:
        outline=poly(dict(outline=panel))
        for side in data.get("assembly_sides",[]):
            current=[m for m in marks if m.get("side")==side];limit=rules.get("panel_mark_count_min")
            if limit is not None:
                checked["panel_mark_layout"]+=1
                if len(current)<limit:emit("panel_mark_layout","完整Panel资料中该面明确Panel Mark数量不足；单板Mark不计入",outline,"fail",len(current),limit,side=side,unit_override="count")
            edge_limit=rules.get("panel_mark_edge_mm")
            for m in current:
                p=Point(m["x_mm"],m["y_mm"]);actual=p.distance(outline.boundary) if outline.covers(p) else 0
                if edge_limit is not None:
                    checked["panel_mark_layout"]+=1
                    if actual<edge_limit-tolerance:emit("panel_mark_layout","Panel Mark中心到实际Panel板边距离不足",p,"fail",round(actual,6),edge_limit,refdes=m.get("id"),side=side)
            if len(current)==3:
                distances=sorted(math.dist((a["x_mm"],a["y_mm"]),(b["x_mm"],b["y_mm"])) for i,a in enumerate(current) for b in current[i+1:])
                checked["panel_mark_layout"]+=1
                if min(distances)<tolerance or min(abs(distances[1]-distances[0]),abs(distances[2]-distances[1]))<tolerance:
                    emit("panel_mark_layout","三Panel Mark重复或构成对称三角形，需设备识别/方向防呆复核",outline,side=side)
    else:missing["panel_mark_layout"]+=1
    limit=rules.get("thermal_void_ratio_max")
    for record in data.get("thermal_void_records",[]):
        value=record.get("void_ratio")
        if limit is None or record.get("confirmed") is not True or not record.get("source") or record.get("method")!="xray" or value is None:
            missing["thermal_void_evidence"]+=1;continue
        if isinstance(value,bool) or not math.isfinite(float(value)) or not 0<=float(value)<=1:raise ValueError("空洞率必须为0..1比例")
        checked["thermal_void_evidence"]+=1
        if float(value)>limit:emit("thermal_void_evidence","当前X-ray记录空洞率超过明确客户标准；Via设计不能替代此证据",status="fail",actual=float(value),limit=limit,refdes=record.get("refdes"),unit_override="ratio",evidence_source=record["source"])

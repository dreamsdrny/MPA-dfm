"""Seven reference report lessons; leads, masks and panel scope stay separate."""
import math
from shapely.geometry import LineString,Point
from shapely.ops import nearest_points,unary_union
from shapely.strtree import STRtree

CHECKS={
    "physical_pin_mask":("Pin touched soldermask（实体引脚）","实体引脚轮廓/no_solder及最终阻焊；铜盘开窗不代替引脚"),
    "laser_trace_test_clearance":("二维码框内走线/测试点避让","确认镭雕区、实际同面走线与测试点；test_to_laser_mm及laser_no_trace"),
    "mark_to_silkscreen":("Mark铜边到丝印净空","明确Mark/同面丝印，mark_to_silk_mm，不当阻焊扩窗"),
    "panel_corner_support":("Panel四角填补/进板支撑","明确panel_corner_support工程评审，不凭包围盒推断填板"),
}
NUMERIC_RULES={"test_to_laser_mm","mark_to_silk_mm"}
OTHER_RULES={"laser_no_trace"}


def scan_report_extensions(data,films,rules,emit,consume,checked,missing,poly,tolerance):
    dnp={r["refdes"].upper() for r in data.get("dnp_components",[])}
    for c in data.get("components",[]):
        if c.get("refdes","").upper() in dnp:continue
        mask=films.get("mask_"+str(c.get("side","")).lower())
        parts=[];tree=None
        if mask:
            from src.domain.layout_geometry import polygons
            parts=polygons(mask["geometry"]);tree=STRtree(parts)
        for lead in c.get("physical_leads",[]):
            if not consume():return
            if lead.get("no_solder") is True:continue
            if not mask or lead.get("confirmed") is not True or not lead.get("outline"):
                missing["physical_pin_mask"]+=1;continue
            shape=poly(lead);local=unary_union([parts[int(i)] for i in tree.query(shape)])
            uncovered=shape.difference(local.buffer(.002));checked["physical_pin_mask"]+=1
            if uncovered.area>.002**2:
                emit("physical_pin_mask","已确认实体焊端接触阻焊覆盖区；不是铜盘覆盖比例",uncovered,
                     "fail" if lead.get("no_solder") is False else "review",round(uncovered.area/shape.area,6),0,
                     refdes=c["refdes"],pin=lead.get("pin"),side=c.get("side"),unit_override="ratio")
    for region in data.get("laser_regions",[]):
        if region.get("confirmed") is not True or not region.get("outline") or not region.get("source"):
            missing["laser_trace_test_clearance"]+=1;continue
        area=poly(region);side=region.get("side")
        if side not in ("TOP","BOTTOM"):missing["laser_trace_test_clearance"]+=1;continue
        if rules.get("laser_no_trace") is True:
            for trace in data.get("native_traces",[]):
                if not consume():return
                if trace.get("layer","").split("/")[-1]!=side:continue
                if not trace.get("points") or not trace.get("width_mm"):
                    missing["laser_trace_test_clearance"]+=1;continue
                if isinstance(trace["width_mm"],bool) or not math.isfinite(float(trace["width_mm"])) or trace["width_mm"]<=0:raise ValueError("走线宽度无效")
                if any(len(p)!=2 or any(isinstance(v,bool) or not math.isfinite(float(v)) for v in p) for p in trace["points"]):raise ValueError("走线坐标无效")
                stroke=LineString(trace["points"]).buffer(trace["width_mm"]/2)
                checked["laser_trace_test_clearance"]+=1
                if area.intersects(stroke):emit("laser_trace_test_clearance","确认镭雕框与同面实际走线相交；未认证表面平整/扫码等级",area.intersection(stroke),"fail",refdes=region.get("id"),side=side)
        else:missing["laser_trace_test_clearance"]+=1
        limit=rules.get("test_to_laser_mm")
        for test in data.get("test_pads",[]):
            if not consume():return
            if test.get("side")!=side:continue
            if test.get("confirmed") is not True or not test.get("outline") or limit is None:
                missing["laser_trace_test_clearance"]+=1;continue
            shape=poly(test);distance=area.distance(shape);checked["laser_trace_test_clearance"]+=1
            if distance<limit+tolerance:
                p,q=nearest_points(shape,area)
                emit("laser_trace_test_clearance","测试点真实边缘到镭雕框距离不足，不混测试点中心距",Point((p.x+q.x)/2,(p.y+q.y)/2),
                     "fail" if distance<limit-tolerance else "review",round(distance,6),limit,refdes=test.get("refdes"),other_refdes=region.get("id"),side=side)
    limit=rules.get("mark_to_silk_mm")
    for mark in data.get("marks",[]):
        side=mark.get("side");silk=films.get("silkscreen_"+str(side).lower())
        if mark.get("global_confirmed") is not True or not mark.get("diameter_mm") or not silk or limit is None:
            missing["mark_to_silkscreen"]+=1;continue
        if not consume():return
        if isinstance(mark["diameter_mm"],bool) or not math.isfinite(float(mark["diameter_mm"])) or mark["diameter_mm"]<=0:raise ValueError("Mark直径无效")
        shape=Point(mark["x_mm"],mark["y_mm"]).buffer(mark["diameter_mm"]/2)
        actual=shape.distance(silk["geometry"]);checked["mark_to_silkscreen"]+=1
        if actual<limit+tolerance:emit("mark_to_silkscreen","Mark铜边到丝印边距离不足；绿油/铜清洁区需要独立检查",shape,
             "fail" if actual<limit-tolerance else "review",round(actual,6),limit,refdes=mark.get("id"),side=side)
    record=data.get("engineering_review",{}).get("panel_corner_support",{})
    if record.get("source") and record.get("status") in ("pass","fail","review","not_applicable"):
        checked["panel_corner_support"]+=1
        if record["status"] in ("fail","review"):emit("panel_corner_support",record.get("note") or "Panel四角填补/支撑工程评审",status=record["status"],review_source=record["source"])
    else:missing["panel_corner_support"]+=1

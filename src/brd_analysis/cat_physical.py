"""第 2 类: PCB 物理结构分析。

对应 PCB Editor 的: Board Geometry / Layer Stackup / Cross Section / Copper
Analysis / Via Structure / Shape Analysis / Component Placement。

这一类的特点是「一半能算, 一半要问」:
  * 板框、铜箔分布、过孔结构在 .brd 里拿得到 (二进制 + 内嵌快照), 直接出结论;
  * 叠层截面 (介质厚度/Er)、器件 XY/旋转角只存在于设计数据库, 没有导出文件时
    如实写"缺什么、怎么补", 不拿经验值冒充设计值。
"""
from typing import Dict, List, Optional

from src.brd_analysis import facts, geom
from src.brd_analysis.context import Context
from src.brd_analysis.model import (FAIL, INFO, PASS, UNAVAILABLE, WARN, Capability,
                                    Result, unavailable)

# 铜皮/平面的判定: 快照里线宽很大的图形基本是铜皮轮廓或平面
_SHAPE_WIDTH_MM = 0.5


def _count_closed_loops(segs: List[dict], tol: float = 0.02) -> int:
    """数一数这些图形里有多少个闭合环 (每个点恰好接两条边 = 一个环)。

    板框的闭合环: 最外一圈是外形, 多出来的环就是开槽/内孔。用端点吸附
    (0.02mm 容差) 建图, 只把"所有点度数都是 2"的连通片算作闭合环, 避免把
    零散线段也算成环。
    """
    def key(pt):
        return (round(pt[0] / tol), round(pt[1] / tol))

    adj: Dict[tuple, set] = {}
    for seg in segs:
        pts = seg["pts"]
        for i in range(len(pts) - 1):
            a, b = key(pts[i]), key(pts[i + 1])
            if a == b:
                continue
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)

    seen = set()
    loops = 0
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
            if len(adj.get(cur, ())) != 2:
                closed = False
            for nxt in adj.get(cur, ()):
                if nxt not in seen:
                    stack.append(nxt)
        if closed and len(comp) >= 3:
            loops += 1
    return loops


# --------------------------------------------------------------------------
# 2.1 Board Geometry
# --------------------------------------------------------------------------
def cap_board_geometry(ctx: Context) -> Result:
    cap = CATALOG_MAP["physical.board_geometry"]
    warn = facts.geometry_warning(ctx)
    area = facts.region_areas(ctx)
    if not area.get("ok"):
        return unavailable(cap, area.get("reason", "没有板框几何"),
                           "确保 .brd 里有 BOARD GEOMETRY/OUTLINE, 或提供 DXF/机械图")
    res = Result(cap, PASS, "")
    res.metric("板宽", area["width_mm"], "mm", "内嵌快照 BOARD GEOMETRY/OUTLINE",
               "high" if ctx.scale_confident else "low")
    res.metric("板高", area["height_mm"], "mm", "同上",
               "high" if ctx.scale_confident else "low")
    res.metric("外接矩形面积", area["bbox_area_mm2"], "mm²", "板框 bbox")
    if area.get("polygon_area_mm2"):
        res.metric("板框多边形面积", area["polygon_area_mm2"], "mm²", "板框折线鞋带公式", "medium")
    res.metric("板框图形段数", area["outline_count"], "组", "板框端点连通分组")
    outline_segs = ctx.segments(["outline"])
    res.metric("板框图形条数", len(outline_segs), "条", "内嵌快照 BOARD GEOMETRY/OUTLINE")

    # 开槽: 板框里除最外圈以外的闭合环
    loops = _count_closed_loops(outline_segs) if outline_segs else 0
    inner = max(0, loops - 1)
    res.metric("闭合环数量", loops, "个", "端点连通分析")
    res.metric("疑似开槽/内孔", inner, "个", "除最外圈以外的闭合环")
    res.add_all([{"项目": "外接尺寸", "值": f"{area['width_mm']} × {area['height_mm']} mm"},
                 {"项目": "板框分组", "值": area["outline_count"]},
                 {"项目": "闭合环", "值": loops},
                 {"项目": "疑似开槽", "值": inner}],
                ["项目", "值"])
    res.summary = (f"板尺寸 {area['width_mm']} × {area['height_mm']} mm, "
                   f"板框 {area['outline_count']} 组图形, 闭合环 {loops} 个"
                   + (f" (含 {inner} 个疑似开槽)" if inner else ""))
    if warn:
        res.warn(warn)
    res.source("内嵌设计快照 BOARD GEOMETRY/OUTLINE (Draw Size 标定)")
    return res


# --------------------------------------------------------------------------
# 2.2 Layer Stackup
# --------------------------------------------------------------------------
def cap_stackup(ctx: Context) -> Result:
    cap = CATALOG_MAP["physical.stackup"]
    rows = facts.stackup_rows(ctx)
    if not rows:
        return unavailable(cap, "没有解析到层信息 (层数与层名都为空)",
                           "确认 .brd 的 _QuickViewText 或 SubClass.xml 完整")
    res = Result(cap, PASS, "")
    res.add_all(rows, ["层号", "层名", "用途", "有铜箔图形", "来源"])
    count = ctx.layer_count or len(rows)
    res.metric("层数", count, "层", ctx.design.get("layer_count_source") or "层名推算",
               ctx.design.get("layer_count_confidence") or "medium")
    res.metric("层名数量", len(ctx.layers), "个", "BRD SubClass.xml (Etch 层)")
    signals = sum(1 for r in rows if "信号" in r["用途"])
    planes = sum(1 for r in rows if "平面" in r["用途"])
    res.metric("信号层", signals, "层", "按层名关键字判定")
    res.metric("平面层", planes, "层", "按层名关键字判定")
    etch_layers = ctx.etch_layers
    if etch_layers:
        res.metric("快照里有铜箔的层", len(etch_layers), "层", "内嵌快照 ETCH 图形",
                   "medium")
    res.summary = (f"{count} 层 ({signals} 信号 / {planes} 平面); "
                   f"层名 {len(ctx.layers)} 个")
    if ctx.design.get("layer_note"):
        res.warn(ctx.design["layer_note"])
    if etch_layers and len(etch_layers) < count:
        missing = [r["层名"] for r in rows
                   if r["层名"] not in etch_layers and r["用途"] != "未标注"]
        if missing:
            res.warn(f"这些层在快照里没有铜箔图形: {'、'.join(missing[:8])} "
                     f"(可能是纯平面层或快照未导出该层)")
    res.source("二进制 _QuickViewText + objects/SubClass.xml")
    return res


# --------------------------------------------------------------------------
# 2.3 Cross Section
# --------------------------------------------------------------------------
def cap_cross_section(ctx: Context) -> Result:
    cap = CATALOG_MAP["physical.cross_section"]
    d = ctx.design
    notes = (d.get("raw_note") or {}).get("notes") or {}
    thickness = d.get("board_thickness_mm")
    copper_oz = notes.get("copper_oz")
    finishes = d.get("surface_finishes") or []
    if thickness is None and copper_oz is None:
        res = unavailable(cap, "BRD 里没有叠层截面数据 (介质厚度/铜厚/板厚)",
                          ctx.howto_stackup())
        res.metric("层数", ctx.layer_count, "层", "BRD 预览元数据")
        return res
    res = Result(cap, INFO, "")
    if thickness:
        res.metric("板厚", thickness, "mm", "制程说明文本", "low")
    if copper_oz:
        res.metric("成品铜厚", copper_oz, "oz", "制程说明文本", "low")
    if finishes:
        res.metric("表面处理", "、".join(finishes), "", "制程说明文本", "low")
    res.metric("层数", ctx.layer_count, "层", "BRD 预览元数据", "high")
    res.add_all([{"项目": "板厚", "值": thickness, "单位": "mm", "来源": "制程说明"},
                 {"项目": "铜厚", "值": copper_oz, "单位": "oz", "来源": "制程说明"}],
                ["项目", "值", "单位", "来源"])
    res.summary = (f"板厚 {thickness or '—'}mm, 铜厚 {copper_oz or '—'}oz; "
                   f"介质厚度与 Er 需要叠层报告")
    res.need("每层介质厚度与介电常数 (Dk/Er)",
             ctx.howto_stackup() + "; 有了它才能算阻抗 (见 electrical.impedance)")
    res.warn("板厚/铜厚是从制程说明文本里抓的, 置信度低, 必须与叠层表核对")
    res.source("二进制制程说明文本 + 预览元数据")
    return res


# --------------------------------------------------------------------------
# 2.4 Copper Analysis
# --------------------------------------------------------------------------
def cap_copper(ctx: Context) -> Result:
    cap = CATALOG_MAP["physical.copper"]
    segs = ctx.segments(["etch"])
    if not segs:
        return unavailable(cap, "内嵌快照里没有 ETCH 铜箔图形, 无法做铜层分析",
                           "Allegro: 确认设计快照导出包含 ETCH 层; 或提供 Gerber")
    rows = facts.copper_stats(ctx)
    res = Result(cap, INFO, "")
    res.add_all(rows, ["层", "图形数", "走线总长mm", "最小线宽mm", "中位线宽mm",
                       "最大线宽mm", "铜面积估算mm2", "铜占比"])
    by_layer = ctx.segments_by_layer(["etch"])
    total_len = geom.total_length_mm(segs)
    res.metric("铜箔层数", len(by_layer), "层", "内嵌快照 ETCH 图形")
    res.metric("铜箔图形总数", len(segs), "条", "内嵌快照 ETCH 图形")
    res.metric("走线总长", total_len, "mm", "各层折线长度求和", "medium")
    ws = geom.width_summary(segs)
    if ws["has_widths"]:
        res.metric("最小线宽", ws["min_mm"], "mm", "ETCH 图形 width 字段", "medium")
        res.metric("中位线宽", ws["median_mm"], "mm", "同上", "medium")
        res.metric("不同线宽种类", len(ws["distinct"]), "种", "同上", "medium")
    else:
        res.warn("ETCH 图形没有线宽字段, 线宽分布不可用")
    worst = min(rows, key=lambda r: r["最小线宽mm"] or 9e9) if rows else None
    res.summary = (f"{len(by_layer)} 层铜箔共 {len(segs)} 条图形, 总长 {total_len}mm"
                   + (f"; 最细线宽出现在 {worst['层']} ({worst['最小线宽mm']}mm)"
                      if worst and worst.get("最小线宽mm") else ""))
    res.warn("铜占比是按「折线长度 × 平均线宽」估算的, 不含铜皮填充面积, "
             "只用于横向比较各层, 不能当实际铜面积")
    if not ctx.scale_confident:
        res.warn(facts.geometry_warning(ctx) or "")
    res.source("内嵌快照 ETCH 图形 (class=ETCH)")
    return res


# --------------------------------------------------------------------------
# 2.5 Via Structure
# --------------------------------------------------------------------------
def cap_via_structure(ctx: Context) -> Result:
    cap = CATALOG_MAP["physical.via_structure"]
    vias = facts.via_rows(ctx)
    drills = facts.drill_by_size(ctx)
    if not vias and not drills:
        return unavailable(cap, "BRD 里既没有过孔焊盘栈也没有钻孔表",
                           "确认 PhysicalCSet 的 VIA_LIST, 或提供 NC Drill 文件")
    res = Result(cap, INFO, "")
    kinds: Dict[str, int] = {}
    for v in vias:
        kinds[v["kind"]] = kinds.get(v["kind"], 0) + 1
    for row in vias:
        res.add(焊盘栈=row["name"], 类型=facts.hole_kind_label(row["kind"]),
                钻孔mm=row["drill_mm"], 焊盘mm=row["pad_mm"],
                单边孔环mm=row["annular_mm"], 跨层=(f"{row['span'][0]}-{row['span'][1]}"
                                                if row.get("span") else ""),
                判据=row.get("kind_reason", ""))
    res.columns = ["焊盘栈", "类型", "钻孔mm", "焊盘mm", "单边孔环mm", "跨层", "判据"]
    res.metric("焊盘栈种类", len(vias), "种", "PhysicalCSet VIA_LIST / 二进制焊盘栈名")
    for kind, count in sorted(kinds.items()):
        res.metric(f"{facts.hole_kind_label(kind)}焊盘栈", count, "种", "按孔径/跨层/命名判定")
    through = [v for v in vias if v["kind"] in ("through", "both")]
    blind = [v for v in vias if v["kind"] in ("blind", "both")]
    if through:
        res.metric("最小机械通孔", min(v["drill_mm"] for v in through), "mm",
                   "焊盘栈命名", "medium")
    if blind:
        res.metric("最小盲/埋孔", min(v["drill_mm"] for v in blind), "mm",
                   "焊盘栈命名", "medium")
    if drills:
        res.metric("钻孔表孔径种类", len(drills), "种", "二进制 DRILL CHART")
        res.metric("钻孔总数", ctx.design.get("hole_count") or "部分统计",
                   "个", "二进制 DRILL CHART 数量列")
        res.metric("NPTH 孔数", ctx.design.get("npth_holes"), "个", "二进制 DRILL CHART")
    for row in drills:
        res.extra.setdefault("drill_rows", []).append(row)
    res.summary = (f"{len(vias)} 种过孔焊盘栈"
                   + (f" ({', '.join(f'{facts.hole_kind_label(k)} {v}' for k, v in kinds.items())})"
                      if kinds else "")
                   + (f"; 钻孔表 {len(drills)} 种孔径" if drills else ""))
    unknown = ctx.design.get("via_kind_unknown") or []
    if unknown:
        res.warn(f"{len(unknown)} 个焊盘栈的孔类型无法判定 (孔径同时出现在通孔与盲埋跨度), "
                 f"已单列待人工确认")
    if not drills:
        res.need("每个孔的坐标 (判孔到孔、孔到边距离要用)",
                 ctx.howto_holes_xy())
    res.source("PhysicalCSet.xml VIA_LIST + 二进制 DRILL CHART / 焊盘栈命名")
    return res


# --------------------------------------------------------------------------
# 2.6 Shape Analysis
# --------------------------------------------------------------------------
def cap_shape(ctx: Context) -> Result:
    cap = CATALOG_MAP["physical.shape"]
    segs = ctx.segments(["etch"])
    if not segs:
        return unavailable(cap, "没有铜箔图形, 无法做铜皮/平面分析",
                           "需要含 ETCH 图形的内嵌快照, 或 Gerber")
    big = [s for s in segs if s["has_width"] and s["width_mm"] >= _SHAPE_WIDTH_MM]
    arcs = [s for s in segs if s["arc"]]
    by_layer: Dict[str, List[dict]] = {}
    for seg in segs:
        by_layer.setdefault(seg["layer"], []).append(seg)
    res = Result(cap, INFO, "")
    res.metric("铜箔图形总数", len(segs), "条", "内嵌快照 ETCH 图形")
    res.metric(f"粗图形 (线宽 ≥{_SHAPE_WIDTH_MM}mm)", len(big), "条",
               "线宽字段, 通常是铜皮/平面轮廓", "low")
    res.metric("圆弧图形", len(arcs), "条", "快照 type=6")
    for layer, layer_segs in sorted(by_layer.items()):
        layer_big = [s for s in layer_segs if s["has_width"]
                     and s["width_mm"] >= _SHAPE_WIDTH_MM]
        res.add(层=layer, 图形数=len(layer_segs), 粗图形数=len(layer_big),
                最大线宽mm=max((s["width_mm"] for s in layer_segs
                              if s["has_width"]), default=None),
                圆弧数=sum(1 for s in layer_segs if s["arc"]))
    res.columns = ["层", "图形数", "粗图形数", "最大线宽mm", "圆弧数"]
    res.summary = (f"铜箔图形 {len(segs)} 条, 其中粗图形(疑似铜皮轮廓) {len(big)} 条, "
                   f"圆弧 {len(arcs)} 条")
    res.need("铜皮/平面的填充几何与网络归属",
             "内嵌快照只导出轮廓线, 不含填充; 判孤岛 (island) 与死铜需要 Gerber "
             "或 Allegro 的 Shape 报告")
    res.warn("本项只能给轮廓级统计: 孤岛/死铜/平面分割是否合理必须看填充几何")
    res.source("内嵌快照 ETCH 图形 (轮廓级)")
    return res


# --------------------------------------------------------------------------
# 2.7 Component Placement
# --------------------------------------------------------------------------
def cap_placement(ctx: Context) -> Result:
    cap = CATALOG_MAP["physical.placement"]
    comp = facts.components(ctx)
    comps = ctx.exports.get("components") or []
    res = Result(cap, INFO, "")
    if comp.get("ok"):
        res.metric("器件数", comp["count"], "个", comp.get("source", "objects/Part.xml"),
                   "high")
        for label, count in list((comp.get("by_category") or {}).items())[:10]:
            res.metric(f"其中 {label}", count, "个", "位号前缀分类")
    else:
        res.need("器件清单 (位号)", comp.get("reason", "BRD 无 Part.xml"))
    if not comps:
        res.status = UNAVAILABLE if not comp.get("ok") else INFO
        res.summary = (f"器件 {comp.get('count', 0)} 个; "
                       f"XY 坐标与旋转角不在 .brd 里, 需要 Placement 导出")
        res.need("器件 XY 坐标 / 旋转角 / 面别", ctx.howto_components_xy())
        res.source("objects/Part.xml 位号清单")
        return res

    xs = [c.get("x_mm") for c in comps if c.get("x_mm") is not None]
    ys = [c.get("y_mm") for c in comps if c.get("y_mm") is not None]
    sides: Dict[str, int] = {}
    rots: Dict[str, int] = {}
    for c in comps:
        sides[c.get("side") or "?"] = sides.get(c.get("side") or "?", 0) + 1
        rot = c.get("rotation")
        if rot is not None:
            key = f"{round(float(rot) / 90) * 90:.0f}°"
            rots[key] = rots.get(key, 0) + 1
    res.status = PASS
    res.metric("有坐标的器件", len(comps), "个", "导入的 Placement/SKILL 坐标文件", "high")
    if xs and ys:
        res.metric("布局范围 X", f"{min(xs):.2f} ~ {max(xs):.2f}", "mm", "导入的坐标文件")
        res.metric("布局范围 Y", f"{min(ys):.2f} ~ {max(ys):.2f}", "mm", "导入的坐标文件")
        res.metric("布局跨度", f"{max(xs) - min(xs):.2f} × {max(ys) - min(ys):.2f}",
                   "mm", "导入的坐标文件")
    for side, count in sorted(sides.items()):
        res.metric(f"{side} 面器件", count, "个", "导入的坐标文件")
    for rot, count in sorted(rots.items(), key=lambda kv: -kv[1])[:6]:
        res.add(旋转角=rot, 器件数=count)
    res.columns = ["旋转角", "器件数"]
    res.summary = (f"{len(comps)} 个器件有坐标; "
                   f"{' / '.join(f'{k} {v}' for k, v in sorted(sides.items()))}; "
                   f"旋转角分布见明细")
    if comp.get("ok") and len(comps) != comp["count"]:
        res.warn(f"Part.xml 有 {comp['count']} 个位号, 坐标文件只有 {len(comps)} 个, "
                 f"两者不一致 (可能有未摆放器件或坐标文件导出范围不同)")
    res.source("objects/Part.xml + 导入的 Placement/SKILL 坐标文件")
    return res


# --------------------------------------------------------------------------
# 2.8 Pad Geometry —— 焊盘尺寸与引脚间距 (逐器件)
# --------------------------------------------------------------------------
def _pad_analysis(ctx: Context) -> dict:
    """跑一次焊盘几何分析 (结果在 extra 里缓存, 供 drc.pad_clearance 复用)。"""
    cached = ctx.shared.get("pad_geometry")
    if cached is not None:
        return cached
    from src.pad_geometry import analyze as analyze_pads

    qv = (ctx.design.get("raw_note") or {}).get("quickview") or {}
    try:
        got = analyze_pads(ctx.snapshot, ctx.scale_mm_per_unit,
                           inventory=ctx.exports, quickview=qv if qv.get("found") else None)
    except Exception as exc:  # noqa: BLE001  几何失败不能拖垮整轮分析
        got = {"ok": False, "reason": f"焊盘几何解析失败: {exc}"}
    ctx.shared["pad_geometry"] = got
    return got


def cap_pad_geometry(ctx: Context) -> Result:
    cap = CATALOG_MAP["physical.pad_geometry"]
    got = _pad_analysis(ctx)
    if not got.get("ok"):
        res = unavailable(cap, got.get("reason") or "读不到焊盘几何",
                          "在 PCB Editor 里跑一次 SKILL 导出 (--export-skill <目录> "
                          "生成脚本, 得到 evan_pads.csv), 或提供内嵌快照带焊盘图形的 .brd")
        for reason in got.get("reasons") or []:
            if reason:
                res.warn(reason)
        return res

    pads = got.get("pads") or {}
    rows = got.get("rows") or []
    res = Result(cap, INFO, "")
    res.add_all(rows, ["器件", "焊盘数", "焊盘尺寸mm", "最小焊盘mm", "最大焊盘mm",
                       "引脚间距mm", "主间距mm", "焊盘间距mm", "层/面", "分组依据",
                       "跨度mm"])
    res.metric("焊盘总数", pads.get("count"), "个", "、".join(got.get("sources") or []),
               "high" if pads.get("named") else "medium")
    res.metric("器件/簇数量", got.get("grouped"), "个", "按位号分组, 无位号时按几何聚类")
    named = pads.get("named") or 0
    if named:
        res.metric("带位号的焊盘", named, "个", "SKILL 导出 evan_pads.csv", "high")
    if pads.get("count") and named < pads.get("count"):
        res.metric("仅靠几何聚类的焊盘", pads["count"] - named, "个",
                   "快照焊盘图形没有位号, 按边缘间距聚类", "medium")
    sizes = [r["最小焊盘mm"] for r in rows if r.get("最小焊盘mm") is not None]
    if sizes:
        res.metric("最小焊盘尺寸", round(min(sizes), 3), "mm", "各器件最小焊盘取最小")
    pitches = [r["引脚间距mm"] for r in rows if r.get("引脚间距mm")]
    if pitches:
        tight = min(rows, key=lambda r: r.get("引脚间距mm") or 9e9)
        res.metric("最小引脚间距", tight.get("引脚间距mm"), "mm",
                   f"{tight.get('器件')} 的焊盘中心距", "medium")
    # 判定: 有了逐器件间距, 就能做一次"最细间距"筛查。这里用组装能力库里的
    # BGA/QFN 间距能力值当**筛查线**, 低于它只报"需专项分析", 不直接判不合格
    # (细间距连接器/COB 的判定口径要按器件类型来, 本工具手里没有器件类型)。
    from src.dfm_rules import ASSEMBLY_RULES

    limit = ASSEMBLY_RULES.get("bga_pitch_min")
    if limit and pitches:
        bad = [r for r in rows if r.get("引脚间距mm") and r["引脚间距mm"] < limit]
        if bad:
            res.status = WARN
            res.metric("筛查线 (最细引脚间距)", limit, "mm", "组装能力库 bga_pitch_min")
            res.summary = (f"{got.get('grouped')} 个器件/簇共 {pads.get('count')} 个焊盘; "
                           f"最细引脚间距 {min(pitches)}mm, 有 {len(bad)} 个器件低于筛查线 "
                           f"{limit}mm, 需专项制造符合性分析")
        else:
            res.summary = (f"{got.get('grouped')} 个器件/簇共 {pads.get('count')} 个焊盘; "
                           f"最细引脚间距 {min(pitches)}mm, 不低于筛查线 {limit}mm")
    else:
        res.summary = (f"{got.get('grouped')} 个器件/簇共 {pads.get('count')} 个焊盘; "
                       f"焊盘尺寸与引脚间距明细见下表")
    if pads.get("count") and named < pads.get("count"):
        res.warn("无位号的焊盘是按「边缘间距」几何聚类出来的簇, 不是从 BRD 读到的真实位号; "
                 "要按真实器件统计, 请用 SKILL 导出 evan_pads.csv")
    for reason in got.get("reasons") or []:
        if reason:
            res.warn(reason)
    res.source("、".join(got.get("sources") or []) or "BRD 焊盘几何")
    res.extra["pad_geometry"] = got
    return res


CATALOG: List[Capability] = [
    Capability("physical.board_geometry", "physical", "Board Geometry", "板框与板边",
               "板尺寸、板框段数、闭合环与开槽", cap_board_geometry, "snapshot",
               ["内嵌快照 BOARD GEOMETRY/OUTLINE"], ["板尺寸", "开槽数"]),
    Capability("physical.stackup", "physical", "Layer Stackup", "层叠结构",
               "层数、层序、每层用途与是否有铜箔", cap_stackup, "brd",
               ["预览元数据", "SubClass.xml"], ["层表"]),
    Capability("physical.cross_section", "physical", "Cross Section", "叠层截面",
               "板厚、铜厚、表面处理; 介质厚度需要叠层报告", cap_cross_section, "brd",
               ["制程说明文本"], ["板厚/铜厚/表面处理"]),
    Capability("physical.copper", "physical", "Copper Analysis", "铜箔分析",
               "每层铜箔图形数、走线总长、线宽分布、铜占比估算", cap_copper,
               "snapshot", ["内嵌快照 ETCH 图形"], ["每层铜箔统计表"]),
    Capability("physical.via_structure", "physical", "Via Structure", "过孔结构",
               "过孔类型、钻孔/焊盘/孔环、钻孔表孔径分布", cap_via_structure, "brd",
               ["物理约束 VIA_LIST", "钻孔表"], ["焊盘栈明细", "钻孔分布"]),
    Capability("physical.shape", "physical", "Shape Analysis", "铜皮与平面",
               "铜箔轮廓、铜皮轮廓、圆弧统计; 孤岛需填充几何", cap_shape,
               "snapshot", ["内嵌快照 ETCH 图形"], ["每层轮廓统计"]),
    Capability("physical.placement", "physical", "Component Placement", "元件布局",
               "器件数量与分类; XY/旋转角需要 Placement 导出", cap_placement, "brd",
               ["objects/Part.xml", "Placement/SKILL 坐标文件"],
               ["器件清单", "布局范围与旋转角分布"]),
    Capability("physical.pad_geometry", "physical", "Pad Geometry", "焊盘尺寸与引脚间距",
               "逐器件统计焊盘大小、引脚间距(中心距)、焊盘间距(边缘距); "
               "有位号按位号分组, 无位号按几何聚类", cap_pad_geometry, "export",
               ["SKILL 导出 evan_pads.csv (焊盘外框)", "内嵌快照焊盘图形"],
               ["每个器件的焊盘尺寸/引脚间距表", "最小引脚间距"]),
]
CATALOG_MAP: Dict[str, Capability] = {c.id: c for c in CATALOG}

"""第 1 类: 设计规则检查 (DRC)。

能力与 PCB Editor 的 DRC 菜单一一对应: DRC Check / Clearance / Width / Via /
Short / Open / Constraint / Unconnected。

口径 (这一节比代码重要)
----------------------
本工具**不替代** Allegro 的 DRC 引擎。Allegro DRC 是在带网络的数据库上跑的,
能判"同网络允许相连、异网络必须分开"; 本工具手里只有快照里的**无网络图形**,
所以:

  * Width Check  —— 真做: 快照里的每段 ETCH 图形都有线宽, 直接和约束比;
  * Clearance    —— 真做, 但**不区分网络**: 同一层图形两两算净间距, 同网络本来
                    连着的铜也会被算进去, 结论写清"含同网络, 需 DRC 报告复核";
  * Via Check    —— 真做: 焊盘栈的钻孔/焊盘/孔环 vs 约束与工艺常识;
  * Constraint   —— 真做: 检查约束集本身是否完整、是否互相矛盾;
  * Short/Open/  —— 做不了: 这三项必须要有网络连通性。给了 Allegro 的 DRC 报告
    Unconnected     就解析报告出结论, 没给就如实写"缺什么、怎么补"。
"""
from typing import Dict, List, Optional

from src.brd_analysis import facts, geom
from src.brd_analysis.context import Context
from src.brd_analysis.model import (FAIL, INFO, PASS, UNAVAILABLE, WARN, Capability,
                                    Result, unavailable)

# 从导入的 DRC 报告里按关键字归类
_SHORT_KEYS = ("SHORT", "短路", "OVERLAP")
_OPEN_KEYS = ("OPEN", "开路", "BROKEN")
_UNCONN_KEYS = ("UNCONNECTED", "RATSNEST", "UNROUTED", "未连接", "飞线")


def _imported_drc(ctx: Context) -> Optional[dict]:
    drc = ctx.exports.get("drc")
    return drc if drc and drc.get("ok") else None


def _match_classes(drc: dict, keys) -> List[dict]:
    out = []
    for item in drc.get("items") or []:
        blob = f"{item.get('class', '')} {item.get('detail', '')}".upper()
        if any(k.upper() in blob for k in keys):
            out.append(item)
    return out


# --------------------------------------------------------------------------
# 1.1 DRC Check —— 违规总数
# --------------------------------------------------------------------------
def cap_drc_check(ctx: Context) -> Result:
    cap = CATALOG_MAP["drc.check"]
    drc = _imported_drc(ctx)
    if drc:
        res = Result(cap, FAIL if drc["count"] else PASS,
                     f"Allegro DRC 报告共 {drc['count']} 条违规"
                     + (f", 集中在 {list(drc['by_class'])[:3]}" if drc["by_class"] else ""))
        res.metric("违规总数", drc["count"], "条", f"导入的 DRC 报告 ({drc['source']})")
        res.metric("违规类别数", len(drc["by_class"]), "类", f"导入的 DRC 报告")
        res.add_all([{"违规类别": k, "数量": v} for k, v in drc["by_class"].items()],
                    ["违规类别", "数量"])
        res.source(f"Allegro DRC 报告: {drc['source']}")
        if drc.get("unrecognized_blocks"):
            res.warn(f"报告里有 {drc['unrecognized_blocks']} 段没能解析出字段, "
                     f"可能少算了违规")
        return res

    # 没有 DRC 报告: 用能算的两项几何检查给出"自查"结果
    width = cap_drc_width(ctx)
    clearance = cap_drc_clearance(ctx)
    measured = [r for r in (width, clearance) if r.measured]
    res = Result(cap, WARN if measured else UNAVAILABLE, "")
    rows = []
    for sub in (width, clearance):
        rows.append({"检查项": sub.name_cn, "状态": sub.status_label, "结论": sub.summary})
    res.add_all(rows, ["检查项", "状态", "结论"])
    if measured:
        bad = sum(1 for r in (width, clearance) if r.status == FAIL)
        res.status = FAIL if bad else PASS
        res.summary = (f"未提供 Allegro DRC 报告; 用本工具的几何自查代替: "
                       f"{len(measured)} 项能算, {bad} 项不符合。"
                       f"这不是完整 DRC (不含短路/开路/未连接)")
        res.warn("几何自查不含网络信息, 短路/开路/未连接判不了, 必须另跑 Allegro DRC")
    else:
        res.summary = "没有 DRC 报告, 也没有可用的几何数据, 无法给出 DRC 结论"
    res.need("Allegro DRC 报告 (含违规类别/层/位置/实测值/规则值)",
             ctx.howto_drc_report())
    res.need("网络连通性数据 (判短路/开路/未连接必须)",
             "Allegro: Tools > Quick Reports > DRC, 或 Manufacture > DRC 后导出报告")
    res.source("本工具几何自查 (快照 ETCH 图形, 不含网络)")
    return res


# --------------------------------------------------------------------------
# 1.2 Clearance Check —— 间距
# --------------------------------------------------------------------------
def _clearance_threshold(ctx: Context) -> tuple:
    """返回 (搜索阈值 mm, 判定阈值 mm, 判定依据文本)。"""
    declared = ctx.design.get("min_spacing_mm")
    if declared:
        return (round(min(max(declared * 1.5, 0.3), 1.0), 4), float(declared),
                f"BRD SpacingCSet 声明的 LINE_TO_LINE_SPACING 最严值 {declared}mm")
    return (0.3, None, "BRD 未声明线距约束, 只给实测最小值, 不做判定")


def cap_drc_clearance(ctx: Context) -> Result:
    cap = CATALOG_MAP["drc.clearance"]
    segs = ctx.segments(["etch"])
    if not segs:
        return unavailable(cap, "没有可用的铜箔(ETCH)几何, 无法量取线到线间距",
                           "提供 Allegro DRC 报告, 或让设计导出含 ETCH 图形的内嵌快照")
    search, judge, basis = _clearance_threshold(ctx)
    scan = geom.same_layer_gaps(segs, search)
    res = Result(cap, INFO, "")
    res.metric("参与计算的铜箔图形", scan["segment_count"], "段", "内嵌快照 ETCH 图形")
    res.metric("两两比较次数", scan["examined"], "次", "包围盒粗筛后的精确距离计算")
    if scan["min_gap_mm"] is None:
        res.status = INFO
        res.summary = (f"在 {search}mm 搜索范围内没有找到更近的铜箔对; "
                       f"实测最小净间距 > {search}mm")
        res.source("内嵌快照 ETCH 图形 (不含网络信息)")
        return res

    res.metric("实测最小净间距", scan["min_gap_mm"], "mm", "内嵌快照 ETCH 图形", "medium")
    res.metric("小于阈值的对数", len(scan["pairs"]), "对", "同上")
    touch = scan.get("touching") or []
    if touch:
        res.metric("相接/相交的图形对", len(touch), "对",
                   "中心线距离为 0: 连接点或交叉", "medium")
    if judge:
        violations = [p for p in scan["pairs"] if p["gap_mm"] < judge]
        res.status = FAIL if violations else PASS
        res.summary = (f"实测最小净间距 {scan['min_gap_mm']}mm; 判定依据 {basis}; "
                       f"低于该值的 {len(violations)} 处"
                       if violations else
                       f"实测最小净间距 {scan['min_gap_mm']}mm, 不低于声明的 {judge}mm")
        res.metric("判定阈值", judge, "mm", basis)
    else:
        res.status = INFO
        res.summary = f"实测最小净间距 {scan['min_gap_mm']}mm ({basis})"
    for p in scan["pairs"][:200]:
        a, b = segs[p["a"]], segs[p["b"]]
        res.add(层=p["layer"], 净间距mm=p["gap_mm"], 中心距mm=p["center_mm"],
                线宽A=p["a_width_mm"], 线宽B=p["b_width_mm"],
                位置A="%.3f, %.3f" % geom.mid_point(a),
                位置B="%.3f, %.3f" % geom.mid_point(b))
    if touch:
        res.warn(f"另有 {len(touch)} 对图形相接或相交 (中心线距离 0), 已从间距违规里排除: "
                 f"相接的绝大多数是同一网络的拐角/T 型接头, 相交才可能是短路, "
                 f"而判短路需要网络信息 —— 请以 Allegro DRC 报告为准")
    res.warn("此结果基于无网络图形: 同网络本来相连的铜也会被算成间距不足, "
             "必须用 Allegro DRC 报告复核; 异网络短路同理只能靠 DRC 判")
    if scan["truncated"]:
        res.warn("线段对数量超过上限, 计算已提前结束, 结果可能不完整")
    if not ctx.scale_confident:
        res.warn("快照坐标用经验系数换算, 绝对尺寸需核对")
    res.source("SpacingCSet.xml 约束 + 内嵌快照 ETCH 图形")
    res.extra["min_gap_mm"] = scan["min_gap_mm"]
    return res


# --------------------------------------------------------------------------
# 1.3 Width Check —— 线宽
# --------------------------------------------------------------------------
def cap_drc_width(ctx: Context) -> Result:
    cap = CATALOG_MAP["drc.width"]
    segs = ctx.segments(["etch"])
    if not segs:
        return unavailable(cap, "没有可用的铜箔(ETCH)几何, 无法统计实际走线宽度",
                           "提供含 ETCH 图形的内嵌快照, 或 Allegro 的 Etch Report")
    res = Result(cap, INFO, "")
    by_layer: Dict[str, List[dict]] = {}
    for seg in segs:
        by_layer.setdefault(seg["layer"], []).append(seg)

    declared = ctx.design.get("min_line_width_mm")
    neck = ctx.design.get("min_neck_width_mm")
    res.metric("参与统计的图形", len(segs), "段", "内嵌快照 ETCH 图形")
    if declared:
        res.metric("声明最小线宽", declared, "mm",
                   ctx.design.get("sources", {}).get("min_line_width_mm", {}).get(
                       "source", "PhysicalCSet.xml"))
    if neck:
        res.metric("声明最小颈宽", neck, "mm", "PhysicalCSet.xml / MIN_NECK_WIDTH")

    total_violations = []
    for layer, layer_segs in sorted(by_layer.items()):
        ws = geom.width_summary(layer_segs)
        if not ws["has_widths"]:
            res.add(层=layer, 图形数=len(layer_segs), 最小线宽mm=None,
                    中位线宽mm=None, 最大线宽mm=None, 低于约束段数=None,
                    说明="该层快照图形没有线宽字段, 无法判线宽")
            continue
        bad = []
        if declared:
            bad = [s for s in layer_segs
                   if s["has_width"] and 0 < s["width_mm"] < declared - 1e-6]
            for s in bad[:50]:
                total_violations.append(s)
        res.add(层=layer, 图形数=len(layer_segs), 最小线宽mm=ws["min_mm"],
                中位线宽mm=ws["median_mm"], 最大线宽mm=ws["max_mm"],
                低于约束段数=(len(bad) if declared else None),
                说明=("" if declared else "无声明约束, 只报分布"))
    res.add_all([], ["层", "图形数", "最小线宽mm", "中位线宽mm", "最大线宽mm",
                     "低于约束段数", "说明"])

    all_ws = geom.width_summary(segs)
    if all_ws["has_widths"]:
        res.metric("全板最小线宽", all_ws["min_mm"], "mm", "内嵌快照 ETCH 图形", "medium")
        res.metric("全板中位线宽", all_ws["median_mm"], "mm", "同上", "medium")
    if declared:
        res.status = FAIL if total_violations else PASS
        res.summary = (f"最小线宽 {all_ws.get('min_mm')}mm, 低于声明 {declared}mm 的有 "
                       f"{len(total_violations)} 段" if total_violations else
                       f"最小线宽 {all_ws.get('min_mm')}mm, 不低于声明的 {declared}mm")
    else:
        res.status = INFO if all_ws["has_widths"] else UNAVAILABLE
        res.summary = (f"实测线宽 {all_ws.get('min_mm')}~{all_ws.get('max_mm')}mm "
                       f"(BRD 未声明最小线宽, 未做判定)"
                       if all_ws["has_widths"] else "快照图形没有线宽字段, 判不了线宽")
        if not all_ws["has_widths"]:
            res.need("带线宽的设计数据", "Allegro: Tools > Report > Etch Report, "
                                      "或导出 Gerber 后由 CAM 工具统计")
    res.warn("快照里的 ETCH 图形包含铜皮/平面分割等非走线图形, "
             "统计值比纯走线偏大或偏小, 精细判定请用 Allegro 的 Etch Report")
    res.source("内嵌快照 ETCH 图形线宽 + PhysicalCSet.xml 约束")
    return res


# --------------------------------------------------------------------------
# 1.4 Via Check —— 过孔
# --------------------------------------------------------------------------
def cap_drc_via(ctx: Context) -> Result:
    cap = CATALOG_MAP["drc.via"]
    vias = facts.via_rows(ctx)
    if not vias:
        return unavailable(cap, "BRD 里没有解析到过孔焊盘栈 (VIA_LIST 为空)",
                           "确认 .brd 的 PhysicalCSet 里有 VIA_LIST, 或提供钻孔表")
    res = Result(cap, INFO, "")
    declared_via = set()
    for entry in (ctx.design.get("physical_csets") or {}).values():
        declared_via.update(entry.get("via_list") or [])

    issues = []
    for v in vias:
        name = v["name"]
        drill, pad, ring = v["drill_mm"], v["pad_mm"], v["annular_mm"]
        note = []
        if drill and pad and drill >= pad:
            note.append("钻孔不小于焊盘")
        if ring is not None and ring <= 0:
            note.append("孔环为零")
        if ring is not None and ring < 0.05 and v["kind"] in ("through", "both"):
            note.append(f"机械孔孔环 {ring}mm 偏小 (<0.05)")
        if declared_via and name.split(".")[0].lower() not in \
                {d.split(".")[0].lower() for d in declared_via}:
            note.append("不在 PhysicalCSet 的 VIA_LIST 里")
        res.add(焊盘栈=name, 类型=facts.hole_kind_label(v["kind"]),
                钻孔mm=drill, 焊盘mm=pad, 反焊盘mm=v.get("antipad_mm"),
                单边孔环mm=ring, 跨层=(f"{v['span'][0]}-{v['span'][1]}"
                                    if v.get("span") else ""),
                判定=("异常: " + "; ".join(note)) if note else "正常",
                判据=v.get("kind_reason", ""))
        if note:
            issues.append((name, note))
    res.columns = ["焊盘栈", "类型", "钻孔mm", "焊盘mm", "反焊盘mm", "单边孔环mm",
                   "跨层", "判定", "判据"]
    res.metric("焊盘栈数量", len(vias), "个", "PhysicalCSet.xml VIA_LIST / 二进制焊盘栈名")
    rings = [v["annular_mm"] for v in vias if v["annular_mm"] is not None]
    if rings:
        res.metric("最小单边孔环", round(min(rings), 4), "mm", "焊盘栈 (焊盘-钻孔)/2", "medium")
    drills = [v["drill_mm"] for v in vias if v["drill_mm"]]
    if drills:
        res.metric("最小过孔钻孔", round(min(drills), 4), "mm", "焊盘栈命名", "medium")
    res.metric("异常焊盘栈", len(issues), "个", "本工具规则: 孔环/孔径/VIA_LIST 一致性")
    res.status = WARN if issues else PASS
    res.summary = (f"{len(vias)} 个过孔焊盘栈, {len(issues)} 个有异常: "
                   f"{'、'.join(n for n, _ in issues[:4])}" if issues
                   else f"{len(vias)} 个过孔焊盘栈尺寸关系正常")
    if not declared_via:
        res.warn("没有读到 VIA_LIST, 无法核对焊盘栈是否都在约束清单里")
    res.source("PhysicalCSet.xml VIA_LIST + 二进制焊盘栈命名 (VIA<pad>D<drill>A<anti>)")
    return res


# --------------------------------------------------------------------------
# 1.5 / 1.6 / 1.7 Short / Open / Unconnected —— 必须靠 DRC 报告或网表
# --------------------------------------------------------------------------
def _connectivity_cap(ctx: Context, cap_id: str, keys, what: str) -> Result:
    cap = CATALOG_MAP[cap_id]
    drc = _imported_drc(ctx)
    if drc:
        hits = _match_classes(drc, keys)
        res = Result(cap, FAIL if hits else PASS,
                     f"Allegro DRC 报告里 {what} {len(hits)} 条" if hits
                     else f"Allegro DRC 报告里没有 {what} 记录")
        res.metric(f"{what}条数", len(hits), "条", f"导入的 DRC 报告 ({drc['source']})")
        for it in hits[:200]:
            res.add(类别=it.get("class"), 层=it.get("layer"), 网络=it.get("net"),
                    位置=it.get("location"), 器件=it.get("refdes"),
                    实测=it.get("actual"), 规则=it.get("required"))
        res.columns = ["类别", "层", "网络", "位置", "器件", "实测", "规则"]
        res.source(f"Allegro DRC 报告: {drc['source']}")
        if not hits and drc.get("unrecognized_blocks"):
            res.status = WARN
            res.warn(f"报告有 {drc['unrecognized_blocks']} 个未识别块，不能确认没有 {what}")
        return res

    res = unavailable(cap,
                      f"{what}必须在带网络的数据库上判定, .brd 的几何快照不含网络连通性, "
                      f"本工具算不了",
                      ctx.howto_drc_report())
    if cap_id == "drc.unconnected":
        nets = facts.net_names(ctx)
        count = ctx.design.get("net_count") or len(nets)
        res.metric("网络总数", count, "个", "objects/Net.xml")
        res.metric("已布线情况", "未知", "", "需要 Allegro 的 Unconnected Pins 报告")
        res.summary = (f"本工具算不了未连接网络; 板上有 {count} 个网络, "
                       f"未连接明细需 Allegro 报告")
    return res


def cap_drc_short(ctx: Context) -> Result:
    return _connectivity_cap(ctx, "drc.short", _SHORT_KEYS, "短路")


def cap_drc_open(ctx: Context) -> Result:
    return _connectivity_cap(ctx, "drc.open", _OPEN_KEYS, "开路/断线")


def cap_drc_unconnected(ctx: Context) -> Result:
    return _connectivity_cap(ctx, "drc.unconnected", _UNCONN_KEYS, "未连接网络")


# --------------------------------------------------------------------------
# 1.8 Constraint Check —— 约束完整性
# --------------------------------------------------------------------------
def cap_drc_constraint(ctx: Context) -> Result:
    cap = CATALOG_MAP["drc.constraint"]
    physical = ctx.design.get("physical_csets") or {}
    spacing = ctx.design.get("spacing_csets") or {}
    diff_pairs = ctx.design.get("diff_pair_count") or 0
    if not physical and not spacing:
        return unavailable(cap, "BRD 归档里没有 PhysicalCSet.xml / SpacingCSet.xml, "
                                "读不到任何约束",
                           "确认 .brd 是 17.x 及以后版本 (16.x 纯二进制无约束对象)")
    res = Result(cap, PASS, "")
    findings = []

    def note(level: str, text: str):
        findings.append({"检查项": text, "级别": level})

    if "DEFAULT" not in physical:
        note("缺", "PhysicalCSet 没有 DEFAULT 约束集")
    for cset, entry in physical.items():
        if not entry.get("min_line_width_mm"):
            note("缺", f"PhysicalCSet/{cset} 没有 MIN_LINE_WIDTH")
        if not entry.get("via_list"):
            note("缺", f"PhysicalCSet/{cset} 没有 VIA_LIST (过孔未约束)")
    if not spacing:
        note("缺", "没有 SpacingCSet (线到线/线到孔间距未约束)")
    else:
        for cset, entry in spacing.items():
            if "LINE_TO_LINE_SPACING" not in entry:
                note("缺", f"SpacingCSet/{cset} 没有 LINE_TO_LINE_SPACING")
            if "LINE_TO_THRUVIA_SPACING" not in entry:
                note("缺", f"SpacingCSet/{cset} 没有 LINE_TO_THRUVIA_SPACING")
    if diff_pairs:
        has_gap = any("diff_gap_mm" in entry for entry in physical.values())
        if not has_gap:
            note("缺", f"有 {diff_pairs} 对差分对, 但 PhysicalCSet 里没有 DIFFP_PRIMARY_GAP")

    # 约束互相矛盾: 线宽 > 线距 这类组合在工艺上说不通, 值得提示
    width = ctx.design.get("min_line_width_mm")
    space = ctx.design.get("min_spacing_mm")
    if width and space and width > space * 3:
        note("疑", f"最小线宽 {width}mm 是最小线距 {space}mm 的 {width / space:.1f} 倍, "
                   f"确认约束是否写反")
    neck = ctx.design.get("min_neck_width_mm")
    if neck and width and neck > width:
        note("疑", f"最小颈宽 {neck}mm 大于最小线宽 {width}mm")

    res.add_all(findings, ["检查项", "级别"])
    res.metric("PHYSICAL 约束集", len(physical), "个", "objects/PhysicalCSet.xml")
    res.metric("SPACING 约束集", len(spacing), "个", "objects/SpacingCSet.xml")
    res.metric("发现的约束缺口", len(findings), "项", "本工具约束完整性规则")
    if width:
        res.metric("最小线宽约束", width, "mm", "PhysicalCSet.xml", "high")
    if space:
        res.metric("最小线距约束", space, "mm", "SpacingCSet.xml", "high")
    if findings:
        res.status = WARN
        res.summary = f"约束对象存在 ({len(physical)} 个 Physical / {len(spacing)} 个 Spacing), 但有 {len(findings)} 处缺口"
    else:
        res.summary = f"约束对象完整: {len(physical)} 个 Physical / {len(spacing)} 个 Spacing"
    res.source("objects/PhysicalCSet.xml + objects/SpacingCSet.xml")
    return res


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 1.9 Pad Clearance —— 焊盘到铜皮 / 焊盘到走线 的距离
# --------------------------------------------------------------------------
def cap_pad_clearance(ctx: Context) -> Result:
    cap = CATALOG_MAP["drc.pad_clearance"]
    try:
        from src.brd_analysis.cat_physical import _pad_analysis
    except Exception as exc:  # noqa: BLE001  焊盘几何模块不可用时如实报数据不足
        return unavailable(cap, f"焊盘几何模块不可用: {exc}",
                           "确认 src/pad_geometry.py 可导入, 或用 SKILL 导出 evan_pads.csv 后重试")

    got = _pad_analysis(ctx)
    if not got.get("ok"):
        return unavailable(cap, got.get("reason") or "读不到器件焊盘几何",
                           "用 --export-skill 生成 SKILL 脚本, 在 PCB Editor 里导出 "
                           "evan_pads.csv (每个引脚的焊盘外框), 或提供内嵌快照带焊盘图形的 .brd")
    clearance = got.get("clearance") or {}
    if not clearance.get("ok"):
        return unavailable(cap, clearance.get("reason") or "没有铜箔几何, 量不了距离",
                           "让设计导出含 ETCH 图形的内嵌快照 (走线/铜皮轮廓), "
                           "或提供 Allegro 的 Etch/Shape 报告与 DRC 报告")
    rows = got.get("clearance_rows") or []
    if not rows:
        return unavailable(cap, "没有可用的焊盘位置 (没有焊盘参与测量)",
                           "确认 evan_pads.csv 有数据, 或快照里有焊盘图形")

    summary = got.get("clearance_summary") or {}
    res = Result(cap, INFO, "")
    res.add_all(rows, ["器件", "焊盘数", "到铜皮mm", "铜皮层", "铜皮线宽mm",
                       "到走线mm", "走线层", "走线宽mm", "走线长mm",
                       "在铜皮内焊盘", "相接铜箔数", "数据来源"])
    res.metric("参与测量的焊盘", got.get("pads", {}).get("count"), "个",
               "、".join(got.get("sources") or []))
    res.metric("搜索半径", clearance.get("search_mm"), "mm", "超过该距离记 >搜索半径")
    res.metric("测到铜皮的焊盘", clearance.get("measured_pour"), "个", "内嵌快照 ETCH 图形")
    res.metric("测到走线的焊盘", clearance.get("measured_trace"), "个", "同上")
    if clearance.get("touching_pads"):
        res.metric("有相接铜箔的焊盘", clearance.get("touching_pads"), "个",
                   "多为同网络连线/热焊盘, 不计入间距", "medium")
    if clearance.get("inside_pour_pads"):
        res.metric("落在铜皮轮廓内的焊盘", clearance.get("inside_pour_pads"), "个",
                   "铜皮把焊盘包进去了 (通常同网络), 不给间距数字", "medium")

    declared_line = ctx.design.get("min_pad_to_line_mm")
    declared_shape = ctx.design.get("min_pad_to_shape_mm")
    if declared_line:
        res.metric("声明 (走线到焊盘)", declared_line, "mm",
                   (ctx.design.get("sources", {}).get("min_pad_to_line_mm") or {}).get(
                       "source", "SpacingCSet.xml"))
    if declared_shape:
        res.metric("声明 (铜皮到焊盘)", declared_shape, "mm",
                   (ctx.design.get("sources", {}).get("min_pad_to_shape_mm") or {}).get(
                       "source", "SpacingCSet.xml"))

    bad = []
    if declared_line and summary.get("min_trace_mm") is not None:
        if summary["min_trace_mm"] < declared_line - 1e-6:
            bad.append(f"焊盘到走线 {summary['min_trace_mm']}mm < 声明 {declared_line}mm")
    if declared_shape and summary.get("min_pour_mm") is not None:
        if summary["min_pour_mm"] < declared_shape - 1e-6:
            bad.append(f"焊盘到铜皮 {summary['min_pour_mm']}mm < 声明 {declared_shape}mm")

    text = []
    if summary.get("min_trace_mm") is not None:
        worst = (summary.get("worst_trace") or {}).get("component", "")
        text.append(f"焊盘到走线最近 {summary['min_trace_mm']}mm ({worst})")
    if summary.get("min_pour_mm") is not None:
        worst = (summary.get("worst_pour") or {}).get("component", "")
        text.append(f"焊盘到铜皮最近 {summary['min_pour_mm']}mm ({worst})")
    if not text:
        text.append(f"所有焊盘在 {clearance.get('search_mm')}mm 内都没有未相连的铜箔")
    res.summary = (f"{summary.get('components', 0)} 个器件/簇参与测量; " + "; ".join(text)
                   + (f"; {len(bad)} 项低于声明间距" if bad else ""))
    if declared_line or declared_shape:
        res.status = FAIL if bad else PASS
    else:
        res.status = INFO
        res.warn("BRD 没有声明「走线到焊盘 / 铜皮到焊盘」间距约束, 只给实测值, 不做判定")
    if bad:
        res.warn("低于声明间距的位置: " + "; ".join(bad))
    res.warn("快照里没有网络信息: 相接的铜箔按同网络连线排除, 但同网络但不接触的铜箔"
             "(如热焊盘开口)仍会被算成间距; 判定请以 Allegro DRC 报告为准")
    res.warn("铜皮只有轮廓没有填充面, 量到的是焊盘到铜皮轮廓的距离")
    if not ctx.scale_confident:
        res.warn("快照坐标用经验系数换算, 绝对尺寸需核对")
    res.source("焊盘几何 (" + "、".join(got.get("sources") or []) + ") + 内嵌快照 ETCH 图形")
    res.extra["pad_clearance"] = {k: v for k, v in clearance.items()}
    res.extra["pad_clearance_rows"] = rows
    return res


CATALOG: List[Capability] = [
    Capability("drc.check", "drc", "DRC Check", "DRC 违规汇总",
               "整板设计规则违规数量与类别分布", cap_drc_check, "export",
               ["Allegro DRC 报告 或 内嵌快照几何"],
               ["违规总数", "按类别分布"]),
    Capability("drc.clearance", "drc", "Clearance Check", "间距检查",
               "线与线、线与孔的同层净间距, 对照最小线距约束", cap_drc_clearance,
               "snapshot", ["内嵌快照 ETCH 图形", "SpacingCSet.xml"],
               ["实测最小净间距", "间距不足的位置明细"]),
    Capability("drc.width", "drc", "Width Check", "线宽检查",
               "实际走线宽度分布, 低于最小线宽的段", cap_drc_width, "snapshot",
               ["内嵌快照 ETCH 图形线宽", "PhysicalCSet.xml"],
               ["每层线宽分布", "低于约束的段"]),
    Capability("drc.via", "drc", "Via Check", "过孔检查",
               "过孔尺寸、孔环、类型与 VIA_LIST 一致性", cap_drc_via, "brd",
               ["PhysicalCSet.xml VIA_LIST"], ["焊盘栈明细", "异常焊盘栈"]),
    Capability("drc.short", "drc", "Short Check", "短路检查",
               "网络间短路; 需要网络连通性, 只能由 Allegro DRC 或网表比对给出",
               cap_drc_short, "external", ["Allegro DRC 报告"], ["短路条目明细"]),
    Capability("drc.open", "drc", "Open Check", "开路检查",
               "网络开路/断线; 需要网络连通性, 只能由 Allegro DRC 或网表比对给出",
               cap_drc_open, "external", ["Allegro DRC 报告"], ["开路条目明细"]),
    Capability("drc.unconnected", "drc", "Unconnected Check", "未连接检查",
               "未完成布线的网络与飞线; 需要网络的连接关系", cap_drc_unconnected,
               "external", ["Allegro DRC 报告 / Unconnected Pins 报告"],
               ["未连接网络数", "明细"]),
    Capability("drc.constraint", "drc", "Constraint Check", "约束检查",
               "物理/间距约束是否完整、是否互相矛盾", cap_drc_constraint, "brd",
               ["PhysicalCSet.xml", "SpacingCSet.xml"], ["约束缺口清单"]),
    Capability("drc.pad_clearance", "drc", "Pad Clearance", "焊盘到铜皮与走线间距",
               "逐器件量取焊盘到铜皮(平面/覆铜)与到走线(线路)的最近距离 (mm), "
               "相接的同网络铜箔单列, 落在铜皮轮廓内的焊盘单独说明",
               cap_pad_clearance, "snapshot",
               ["焊盘几何 (SKILL evan_pads.csv / 内嵌快照焊盘图形)",
                "内嵌快照 ETCH 图形", "SpacingCSet 的焊盘间距约束"],
               ["每个器件的焊盘到铜皮/走线距离表", "全板最小间距"]),
]
CATALOG_MAP: Dict[str, Capability] = {c.id: c for c in CATALOG}

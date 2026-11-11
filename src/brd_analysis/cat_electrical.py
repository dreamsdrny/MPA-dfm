"""第 3 类: 信号完整性 (SI) 与电气分析。

对应 PCB Editor / Sigrity 侧的: Impedance / Capacitance / Inductance /
Resistance / Delay / Crosstalk / Signal Integrity / Net Analysis。

口径 (这一节比代码重要)
----------------------
.brd 里拿得到的电气相关数据只有三样:

  1. 内嵌快照的 ETCH 几何 —— 每段铜箔的线宽、折线长度、所在层;
  2. 归档对象 —— 网络名 (objects/Net.xml)、差分对名 (objects/DiffPair.xml)、
     物理约束 (objects/PhysicalCSet.xml 的最小线宽/差分间距);
  3. 二进制制程说明文本 —— 铜厚 (copper_oz)、板厚、表面处理、是否声明受控阻抗。

**.brd 里没有的东西**: 叠层介质厚度与介电常数 Dk、每个网络的归属与真实路径、
每个网络的引脚清单、任何场/传输线求解结果。于是本模块分成两半:

  * 能算的: 阻抗 / 寄生电容 / 寄生电感 / 网络电阻 / 传播延迟 —— 全部是
    「IPC-2141 经验公式或集中参数公式 + 假设叠层」算出来的**板级量级估算**,
    状态一律 INFO, 每条都写明假设与出处, 绝不写成 PASS/FAIL;
  * 算不了的: 串扰只能给「几何代理指标」(平行耦合长度), 网络级电气参数
    (每个网络的 R/L/C/Z0) 因为快照没有网络归属而做不了, 完整的 SI 仿真需要
    Sigrity 授权 —— 这些一律如实写 need(), 不拿估算值冒充判定结果。

设计纪律: **缺数据 ≠ 通过**。任何一项在数据不够时返回 unavailable, 并写清缺什么、
怎么补 (Allegro 菜单路径 / 命令)。
"""
import math
from typing import Dict, List, Optional, Tuple

from src.brd_analysis import facts, geom
from src.brd_analysis.context import Context
from src.brd_analysis.model import (INFO, UNAVAILABLE, WARN, Capability, Result,
                                    unavailable)

# --------------------------------------------------------------------------
# 假设条件: 集中放在这里, 并逐条写进 metrics / warnings, 让"这是估算"无处可藏。
#
# .brd 归档对象与二进制里都没有 Cross-section 的介质参数 (Er / 介质厚度读不到),
# 但阻抗 / 电容 / 电感 / 延迟的量级必须先有 Er 与介质厚度才能算。下面给的是
# **业内常见 FR-4 叠层**的典型值, 用来给出量级, 不是这块板的真实叠层。
# --------------------------------------------------------------------------
_ASSUMED_ER = 4.2            # 假设介电常数 Dk (FR-4 典型值)
_ASSUMED_H_MM = 0.20         # 假设单层介质厚度 (外层: 走线到最近参考层; 内层: 带状线到最近平面)
_ASSUMED_COPPER_OZ = 1.0     # BRD 读不到 copper_oz 时的假设铜厚
_OZ_TO_MM = 0.035            # 1oz ≈ 35µm
# 有效介电常数: 微带线一半在空气、一半在介质里, 常用 (Er+1)/2; 带状线全在介质里 = Er
_ER_EFF_MICROSTRIP = round((_ASSUMED_ER + 1.0) / 2.0, 3)   # 2.6
_ER_EFF_STRIPLINE = _ASSUMED_ER                            # 4.2

# 物理常数 (SI 单位)
_RHO_CU = 1.72e-8            # 铜的体电阻率 Ω·m (20°C)
_EPS0 = 8.854e-12            # 真空介电常数 F/m
_MU0 = 1.2566e-6             # 真空磁导率 H/m
_C0 = 299792458.0            # 真空光速 m/s

# 串扰 (几何代理指标) 的参数
_CROSSTALK_SEARCH_FALLBACK_MM = 0.6   # BRD 未声明线距时的同层搜索阈值
_CROSSTALK_WARN_COUPLING_MM = 5.0     # 平行耦合长度超过它的线段对计入"需关注"
_CROSSTALK_TOP_N = 50                 # 明细里最多列多少对
_STACKUP_ASSUMED_T = f"假设叠层 (Er={_ASSUMED_ER}, h={_ASSUMED_H_MM}mm, t 见铜厚)"

# 明细行数上限 (Excel 报告要能看, 全板几万条图形不能全塞进去)
_MAX_IMPEDANCE_ROWS = 300
_MAX_NET_ROWS = 300

# 三个反复出现的说明, 抽成常量保证 8 项能力口径一致
_NET_LEVEL_NEED = "网络级的走线长度与线宽（每个网络的真实路径）"
_NET_LEVEL_HOWTO = ("Allegro: Tools > Report > Etch Report，或用 Sigrity 做 net-based 提取")
_NO_NET_OWNERSHIP = ("内嵌快照的 ETCH 图形**不带网络归属**, 只有几何; 本项算的是"
                     "「把整层铜折成一条等效导线」的板级量级, 不是任何具体网络的电气参数")
_SIGRITY_HOWTO = ("Cadence Sigrity (PowerSI / SystemSI) 或第三方场求解器做真正的 "
                  "SI 提取; Allegro PCB Editor 17.2+ 的 Analyze 工作流只覆盖部分能力, "
                  "完整的阻抗/串扰/眼图/PDN 分析需要额外 Sigrity 授权或分析模块")


# --------------------------------------------------------------------------
# 共用小工具
# --------------------------------------------------------------------------
def _notes(ctx: Context) -> dict:
    """二进制制程说明文本里抓到的 notes (板厚/铜厚/表面处理)。读不到给空字典。"""
    return ((ctx.design.get("raw_note") or {}).get("notes") or {})


def _copper_thickness(ctx: Context) -> Tuple[float, str, bool]:
    """铜厚 (mm) + 出处 + 是否为假设值。

    1oz ≈ 35µm; .brd 的制程说明文本里有时会写 "1 OZ COPPER", 抓不到就按 1oz 假设
    并在结论里说明 (不能用假设值冒充实测值)。
    """
    oz = _notes(ctx).get("copper_oz")
    try:
        oz = float(oz) if oz is not None else None
    except (TypeError, ValueError):
        oz = None
    if oz and oz > 0:
        return (round(oz * _OZ_TO_MM, 4),
                f"二进制制程说明文本 copper_oz={oz:g} (1oz≈{_OZ_TO_MM * 1000:.0f}µm)",
                False)
    return (round(_ASSUMED_COPPER_OZ * _OZ_TO_MM, 4),
            f"BRD 未读到 copper_oz, 按 {_ASSUMED_COPPER_OZ:g}oz"
            f"≈{_ASSUMED_COPPER_OZ * _OZ_TO_MM * 1000:.0f}µm 假设",
            True)


def _layer_is_outer(name: str, layer_count: Optional[int]) -> Optional[bool]:
    """层是不是外层 (决定用微带还是带状线公式)。判不出来返回 None。"""
    up = (name or "").upper().strip()
    if up in ("TOP", "BOTTOM"):
        return True
    if geom.is_outer_layer(name, layer_count):
        return True
    idx = geom.layer_index(name)
    if idx == 1:
        return True
    if idx is not None and idx > 1:
        return False
    return None


def _etch_rows(ctx: Context) -> List[dict]:
    """每层铜箔的几何汇总: 长度 / 平均线宽 / 线宽种类 / 是否外层。

    这是板级电气量级估算的共同输入: 快照没有网络归属, 拿不到每个网络的真实路径,
    只能按层把铜箔长度与平均线宽折成一条等效导线来估量级。
    """
    rows: List[dict] = []
    for layer, segs in sorted(ctx.segments_by_layer(["etch"]).items()):
        widths = [s["width_mm"] for s in segs
                  if s.get("has_width") and s["width_mm"] > 0]
        ws = geom.width_summary(segs)
        rows.append({
            "层": layer,
            "图形数": len(segs),
            "走线总长mm": geom.total_length_mm(segs),
            "平均线宽mm": round(sum(widths) / len(widths), 4) if widths else None,
            "中位线宽mm": ws.get("median_mm"),
            "线宽种类": list(ws.get("distinct") or []),
            "外层": _layer_is_outer(layer, ctx.layer_count),
        })
    return rows


def _no_geometry(cap: Capability, what: str) -> Result:
    """几何缺失时的统一写法: unavailable + 说清缺什么/怎么补。"""
    return unavailable(cap, f"内嵌快照里没有 ETCH 铜箔图形, 无法{what}",
                       "提供含 ETCH 图形的内嵌设计快照, 或 Allegro 的 Etch Report")


def _widths_unavailable(cap: Capability) -> Result:
    """ETCH 图形没有线宽字段: 公式里的线宽 w 取不到, 如实 unavailable。"""
    res = Result(cap, UNAVAILABLE,
                 "快照 ETCH 图形没有线宽字段 (width=0), 电气估算里的线宽 w 取不到")
    res.need("每层的走线线宽 (ETCH 图形的 width 字段)",
             "Allegro: Tools > Report > Etch Report 导出线宽清单, "
             "或重新导出一份含线宽的内嵌设计快照")
    return res


def _diff_gap(ctx: Context) -> Tuple[Optional[float], str]:
    """差分对的声明间距 (PhysicalCSet 的 DIFFP_PRIMARY_GAP) + 出处。"""
    for cset, entry in (ctx.design.get("physical_csets") or {}).items():
        gap = entry.get("diff_gap_mm")
        if gap:
            return float(gap), f"objects/PhysicalCSet.xml / {cset} 的 DIFFP_PRIMARY_GAP"
    return None, ""


def _assumption_lines(t_mm: float, t_source: str) -> List[str]:
    """叠层假设逐条列出来 (进 warnings, 也会被指标引用)。"""
    return [
        f"假设 1: 介电常数 Er={_ASSUMED_ER} (.brd 里没有叠层介质参数, 按 FR-4 典型值取)",
        f"假设 2: 单层介质厚度 h={_ASSUMED_H_MM}mm "
        f"(外层=走线到最近参考层, 内层=带状线到最近参考平面)",
        f"假设 3: 铜厚 t={t_mm}mm ({t_source})",
        "假设 4: 未计入阻焊层、铜箔表面粗糙度、蚀刻梯形截面、参考平面开槽/跨分割",
        f"假设 5: 有效介电常数取 微带 {_ER_EFF_MICROSTRIP} / 带状线 {_ER_EFF_STRIPLINE}",
    ]


# --------------------------------------------------------------------------
# 阻抗公式 (IPC-2141 经典式; 只在假设叠层下给量级, 不做验收)
# --------------------------------------------------------------------------
def _microstrip_z0(w_mm: float, h_mm: float, t_mm: float, er: float) -> Optional[float]:
    """外层微带: Z0 = 87/√(Er+1.41) · ln(5.98h/(0.8w+t))。"""
    if w_mm <= 0 or h_mm <= 0 or t_mm <= 0:
        return None
    arg = 5.98 * h_mm / (0.8 * w_mm + t_mm)
    if arg <= 1.0:      # 对数非正, 公式已不适用, 不给数
        return None
    return 87.0 / math.sqrt(er + 1.41) * math.log(arg)


def _stripline_z0(w_mm: float, h_mm: float, t_mm: float, er: float) -> Optional[float]:
    """内层带状线: Z0 = 60/√Er · ln(4h/(0.67πw(0.8+t/w)))。"""
    if w_mm <= 0 or h_mm <= 0 or t_mm <= 0:
        return None
    arg = 4.0 * h_mm / (0.67 * math.pi * w_mm * (0.8 + t_mm / w_mm))
    if arg <= 1.0:
        return None
    return 60.0 / math.sqrt(er) * math.log(arg)


def _delay_ps_per_mm(er_eff: float) -> float:
    """每毫米传播延迟 (ps/mm) = √Er_eff / c。"""
    return round(math.sqrt(er_eff) / _C0 * 1e12 / 1000.0, 4)


# --------------------------------------------------------------------------
# 串扰用: 平行耦合长度 (几何代理指标)
# --------------------------------------------------------------------------
def _principal_dir(pts: List[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    """折线的主方向 = 最长那一段的单位方向; 没有长度时返回 None。"""
    best, best_len = None, 0.0
    for i in range(len(pts) - 1):
        dx = pts[i + 1][0] - pts[i][0]
        dy = pts[i + 1][1] - pts[i][1]
        dist = math.hypot(dx, dy)
        if dist > best_len:
            best_len, best = dist, (dx / dist, dy / dist)
    return best


def _projection_overlap(pts_a, pts_b, direction) -> float:
    """两条折线在同一条方向轴上的投影重叠长度 (mm)。"""
    ta = [p[0] * direction[0] + p[1] * direction[1] for p in pts_a]
    tb = [p[0] * direction[0] + p[1] * direction[1] for p in pts_b]
    lo = max(min(ta), min(tb))
    hi = min(max(ta), max(tb))
    return max(0.0, hi - lo)


def _coupling_length_mm(pts_a, pts_b) -> float:
    """平行耦合长度 (mm): 两条折线在彼此方向上的投影重叠长度, 取较小值 (保守)。

    这只是**几何代理指标**: 真正的耦合量还要看线宽、介质厚度、参考平面、
    信号上升时间与端接方式, 必须由场求解器给出。
    """
    dir_a = _principal_dir(pts_a)
    dir_b = _principal_dir(pts_b)
    if not dir_a or not dir_b:
        return 0.0
    return round(min(_projection_overlap(pts_a, pts_b, dir_a),
                     _projection_overlap(pts_a, pts_b, dir_b)), 3)


# ==========================================================================
# 3.1 Impedance Analysis —— 特性阻抗
# ==========================================================================
def cap_impedance(ctx: Context) -> Result:
    cap = CATALOG_MAP["electrical.impedance"]
    segs = ctx.segments(["etch"])
    if not segs:
        return _no_geometry(cap, "取走线线宽做阻抗估算")
    rows = _etch_rows(ctx)
    distinct_total = sum(len(r["线宽种类"]) for r in rows)
    if not distinct_total:
        return _widths_unavailable(cap)

    t_mm, t_source, _t_assumed = _copper_thickness(ctx)
    res = Result(cap, INFO, "")

    # ---- 假设条件: 逐条进 warnings, 并进 metrics ----
    for line in _assumption_lines(t_mm, t_source):
        res.warn(line)
    res.metric("假设介电常数 Er", _ASSUMED_ER, "",
               "本工具假设值 (FR-4 典型), .brd 无叠层数据", "low")
    res.metric("假设介质厚度 h", _ASSUMED_H_MM, "mm",
               "本工具假设值, .brd 无叠层数据", "low")
    res.metric("铜厚 t", t_mm, "mm", t_source, "low")
    res.metric("参与估算的铜箔层数", len(rows), "层", "内嵌快照 ETCH 图形")
    res.metric("参与估算的线宽种类", distinct_total, "种", "各层 ETCH 图形的 distinct 线宽")

    # ---- 对每种线宽算一遍 ----
    zs: List[float] = []
    n_rows = 0
    for row in rows:
        outer = row["外层"]
        if outer is None:
            kind, formula = "单端-层序未知(按微带)", "微带 87/√(Er+1.41)·ln(5.98h/(0.8w+t))"
        elif outer:
            kind, formula = "单端-外层(微带)", "微带 87/√(Er+1.41)·ln(5.98h/(0.8w+t))"
        else:
            kind, formula = "单端-内层(带状线)", "带状线 60/√Er·ln(4h/(0.67πw(0.8+t/w)))"
        for w in row["线宽种类"]:
            if not w or w <= 0 or n_rows >= _MAX_IMPEDANCE_ROWS:
                continue
            if outer is False:
                z0 = _stripline_z0(w, _ASSUMED_H_MM, t_mm, _ASSUMED_ER)
            else:
                z0 = _microstrip_z0(w, _ASSUMED_H_MM, t_mm, _ASSUMED_ER)
            if z0 is None:
                note = "该线宽/介质组合超出公式适用区间 (对数为非正), 不给阻抗值"
            elif outer is None:
                note = "层序未标识, 暂按微带公式处理"
            else:
                note = ""
            if z0 is not None:
                zs.append(round(z0, 2))
            res.add(类型=kind, 层=row["层"], 线宽mm=w, 间距mm=None,
                    阻抗ohm=(round(z0, 2) if z0 is not None else None),
                    公式=formula,
                    假设=f"Er={_ASSUMED_ER}, h={_ASSUMED_H_MM}mm, t={t_mm}mm",
                    说明=note)
            n_rows += 1

    # ---- 差分对: 有对数 + 有声明间距才报, 只给定性关系 ----
    diff_count = ctx.design.get("diff_pair_count") or len(facts.diff_pair_names(ctx))
    diff_gap, diff_gap_src = _diff_gap(ctx)
    if diff_count:
        res.metric("差分对数量", diff_count, "对", "objects/DiffPair.xml", "high")
    if diff_count and diff_gap:
        ref_w = ctx.design.get("min_line_width_mm") or (
            rows[0]["中位线宽mm"] if rows else None)
        res.add(类型="差分对", 层="(全板)", 线宽mm=ref_w, 间距mm=diff_gap,
                阻抗ohm=None,
                公式="不作精确计算, 只给「差分阻抗 ≈ 2 × 单端奇模阻抗」的定性关系",
                假设=f"间距取 {diff_gap_src}; 线宽取声明最小线宽/该层中位线宽",
                说明=f"共 {diff_count} 对差分对; 差分阻抗要把线宽/间距/介质厚度三者联立, "
                     f"本工具只报已知条件, 不编造精确公式")
        res.metric("差分对声明间距", diff_gap, "mm", diff_gap_src, "high")
        res.metric("差分对参考线宽", ref_w, "mm",
                   "PhysicalCSet 最小线宽 / 该层中位线宽", "low")
        res.warn("差分阻抗 ≈ 2 × 单端奇模阻抗 只是定性关系: 奇模阻抗本身随间距/介质厚度"
                 "变化, 真正 Zdiff 必须用场求解器 (Sigrity/HFSS) 提取")
    elif diff_count:
        res.warn(f"板上有 {diff_count} 对差分对, 但 PhysicalCSet 里没有 DIFFP_PRIMARY_GAP, "
                 f"拿不到声明间距, 差分对的几何条件不全, 不做任何差分阻抗估算")

    res.columns = ["类型", "层", "线宽mm", "间距mm", "阻抗ohm", "公式", "假设", "说明"]
    zrange = f"{min(zs)} ~ {max(zs)}" if zs else "无法给出"
    if zs:
        res.metric("单端阻抗估算范围", zrange, "ohm",
                   f"IPC-2141 经验公式 + {_STACKUP_ASSUMED_T}", "low")
    res.metric("出数的线宽条数", len(zs), "条", "同上")
    res.summary = (
        f"用 IPC-2141 经验公式 + 假设叠层 (Er={_ASSUMED_ER}, h={_ASSUMED_H_MM}mm, "
        f"t={t_mm}mm) 对 {len(rows)} 层共 {n_rows} 种线宽做阻抗**估算**: "
        f"单端阻抗约 {zrange} ohm。.brd 里没有介质厚度与 Dk, 这些值来自假设而非设计, "
        f"**不可用于验收**; 真值需要叠层报告 + 场求解器。")
    res.need("每层介质厚度与介电常数 Dk", ctx.howto_stackup())
    res.need("受控阻抗线的目标阻抗值与对应网络清单",
             "Allegro: Setup > Constraints > Electrical (Impedance), 导出约束报告")
    res.warn("本项是估算, 不是判定: 同一线宽既可能是受控阻抗线, 也可能是普通信号线, "
             "快照图形不带网络归属, 分不开")
    res.warn("快照 ETCH 图形含铜皮/平面分割, 其线宽不是走线线宽, 会让统计偏粗")
    res.warn("明细里的线宽是各层 ETCH 图形的统计档位 (distinct), 不代表某条网络的线宽")
    if not ctx.scale_confident:
        res.warn(facts.geometry_warning(ctx) or "")
    res.source("内嵌快照 ETCH 图形线宽 + IPC-2141 经验公式 + 本工具假设叠层 (非真实叠层)")
    res.extra["assumed_er"] = _ASSUMED_ER
    res.extra["assumed_h_mm"] = _ASSUMED_H_MM
    res.extra["copper_t_mm"] = t_mm
    res.extra["z0_range_ohm"] = [min(zs), max(zs)] if zs else None
    return res


# ==========================================================================
# 3.2 / 3.3 / 3.4 / 3.5 板级量级估算: 电容 / 电感 / 电阻 / 延迟
#
# 这四项的共同口径: 快照只有「每层铜箔的几何」, 没有网络归属, 所以只能把每层铜
# 折成一条 w×t 的等效导线做板级量级估算, **做不了网络级参数**。每项的结论里
# 都必须写明这一点, 并 need() 网络级数据。
# ==========================================================================
def _estimate_base(ctx: Context, cap: Capability) -> Tuple[Optional[Result], List[dict],
                                                           float, str]:
    """四项估算的公共前置: 取几何、取铜厚, 判断能不能算。

    返回 (直接返回的 Result 或 None, 可用层列表, 铜厚 mm, 铜厚出处)。
    """
    all_rows = _etch_rows(ctx)
    if not all_rows:
        return _no_geometry(cap, "做板级电气量级估算"), [], 0.0, ""
    usable = [r for r in all_rows if r["走线总长mm"] > 0 and r["平均线宽mm"]]
    if not usable:
        return _widths_unavailable(cap), [], 0.0, ""
    t_mm, t_source, _assumed = _copper_thickness(ctx)
    return None, usable, t_mm, t_source


def _finish_estimate(res: Result, cap: Capability, ctx: Context, what: str,
                     rng: str, unit: str, formula: str, extra_note: str) -> Result:
    """四项估算的公共收尾: 结论口径、need、warnings、source 全部统一。"""
    res.status = INFO
    res.summary = (
        f"按每层铜箔几何做的**板级量级估算** ({formula}): 单层等效{what} "
        f"{rng} {unit}。{_NO_NET_OWNERSHIP}, 所以**做不了网络级参数**, "
        f"数值只用于横向比较各层量级, 不可用于验收。{extra_note}")
    res.need(_NET_LEVEL_NEED, _NET_LEVEL_HOWTO)
    res.need("叠层介质参数 (每层 Er 与介质厚度)", ctx.howto_stackup())
    res.warn("这是板级量级估算, 不是任何网络的真实参数: 快照没有网络归属, "
             "按层把铜箔长度与平均线宽折成一条等效导线算出来的")
    res.warn("快照 ETCH 图形含铜皮/平面分割, 长度与平均线宽都不是纯走线口径")
    res.warn("使用假设叠层: " + _STACKUP_ASSUMED_T)
    if not ctx.scale_confident:
        res.warn(facts.geometry_warning(ctx) or "")
    res.source("内嵌快照 ETCH 图形 (每层长度/平均线宽) + 假设叠层 " + _STACKUP_ASSUMED_T)
    return res


def cap_capacitance(ctx: Context) -> Result:
    """寄生电容 (板级量级): C ≈ eps0 · Er · (w·L) / h。"""
    cap = CATALOG_MAP["electrical.capacitance"]
    early, usable, t_mm, t_source = _estimate_base(ctx, cap)
    if early is not None:
        return early
    res = Result(cap, INFO, "")
    res.metric("真空介电常数 eps0", _EPS0, "F/m", "物理常数", "high")
    res.metric("假设介电常数 Er", _ASSUMED_ER, "", "本工具假设值 (FR-4 典型)", "low")
    res.metric("假设介质厚度 h", _ASSUMED_H_MM, "mm", "本工具假设值", "low")
    res.metric("铜厚 t", t_mm, "mm", t_source, "low")

    caps_pf: List[float] = []
    for r in usable:
        L_m = r["走线总长mm"] / 1000.0
        w_m = r["平均线宽mm"] / 1000.0
        h_m = _ASSUMED_H_MM / 1000.0
        c_f = _EPS0 * _ASSUMED_ER * (w_m * L_m) / h_m
        c_pf = c_f * 1e12
        caps_pf.append(c_pf)
        res.add(层=r["层"], 图形数=r["图形数"], 走线总长mm=r["走线总长mm"],
                平均线宽mm=r["平均线宽mm"], 面积mm2=round(r["走线总长mm"] * r["平均线宽mm"], 4),
                电容pF=round(c_pf, 4), 公式="C ≈ eps0·Er·(w·L)/h",
                出处="内嵌快照 ETCH 图形 + 假设叠层", 置信度="low",
                说明="把该层所有铜折成一条 w×L 的平板折算到 h 的介质上")
    res.columns = ["层", "图形数", "走线总长mm", "平均线宽mm", "面积mm2", "电容pF",
                   "公式", "出处", "置信度", "说明"]
    rng = f"{round(min(caps_pf), 4)} ~ {round(max(caps_pf), 4)}" if caps_pf else "无法给出"
    res.metric("单层等效寄生电容范围", rng, "pF",
               f"C ≈ eps0·Er·(w·L)/h + {_STACKUP_ASSUMED_T}", "low")
    return _finish_estimate(res, cap, ctx, "寄生电容", rng, "pF",
                            "C ≈ eps0·Er·(w·L)/h",
                            "真实电容还要看参考平面的距离与完整度。")


def cap_inductance(ctx: Context) -> Result:
    """寄生电感 (板级量级): L ≈ mu0 · h · l / w。"""
    cap = CATALOG_MAP["electrical.inductance"]
    early, usable, t_mm, t_source = _estimate_base(ctx, cap)
    if early is not None:
        return early
    res = Result(cap, INFO, "")
    res.metric("真空磁导率 mu0", _MU0, "H/m", "物理常数", "high")
    res.metric("假设介质厚度 h", _ASSUMED_H_MM, "mm", "本工具假设值 (走线到参考平面的高度)", "low")
    res.metric("铜厚 t", t_mm, "mm", t_source, "low")

    l_nh: List[float] = []
    for r in usable:
        l_m = r["走线总长mm"] / 1000.0
        w_m = r["平均线宽mm"] / 1000.0
        h_m = _ASSUMED_H_MM / 1000.0
        ind_h = _MU0 * h_m * l_m / w_m
        ind_nh = ind_h * 1e9
        l_nh.append(ind_nh)
        res.add(层=r["层"], 图形数=r["图形数"], 走线总长mm=r["走线总长mm"],
                平均线宽mm=r["平均线宽mm"], 电感nH=round(ind_nh, 4),
                公式="L ≈ mu0·h·l/w", 出处="内嵌快照 ETCH 图形 + 假设叠层",
                置信度="low",
                说明="把该层所有铜折成一条 w×l、离参考平面 h 的回路")
    res.columns = ["层", "图形数", "走线总长mm", "平均线宽mm", "电感nH",
                   "公式", "出处", "置信度", "说明"]
    rng = f"{round(min(l_nh), 4)} ~ {round(max(l_nh), 4)}" if l_nh else "无法给出"
    res.metric("单层等效寄生电感范围", rng, "nH",
               f"L ≈ mu0·h·l/w + {_STACKUP_ASSUMED_T}", "low")
    return _finish_estimate(res, cap, ctx, "寄生电感", rng, "nH",
                            "L ≈ mu0·h·l/w",
                            "真实电感还要看回路面积 (返回路径是否连续)。")


def cap_resistance(ctx: Context) -> Result:
    """网络电阻 (板级量级): R = rho_cu · L / (w · t)。"""
    cap = CATALOG_MAP["electrical.resistance"]
    early, usable, t_mm, t_source = _estimate_base(ctx, cap)
    if early is not None:
        return early
    res = Result(cap, INFO, "")
    res.metric("铜的体电阻率 rho", _RHO_CU, "Ω·m", "铜 20°C 标准值", "high")
    res.metric("铜厚 t", t_mm, "mm", t_source, "low")

    ohms: List[float] = []
    for r in usable:
        L_m = r["走线总长mm"] / 1000.0
        w_m = r["平均线宽mm"] / 1000.0
        t_ = t_mm / 1000.0
        ohm = _RHO_CU * L_m / (w_m * t_)
        ohms.append(ohm)
        res.add(层=r["层"], 图形数=r["图形数"], 走线总长mm=r["走线总长mm"],
                平均线宽mm=r["平均线宽mm"], 铜厚mm=t_mm, 电阻ohm=round(ohm, 4),
                公式="R = rho·L/(w·t)", 出处="内嵌快照 ETCH 图形 + 铜电阻率",
                置信度="low", 说明="把该层所有铜折成一条 w×t 的直导线")
    res.columns = ["层", "图形数", "走线总长mm", "平均线宽mm", "铜厚mm", "电阻ohm",
                   "公式", "出处", "置信度", "说明"]
    rng = f"{round(min(ohms), 4)} ~ {round(max(ohms), 4)}" if ohms else "无法给出"
    res.metric("单层等效电阻范围", rng, "Ω",
               f"R = rho·L/(w·t), rho={_RHO_CU}", "low")
    return _finish_estimate(res, cap, ctx, "电阻", rng, "Ω",
                            "R = rho·L/(w·t)",
                            "真实网络电阻要把每个网络的路径长度与线宽逐段累加, "
                            "还要算过孔与铜皮。")


def cap_delay(ctx: Context) -> Result:
    """传播延迟 (板级量级): t = l · √Er_eff / c, 微带/带状线分别取 Er_eff。"""
    cap = CATALOG_MAP["electrical.delay"]
    early, usable, t_mm, t_source = _estimate_base(ctx, cap)
    if early is not None:
        return early
    res = Result(cap, INFO, "")
    res.metric("外层(微带)有效介电常数 Er_eff", _ER_EFF_MICROSTRIP, "",
               f"由假设 Er={_ASSUMED_ER} 按 (Er+1)/2 取", "low")
    res.metric("内层(带状线)有效介电常数 Er_eff", _ER_EFF_STRIPLINE, "",
               f"取假设 Er={_ASSUMED_ER}", "low")
    res.metric("真空光速 c", _C0, "m/s", "物理常数", "high")
    res.metric("外层每毫米延迟", _delay_ps_per_mm(_ER_EFF_MICROSTRIP), "ps/mm",
               "t = √Er_eff/c + 假设叠层", "low")
    res.metric("内层每毫米延迟", _delay_ps_per_mm(_ER_EFF_STRIPLINE), "ps/mm",
               "t = √Er_eff/c + 假设叠层", "low")
    res.metric("铜厚 t", t_mm, "mm", t_source, "low")

    delays_ns: List[float] = []
    for r in usable:
        outer = r["外层"]
        er_eff = _ER_EFF_MICROSTRIP if outer is not False else _ER_EFF_STRIPLINE
        kind = ("内层/带状线" if outer is False
                else ("外层/微带" if outer else "层序未知, 按微带"))
        l_m = r["走线总长mm"] / 1000.0
        delay_s = l_m * math.sqrt(er_eff) / _C0
        delay_ns = delay_s * 1e9
        delays_ns.append(delay_ns)
        res.add(层=r["层"], 类型=kind, 走线总长mm=r["走线总长mm"],
                有效Er=er_eff, 延迟ns=round(delay_ns, 5),
                每毫米ps=round(delay_ns * 1000.0 / max(r["走线总长mm"], 1e-9), 4),
                公式="t = l·√Er_eff/c", 出处="内嵌快照 ETCH 图形长度 + 假设 Er_eff",
                置信度="low", 说明="按该层总长算的传输延迟")
    res.columns = ["层", "类型", "走线总长mm", "有效Er", "延迟ns", "每毫米ps",
                   "公式", "出处", "置信度", "说明"]
    rng = f"{round(min(delays_ns), 5)} ~ {round(max(delays_ns), 5)}" if delays_ns else "无法给出"
    res.metric("单层等效延迟范围", rng, "ns",
               f"t = l·√Er_eff/c + {_STACKUP_ASSUMED_T}", "low")
    return _finish_estimate(res, cap, ctx, "传播延迟", rng, "ns",
                            "t = l·√Er_eff/c",
                            "真实延迟还受串扰、过孔 stub、参考平面跨分割影响。")


# ==========================================================================
# 3.6 Crosstalk Analysis —— 串扰 (几何代理指标)
# ==========================================================================
def cap_crosstalk(ctx: Context) -> Result:
    cap = CATALOG_MAP["electrical.crosstalk"]
    segs = ctx.segments(["etch"])
    if not segs:
        return _no_geometry(cap, "找同层近距离线段对做耦合分析")

    declared = ctx.design.get("min_spacing_mm")
    if declared:
        threshold = round(float(declared) * 3.0, 4)
        basis = f"BRD 声明的最小线距 {declared}mm × 3"
    else:
        threshold = _CROSSTALK_SEARCH_FALLBACK_MM
        basis = f"BRD 未声明线距, 取经验阈值 {_CROSSTALK_SEARCH_FALLBACK_MM}mm"

    scan = geom.same_layer_gaps(segs, threshold)
    res = Result(cap, INFO, "")
    res.metric("同层搜索阈值", threshold, "mm", basis)
    res.metric("参与计算的铜箔图形", scan["segment_count"], "段", "内嵌快照 ETCH 图形")
    res.metric("两两比较次数", scan["examined"], "次", "包围盒粗筛后的精确距离计算")

    pairs = scan["pairs"]
    if not pairs:
        res.summary = (f"在 {threshold}mm 搜索范围内 ({basis}) 没有找到同层近距离线段对; "
                       f"按几何代理指标看不存在平行耦合风险 —— 但这**不是串扰结论**, "
                       f"真正的串扰需要场求解器")
        res.need("真正的串扰分析 (耦合系数 / 近端远端串扰 / 上升时间)",
                 _SIGRITY_HOWTO)
        res.warn("几何代理只能说明「没有靠得很近的同层线段」, 不能说明串扰合格")
        res.source("内嵌快照 ETCH 图形 + 同层间距索引")
        return res

    scored = []
    for p in pairs:
        a, b = segs[p["a"]], segs[p["b"]]
        scored.append((_coupling_length_mm(a["pts"], b["pts"]), p, a, b))
    scored.sort(key=lambda item: -item[0])

    total = round(sum(item[0] for item in scored), 3)
    long_pairs = [item for item in scored if item[0] > _CROSSTALK_WARN_COUPLING_MM]
    for cl, p, a, b in scored[:_CROSSTALK_TOP_N]:
        res.add(层=p["layer"], 净间距mm=p["gap_mm"] + 0.0, 中心距mm=p["center_mm"] + 0.0,
                耦合长度mm=cl,
                位置="%.3f, %.3f ↔ %.3f, %.3f" % (geom.mid_point(a) + geom.mid_point(b)),
                线宽A_mm=p["a_width_mm"], 线宽B_mm=p["b_width_mm"])
    res.columns = ["层", "净间距mm", "中心距mm", "耦合长度mm", "位置",
                   "线宽A_mm", "线宽B_mm"]

    res.metric("近距离线段对", len(scored), "对", f"同层净间距 ≤ {threshold}mm ({basis})")
    res.metric("平行耦合总长度", total, "mm",
               "各对耦合长度求和 (折线在彼此方向上的投影重叠, 取两方向较小值)", "low")
    res.metric("最长耦合长度", scored[0][0] if scored else None, "mm",
               "同上", "low")
    res.metric(f"耦合长度 > {_CROSSTALK_WARN_COUPLING_MM}mm 的对数", len(long_pairs), "对",
               "本工具的 WARN 阈值 (几何代理, 非串扰判据)")

    if long_pairs:
        res.status = WARN
        top = long_pairs[0]
        res.summary = (
            f"几何代理指标: {len(scored)} 对同层线段净间距 ≤ {threshold}mm ({basis}), "
            f"累计平行耦合长度 {total}mm, 其中 {len(long_pairs)} 对的耦合长度超过 "
            f"{_CROSSTALK_WARN_COUPLING_MM}mm (最长 {top[0]}mm, 层 {top[1]['layer']}, "
            f"净间距 {top[1]['gap_mm']}mm)。这只说明「存在平行走线且耦合长度偏长」, "
            f"**真正的串扰必须用 Sigrity/场求解器算**, 本指标不能当串扰判定。")
    else:
        res.summary = (
            f"几何代理指标: {len(scored)} 对同层线段净间距 ≤ {threshold}mm ({basis}), "
            f"累计平行耦合长度 {total}mm, 没有耦合长度超过 "
            f"{_CROSSTALK_WARN_COUPLING_MM}mm 的线段对。这只是几何代理, "
            f"真正的串扰需要用 Sigrity/场求解器算。")

    res.need("真正的串扰分析 (耦合系数 / 近端远端串扰 / 信号上升时间与端接)",
             _SIGRITY_HOWTO)
    res.need(_NET_LEVEL_NEED, _NET_LEVEL_HOWTO)
    res.warn("**这是几何代理指标**: 耦合长度只是两条折线投影重叠的长度, "
             "没有任何电磁场计算, 不能当作串扰结论")
    res.warn("快照图形不带网络号: 同网络本来就连在一起的铜也会被算成「近距离对」, "
             "必须用 Allegro DRC 报告复核")
    res.warn("真正的串扰还取决于线宽/介质厚度/参考平面距离/上升时间/端接方式, "
             "几何代理一个都没算")
    if scan["truncated"]:
        res.warn("线段对数量超过上限, 计算已提前结束, 结果可能不完整")
    if scan["item_truncated"]:
        res.warn(f"明细只保留了前 {len(pairs)} 对, 耦合长度排名可能不完整")
    if not ctx.scale_confident:
        res.warn(facts.geometry_warning(ctx) or "")
    res.source("内嵌快照 ETCH 图形 + 同层间距索引 + 折线投影重叠 (几何代理)")
    res.extra["coupling_total_mm"] = total
    res.extra["long_pair_count"] = len(long_pairs)
    return res


# ==========================================================================
# 3.7 Signal Integrity —— SI 前置条件汇总 (不做 SI 判定)
# ==========================================================================
def cap_signal_integrity(ctx: Context) -> Result:
    cap = CATALOG_MAP["electrical.signal_integrity"]
    d = ctx.design
    segs = ctx.segments(["etch"])
    if not d.get("ok") and not segs:
        return unavailable(cap,
                           "BRD 没解析成功, 也没有内嵌快照几何, 拿不到任何 SI 相关条件",
                           "确认 .brd 完整可读 (17.x 及以后版本), "
                           "或提供 Allegro 导出的叠层报告与约束报告")

    stack = facts.stackup_rows(ctx)
    planes = [r for r in stack if "平面" in (r.get("用途") or "")]
    signals = [r for r in stack if "信号" in (r.get("用途") or "")]
    t_mm, t_source, t_assumed = _copper_thickness(ctx)
    diff_pairs = facts.diff_pair_names(ctx)
    diff_count = d.get("diff_pair_count") or len(diff_pairs)
    finishes = d.get("surface_finishes") or []
    etch_layers = ctx.etch_layers

    res = Result(cap, INFO, "")
    has_impedance = bool(d.get("impedance"))
    res.metric("受控阻抗声明",
               "已声明" if has_impedance else "未在制程说明里发现", "",
               "二进制制程说明文本 (CONTROLLED IMPEDANCE / IMPEDANCE TABLE)",
               "low")
    res.metric("差分对数量", diff_count, "对", "objects/DiffPair.xml", "high")
    res.metric("最小线宽", d.get("min_line_width_mm"), "mm",
               d.get("sources", {}).get("min_line_width_mm", {}).get(
                   "source", "PhysicalCSet.xml"), "high")
    res.metric("铜厚", t_mm, "mm", t_source, "low")
    res.metric("层数", ctx.layer_count, "层",
               d.get("layer_count_source") or "预览元数据", "high")
    res.metric("信号层", len(signals), "层", "按层名关键字判定", "medium")
    res.metric("平面层 (可作参考平面)", len(planes), "层", "按层名关键字判定", "medium")
    res.metric("快照里有铜箔的层", len(etch_layers), "层", "内嵌快照 ETCH 图形", "medium")
    res.metric("表面处理", "、".join(finishes) if finishes else None, "",
               "二进制制程说明文本", "low")
    res.metric("网络数", d.get("net_count") or 0, "个", "objects/Net.xml", "high")

    rows = [
        {"SI 前置条件": "受控阻抗声明", "取值": "已声明" if has_impedance else "未发现",
         "来源": "制程说明文本",
         "说明": "只说明文本里提到过阻抗控制, 没有目标阻抗值与对应网络"},
        {"SI 前置条件": "差分对", "取值": f"{diff_count} 对", "来源": "objects/DiffPair.xml",
         "说明": "只有对数与名字, 没有每对的线宽/间距/目标阻抗"},
        {"SI 前置条件": "最小线宽", "取值": d.get("min_line_width_mm"),
         "来源": "PhysicalCSet.xml", "说明": "板级最严值, 不是每条 SI 线的线宽"},
        {"SI 前置条件": "铜厚", "取值": f"{t_mm}mm", "来源": t_source,
         "说明": "假设值" if t_assumed else "来自制程说明文本, 置信度低"},
        {"SI 前置条件": "层数 / 平面层",
         "取值": f"{ctx.layer_count or '—'} 层 / 平面 {len(planes)} 层",
         "来源": "预览元数据 + 层名关键字",
         "说明": "平面层可作参考平面, 但参考平面的完整度要看铜皮填充"},
        {"SI 前置条件": "叠层介质参数 (Er / 介质厚度)", "取值": None,
         "来源": "—", "说明": "**.brd 里没有**, 是 SI 计算的前提, 必须提供叠层报告"},
        {"SI 前置条件": "目标阻抗 / 拓扑 / 端接", "取值": None, "来源": "—",
         "说明": "约束管理器里的 Electrical 约束, .brd 归档对象里读不到"},
    ]
    res.add_all(rows, ["SI 前置条件", "取值", "来源", "说明"])

    res.summary = (
        f"汇总 .brd 里能拿到的 SI 前置条件: 受控阻抗"
        f"{'已声明' if has_impedance else '未发现'}、差分对 {diff_count} 对、"
        f"最小线宽 {d.get('min_line_width_mm') or '—'}mm、铜厚 {t_mm}mm、"
        f"{ctx.layer_count or '—'} 层 (平面 {len(planes)} 层可作参考平面)、"
        f"表面处理 {'、'.join(finishes) if finishes else '—'}。"
        f"本项**不做任何 SI 判定** (没有判定口径, 也不允许拿估算冒充): "
        f"Cadence 从 17.2 起在 PCB Editor 里集成了部分 Sigrity 分析能力 (Analyze 工作流), "
        f"但完整的 SI 仿真 (阻抗/串扰/眼图/PDN) 需要额外 Sigrity 授权或分析模块。")
    res.need("每层介质厚度与介电常数 Dk", ctx.howto_stackup())
    res.need("Sigrity / 场求解器授权与完整的 SI 仿真流程", _SIGRITY_HOWTO)
    res.need(_NET_LEVEL_NEED, _NET_LEVEL_HOWTO)
    res.need("受控阻抗线的目标阻抗值与对应网络清单",
             "Allegro: Setup > Constraints > Electrical (Impedance), 导出约束报告")
    res.warn("本项只汇总前置条件, 不做 SI 判定; 缺叠层介质参数与 Sigrity 授权, "
             "任何 PASS/FAIL 都是假的")
    res.warn("Cadence 从 17.2 起在 PCB Editor 里集成了部分 Sigrity 分析能力 "
             "(Analyze 工作流), 但完整的 SI 仿真需要额外 Sigrity 授权或分析模块")
    if not segs and d.get("ok"):
        res.warn("没有内嵌快照 ETCH 图形, 层/线宽这类几何信息只能靠归档对象与说明文本")
    res.source("objects/Net.xml / DiffPair.xml / PhysicalCSet.xml + 二进制制程说明文本")
    return res


# ==========================================================================
# 3.8 Net Analysis —— 网络清单与电气参数
# ==========================================================================
def _net_class(net: str, declared_pairs: set, guessed_members: set,
               power_ground: set) -> str:
    if net in power_ground:
        return "电源/地"
    if net in declared_pairs or net in guessed_members:
        return "差分对"
    return "信号"


def cap_net_analysis(ctx: Context) -> Result:
    cap = CATALOG_MAP["electrical.net_analysis"]
    names = facts.net_names(ctx)
    netlist = ctx.exports.get("netlist") or {}
    nl_ok = bool(netlist.get("ok"))

    if not names and not nl_ok:
        declared = ctx.design.get("net_count") or 0
        res = unavailable(
            cap,
            "读不到网络名 (归档里没有 objects/Net.xml 节点名), 也没有导入网表, "
            "网络清单与网络间的连接关系都拿不到",
            "提供 Allegro 第三方网表 (pstxnet.dat) 或 IPC-D-356, "
            "或确认 .brd 是 17.x 及以后版本 (16.x 纯二进制没有 Net.xml 对象)")
        if declared:
            res.metric("BRD 元数据里的网络数", declared, "个",
                       "objects/Net.xml 节点数 (节点名读不到)", "medium")
        res.warn("本工具没有网表时**不会**用二进制文本正则去猜网络: "
                 "那样会把器件名、层名当成网络, 结论是错的")
        return res

    res = Result(cap, INFO, "")
    declared_count = ctx.design.get("net_count")
    declared_pairs = set(facts.diff_pair_names(ctx))
    diff_count = ctx.design.get("diff_pair_count") or len(declared_pairs)
    guessed = facts.diff_pair_guess(names)
    guessed_members = {n for pair in guessed for n in pair}
    pg = set(facts.power_ground_nets(names))
    base = names or sorted((netlist.get("nets") or {}).keys())

    res.metric("网络数", declared_count or len(base), "个",
               "objects/Net.xml 节点", "high" if names else "medium")
    if diff_count:
        res.metric("差分对数量", diff_count, "对", "objects/DiffPair.xml", "high")
    res.metric("电源/地网络", len(pg), "个",
               "按网络名前缀识别 (GND/VCC/VDD/数字+V)", "medium")
    if guessed:
        res.metric("按 _P/_N 后缀推断出的差分对", len(guessed), "对",
                   "本工具按命名后缀配对 (推断, 非 DiffPair.xml 声明)", "low")

    if nl_ok:
        nets = netlist.get("nets") or {}
        counts = {n: len(v) for n, v in nets.items()}
        values = list(counts.values())
        res.metric("网表网络数", netlist.get("count") or len(nets), "个",
                   f"导入的网表 {netlist.get('source', '')}", "high")
        res.metric("网表引脚总数", netlist.get("pin_count") or sum(values), "个",
                   f"导入的网表 {netlist.get('source', '')}", "high")
        if counts:
            top = max(counts, key=lambda n: (counts[n], n))
            least = min(counts, key=lambda n: (counts[n], n))
            res.metric("引脚最多的网络", f"{top} ({counts[top]} 引脚)", "",
                       "导入的网表", "high")
            res.metric("引脚最少的网络", f"{least} ({counts[least]} 引脚)", "",
                       "导入的网表", "high")
            res.metric("平均引脚数", round(sum(values) / len(values), 2), "引脚/网络",
                       "导入的网表", "high")
        shown = sorted(counts, key=lambda n: (-counts[n], n))[:_MAX_NET_ROWS]
        for net in shown:
            res.add(网络名=net,
                    类别=_net_class(net, declared_pairs, guessed_members, pg),
                    引脚数=counts[net],
                    差分对=("是(声明)" if net in declared_pairs
                          else ("是(按 _P/_N 推断)" if net in guessed_members else "")),
                    电源地=("是" if net in pg else ""))
        if len(counts) > len(shown):
            res.warn(f"网络明细只列了引脚数最多的 {len(shown)} 个 (共 {len(counts)} 个)")
        res.source(f"objects/Net.xml 节点名 + 导入的网表 {netlist.get('source', '')}")
        res.summary = (
            f"网络 {declared_count or len(base)} 个, 其中差分对 {diff_count} 对、"
            f"电源/地 {len(pg)} 个; 已导入网表 ({netlist.get('source', '')}): "
            f"{len(counts)} 个网络 / {netlist.get('pin_count') or sum(values)} 个引脚, "
            f"引脚最多的是 {max(counts, key=lambda n: (counts[n], n)) if counts else '—'}。"
            f"网络名来自 objects/Net.xml, 连接关系与引脚数来自网表。")
    else:
        for net in sorted(pg)[:100]:
            res.add(网络名=net, 类别="电源/地", 引脚数=None, 差分对="", 电源地="是")
        res.need("网表 (网络间的电气连接关系与每个网络的引脚清单)",
                 "Allegro: File > Export > Nets 导出 pstxnet.dat, "
                 "或提供 IPC-D-356 (IPC-356A) 测试网表")
        res.warn("网络名来自 objects/Net.xml; 网络之间的电气连接关系 (谁和谁同网、"
                 "每个网络几个引脚) 只在网表里, .brd 的归档对象里没有。"
                 "本工具在**没有网表时不猜网络连接**: 不用 src.pcb_reader._extract_nets "
                 "那类文本正则去扫二进制 (会把器件名、层名当成网络), 所以引脚数一律留空。")
        res.summary = (
            f"网络 {declared_count or len(base)} 个, 其中差分对 {diff_count} 对、"
            f"电源/地 {len(pg)} 个。网络名来自 objects/Net.xml; "
            f"网络间的电气连接关系与引脚数需要网表, 本次没有导入, 未做任何推断。")
    res.columns = ["网络名", "类别", "引脚数", "差分对", "电源/地"]
    res.need(_NET_LEVEL_NEED, _NET_LEVEL_HOWTO)
    if not ctx.scale_confident and ctx.has_snapshot:
        res.warn(facts.geometry_warning(ctx) or "")
    return res


# ==========================================================================
CATALOG: List[Capability] = [
    Capability("electrical.impedance", "electrical", "Impedance Analysis", "特性阻抗",
               "按每层线宽与假设叠层, 用 IPC-2141 公式估算单端/差分特性阻抗",
               cap_impedance, "snapshot",
               ["内嵌快照 ETCH 图形线宽", "假设叠层 (Er/h/t)",
                "PhysicalCSet.xml 差分间距"],
               ["每种线宽的阻抗估算明细", "差分对条件", "假设条件清单"]),
    Capability("electrical.capacitance", "electrical", "Capacitance Analysis", "寄生电容",
               "按每层铜箔几何与假设叠层估算板级寄生电容 (非网络级)",
               cap_capacitance, "snapshot",
               ["内嵌快照 ETCH 图形长度与线宽", "假设叠层 (Er/h)"],
               ["每层等效电容估算"]),
    Capability("electrical.inductance", "electrical", "Inductance Analysis", "寄生电感",
               "按每层铜箔几何与假设介质厚度估算板级寄生电感 (非网络级)",
               cap_inductance, "snapshot",
               ["内嵌快照 ETCH 图形长度与线宽", "假设介质厚度 h"],
               ["每层等效电感估算"]),
    Capability("electrical.resistance", "electrical", "Resistance Analysis", "网络电阻",
               "按每层铜箔长度/平均线宽与铜厚估算板级电阻 (非网络级)",
               cap_resistance, "snapshot",
               ["内嵌快照 ETCH 图形长度与线宽", "铜厚 copper_oz"],
               ["每层等效电阻估算"]),
    Capability("electrical.delay", "electrical", "Delay Analysis", "传播延迟",
               "按每层总长与假设有效介电常数估算传输延迟 (非网络级)",
               cap_delay, "snapshot",
               ["内嵌快照 ETCH 图形长度", "假设 Er_eff"],
               ["每层等效延迟估算", "每毫米延迟"]),
    Capability("electrical.crosstalk", "electrical", "Crosstalk Analysis", "串扰",
               "找同层近距离线段对并估算平行耦合长度 (几何代理指标, 非串扰判定)",
               cap_crosstalk, "snapshot",
               ["内嵌快照 ETCH 图形", "SpacingCSet.xml 最小线距"],
               ["耦合最长的线段对", "平行耦合总长度"]),
    Capability("electrical.signal_integrity", "electrical", "Signal Integrity",
               "信号完整性分析",
               "汇总 .brd 里可获得的 SI 前置条件; 完整 SI 仿真需要 Sigrity 授权",
               cap_signal_integrity, "external",
               ["Net.xml / DiffPair.xml / PhysicalCSet.xml", "制程说明文本",
                "Sigrity 授权与叠层介质参数"],
               ["SI 前置条件清单", "缺口清单"]),
    Capability("electrical.net_analysis", "electrical", "Net Analysis", "网络电气参数",
               "网络清单、差分对与电源/地网络; 引脚数等连接关系需要网表",
               cap_net_analysis, "brd",
               ["objects/Net.xml", "objects/DiffPair.xml", "网表 (可选)"],
               ["网络清单", "网络分类统计", "引脚数统计"]),
]
CATALOG_MAP: Dict[str, Capability] = {c.id: c for c in CATALOG}

"""
BRD-AI: DFM 规则引擎 (结构化问题模型)
=============================================
将设计参数/制程能力比对结果统一为「DFM 问题 (Issue)」, 与 Valor DFM 报告
(参考 S-860-000883_odbjob_Rev_A-V1.0_1_DFM_Report.xlsm) 的字段对齐:

  问题序号 / 规则名称 / 涉及层 / 从…开始 / 到…结束 / 测量值mm / 规则值mm /
  X位置mm / Y位置mm / 严重度 / 状态 / 问题描述 / 专家建议

严重度 (severity) 分级: 危险 / 高 / 中 / 低 / 关闭
覆盖范围:
  - 制造工艺 DFM (Fabrication): 线路/钻孔/孔环/纵横比/阻焊/丝印/塞孔/覆盖膜/表面处理
  - 组装工艺 DFM (Assembly): 器件间距/基准点/测试点/焊盘对称性/极性/BGA
  - 拼板与成本 (Panelization): 拼板尺寸/工艺边/Mark/定位孔/利用率
  - 风险与良率评分 (risk score)

本模块不解析 Gerber/ODB++ 几何, 输入为设计参数字典 (与 dfm_engine.analyze_design 一致),
便于后续接入 fab_data_reader (Gerber/ODB++/NC Drill) 产出的几何统计量。
"""
from typing import Any, Dict, List, Optional

MIL_TO_MM = 0.0254

SEVERITY_LEVELS = ["危险", "高", "中", "低", "关闭"]
SEVERITY_ORDER = {s: i for i, s in enumerate(SEVERITY_LEVELS)}
SEVERITY_LABEL_EN = {"危险": "Critical", "高": "High", "中": "Medium", "低": "Low", "关闭": "Closed"}
SEVERITY_COLOR = {
    "危险": "FFC7CE",
    "高": "FFD8A8",
    "中": "FFEB9C",
    "低": "DDEBF7",
    "关闭": "E2EFDA",
}
SEVERITY_WEIGHT = {"危险": 10.0, "高": 5.0, "中": 2.0, "低": 0.5, "关闭": 0.0}

STATUS_PASS = "pass"
STATUS_WARN = "warn_limit"
STATUS_FAIL = "fail"
STATUS_INFO = "info"

STATUS_TO_SEVERITY = {
    STATUS_FAIL: "危险",
    STATUS_WARN: "高",
    STATUS_INFO: "低",
}

CATEGORY_PREFIX = {
    "线路": "FAB-TR", "钻孔": "FAB-DR", "阻焊": "FAB-SM", "丝印": "FAB-SK",
    "塞孔": "FAB-PL", "覆盖膜": "FAB-CV", "测试": "FAB-TS", "压合": "FAB-LM",
    "板厚": "FAB-LM", "表面处理": "FAB-SF", "锣型": "FAB-RT", "补强": "FAB-ST",
    "拼板": "PNL", "组装": "ASM",
}
DEFAULT_PREFIX = "DFM"

DIRECTION_HINT = {"线路": "top", "钻孔": "all", "阻焊": "sm", "丝印": "silk",
                  "拼板": "board", "组装": "assy"}

SUGGESTIONS = {
    "最小线宽": "增大线宽至制程能力以上，或改用更高能力等级/板厂",
    "最小线距": "增大线距至制程能力以上，或改用更高能力等级/板厂",
    "最小机械钻孔": "增大机械钻孔孔径至制程能力以上",
    "最大机械钻孔": "减小机械钻孔孔径至制程能力以内",
    "最小激光孔": "增大激光孔径至制程能力以上，或与板厂评估专用治具",
    "最小激光孔环": "增大激光孔单边孔环至制程能力以上",
    "单边孔环(机械孔)": "增大机械孔单边孔环至制程能力以上",
    "不同网络孔壁间距": "增大不同网络孔壁间距，降低 CAF 风险",
    "NPTH孔到板边": "增大 NPTH 孔壁到板边距离，避免破孔",
    "内层线路到板边": "增大内层线路到板边距离",
    "外层线路到板边": "增大外层线路到板边距离",
    "最小阻焊桥": "加宽阻焊桥，避免焊接锡桥短路",
    "阻焊开窗最小尺寸": "增大阻焊开窗尺寸，保证锡膏释放率",
    "阻焊油墨厚度": "调整阻焊油墨厚度至能力范围",
    "最小文字线宽": "增大丝印线宽至制程能力以上",
    "最小文字间隙": "增大丝印间隙至制程能力以上",
    "最小字符尺寸": "增大丝印字符尺寸至制程能力以上",
    "丝印到阻焊": "增大丝印到阻焊距离",
    "丝印离成型线": "增大丝印到成型线距离",
    "树脂塞孔条件": "调整孔径/板厚满足树脂塞孔条件，或改用板厂确认的塞孔工艺",
    "树脂塞孔深度": "提高树脂塞孔深度，或与板厂确认真空塞孔能力",
    "油墨塞孔深度": "提高油墨塞孔深度至能力要求",
    "塞孔纵横比": "减小板厚孔径比，或更换塞孔能力更强的板厂",
    "覆盖膜开窗最小间距": "增大覆盖膜开窗间距",
    "开窗边缘到焊盘距离": "增大覆盖膜开窗边缘到焊盘距离",
    "开窗边缘到线路距离": "增大覆盖膜开窗边缘到线路距离",
    "覆盖膜最小开窗": "增大覆盖膜最小开窗尺寸",
    "电测PAD(二线)": "增大电测 PAD 尺寸，保证探针接触",
    "电测PAD(四线-宽)": "增大电测 PAD 尺寸，保证探针接触",
    "板尺寸": "调整板尺寸/拼板方式至设备可加工范围",
    "板厚范围": "调整板厚至制程能力范围",
    "PTH纵横比": "增大最小孔径或减小板厚，降低纵横比",
    "表面处理": "改用板厂支持的表面处理工艺",
    "器件间距": "增大器件间距，避免贴片碰撞/无法返修",
    "基准点数量": "确保每面至少 2-3 个全局基准点 (Fiducial)",
    "器件到板边": "增大器件到非传送边距离或增加工艺边",
    "测试点尺寸": "将测试点焊盘尺寸扩至能力要求",
    "测试点到板边": "增大测试点到非传送边距离",
    "片式元件焊盘对称性": "平衡片式元件两端焊盘铜箔/散热，避免墓碑效应",
    "极性标识": "补充极性元件方向标识，避免反向贴装",
    "BGA焊盘": "增大 BGA 焊盘/孔环或减小 QFN/BGA 引脚间距",
    "拼板方式": "进行拼板设计并添加工艺边/Mark/定位孔",
    "工艺边": "增加工艺边宽度，满足传送轨道要求",
    "Mark点数量": "每面至少 3 个 Mark 点，并保证 Mark 到工艺边间距",
    "定位孔": "增加定位孔 (Tooling Hole) 数量/孔径",
    "板材利用率": "优化拼板排布，提高板材利用率以降低成本",
}

ASSEMBLY_RULES = {
    "min_component_spacing": 0.5,
    "fiducial_min": 2,
    "component_to_edge": 2.0,
    "test_pad_diameter": 0.8,
    "test_pad_to_edge": 1.27,
    "chip_pad_symmetry_ratio": 0.8,
    "bga_pitch_min": 0.4,
    "bga_pad_annular": 0.05,
}

PANEL_RULES = {
    "process_edge": 3.0,
    "mark_count": 3,
    "mark_to_edge": 3.5,
    "tooling_hole_count": 2,
    "min_utilization": 0.7,
}


def make_issue(code: str, category: str, rule: str, severity: str,
               description: str, suggestion: str = "",
               measured: Any = None, required: Any = None, unit: str = "",
               layer: str = "", from_: str = "", to: str = "",
               x: Any = None, y: Any = None, status: str = "打开",
               count: int = 1, note: str = "") -> dict:
    severity = severity if severity in SEVERITY_ORDER else "中"
    return {
        "code": code,
        "category": category,
        "rule": rule,
        "layer": layer or DIRECTION_HINT.get(category, ""),
        "from": from_,
        "to": to,
        "measured": measured,
        "required": required,
        "unit": unit,
        "x": x,
        "y": y,
        "severity": severity,
        "status": status,
        "description": description,
        "suggestion": suggestion or SUGGESTIONS.get(rule, "请与板厂/工艺确认并优化设计"),
        "count": int(count),
        "note": note,
    }


def _num(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_mm(spec) -> Optional[float]:
    if not isinstance(spec, dict):
        return None
    v = _num(spec.get("value"))
    if v is None:
        return None
    unit = spec.get("unit", "mm")
    if unit == "um":
        return v / 1000.0
    if unit == "mil":
        return v * MIL_TO_MM
    return v


def _match_band(rows: List[dict], value: Optional[float], key="width"):
    if value is None:
        return None
    for row in rows or []:
        lo = _num(row.get(f"{key}_min_um")) or _num(row.get(f"{key}_min"))
        hi = _num(row.get(f"{key}_max_um")) or _num(row.get(f"{key}_max"))
        if lo is not None and value < lo:
            continue
        if hi is not None and value > hi:
            continue
        return row
    return None


def _items_to_issues(items: List[dict]) -> List[dict]:
    sev_counter: Dict[str, int] = {}
    issues: List[dict] = []
    for it in items:
        status = it.get("status")
        if status not in (STATUS_FAIL, STATUS_WARN, STATUS_INFO):
            continue
        category = it.get("category", "DFM")
        prefix = CATEGORY_PREFIX.get(category, DEFAULT_PREFIX)
        sev_counter[prefix] = sev_counter.get(prefix, 0) + 1
        code = f"{prefix}-{sev_counter[prefix]:02d}"
        sev = STATUS_TO_SEVERITY.get(status, "中")
        measured = it.get("design")
        required = it.get("capability")
        unit = it.get("unit", "mm")
        note = it.get("note", "")
        if status == STATUS_INFO:
            sev = "低"
        issues.append(make_issue(
            code=code, category=category, rule=it.get("item", ""),
            severity=sev, description=it.get("message", ""),
            measured=measured, required=required, unit=unit, note=note,
        ))
    return issues


def _relay_issue(code_counter: Dict[str, int], category: str, rule: str,
                 severity: str, description: str, measured, required,
                 unit: str, suggestion: str = "", note: str = "",
                 layer: str = "") -> dict:
    prefix = CATEGORY_PREFIX.get(category, DEFAULT_PREFIX)
    code_counter[prefix] = code_counter.get(prefix, 0) + 1
    code = f"{prefix}-{code_counter[prefix]:02d}"
    return make_issue(code=code, category=category, rule=rule, severity=severity,
                      description=description, measured=measured, required=required,
                      unit=unit, suggestion=suggestion, note=note, layer=layer)


def _fabrication_extra_issues(design: dict, capability: dict,
                              copper_um: float, copper_oz: float,
                              code_counter: Dict[str, int]) -> List[dict]:
    issues: List[dict] = []
    drill = capability.get("drill", {})
    plating = capability.get("plating", {})
    plug = capability.get("plug_hole", {})
    sm = capability.get("solder_mask", {})
    silk = capability.get("silkscreen", {})
    board_thickness = _num(design.get("board_thickness"))

    ar_laser = _to_mm(drill.get("annular_ring_laser"))
    d_ar_laser = _num(design.get("min_annular_ring_laser"))
    if ar_laser is not None and d_ar_laser is not None:
        if d_ar_laser < ar_laser:
            issues.append(_relay_issue(
                code_counter, "钻孔", "最小激光孔环", "危险",
                f"最小激光孔环 {d_ar_laser}mm < 制程能力 {round(ar_laser,4)}mm",
                d_ar_laser, round(ar_laser, 4), "mm"))

    fill = _num((plating.get("blind_via_fill_aspect") or {}).get("value"))
    d_fill = _num(design.get("blind_via_fill_aspect"))
    if fill is not None and d_fill is not None and d_fill < fill:
        issues.append(_relay_issue(
            code_counter, "钻孔", "盲孔填孔纵横比", "高",
            f"盲孔填孔纵横比 {d_fill} < 制程能力 {fill}",
            d_fill, fill, "ratio"))

    laser_limit = drill.get("laser_dielectric_limit", [])
    laser_hole_um = (_num(design.get("min_laser_via")) or 0) * 1000.0
    laser_diel_um = _num(design.get("laser_dielectric_um"))
    if laser_limit and laser_diel_um is not None and laser_hole_um > 0:
        chosen = None
        for row in laser_limit:
            h = _num(row.get("hole_um"))
            if h is not None and h <= laser_hole_um:
                chosen = row
            elif h is not None and chosen is None:
                chosen = row
        if chosen is None:
            chosen = laser_limit[0]
        cap_diel = _num(chosen.get("dielectric_max_um"))
        if cap_diel is not None and laser_diel_um > cap_diel:
            issues.append(_relay_issue(
                code_counter, "钻孔", "激光孔介质厚度", "高",
                f"激光孔介质厚度 {laser_diel_um}um > 制程能力 {cap_diel}um "
                f"(孔径 {laser_hole_um:.0f}um)",
                laser_diel_um, cap_diel, "um"))

    resin_depth = _num((plug.get("resin_plug_depth") or {}).get("value"))
    d_resin = _num(design.get("resin_plug_depth_percent"))
    if resin_depth is not None and d_resin is not None and d_resin < resin_depth:
        issues.append(_relay_issue(
            code_counter, "塞孔", "树脂塞孔深度", "中",
            f"树脂塞孔深度 {d_resin}% < 制程能力 {resin_depth}%",
            d_resin, resin_depth, "%"))

    ink_depth = _num((plug.get("ink_plug_depth") or {}).get("value"))
    d_ink = _num(design.get("ink_plug_depth_percent"))
    if ink_depth is not None and d_ink is not None and d_ink < ink_depth:
        issues.append(_relay_issue(
            code_counter, "塞孔", "油墨塞孔深度", "中",
            f"油墨塞孔深度 {d_ink}% < 制程能力 {ink_depth}%",
            d_ink, ink_depth, "%"))

    plug_ratio_cap = _num((plug.get("plug_aspect_ratio") or {}).get("value"))
    plug_hole_d = _num(design.get("plug_hole_diameter"))
    if plug_ratio_cap and plug_hole_d and board_thickness:
        aspect = board_thickness / plug_hole_d
        if aspect > plug_ratio_cap:
            issues.append(_relay_issue(
                code_counter, "塞孔", "塞孔纵横比", "高",
                f"塞孔纵横比 {aspect:.2f}:1 (板厚{board_thickness}mm/孔径{plug_hole_d}mm) "
                f"> 制程能力 {plug_ratio_cap:.0f}:1",
                round(aspect, 2), plug_ratio_cap, ":1"))

    ink_rows = sm.get("ink_thickness_by_copper", [])
    d_ink_um = _num(design.get("solder_mask_ink_um"))
    if ink_rows and d_ink_um is not None:
        chosen = ink_rows[0]
        for row in ink_rows:
            c = _num(row.get("copper_oz"))
            if c is not None and c <= copper_oz:
                chosen = row
        lo = _num(chosen.get("min_um"))
        hi = _num(chosen.get("max_um"))
        if lo is not None and d_ink_um < lo:
            issues.append(_relay_issue(
                code_counter, "阻焊", "阻焊油墨厚度", "中",
                f"阻焊油墨厚度 {d_ink_um}um < 制程能力 {lo}um (铜厚 {copper_oz}oz)",
                d_ink_um, lo, "um"))
        elif hi is not None and d_ink_um > hi:
            issues.append(_relay_issue(
                code_counter, "阻焊", "阻焊油墨厚度", "中",
                f"阻焊油墨厚度 {d_ink_um}um > 制程能力 {hi}um (铜厚 {copper_oz}oz)",
                d_ink_um, hi, "um"))

    s2m = _to_mm(silk.get("min_to_solder_mask"))
    d_s2m = _num(design.get("silkscreen_to_mask"))
    if s2m is not None and d_s2m is not None and d_s2m < s2m:
        issues.append(_relay_issue(
            code_counter, "丝印", "丝印到阻焊", "中",
            f"丝印到阻焊距离 {d_s2m}mm < 制程能力 {round(s2m,4)}mm",
            d_s2m, round(s2m, 4), "mm"))

    s2o = _to_mm(silk.get("min_to_outline"))
    d_s2o = _num(design.get("silkscreen_to_outline"))
    if s2o is not None and d_s2o is not None and d_s2o < s2o:
        issues.append(_relay_issue(
            code_counter, "丝印", "丝印离成型线", "中",
            f"丝印离成型线距离 {d_s2o}mm < 制程能力 {round(s2o,4)}mm",
            d_s2o, round(s2o, 4), "mm"))

    finish = capability.get("surface_finish", {})
    d_finish = design.get("surface_finish")
    if finish and d_finish:
        keys = {str(k).lower(): str(k) for k in finish.keys()}
        dtxt = str(d_finish).strip()
        if dtxt.lower() not in keys and dtxt not in finish:
            issues.append(_relay_issue(
                code_counter, "表面处理", "表面处理", "高",
                f"表面处理 '{d_finish}' 不在板厂能力 {list(finish.keys())} 中",
                d_finish, "/".join(map(str, finish.keys())), ""))

    return issues


def _assembly_issues(design: dict, code_counter: Dict[str, int]) -> List[dict]:
    issues: List[dict] = []
    r = ASSEMBLY_RULES

    cs = _num(design.get("min_component_spacing"))
    if cs is not None and cs < r["min_component_spacing"]:
        issues.append(_relay_issue(
            code_counter, "组装", "器件间距", "中",
            f"器件最小间距 {cs}mm < 建议 {r['min_component_spacing']}mm，贴片易碰撞/难返修",
            cs, r["min_component_spacing"], "mm"))

    for side_key, side_label in (("fiducial_count_top", "A面"), ("fiducial_count_bottom", "B面")):
        fc = _num(design.get(side_key))
        if fc is not None and fc < r["fiducial_min"]:
            issues.append(_relay_issue(
                code_counter, "组装", "基准点数量", "高",
                f"{side_label}全局基准点数量 {int(fc)} < 要求 {r['fiducial_min']} 个",
                int(fc), r["fiducial_min"], "个"))

    ce = _num(design.get("component_to_edge"))
    if ce is not None and ce < r["component_to_edge"]:
        issues.append(_relay_issue(
            code_counter, "组装", "器件到板边", "中",
            f"器件到非传送边距离 {ce}mm < 建议 {r['component_to_edge']}mm，操作时易碰损器件",
            ce, r["component_to_edge"], "mm"))

    tp = _num(design.get("test_pad_diameter"))
    if tp is not None and tp < r["test_pad_diameter"]:
        issues.append(_relay_issue(
            code_counter, "组装", "测试点尺寸", "中",
            f"测试点焊盘直径 {tp}mm < 建议 {r['test_pad_diameter']}mm，测试探针接触不良",
            tp, r["test_pad_diameter"], "mm"))

    tpe = _num(design.get("test_pad_to_edge"))
    if tpe is not None and tpe < r["test_pad_to_edge"]:
        issues.append(_relay_issue(
            code_counter, "组装", "测试点到板边", "中",
            f"测试点到非传送边距离 {tpe}mm < 建议 {r['test_pad_to_edge']}mm",
            tpe, r["test_pad_to_edge"], "mm"))

    sym = _num(design.get("chip_pad_symmetry_ratio"))
    if sym is not None and sym < r["chip_pad_symmetry_ratio"]:
        issues.append(_relay_issue(
            code_counter, "组装", "片式元件焊盘对称性", "中",
            f"片式元件两端焊盘/散热对称比 {sym} < 建议 {r['chip_pad_symmetry_ratio']}，存在墓碑效应风险",
            sym, r["chip_pad_symmetry_ratio"], "ratio"))

    pol = design.get("polarity_marked")
    if pol is not None and not pol:
        issues.append(_relay_issue(
            code_counter, "组装", "极性标识", "高",
            "极性元件缺失方向标识，存在反向贴装风险",
            "缺失", "需标注", ""))

    pitch = _num(design.get("bga_pitch"))
    if pitch is not None and pitch < r["bga_pitch_min"]:
        issues.append(_relay_issue(
            code_counter, "组装", "BGA焊盘", "中",
            f"BGA/QFN 引脚间距 {pitch}mm < 建议 {r['bga_pitch_min']}mm，需专项制造符合性分析",
            pitch, r["bga_pitch_min"], "mm"))

    bga_ar = _num(design.get("bga_pad_annular"))
    if bga_ar is not None and bga_ar < r["bga_pad_annular"]:
        issues.append(_relay_issue(
            code_counter, "组装", "BGA焊盘", "高",
            f"BGA 单边孔环 {bga_ar}mm < 建议 {r['bga_pad_annular']}mm",
            bga_ar, r["bga_pad_annular"], "mm"))

    return issues


def _panel_issues(design: dict, capability: dict,
                  code_counter: Dict[str, int]) -> List[dict]:
    issues: List[dict] = []
    r = PANEL_RULES
    panel = capability.get("panel", {})

    panelized = design.get("panelized")
    if panelized is not None and not panelized:
        bs = design.get("board_size")
        rail = _num(design.get("component_to_edge"))
        desc = "该文件无拼板设计"
        if bs and len(bs) == 2 and _num(rail) is not None and rail < 5:
            desc = ("单拼尺寸不能满足生产需求，且周边元件距板边 <5mm，传送轨道存在撞件不良")
        issues.append(_relay_issue(
            code_counter, "拼板", "拼板方式", "高",
            desc, "未拼板", "需拼板", ""))

    pe = _num(design.get("process_edge_mm"))
    if pe is not None and pe < r["process_edge"]:
        issues.append(_relay_issue(
            code_counter, "拼板", "工艺边", "中",
            f"工艺边宽度 {pe}mm < 建议 {r['process_edge']}mm",
            pe, r["process_edge"], "mm"))

    mc = _num(design.get("mark_count"))
    if mc is not None and mc < r["mark_count"]:
        issues.append(_relay_issue(
            code_counter, "拼板", "Mark点数量", "高",
            f"Mark 点数量 {int(mc)} < 要求 {r['mark_count']} 个",
            int(mc), r["mark_count"], "个"))

    me = _num(design.get("mark_to_edge_mm"))
    if me is not None and me < r["mark_to_edge"]:
        issues.append(_relay_issue(
            code_counter, "拼板", "Mark点数量", "中",
            f"Mark 点到工艺边间距 {me}mm < 建议 {r['mark_to_edge']}mm",
            me, r["mark_to_edge"], "mm"))

    tc = _num(design.get("tooling_hole_count"))
    if tc is not None and tc < r["tooling_hole_count"]:
        issues.append(_relay_issue(
            code_counter, "拼板", "定位孔", "中",
            f"定位孔数量 {int(tc)} < 建议 {r['tooling_hole_count']} 个",
            int(tc), r["tooling_hole_count"], "个"))

    util = _num(design.get("panel_utilization"))
    if util is not None and util < r["min_utilization"]:
        issues.append(_relay_issue(
            code_counter, "拼板", "板材利用率", "低",
            f"板材利用率 {util*100:.1f}% < 建议 {r['min_utilization']*100:.0f}%，制造成本偏高",
            util, r["min_utilization"], "ratio"))

    wp = (panel.get("max_working_panel") or {}).get("value")
    ps = design.get("panel_size")
    if wp and ps and len(ps) == 2 and all(_num(x) is not None for x in wp):
        px, py = sorted(_num(x) for x in ps)
        wx, wy = sorted(_num(x) for x in wp)
        if px > wx or py > wy:
            issues.append(_relay_issue(
                code_counter, "拼板", "板尺寸", "危险",
                f"拼板尺寸 {ps[0]}x{ps[1]}mm 超出最大拼板 {wx}x{wy}mm",
                f"{ps[0]}x{ps[1]}", f"{wx}x{wy}", "mm"))

    return issues


def build_issues(design: dict, capability: dict, items: List[dict]) -> List[dict]:
    """汇总全部 DFM 问题 (旧 items + 新增装配/拼板/制造补充项)"""
    issues = _items_to_issues(items)
    code_counter: Dict[str, int] = {}
    for it in issues:
        prefix = it["code"].rsplit("-", 1)[0]
        code_counter[prefix] = code_counter.get(prefix, 0) + 1

    copper_um = _num(design.get("copper_um")) or 35.0
    copper_oz = _num(design.get("copper_oz")) or max(1.0, round(copper_um / 35.0, 1))

    issues += _fabrication_extra_issues(design, capability, copper_um, copper_oz, code_counter)
    issues += _assembly_issues(design, code_counter)
    issues += _panel_issues(design, capability, code_counter)
    issues.sort(key=lambda x: (SEVERITY_ORDER.get(x["severity"], 9), x["code"]))
    return issues


def summarize_issues(issues: List[dict]) -> Dict[str, int]:
    summary = {s: 0 for s in SEVERITY_LEVELS}
    for it in issues:
        summary[it.get("severity", "中")] = summary.get(it.get("severity", "中"), 0) + 1
    summary["总计"] = len(issues)
    return summary


def compute_risk_score(issues: List[dict]) -> dict:
    by_sev = summarize_issues(issues)
    penalty = sum(SEVERITY_WEIGHT.get(s, 0) * by_sev.get(s, 0) for s in SEVERITY_LEVELS)
    score = max(0.0, 100.0 - min(100.0, penalty))
    score = round(score, 1)
    if by_sev.get("危险", 0):
        risk, verdict = "危险", "存在致命问题，不建议直接投产"
    elif by_sev.get("高", 0):
        risk, verdict = "高", "存在高优先级问题，建议修改后再投产"
    elif by_sev.get("中", 0):
        risk, verdict = "中", "存在中优先级问题，建议评估后投产"
    elif by_sev.get("低", 0):
        risk, verdict = "低", "仅低优先级提示，可投产"
    else:
        risk, verdict = "低", "未发现问题，可投产"
    yield_lower = max(0.0, round(score - 2.0, 1))
    yield_upper = min(100.0, round(score + 2.0, 1))
    return {
        "score": score,
        "risk_level": risk,
        "verdict": verdict,
        "penalty": round(penalty, 1),
        "by_severity": by_sev,
        "yield_estimate": [yield_lower, yield_upper],
    }


def build_info_stats(design: dict, capability: dict, board_type: str) -> dict:
    """构造 Info 表统计 (制造工艺 + 装配工艺)"""
    trace = capability.get("trace", {})
    drill = capability.get("drill", {})
    lam = capability.get("lamination", {})
    board_size = design.get("board_size")
    board_thickness = _num(design.get("board_thickness"))
    min_drill = _num(design.get("min_mech_drill")) or _num((drill.get("min_mech_drill") or {}).get("value"))
    aspect = None
    if board_thickness and min_drill:
        aspect = round(board_thickness / min_drill, 2)

    manufacturing = {
        "外层线宽": design.get("min_line_width"),
        "外层线距": design.get("min_spacing"),
        "内层线宽": design.get("inner_line_width"),
        "内层线距": design.get("inner_spacing"),
        "最小孔径": min_drill,
        "铜层数": design.get("layer_count"),
        "孔层数": design.get("hole_layer_count"),
        "单板厚度": board_thickness,
        "板厚孔径比": aspect,
        "阻抗": "Yes" if design.get("impedance") else ("No" if design.get("impedance") is not None else ""),
        "微孔最小孔径": design.get("min_laser_via"),
        "微孔": "Yes" if design.get("has_microvia") else ("No" if design.get("has_microvia") is not None else ""),
        "埋孔": "Yes" if design.get("has_buried_via") else ("No" if design.get("has_buried_via") is not None else ""),
        "盲孔": "Yes" if design.get("has_blind_via") else ("No" if design.get("has_blind_via") is not None else ""),
        "埋容": "Yes" if design.get("has_buried_cap") else "",
        "埋阻": "Yes" if design.get("has_buried_res") else "",
        "表面处理": design.get("surface_finish") or "",
        "阻焊种类": design.get("solder_mask_type") or "",
        "过孔数量": design.get("via_count"),
        "金属化孔数量": design.get("pth_count"),
        "非金属化孔数量": design.get("npth_count"),
        "单板尺寸": f"{board_size[0]} x {board_size[1]}" if isinstance(board_size, (list, tuple)) and len(board_size) == 2 else "",
        "单板Pin密度": design.get("pin_density"),
        "拼板铆孔密度": design.get("panel_rivet_density"),
    }
    assembly = {
        "器件": design.get("comp_count"),
        "表贴件": design.get("smd_count"),
        "插件": design.get("tht_count"),
        "压接件": design.get("pressfit_count"),
        "引脚": design.get("pin_count"),
        "SMT点数(代工费)": design.get("smt_points"),
        "推荐钢网厚度": design.get("stencil_thickness"),
        "钢网总面积": design.get("stencil_area"),
        "DFT测试覆盖率": design.get("dft_coverage"),
    }
    return {
        "board_type": board_type,
        "manufacturing": {k: v for k, v in manufacturing.items() if v not in (None, "")},
        "assembly": {k: v for k, v in assembly.items() if v not in (None, "")},
        "capability_notes": {
            "线宽公差": _tolerance_text(trace.get("line_width_tolerance"), design.get("min_line_width")),
            "层间对位": (lam.get("layer_registration") or {}).get("value"),
            "孔壁粗糙度": (drill.get("hole_wall_roughness") or {}).get("value"),
            "AOI最小检测": (trace.get("aoi_min_detect") or {}).get("value"),
        },
    }


def _tolerance_text(rows, width_um) -> str:
    if not rows or width_um is None:
        return ""
    w = float(width_um) * 1000.0
    chosen = _match_band(rows, w, "width")
    if not chosen:
        return ""
    if chosen.get("tol_um") is not None:
        return f"设计值±{chosen['tol_um']}um"
    if chosen.get("tol_pct") is not None:
        return f"设计值±{chosen['tol_pct']}%"
    return ""


def build_issue_report(design: dict, capability: dict, board_type: str,
                       factory: str, factory_label: str, items: List[dict],
                       board_meta: Optional[dict] = None) -> dict:
    issues = build_issues(design, capability, items)
    risk = compute_risk_score(issues)
    info = build_info_stats(design, capability, board_type)
    return {
        "board_meta": board_meta or {},
        "factory": factory,
        "factory_label": factory_label,
        "board_type": board_type,
        "issues": issues,
        "issue_summary": summarize_issues(issues),
        "risk": risk,
        "info": info,
    }
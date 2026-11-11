"""DFM 参数表定义 + BRD -> 设计参数映射.

这里只有数据定义与换算, 不含任何 Tk 代码, 界面 (src/ui/dfm_params.py)、
命令行 (main.py) 和报告共用同一份映射, 避免"界面填一套、CLI 算另一套"。

字段来源分三种, 界面按此给每行打标签:
    brd     .brd 里能真实读到的 (约束对象 / 钻孔表 / 焊盘栈 / 制程说明)
    manual  必须人工给 (板框、阻焊、丝印、拼板、覆盖膜等图形数据, BRD 不含)
    assume  表单预填的示例值; 用户没改过就当"未确认", 不参与判定

判定原则: 只有 brd 与 manual(用户确认过的) 值才去和板厂能力比;
示例值宁可不算, 也不能拿假数据算出一个 PASS。
"""
from typing import Dict, List, Optional, Tuple

# pcb_reader._extract_components() 只保留前 100 个器件; 达到该值即视为解析被截断
BRD_COMPONENT_CAP = 100

# (类别, 字段名, 中文标签, 预填值)
# 字段名与 src/dfm_engine.analyze_design() 的入参一致; 长度单位 mm。
DFM_FIELDS = [
    # ---- 板信息 ----
    ("板信息", "layer_count", "铜层数", ""),
    ("板信息", "comp_count", "器件数", ""),
    ("拼板/板尺寸", "board_size_x", "板尺寸X (mm)", ""),
    ("拼板/板尺寸", "board_size_y", "板尺寸Y (mm)", ""),
    # ---- 线路 ----
    ("线路", "min_line_width", "最小线宽 (mm)", ""),
    ("线路", "min_spacing", "最小线距 (mm)", ""),
    ("线路", "min_neck_width", "最小颈宽 (mm)", ""),
    ("线路", "inner_line_width", "内层线宽 (mm)", ""),
    ("线路", "inner_spacing", "内层线距 (mm)", ""),
    ("线路", "pth_to_pth_spacing", "不同网络孔壁间距 (mm)", ""),
    ("线路", "copper_to_edge_inner", "内层线路到板边 (mm)", ""),
    ("线路", "copper_to_edge_outer", "外层线路到板边 (mm)", ""),
    # ---- 钻孔 ----
    ("钻孔", "min_mech_drill", "最小机械钻孔 (mm)", ""),
    ("钻孔", "max_mech_drill", "最大机械钻孔 (mm)", ""),
    ("钻孔", "min_laser_via", "最小激光孔 (mm)", ""),
    ("钻孔", "min_annular_ring", "单边孔环 (mm)", ""),
    ("钻孔", "min_annular_ring_laser", "激光孔单边孔环 (mm)", ""),
    ("钻孔", "board_thickness", "板厚 (mm)", ""),
    ("钻孔", "npth_to_edge", "NPTH孔到板边 (mm)", ""),
    # ---- 阻焊 ----
    ("阻焊", "solder_mask_bridge", "阻焊桥 (mm)", ""),
    ("阻焊", "solder_mask_opening", "阻焊开窗 (mm)", ""),
    ("阻焊", "solder_mask_ink_um", "阻焊油墨厚度 (um)", ""),
    # ---- 塞孔 ----
    ("塞孔", "plug_hole_diameter", "塞孔孔径 (mm)", ""),
    ("塞孔", "resin_plug_depth_percent", "树脂塞孔深度 (%)", ""),
    ("塞孔", "ink_plug_depth_percent", "油墨塞孔深度 (%)", ""),
    # ---- 丝印 ----
    ("丝印", "silkscreen_width", "丝印线宽 (mm)", ""),
    ("丝印", "silkscreen_clearance", "丝印间隙 (mm)", ""),
    ("丝印", "silkscreen_to_mask", "丝印到阻焊 (mm)", ""),
    ("丝印", "silkscreen_to_outline", "丝印离成型线 (mm)", ""),
    # ---- 组装 ----
    ("组装", "min_component_spacing", "器件最小间距 (mm)", ""),
    ("组装", "fiducial_count_top", "A面基准点数量", ""),
    ("组装", "fiducial_count_bottom", "B面基准点数量", ""),
    ("组装", "component_to_edge", "器件到板边 (mm)", ""),
    ("组装", "test_pad_diameter", "测试点直径 (mm)", ""),
    ("组装", "chip_pad_symmetry_ratio", "片式焊盘对称比", ""),
    ("组装", "bga_pitch", "BGA/QFN引脚间距 (mm)", ""),
    ("组装", "bga_pad_annular", "BGA单边孔环 (mm)", ""),
    # ---- 拼板 ----
    ("拼板", "panel_size_x", "拼板尺寸X (mm)", ""),
    ("拼板", "panel_size_y", "拼板尺寸Y (mm)", ""),
    ("拼板", "process_edge_mm", "工艺边宽度 (mm)", ""),
    ("拼板", "mark_count", "Mark点数量", ""),
    ("拼板", "mark_to_edge_mm", "Mark到工艺边 (mm)", ""),
    ("拼板", "tooling_hole_count", "定位孔数量", ""),
    ("拼板", "panel_utilization", "板材利用率(0-1)", ""),
    # ---- 覆盖膜 (软板) ----
    ("覆盖膜(软板)", "coverlay_spacing", "覆盖膜开窗间距 (mm)", ""),
    ("覆盖膜(软板)", "coverlay_to_pad", "开窗到焊盘 (mm)", ""),
    ("覆盖膜(软板)", "coverlay_to_trace", "开窗到线路 (mm)", ""),
    # ---- 测试 ----
    ("测试", "test_pad_2wire", "电测PAD二线 (mm)", ""),
]

# 字段元数据: unit 单位, brd 是否可由 BRD 自动带入, hint 界面提示
FIELD_META: Dict[str, dict] = {
    "layer_count": {"unit": "层", "brd": True},
    "comp_count": {"unit": "个", "brd": True},
    "min_line_width": {"unit": "mm", "brd": True},
    "min_spacing": {"unit": "mm", "brd": True},
    "min_neck_width": {"unit": "mm", "brd": True,
                       "hint": "窄颈位置按最小线宽能力评估, 报告里单列"},
    "inner_line_width": {"unit": "mm", "brd": True, "confidence": "medium",
                         "hint": "BRD 约束未分内外层时按板级最小值代入 (来源里注明)"},
    "inner_spacing": {"unit": "mm", "brd": True, "confidence": "medium",
                      "hint": "BRD 约束未分内外层时按板级最小值代入 (来源里注明)"},
    "pth_to_pth_spacing": {"unit": "mm", "brd": True},
    "copper_to_edge_inner": {"unit": "mm", "brd": True, "confidence": "medium",
                             "hint": "由 ROUTE KEEPIN 到板框距离推算 (设计允许边界), 非实测铜箔位置"},
    "copper_to_edge_outer": {"unit": "mm", "brd": True, "confidence": "medium",
                             "hint": "由 ROUTE KEEPIN 到板框距离推算 (设计允许边界), 非实测铜箔位置"},
    "min_mech_drill": {"unit": "mm", "brd": True},
    "max_mech_drill": {"unit": "mm", "brd": True},
    "min_laser_via": {"unit": "mm", "brd": True},
    "min_annular_ring": {"unit": "mm", "brd": True},
    "min_annular_ring_laser": {"unit": "mm", "brd": True},
    "board_thickness": {"unit": "mm", "brd": True,
                        "hint": "取自制程说明文本, 属低置信, 请核对"},
    "npth_to_edge": {"unit": "mm", "brd": False},
    "solder_mask_bridge": {"unit": "mm", "brd": False,
                           "hint": "阻焊属图形层, BRD 不含"},
    "solder_mask_opening": {"unit": "mm", "brd": False},
    "solder_mask_ink_um": {"unit": "um", "brd": False},
    "silkscreen_width": {"unit": "mm", "brd": False,
                         "hint": "丝印属图形层, BRD 不含"},
    "silkscreen_clearance": {"unit": "mm", "brd": False},
    "silkscreen_to_mask": {"unit": "mm", "brd": False},
    "silkscreen_to_outline": {"unit": "mm", "brd": True,
        "hint": "丝印线段到 BOARD GEOMETRY/OUTLINE 的几何最短距离"},
    "min_component_spacing": {"unit": "mm", "brd": False,
                              "hint": "器件间距要坐标/图形, BRD 不含"},
    "fiducial_count_top": {"unit": "个", "brd": False},
    "fiducial_count_bottom": {"unit": "个", "brd": False},
    "component_to_edge": {"unit": "mm", "brd": False},
    "test_pad_diameter": {"unit": "mm", "brd": True,
        "hint": "按钢网层(PASTEMASK)圆焊盘识别测试点并取最小直径; 快照无焊盘时不给数"},
    "panel_size_x": {"unit": "mm", "brd": False},
    "panel_size_y": {"unit": "mm", "brd": False},
    "process_edge_mm": {"unit": "mm", "brd": False},
    "mark_count": {"unit": "个", "brd": False},
    "coverlay_spacing": {"unit": "mm", "brd": False,
                         "hint": "覆盖膜是图形层, BRD 不含, 需 CAM/图纸确认"},
    "coverlay_to_pad": {"unit": "mm", "brd": False,
                        "hint": "覆盖膜开窗到焊盘, 需 CAM/图纸确认"},
    "coverlay_to_trace": {"unit": "mm", "brd": False,
                          "hint": "覆盖膜开窗到线路, 需 CAM/图纸确认"},
}

# 类别顺序与说明 (界面按类别分带, 并把说明显示在带尾)
CATEGORY_HINTS = {
    "板信息": "层数/器件数可自动读取",
    "拼板/板尺寸": "板框属图形数据, BRD 不含",
    "线路": "最小线宽/线距/颈宽来自 BRD 约束对象; 内层线宽/线距按板级约束, 线路到板边按 ROUTE KEEPIN 推算",
    "钻孔": "孔径/孔环来自钻孔表与焊盘栈命名",
    "阻焊": "阻焊层属图形数据, BRD 不含",
    "塞孔": "塞孔工艺参数需工艺单确认",
    "丝印": "丝印层属图形数据, BRD 不含",
    "组装": "需坐标/装配数据",
    "拼板": "拼板与工艺边需拼板图",
    "覆盖膜(软板)": "覆盖膜属图形层, BRD 不含, 需 CAM/图纸",
    "测试": "测试点需坐标数据",
}

# 已知能由 BRD 判定的字段 (界面统计"BRD 带入了几项"用)
BRD_FIELDS = tuple(key for key, meta in FIELD_META.items() if meta.get("brd"))


def _min_of(csets: dict, key: str) -> Optional[float]:
    values = [c[key] for c in (csets or {}).values() if isinstance(c, dict) and key in c]
    return round(min(values), 4) if values else None


def _max_of(csets: dict, key: str) -> Optional[float]:
    values = [c[key] for c in (csets or {}).values() if isinstance(c, dict) and key in c]
    return round(max(values), 4) if values else None


def _max_drill(info: dict) -> Optional[float]:
    """最大机械钻孔: 取 PTH 钻孔表里的最大孔径 (安装孔也算机械孔)。"""
    sizes = [r.get("size_mm") for r in (info.get("drill_rows") or [])
             if r.get("plating") == "PTH" and r.get("size_mm")]
    return round(max(sizes), 4) if sizes else None


def brd_design_values(info: dict) -> Tuple[Dict[str, float], Dict[str, dict]]:
    """把 .brd 提取结果转成 DFM 字段值。

    返回 (values, sources):
      values   {字段名: 数值} —— 只包含 BRD 里真实读到的项
      sources  {字段名: {"source": 出处, "confidence": 高/中/低}}
    """
    if not info or info.get("error"):
        return {}, {}
    sources: Dict[str, dict] = {}
    values: Dict[str, float] = {}

    def take(field: str, value, source: str = "", confidence: str = "high"):
        if value is None:
            return
        if isinstance(value, (int, float)) and value <= 0:
            return
        values[field] = value
        src = source or ((info.get("sources") or {}).get(field) or {}).get("source") or "BRD"
        conf = confidence or ((info.get("sources") or {}).get(field) or {}).get("confidence", "high")
        sources[field] = {"source": src, "confidence": conf}

    brd_sources = info.get("sources") or {}

    def src_of(brd_key: str) -> str:
        return (brd_sources.get(brd_key) or {}).get("source", "")

    take("layer_count", info.get("layer_count"), src_of("layer_count"))
    size = info.get("board_size_mm") or []
    if isinstance(size, (list, tuple)) and len(size) >= 2 and size[0] and size[1]:
        geo_src = "内嵌设计快照 BOARD GEOMETRY/OUTLINE (Draw Size 标定)"
        take("board_size_x", round(float(size[0]), 2), geo_src, "high")
        take("board_size_y", round(float(size[1]), 2), geo_src, "high")
    # 器件数: 旧格式只解析前 100 个, 达到上限视为截断, 不给数
    comps = info.get("components") or {}
    if comps.get("ok") and comps.get("count"):
        take("comp_count", comps["count"], "objects/Part.xml 位号清单")
    take("min_line_width", info.get("min_line_width_mm"), src_of("min_line_width_mm"))
    take("min_spacing", info.get("min_spacing_mm"), src_of("min_spacing_mm"))
    take("min_neck_width", info.get("min_neck_width_mm"), src_of("min_neck_width_mm"))
    take("min_mech_drill", info.get("min_drill_mm"), src_of("min_drill_mm"))
    take("max_mech_drill", _max_drill(info), "钻孔表 PTH 最大孔径")
    take("min_laser_via", info.get("min_laser_via_mm"), src_of("min_laser_via_mm"), "medium")
    take("min_annular_ring", info.get("min_annular_ring_mm"),
         src_of("min_annular_ring_mm"), "medium")
    take("silkscreen_width", info.get("min_silkscreen_width_mm"),
         src_of("min_silkscreen_width_mm"), "medium")
    silk_edge = info.get("silkscreen_to_outline") or {}
    if silk_edge.get("ok") and (silk_edge.get("min_mm") or 0) > 0.05:
        take("silkscreen_to_outline", silk_edge["min_mm"], silk_edge.get("source", ""), "medium")
    take("min_annular_ring_laser", info.get("min_annular_ring_laser_mm"),
         src_of("min_annular_ring_laser_mm"), "medium")
    take("test_pad_diameter", info.get("min_test_pad_mm"),
         src_of("min_test_pad_mm"), "medium")
    take("board_thickness", info.get("board_thickness_mm"),
         src_of("board_thickness_mm"), "low")
    take("pth_to_pth_spacing", _min_of(info.get("spacing_csets"), "THRUVIA_TO_THRUVIA_SPACING"),
         "SpacingCSet.xml / THRUVIA_TO_THRUVIA_SPACING")

    # ---- 内层线宽 / 内层线距 ----
    # BRD 的物理/间距约束默认是一份板级值; 若设计自己分了 INNER 集, 优先用 INNER 集。
    physical = info.get("physical_csets") or {}
    spacing = info.get("spacing_csets") or {}
    inner_widths = [entry["min_line_width_mm"] for name, entry in physical.items()
                    if "INNER" in str(name).upper()
                    and isinstance(entry, dict) and entry.get("min_line_width_mm")]
    if inner_widths:
        take("inner_line_width", round(min(inner_widths), 4),
             "PhysicalCSet.xml / INNER 集 MIN_LINE_WIDTH")
    elif info.get("min_line_width_mm"):
        take("inner_line_width", info["min_line_width_mm"],
             "PhysicalCSet.xml / MIN_LINE_WIDTH (板级约束, 未分内外层)", "medium")
    inner_spacings = [entry["LINE_TO_LINE_SPACING"] for name, entry in spacing.items()
                      if "INNER" in str(name).upper()
                      and isinstance(entry, dict) and entry.get("LINE_TO_LINE_SPACING")]
    if inner_spacings:
        take("inner_spacing", round(min(inner_spacings), 4),
             "SpacingCSet.xml / INNER 集 LINE_TO_LINE_SPACING")
    elif info.get("min_spacing_mm"):
        take("inner_spacing", info["min_spacing_mm"],
             "SpacingCSet.xml / LINE_TO_LINE_SPACING (板级约束, 未分内外层)", "medium")

    # ---- 内层 / 外层线路到板边 ----
    # 由 ROUTE KEEPIN 到板框的最小距离推算: 这是设计允许布线的边界,
    # 实际铜箔只会更靠内; 没有 keepin 或 keepin 与板框重合时不给值。
    keepin = (info.get("board_geometry") or {}).get("keepin") or {}
    if keepin.get("ok"):
        scope = "分层" if keepin.get("layered") else "全层"
        for field, key in (("copper_to_edge_inner", "inner_mm"),
                           ("copper_to_edge_outer", "outer_mm")):
            value = keepin.get(key)
            if value:
                take(field, value, f"ROUTE KEEPIN({scope}) 到板框距离 (设计允许边界)", "medium")
    return values, sources


def brd_extra_values(info: dict) -> Dict[str, object]:
    """看板选项里能由 BRD 直接确定的项 (表面处理等)。"""
    extra: Dict[str, object] = {}
    finishes = info.get("surface_finishes") or []
    if finishes:
        extra["surface_finish"] = finishes[0]
    return extra


def surface_finish_choices() -> List[str]:
    return ["", "ENIG", "OSP", "hard_gold", "ENEPIG"]


def brd_dfm_fields(extracted: dict) -> Tuple[dict, List[str], List[str]]:
    """兼容旧接口: 返回 (fields, filled, skipped) 供界面提示"带入了什么、缺什么"。"""
    values, sources = brd_design_values(extracted)
    labels = {key: label for _cat, key, label, _d in DFM_FIELDS}
    filled, skipped = [], []
    for _cat, key, label, _default in DFM_FIELDS:
        if key in values:
            unit = "" if key in ("layer_count", "comp_count") else " mm"
            filled.append(f"{labels.get(key, key)} {values[key]}{unit}")
        elif FIELD_META.get(key, {}).get("brd"):
            skipped.append(label)
    return values, filled, skipped

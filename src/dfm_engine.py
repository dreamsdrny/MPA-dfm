"""
BRD-AI: DFM制程能力校验 (Module 4)
支持多板厂制程能力数据库:
  - 红版 (hongban): config/dfm_capability_hongban.yaml
  - 四会 (sihui):   config/dfm_capability_sihui.yaml
支持:
  - 硬板 (rigid): standard / automotive / extreme
  - 软板及软硬结合板 (flex)
  - 按成品铜厚分档的最小线宽/线距校验
  - validate_constraints(): 约束规则校验 (保持旧接口兼容)
  - analyze_design(): DFM 功能分析 (设计参数 -> 制程能力比对报告)
"""
import os
from typing import Any, Dict, List, Optional, Tuple

import yaml

MIL_TO_MM = 0.0254

DEFAULT_FACTORY = "hongban"

FACTORY_FILES = {
    "hongban": "dfm_capability_hongban.yaml",
    "sihui": "dfm_capability_sihui.yaml",
}

FACTORY_LABELS = {
    "hongban": "红版",
    "sihui": "四会",
}

FACTORY_ALIASES = {
    "hongban": "hongban",
    "红版": "hongban",
    "江西红版": "hongban",
    "sihui": "sihui",
    "四会": "sihui",
}

LEVEL_ALIASES = {
    "advanced": "extreme",      # 旧级别兼容
    "extreme": "extreme",
    "standard": "standard",
    "automotive": "automotive",
    "常规": "standard",
    "车规": "automotive",
    "极限": "extreme",
}

_DB_CACHE: Dict[str, dict] = {}


def _load_yaml(filename: str) -> dict:
    config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
    path = os.path.join(config_dir, filename)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_factories() -> List[str]:
    return list(FACTORY_FILES.keys())


def factory_label(factory: str) -> str:
    return FACTORY_LABELS.get(normalize_factory(factory), str(factory))


def normalize_factory(factory: str) -> str:
    key = str(factory or "").strip().lower()
    return FACTORY_ALIASES.get(key, FACTORY_ALIASES.get(str(factory or "").strip(), DEFAULT_FACTORY))


def load_capability_db(factory: str = DEFAULT_FACTORY) -> dict:
    factory = normalize_factory(factory)
    if factory in _DB_CACHE:
        return _DB_CACHE[factory]
    filename = FACTORY_FILES.get(factory, "dfm_capability.yaml")
    config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
    if not os.path.exists(os.path.join(config_dir, filename)):
        filename = "dfm_capability.yaml"   # 回退到默认能力库
    db = _load_yaml(filename)
    _DB_CACHE[factory] = db
    return db


def list_board_types(factory: str = DEFAULT_FACTORY) -> List[str]:
    return list(load_capability_db(factory).get("board_types", {}).keys())


def list_levels(board_type: str = "rigid", factory: str = DEFAULT_FACTORY) -> List[str]:
    db = load_capability_db(factory)
    bt = db.get("board_types", {}).get(board_type)
    if not bt:
        return []
    return list(bt.get("levels", []))


def level_label(board_type: str, level: str, factory: str = DEFAULT_FACTORY) -> str:
    db = load_capability_db(factory)
    bt = db.get("board_types", {}).get(board_type, {})
    return bt.get("level_labels", {}).get(level, level)


def normalize_level(level: str) -> str:
    return LEVEL_ALIASES.get(str(level).strip().lower(), "standard")


def load_capability(board_type: str = "rigid", level: str = "standard",
                    factory: str = DEFAULT_FACTORY) -> dict:
    db = load_capability_db(factory)
    board_type = board_type if board_type in db.get("board_types", {}) else "rigid"
    level = normalize_level(level)
    bt = db["board_types"][board_type]
    if level not in bt:
        level = bt["levels"][0]
    return dict(bt[level])


def _num(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mil_to_mm(value) -> Optional[float]:
    v = _num(value)
    return None if v is None else v * MIL_TO_MM


def _to_mm(spec) -> Optional[float]:
    """将带 {value, unit} 的量统一换算为 mm (支持 mm/um/mil)"""
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


def get_line_spacing_capability(capability: dict, copper_um: float = 35.0) -> Tuple[Optional[float], Optional[float]]:
    """按成品铜厚(um)查询最小线宽/线距能力, 返回 (line_um, space_um)"""
    table = capability.get("trace", {}).get("min_line_spacing_by_copper", [])
    if not table:
        return None, None
    best = None
    for row in table:
        lo = row.get("copper_min_um", 0)
        hi = row.get("copper_max_um", 1e9)
        if lo <= copper_um <= hi:
            return _num(row.get("line_um")), _num(row.get("space_um"))
        if best is None and copper_um > hi:
            best = row
    if best is None:
        best = table[-1]
    return _num(best.get("line_um")), _num(best.get("space_um"))


def get_sm_bridge_capability(capability: dict, copper_oz: float = 1.0) -> Optional[float]:
    """按铜厚(oz)查询最小阻焊桥能力, 返回 mm。
    优先使用 min_bridge_single (单值能力, 与铜厚无关), 否则按 min_bridge_by_copper 分档。
    """
    sm = capability.get("solder_mask", {})
    single = sm.get("min_bridge_single")
    if single:
        return _to_mm(single)
    rows = sm.get("min_bridge_by_copper", [])
    if not rows:
        return None
    chosen = rows[0]
    for row in rows:
        if _num(row.get("copper_oz")) is not None and _num(row.get("copper_oz")) <= copper_oz:
            chosen = row
    v = _num(chosen.get("value"))
    unit = chosen.get("unit", "mil")
    if v is None:
        return None
    if unit == "um":
        return v / 1000.0
    if unit == "mil":
        return v * MIL_TO_MM
    return v


# ============================================================
# DFM 功能分析: 设计参数 -> 制程能力比对
# ============================================================
def analyze_design(design: dict, board_type: str = "rigid",
                   capability_level: str = "standard",
                   factory: str = DEFAULT_FACTORY) -> dict:
    """
    design 支持的参数 (均可选, 长度类单位 mm, 铜厚 um):
      board_size: [x, y]            板尺寸 (mm)
      min_line_width                最小线宽 (mm)
      min_spacing                   最小线距 (mm)
      copper_um                     成品铜厚 (um), 默认 35
      copper_oz                     成品铜厚 (oz), 默认 1.0
      min_mech_drill / max_mech_drill  机械钻孔 (mm)
      min_laser_via                 激光孔 (mm)
      min_annular_ring              单边孔环 (mm)
      board_thickness               板厚 (mm)
      min_drill_for_ar              计算纵横比用最小孔径 (mm), 缺省取 min_mech_drill
      pth_to_pth_spacing            不同网络孔壁间距 (mm)
      copper_to_edge_inner / copper_to_edge_outer  线路到板边 (mm)
      npth_to_edge                  NPTH孔壁到板边 (mm)
      solder_mask_bridge            阻焊桥 (mm)
      solder_mask_opening           阻焊开窗 (mm)
      silkscreen_width              丝印线宽 (mm)
      silkscreen_clearance          丝印间隙 (mm)
      silkscreen_char_width/height  丝印字符 (mm)
      plug_hole_diameter / plug_board_thickness  塞孔孔径/板厚 (mm)
      coverlay_window / coverlay_spacing / coverlay_to_pad / coverlay_to_trace (mm, flex)
      test_pad_2wire / test_pad_4wire  电测PAD (mm)
    """
    factory = normalize_factory(factory)
    capability = load_capability(board_type, capability_level, factory=factory)
    level = normalize_level(capability_level)
    copper_um = _num(design.get("copper_um")) or 35.0
    copper_oz = _num(design.get("copper_oz")) or max(1.0, round(copper_um / 35.0, 1))

    items: List[dict] = []

    def add_check(category: str, name: str, design_val: Optional[float],
                  cap_val: Optional[float], unit: str = "mm",
                  higher_is_better: bool = False, note: str = ""):
        if design_val is None:
            return
        if cap_val is None:
            items.append({
                "category": category, "item": name, "design": design_val,
                "capability": None, "unit": unit, "status": "info",
                "message": f"{name}: 该等级无制程数据，需与板厂评估", "note": note,
            })
            return
        cap_val = round(cap_val, 4)
        design_val = round(design_val, 4)
        ok = design_val >= cap_val if higher_is_better else design_val <= cap_val
        if ok:
            status = "pass"
            message = f"{name}: 设计 {design_val}{unit} 满足能力 {cap_val}{unit}"
            near = (design_val >= cap_val * 0.8) if not higher_is_better else (design_val <= cap_val * 1.2)
            if near:
                status = "warn_limit"
                message = f"{name}: 设计 {design_val}{unit} 接近制程极限 {cap_val}{unit}"
            items.append({
                "category": category, "item": name, "design": design_val,
                "capability": cap_val, "unit": unit, "status": status,
                "message": message, "note": note,
            })
        else:
            direction = "≥" if higher_is_better else "≤"
            items.append({
                "category": category, "item": name, "design": design_val,
                "capability": cap_val, "unit": unit, "status": "fail",
                "message": f"{name}: 设计 {design_val}{unit} 超出制程能力 {direction}{cap_val}{unit}",
                "note": note,
            })

    trace = capability.get("trace", {})
    drill = capability.get("drill", {})
    sm = capability.get("solder_mask", {})
    silk = capability.get("silkscreen", {})
    panel = capability.get("panel", {})
    plug = capability.get("plug_hole", {})
    plating = capability.get("plating", {})
    test = capability.get("test", {})

    # ---- 拼板/板尺寸 ----
    bs = design.get("board_size")
    wp = panel.get("max_working_panel", {}).get("value")
    if bs and wp and len(bs) == 2 and all(_num(x) is not None for x in wp):
        bx, by = sorted(float(x) for x in bs)
        wx, wy = sorted(_num(x) for x in wp)
        ok = bx <= wx and by <= wy
        items.append({
            "category": "拼板", "item": "板尺寸",
            "design": f"{bs[0]}x{bs[1]}", "capability": f"{wx}x{wy}",
            "unit": "mm", "status": "pass" if ok else "fail",
            "message": f"板尺寸 {bs[0]}x{bs[1]}mm {'满足' if ok else '超出'}最大拼板 {wx}x{wy}mm",
            "note": panel.get("max_working_panel", {}).get("note", ""),
        })

    # ---- 板厚范围 ----
    board_thickness = _num(design.get("board_thickness"))
    bt_range = capability.get("lamination", {}).get("board_thickness", {})
    bt_min, bt_max = _num(bt_range.get("min_mm")), _num(bt_range.get("max_mm"))
    if board_thickness and bt_min is not None and bt_max is not None:
        ok = bt_min <= board_thickness <= bt_max
        items.append({
            "category": "压合", "item": "板厚范围",
            "design": board_thickness, "capability": f"{bt_min}-{bt_max}",
            "unit": "mm", "status": "pass" if ok else "fail",
            "message": f"板厚 {board_thickness}mm {'在' if ok else '不在'}能力范围 {bt_min}-{bt_max}mm",
            "note": bt_range.get("note", ""),
        })

    # ---- 线路 ----
    line_cap_um, space_cap_um = get_line_spacing_capability(capability, copper_um)
    add_check("线路", "最小线宽", _num(design.get("min_line_width")),
              None if line_cap_um is None else line_cap_um / 1000.0, "mm",
              higher_is_better=True, note=f"成品铜厚 {copper_um}um")
    add_check("线路", "最小线距", _num(design.get("min_spacing")),
              None if space_cap_um is None else space_cap_um / 1000.0, "mm",
              higher_is_better=True, note=f"成品铜厚 {copper_um}um")

    h2h = trace.get("hole_to_hole_spacing", {})
    add_check("线路", "不同网络孔壁间距", _num(design.get("pth_to_pth_spacing")),
              _num((h2h.get("pth_to_pth") or {}).get("value")), "mm",
              higher_is_better=True, note="涉及CAF问题")

    tbe = trace.get("trace_to_board_edge", {})
    add_check("线路", "内层线路到板边", _num(design.get("copper_to_edge_inner")),
              _num(tbe.get("inner")), "mm", higher_is_better=True)
    add_check("线路", "外层线路到板边", _num(design.get("copper_to_edge_outer")),
              _num(tbe.get("outer")), "mm", higher_is_better=True)

    # ---- 钻孔 ----
    add_check("钻孔", "最小机械钻孔", _num(design.get("min_mech_drill")),
              _num(drill.get("min_mech_drill", {}).get("value")), "mm",
              higher_is_better=True,
              note="软板" if board_type == "flex" else "硬板")
    add_check("钻孔", "最大机械钻孔", _num(design.get("max_mech_drill")),
              _num(drill.get("max_mech_drill", {}).get("value")), "mm")

    min_laser = _num(drill.get("min_laser_via", {}).get("value"))
    if min_laser is not None:
        add_check("钻孔", "最小激光孔", _num(design.get("min_laser_via")),
                  min_laser / 1000.0, "mm", higher_is_better=True)

    ann_mech_mm = _to_mm(drill.get("annular_ring_mech"))
    add_check("钻孔", "单边孔环(机械孔)", _num(design.get("min_annular_ring")),
              ann_mech_mm, "mm", higher_is_better=True,
              note=drill.get("annular_ring_mech", {}).get("note", ""))

    # ---- 纵横比 ----
    ar_drill = _num(design.get("min_drill_for_ar")) or _num(design.get("min_mech_drill"))
    pth_ar = _num(plating.get("pth_aspect_ratio", {}).get("value"))
    if board_thickness and ar_drill and pth_ar:
        if pth_ar < 2:
            items.append({
                "category": "钻孔", "item": "PTH纵横比",
                "design": round(board_thickness / ar_drill, 2), "capability": pth_ar,
                "unit": ":1", "status": "info",
                "message": f"PTH纵横比能力数据存疑({pth_ar:.0f}:1)，请与板厂确认",
                "note": plating.get("pth_aspect_ratio", {}).get("note", ""),
            })
        else:
            ar = board_thickness / ar_drill
            status = "pass" if ar <= pth_ar else "fail"
            if status == "pass" and ar >= pth_ar * 0.8:
                status = "warn_limit"
            items.append({
                "category": "钻孔", "item": "PTH纵横比",
                "design": round(ar, 2), "capability": pth_ar, "unit": ":1",
                "status": status,
                "message": f"纵横比 {ar:.2f}:1 (板厚{board_thickness}mm/孔径{ar_drill}mm) "
                           f"{'超过' if ar > pth_ar else '满足'}能力 {pth_ar:.0f}:1",
                "note": plating.get("pth_aspect_ratio", {}).get("note", ""),
            })

    add_check("钻孔", "NPTH孔到板边", _num(design.get("npth_to_edge")),
              _num(drill.get("npth_to_edge", {}).get("value")), "mm",
              higher_is_better=True, note="孔壁到板边，太小有破孔风险")

    # ---- 阻焊 ----
    bridge_cap = get_sm_bridge_capability(capability, copper_oz)
    add_check("阻焊", "最小阻焊桥", _num(design.get("solder_mask_bridge")),
              bridge_cap, "mm", higher_is_better=True, note=f"铜厚 {copper_oz}oz")
    open_cap_mm = _to_mm(sm.get("min_opening"))
    if open_cap_mm is not None:
        add_check("阻焊", "阻焊开窗最小尺寸", _num(design.get("solder_mask_opening")),
                  open_cap_mm, "mm", higher_is_better=True)

    # ---- 塞孔 ----
    plug_hole_d = _num(design.get("plug_hole_diameter"))
    plug_thk = _num(design.get("plug_board_thickness")) or board_thickness
    resin_min = plug.get("resin_plug_min", {})
    if plug_hole_d is not None and resin_min:
        cap_hole = _num(resin_min.get("hole_mm"))
        cap_thk = _num(resin_min.get("board_thickness_mm"))
        msgs = []
        if cap_hole is not None:
            msgs.append("孔径" + ("满足" if plug_hole_d >= cap_hole else f"低于最小{cap_hole}mm"))
        if cap_thk is not None and plug_thk is not None:
            msgs.append("板厚" + ("满足" if plug_thk >= cap_thk else f"低于最小{cap_thk}mm"))
        status = "pass" if all("满足" in m for m in msgs) else "fail"
        if msgs:
            items.append({
                "category": "塞孔", "item": "树脂塞孔条件",
                "design": plug_hole_d, "capability": cap_hole, "unit": "mm",
                "status": status,
                "message": f"树脂塞孔(孔径{plug_hole_d}mm/板厚{plug_thk}mm): {'，'.join(msgs)}",
                "note": resin_min.get("note", ""),
            })

    # ---- 丝印 ----
    add_check("丝印", "最小文字线宽", _num(design.get("silkscreen_width")),
              _to_mm(silk.get("min_line_width")), "mm",
              higher_is_better=True)
    add_check("丝印", "最小文字间隙", _num(design.get("silkscreen_clearance")),
              _to_mm(silk.get("min_clearance")), "mm",
              higher_is_better=True)
    char_cap = silk.get("min_char_size", {})
    cw = _mil_to_mm(char_cap.get("width_mil"))
    ch = _mil_to_mm(char_cap.get("height_mil"))
    dw, dh = _num(design.get("silkscreen_char_width")), _num(design.get("silkscreen_char_height"))
    if cw and ch and dw and dh:
        ok = dw >= cw and dh >= ch
        items.append({
            "category": "丝印", "item": "最小字符尺寸",
            "design": f"{dw}x{dh}", "capability": f"{round(cw,3)}x{round(ch,3)}",
            "unit": "mm", "status": "pass" if ok else "fail",
            "message": f"字符 {dw}x{dh}mm {'满足' if ok else '小于'}能力 {round(cw,3)}x{round(ch,3)}mm",
            "note": char_cap.get("note", ""),
        })

    # ---- 覆盖膜 (仅软板) ----
    if board_type == "flex":
        cvl = capability.get("coverlay", {})
        add_check("覆盖膜", "覆盖膜开窗最小间距", _num(design.get("coverlay_spacing")),
                  _num(cvl.get("min_window_spacing", {}).get("value")), "mm",
                  higher_is_better=True)
        add_check("覆盖膜", "开窗边缘到焊盘距离", _num(design.get("coverlay_to_pad")),
                  _num(cvl.get("window_to_pad", {}).get("value")), "mm",
                  higher_is_better=True)
        add_check("覆盖膜", "开窗边缘到线路距离", _num(design.get("coverlay_to_trace")),
                  _num(cvl.get("window_to_trace", {}).get("value")), "mm",
                  higher_is_better=True)
        win_cap = cvl.get("min_window_rect", {}).get("value")
        win_design = design.get("coverlay_window")
        if win_cap and win_design and len(win_cap) == 2 and isinstance(win_design, (list, tuple)):
            ok = win_design[0] >= win_cap[0] and win_design[1] >= win_cap[1]
            items.append({
                "category": "覆盖膜", "item": "覆盖膜最小开窗",
                "design": f"{win_design[0]}x{win_design[1]}",
                "capability": f"{win_cap[0]}x{win_cap[1]}", "unit": "mm",
                "status": "pass" if ok else "fail",
                "message": f"开窗 {win_design[0]}x{win_design[1]}mm "
                           f"{'满足' if ok else '小于'}最小开窗 {win_cap[0]}x{win_cap[1]}mm",
                "note": cvl.get("min_window_rect", {}).get("note", ""),
            })

    # ---- 测试 ----
    t2 = test.get("min_pad_2wire") or test.get("min_pad") or {}
    add_check("测试", "电测PAD(二线)", _num(design.get("test_pad_2wire")),
              _num(t2.get("value")), "mm", higher_is_better=True)
    t4 = test.get("min_pad_4wire", {})
    t4v = t4.get("value") if isinstance(t4, dict) else None
    if isinstance(t4v, (list, tuple)) and t4v:
        add_check("测试", "电测PAD(四线-宽)", _num(design.get("test_pad_4wire")),
                  _num(t4v[0]), "mm", higher_is_better=True)

    errors = [it for it in items if it["status"] == "fail"]
    warnings = [it for it in items if it["status"] == "warn_limit"]
    infos = [it for it in items if it["status"] == "info"]

    return {
        "factory": factory,
        "factory_label": factory_label(factory),
        "board_type": board_type,
        "board_type_label": load_capability_db(factory)["board_types"][board_type].get("label", board_type),
        "capability_level": level,
        "capability_label": level_label(board_type, level, factory=factory),
        "copper_um": copper_um,
        "copper_oz": copper_oz,
        "items": items,
        "errors": [it["message"] for it in errors],
        "warnings": [it["message"] for it in warnings],
        "info": [it["message"] for it in infos],
        "error_count": len(errors),
        "warning_count": len(warnings),
        "passed": len(errors) == 0,
    }


# ============================================================
# 约束规则校验 (兼容旧接口)
# ============================================================
def validate_constraints(constraints: dict, capability_level: str = "standard",
                         board_type: str = "rigid", copper_um: float = 35.0,
                         copper_oz: float = 1.0,
                         factory: str = DEFAULT_FACTORY) -> dict:
    factory = normalize_factory(factory)
    capability = load_capability(board_type, capability_level, factory=factory)
    level = normalize_level(capability_level)

    errors: List[str] = []
    warnings: List[str] = []

    trace = capability.get("trace", {})
    drill_cap = capability.get("drill", {})
    sm = capability.get("solder_mask", {})
    silk = capability.get("silkscreen", {})
    plating = capability.get("plating", {})

    line_um, space_um = get_line_spacing_capability(capability, copper_um)
    min_line = None if line_um is None else line_um / 1000.0
    min_spacing = None if space_um is None else space_um / 1000.0

    min_mech_drill = _num(drill_cap.get("min_mech_drill", {}).get("value"))
    ann_mech_mm = _to_mm(drill_cap.get("annular_ring_mech"))
    pth_ar = _num(plating.get("pth_aspect_ratio", {}).get("value"))
    if pth_ar is not None and pth_ar < 2:
        pth_ar = None   # 能力数据存疑(如1:1), 不参与纵横比校验
    bridge_cap = get_sm_bridge_capability(capability, copper_oz)
    silk_min_mm = _to_mm(silk.get("min_line_width"))
    tbe = trace.get("trace_to_board_edge", {})
    edge_inner = _num(tbe.get("inner"))
    edge_outer = _num(tbe.get("outer"))
    h2h_pth = _num((trace.get("hole_to_hole_spacing", {}).get("pth_to_pth") or {}).get("value"))

    # ---- 物理约束: 线宽 ----
    for phys in constraints.get("physical_constraints", []):
        w_min = phys.get("width_min", 0)
        if min_line is None:
            continue
        if w_min < min_line:
            errors.append(
                f"[{phys['name']}] 最小线宽 {w_min}mm < 制程能力 {min_line}mm (铜厚{copper_um}um)"
            )
        elif w_min < min_line * 1.2:
            warnings.append(
                f"[{phys['name']}] 最小线宽 {w_min}mm 接近制程极限 {min_line}mm (铜厚{copper_um}um)"
            )

    # ---- 间距约束 ----
    for spc in constraints.get("spacing_constraints", []):
        if min_spacing is None:
            break
        for key in ["line_to_line", "line_to_pin", "line_to_via"]:
            val = spc.get(key, 0)
            if isinstance(val, str):
                continue
            if val < min_spacing:
                errors.append(
                    f"[{spc['name']}] {key} 间距 {val}mm < 制程能力 {min_spacing}mm (铜厚{copper_um}um)"
                )

    # ---- 过孔约束 ----
    for via in constraints.get("via_constraints", []):
        drill = via.get("drill", 0)
        if min_mech_drill is not None and drill < min_mech_drill:
            errors.append(
                f"[{via['name']}] 钻孔 {drill}mm < 制程能力 {min_mech_drill}mm"
            )
        pad = via.get("pad", 0)
        annular = (pad - drill) / 2
        if ann_mech_mm is not None and annular < ann_mech_mm:
            errors.append(
                f"[{via['name']}] 单边孔环 {annular:.3f}mm < 制程能力 {ann_mech_mm:.3f}mm"
            )

    # ---- 纵横比 ----
    board_thickness = _num(constraints.get("board_thickness_mm"))
    if board_thickness and min_mech_drill and pth_ar:
        ar = board_thickness / min_mech_drill
        if ar > pth_ar:
            errors.append(
                f"[aspect_ratio] 纵横比 {ar:.1f}:1 超过制程能力 {pth_ar:.0f}:1"
            )

    checklist = constraints.get("checklist_rules", {})

    # ---- 板边间距 ----
    for rule_name, rule_val in checklist.get("board_edge", {}).items():
        if not isinstance(rule_val, dict):
            continue
        cap_val = edge_inner if "inner" in rule_name else edge_outer
        if cap_val is not None and rule_val.get("min", 0) < cap_val:
            warnings.append(
                f"[{rule_name}] 板边间距 {rule_val['min']}mm 低于建议值 {cap_val}mm"
            )

    # ---- 阻焊桥 ----
    sm_bridge = checklist.get("solder_mask", {}).get("bridge", {}).get("min")
    if bridge_cap is not None and sm_bridge is not None and sm_bridge < bridge_cap:
        warnings.append(
            f"[solder_mask_bridge] 阻焊桥 {sm_bridge}mm < 制程能力 {bridge_cap:.3f}mm (铜厚{copper_oz}oz)"
        )

    # ---- 丝印线宽 ----
    silk_rule = checklist.get("silkscreen", {}).get("line_width", {}).get("min")
    if silk_min_mm is not None and silk_rule is not None and silk_rule < silk_min_mm:
        warnings.append(
            f"[silkscreen] 丝印线宽 {silk_rule}mm < 制程能力 {silk_min_mm:.3f}mm"
        )

    # ---- 孔壁间距 ----
    if h2h_pth is not None:
        pth_rule = checklist.get("spacing", {}).get("pth_to_pth", {}).get("min")
        if pth_rule is not None and pth_rule < h2h_pth:
            warnings.append(
                f"[pth_to_pth] 通孔间距 {pth_rule}mm < 制程能力 {h2h_pth}mm (CAF风险)"
            )

    # ---- 差分对 ----
    for elec in constraints.get("electrical_constraints", []):
        if not elec.get("is_diff_pair"):
            continue
        if min_line is not None and elec.get("min_line_width", 0) < min_line:
            errors.append(
                f"[{elec['name']}] 差分线宽 {elec.get('min_line_width')}mm < 制程能力 {min_line}mm"
            )
        if min_spacing is not None and elec.get("min_gap", 0) < min_spacing:
            errors.append(
                f"[{elec['name']}] 差分线距 {elec.get('min_gap')}mm < 制程能力 {min_spacing}mm"
            )

    cap_summary = {
        "min_line_spacing": (f"{line_um}/{space_um}um"
                             if line_um is not None else "N/A (需评估)"),
        "min_mech_drill": min_mech_drill,
        "annular_ring_mech_mm": ann_mech_mm,
        "pth_aspect_ratio": pth_ar,
        "solder_mask_bridge_mm": bridge_cap,
        "silkscreen_min_mm": silk_min_mm,
        "copper_um": copper_um,
        "copper_oz": copper_oz,
    }

    return {
        "factory": factory,
        "factory_label": factory_label(factory),
        "board_type": board_type,
        "board_type_label": load_capability_db(factory)["board_types"][board_type].get("label", board_type),
        "capability_level": level,
        "capability_label": level_label(board_type, level, factory=factory),
        "capability": capability,
        "capability_summary": cap_summary,
        "errors": errors,
        "warnings": warnings,
        "passed": len(errors) == 0,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }

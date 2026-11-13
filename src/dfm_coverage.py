"""能力库全覆盖: 把 dfm_capability_*.yaml 里的每个选项都摊到结果表里。

背景
----
能力库一条条列了几十项（硬板 standard 就有 41 项），但引擎只对其中十几项
有明确设计字段。以前的界面只显示"有数据可判"的那十几行，看起来就像"能力库
那么多项，结果只比了几个"——不合理，也不透明。

做法
----
把能力库按 `章节/键` 摊平，逐项给出状态：

    已判定   设计值存在 -> 按方向 (≥ / ≤) 判 PASS / 接近 / FAIL
    缺数据   有对应设计字段但这次没值 -> 写明需要什么数据
    未接入   能力库有这一项，但程序没有对应设计字段 -> 写明缺什么、建议人工核对

已由引擎原有检查覆盖的项自动跳过（按 `field` 或条目名去重），不重复。
"""
from typing import List, Optional, Tuple

# 能力库路径 -> (类别, 名称, 设计字段, 方向, 缺数据时需要什么)
#   方向: "ge" = 设计值必须 ≥ 能力值; "le" = 设计值必须 ≤ 能力值; None = 不比
CAP_ITEMS = {
    # ---- 拼板 ----
    "panel/max_working_panel": ("拼板", "最大工作板尺寸", "panel_size_x", "le",
                                "拼板尺寸 X/Y (mm)"),
    "panel/max_array": ("拼板", "最大阵列尺寸", None, None, "拼板图/阵列排布"),
    "panel/min_pcs": ("拼板", "最小拼板数量", None, None, "拼板图 (PCS 数)"),
    "panel/min_breakaway_rail": ("拼板", "最小工艺边", "process_edge_mm", "ge",
                                 "工艺边宽度 (mm)"),
    # ---- 线路 ----
    "trace/hole_to_hole_spacing/pth_to_pth": ("线路", "不同网络孔壁间距",
                                              "pth_to_pth_spacing", "ge",
                                              "孔到孔间距 (mm), 需钻孔坐标"),
    "trace/hole_to_hole_spacing/pth_to_blind": ("线路", "通孔到盲孔孔壁间距", None, None,
                                                "孔坐标 (钻孔/盲孔)"),
    "trace/hole_to_hole_spacing/blind_to_blind": ("线路", "盲孔到盲孔孔壁间距", None, None,
                                                  "孔坐标 (盲孔)"),
    "trace/aoi_min_detect": ("线路", "AOI 最小可检", None, None, "AOI 能力评估"),
    # ---- 压合 ----
    "lamination/dielectric_thickness_tol": ("压合", "介质层厚度公差", None, None,
                                            "叠层结构/板厚公差"),
    "lamination/layer_registration": ("压合", "层间对准度", None, None, "层偏测试数据"),
    # ---- 钻孔 ----
    "drill/min_mech_drill": ("钻孔", "最小机械钻孔", "min_mech_drill", "ge",
                             "钻孔表最小孔径 (mm)"),
    "drill/max_mech_drill": ("钻孔", "最大机械钻孔", "max_mech_drill", "le",
                             "钻孔表最大孔径 (mm)"),
    "drill/laser_registration": ("钻孔", "激光孔对准度", None, None, "激光钻孔能力/板厂报告"),
    "drill/min_laser_via": ("钻孔", "最小激光孔", "min_laser_via", "ge",
                            "钻孔表盲埋孔最小孔径 (mm)"),
    "drill/min_laser_blind": ("钻孔", "最小激光盲孔", "min_laser_via", "ge",
                              "盲孔孔径 (mm)"),
    "drill/annular_ring_mech": ("钻孔", "机械孔单边孔环", "min_annular_ring", "ge",
                                "焊盘栈孔径/焊盘直径 (mm)"),
    "drill/annular_ring_laser": ("钻孔", "激光孔单边孔环", "min_annular_ring_laser", "ge",
                                 "盲孔焊盘栈孔径/焊盘直径 (mm)"),
    "drill/hole_wall_roughness": ("钻孔", "孔壁粗糙度", None, None, "孔壁粗糙度检测报告"),
    "drill/npth_to_edge": ("钻孔", "NPTH 孔到板边", "npth_to_edge", "ge",
                           "NPTH 孔坐标与板框 (mm)"),
    # ---- 电镀 ----
    "plating/blind_via_fill_aspect": ("电镀", "盲孔填孔纵横比", "blind_via_fill_aspect", "ge",
                                      "盲孔孔径与孔深 (mm)"),
    "plating/blind_via_fill_rate": ("电镀", "盲孔填孔率", None, None, "填孔检测数据 (%)"),
    "plating/blind_via_fill_dimple": ("电镀", "盲孔凹陷", None, None, "填孔凹陷检测 (um)"),
    "plating/plating_uniformity": ("电镀", "镀层均匀性", None, None, "电镀均匀性报告 (um)"),
    "plating/pth_aspect_ratio": ("电镀", "PTH 纵横比", None, None,
                                 "板厚与最小孔径 (mm)"),
    # ---- 塞孔 ----
    "plug_hole/ink_plug_depth": ("塞孔", "油墨塞孔深度", "ink_plug_depth_percent", "ge",
                                 "油墨塞孔深度 (%)"),
    "plug_hole/resin_plug_depth": ("塞孔", "树脂塞孔深度", "resin_plug_depth_percent", "ge",
                                   "树脂塞孔深度 (%)"),
    "plug_hole/resin_plug_cap_copper": ("塞孔", "树脂塞孔盖铜", None, None, "盖铜厚度 (um)"),
    "plug_hole/plug_aspect_ratio": ("塞孔", "塞孔纵横比", "plug_hole_aspect_ratio", "le",
                                    "塞孔孔径与板厚 (mm)"),
    "plug_hole/plug_alignment": ("塞孔", "塞孔对位精度", None, None, "塞孔对位检测 (mil)"),
    # ---- 阻焊 ----
    "solder_mask/min_opening": ("阻焊", "最小阻焊开窗", "solder_mask_opening", "ge",
                                "阻焊开窗尺寸 (mm)"),
    "solder_mask/opening_tolerance": ("阻焊", "开窗公差", None, None, "阻焊开窗公差 (%)"),
    # ---- 丝印 ----
    "silkscreen/min_line_width": ("丝印", "最小丝印线宽", "silkscreen_width", "ge",
                                  "丝印线宽 (mm)"),
    "silkscreen/min_clearance": ("丝印", "最小丝印间隙", "silkscreen_clearance", "ge",
                                 "丝印间隙 (mm)"),
    "silkscreen/min_to_solder_mask": ("丝印", "丝印到阻焊", "silkscreen_to_mask", "ge",
                                      "丝印到阻焊距离 (mm)"),
    "silkscreen/min_char_size": ("丝印", "最小字符尺寸", "silkscreen_char_width", "ge",
                                 "丝印字符宽/高 (mm)"),
    "silkscreen/min_to_outline": ("丝印", "丝印离成型线", "silkscreen_to_outline", "ge",
                                  "丝印到板框距离 (mm)"),
    "silkscreen/ink_max_thickness": ("丝印", "丝印油墨最大厚度", None, None, "油墨厚度 (um)"),
    "silkscreen/ink_min_thickness": ("丝印", "丝印油墨最小厚度", None, None, "油墨厚度 (um)"),
    # ---- 表面处理 ----
    "surface_finish/thickness": ("表面处理", "表面处理厚度", None, None, "表面处理类型与厚度"),
    # ---- 测试 ----
    "test/min_pad_2wire": ("测试", "电测 PAD(二线)", "test_pad_2wire", "ge",
                           "电测焊盘尺寸 (mm)"),
    "test/min_pad_4wire": ("测试", "电测 PAD(四线)", "test_pad_4wire", "ge",
                           "四线电测焊盘尺寸 (mm)"),
    # ---- 软板专用 ----
    "coverlay/window": ("覆盖膜", "覆盖膜开窗", "coverlay_window", "ge", "开窗尺寸 (mm)"),
    "coverlay/spacing": ("覆盖膜", "覆盖膜开窗间距", "coverlay_spacing", "ge",
                         "开窗间距 (mm)"),
    "coverlay/to_pad": ("覆盖膜", "开窗到焊盘", "coverlay_to_pad", "ge", "开窗到焊盘 (mm)"),
    "coverlay/to_trace": ("覆盖膜", "开窗到线路", "coverlay_to_trace", "ge",
                          "开窗到线路 (mm)"),
}

_UNIT_MM = {"mil": 0.0254, "um": 0.001, "mm": 1.0, "microns": 0.001}


def _flatten(node, path: Tuple[str, ...] = (), out=None) -> List[Tuple[str, dict]]:
    """把能力库某个等级摊平成 [(路径, 原始节点)]。"""
    out = [] if out is None else out
    if isinstance(node, dict):
        if "value" in node or "width_mil" in node or "height_mil" in node:
            out.append(("/".join(path), node))
            return out
        for key, val in node.items():
            _flatten(val, path + (key,), out)
    return out


def _cap_in_mm(node: dict) -> Optional[float]:
    """把能力值换算成 mm (适用于长度类); 非长度类返回 None。"""
    unit = (node.get("unit") or "").lower()
    value = node.get("value")
    if isinstance(value, (int, float)) and unit in _UNIT_MM:
        return round(float(value) * _UNIT_MM[unit], 4)
    if "width_mil" in node:
        return round(float(node["width_mil"]) * 0.0254, 4)
    return None


def _raw_text(node: dict) -> str:
    """能力值的可读文本 (原单位)。"""
    if "width_mil" in node:
        return f"{node.get('width_mil')}x{node.get('height_mil', '?')}mil"
    value = node.get("value")
    if isinstance(value, (list, tuple)):
        return " / ".join(str(v) for v in value)
    unit = node.get("unit") or ""
    return f"{value}{unit}" if unit else f"{value}"


def coverage_rows(capability: dict, design: dict, existing_items: List[dict],
                  num, to_mm) -> Tuple[List[dict], dict]:
    """返回 (补充的能力库覆盖行, 统计)。已判定的项不重复。"""
    leaves = _flatten(capability or {})
    existing_fields = {it.get("field") for it in existing_items if it.get("field")}
    existing_names = {it.get("item") for it in existing_items}
    rows: List[dict] = []
    judged = covered = missing = unmapped = 0

    for path, node in leaves:
        meta = CAP_ITEMS.get(path)
        if meta:
            cat, name, field, direction, need = meta
        else:
            top = path.split("/")[0]
            cat, name = top, path.split("/")[-1].replace("_", " ")
            field, direction, need = None, None, "能力库未映射的设计字段"

        if (field and field in existing_fields) or name in existing_names:
            covered += 1
            continue

        if field and direction:
            val = num(design.get(field))
            cap_val = _cap_in_mm(node)
            if val is not None and cap_val is not None:
                unit = "mm"
                if direction == "ge":
                    ok = val >= cap_val
                    msg = (f"{name}: 设计 {val}mm 满足能力 ≥{cap_val}mm" if ok
                           else f"{name}: 设计 {val}mm 低于制程能力下限 ≥{cap_val}mm")
                else:
                    ok = val <= cap_val
                    msg = (f"{name}: 设计 {val}mm 满足能力 ≤{cap_val}mm" if ok
                           else f"{name}: 设计 {val}mm 超过制程能力上限 ≤{cap_val}mm")
                rows.append({
                    "category": cat, "item": name, "design": val,
                    "capability": cap_val, "unit": unit,
                    "status": "pass" if ok else "fail",
                    "message": msg,
                    "note": "能力库自动覆盖项", "field": field,
                    "source": "能力库",
                })
                judged += 1
            else:
                rows.append({
                    "category": cat, "item": name, "design": None,
                    "capability": _cap_in_mm(node), "unit": "mm",
                    "status": "info",
                    "message": f"缺数据: 能力 ≥{_raw_text(node)}; 需要 {need}",
                    "note": "能力库里有这一项, 这次没有设计值",
                    "field": field, "source": "能力库",
                })
                missing += 1
        else:
            rows.append({
                "category": cat, "item": name, "design": None,
                "capability": _cap_in_mm(node), "unit": "",
                "status": "info",
                "message": f"能力项 {_raw_text(node)}: 程序没有对应设计字段, 需 {need}",
                "note": "能力库覆盖清单: 该项未接入自动判定",
                "field": "", "source": "能力库",
            })
            unmapped += 1

    stats = {"total": len(leaves), "covered": covered, "judged": judged,
             "missing": missing, "unmapped": unmapped}
    return rows, stats

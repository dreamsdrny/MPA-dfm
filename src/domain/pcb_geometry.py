"""新增制造规则：真实孔轮廓和显式逐焊盘资料，缺数据不等于通过。"""
from math import isfinite

from shapely.geometry import Point, Polygon, box
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree

from src.domain.pcb_rules import _to_mm
from src.domain.layout_geometry import polygons

TOLERANCE = .004  # 两个离散轮廓的保守判定带。


def _limit(options, key):
    value = options.get("limits", {}).get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{key} 必须为非负有限毫米数")
    value = float(value)
    if not isfinite(value) or value < 0:
        raise ValueError(f"{key} 必须为非负有限毫米数")
    return value


def supplemental_checks(available, hole_records, outline, capability, layers, options, limits,
                        emit, pairs, coverage, errors, *, object_rule_bundle):
    """由上层统一控制问题预算；跨文件比较只使用明确对齐的数据。"""
    if not isinstance(options.get("limits", {}), dict):
        raise ValueError("limits 必须为板厂确认的毫米能力对象")

    def covered(rule, status, note, required_data="", **attrs):
        coverage.append(dict(rule=rule, status=status, note=note, required_data=required_data, **attrs))

    def minimum(film, point, name, field, actual, required, note, **attrs):
        if actual < required + TOLERANCE:
            emit(film, point, name, field, actual, required,
                 "fail" if actual < required - TOLERANCE else "warn_limit", note, **attrs)

    contacts_budget = 0
    budget_exhausted = False

    diameter_checked = diameter_missing = edge_checked = edge_missing = 0
    copper_checked = copper_missing = 0
    npth_copper_limit = _limit(options, "npth_to_copper_mm")
    npth_edge_limit = _to_mm(capability.get("drill", {}).get("npth_to_edge"))
    indexed = {}
    for role, film in available.items():
        if role.startswith("copper"):
            index = film.get("copper_index")
            if not index:
                from src.domain.stackup import layer_number
                index = layer_number(role.removeprefix("copper_"), max((r["index"] for r in layers), default=0))
            if index:
                pieces = polygons(film["geometry"])
                indexed[index] = (film, pieces, STRtree(pieces))
    for drill, hole in hole_records:
        point = hole["geometry"].representative_point()
        process = hole["process"]
        keys = ("min_mech_drill", "max_mech_drill") if process == "mechanical" else ("min_laser_via",) if process == "laser" else ()
        values = [_to_mm(capability.get("drill", {}).get(key)) for key in keys]
        if not keys or any(value is None for value in values):
            diameter_missing += 1
        for key, required in zip(keys, values):
            if required is None:
                continue
            diameter_checked += 1
            actual = hole["diameter_mm"]
            note = f"Excellon 工具孔径；孔索引 {hole['index']}；{process}；非成品孔公差证明"
            if key.startswith("max"):
                if actual > required + TOLERANCE:
                    emit(drill, point, "Excellon 最大机械工具孔径", "gerber_drill_diameter_max",
                         actual, required, "fail", note, comparison="max")
                elif actual > required - TOLERANCE:
                    emit(drill, point, "Excellon 最大机械工具孔径", "gerber_drill_diameter_max",
                         actual, required, "warn_limit", note, comparison="max")
            else:
                minimum(drill, point, "Excellon 最小工具孔径", "gerber_drill_diameter_min", actual, required, note)
        if hole["plated"] is not False:
            if hole["plated"] is None:
                edge_missing += 1
                copper_missing += 1
            continue
        if not hole["alignment_confirmed"] or outline.is_empty or npth_edge_limit is None:
            edge_missing += 1
        else:
            edge_checked += 1
            actual = hole["geometry"].distance(outline.boundary) if outline.covers(hole["geometry"]) else 0
            minimum(drill, point, "Excellon NPTH 孔壁到板边", "gerber_npth_edge", actual, npth_edge_limit,
                    f"实际圆孔/槽孔轮廓到封闭板框（含挖空）；孔索引 {hole['index']}")
        start, end = hole["start_layer"], hole["end_layer"]
        if not hole["alignment_confirmed"] or start is None or npth_copper_limit is None:
            copper_missing += 1
            continue
        if budget_exhausted:
            copper_missing += 1
            continue
        expected_count = max((r["index"] for r in layers), default=0)
        if not expected_count or end > expected_count:
            copper_missing += 1
            continue
        for index in range(start, end + 1):
            if index not in indexed:
                copper_missing += 1
                continue
            copper_checked += 1
            film, pieces, tree = indexed[index]
            for j in tree.query(hole["geometry"], predicate="dwithin", distance=npth_copper_limit + TOLERANCE):
                contacts_budget += 1
                if contacts_budget > limits.max_candidates:
                    if not budget_exhausted:
                        errors.append("NPTH 到铜箔候选超过预算，检查未完成")
                        covered("npth_to_copper_budget", "incomplete", "候选预算耗尽", "提高扫描预算后重新分析")
                    budget_exhausted = True
                    break
                shape = pieces[int(j)]
                actual = hole["geometry"].distance(shape)
                a, b = nearest_points(hole["geometry"], shape)
                minimum(film, Point((a.x+b.x)/2, (a.y+b.y)/2), "Gerber NPTH 孔壁到铜箔",
                        "gerber_npth_copper", actual, npth_copper_limit,
                        f"同跨度实际孔壁到最终铜箔；孔索引 {hole['index']}；使用显式板厂能力",
                        drill_source=drill["path"])
            if budget_exhausted:
                copper_missing += 1
                break
    for rule, checked, missing, need in (
            ("drill_tool_diameter", diameter_checked, diameter_missing, "Excellon + mechanical/laser 工艺属性 + 板厂孔径能力"),
            ("npth_to_edge", edge_checked, edge_missing, "Excellon + plated:false + 坐标对齐 + 封闭板框 + 板厂孔边距"),
            ("npth_to_copper", copper_checked, copper_missing, "NPTH 跨层/对齐 + 全跨度铜层 + limits.npth_to_copper_mm")):
        covered(rule, "measured" if checked and not missing else "partial" if checked else "missing",
                f"已测量 {checked} 项；缺数据 {missing} 项；无问题不等于成品工艺保证", need)

    # Different-net hole wall clearance requires object-level nets, not diameter inference.
    count = max((r["index"] for r in layers), default=0)
    eligible = [record for record in hole_records if record[1].get("net") and record[1]["plated"] is True
                and record[1]["alignment_confirmed"] and record[1]["start_layer"] == 1
                and count and record[1]["end_layer"] == count]
    required = _to_mm(capability.get("trace", {}).get("hole_to_hole_spacing", {}).get("pth_to_pth"))
    holes_missing = len(hole_records) - len(eligible)
    if required is not None and eligible:
        shapes = [h["geometry"] for _, h in eligible]
        for i, j in pairs(shapes, required + TOLERANCE, "different_net_hole_spacing"):
            first, a = eligible[i]
            second, b = eligible[j]
            # pth_to_pth only applies to full-span through holes; no blind-via guess.
            if a["net"] == b["net"]:
                continue
            p, q = nearest_points(a["geometry"], b["geometry"])
            minimum(first, Point((p.x+q.x)/2, (p.y+q.y)/2), "Excellon 不同网络通孔孔壁间距",
                    "gerber_pth_spacing", a["geometry"].distance(b["geometry"]), required,
                    f"孔索引 {a['index']}/{b['index']}；网络 {a['net']}/{b['net']}；同版本显式孔网络资料",
                    other_source=second["path"])
    covered("different_net_hole_spacing", "partial" if eligible and required is not None else "missing",
            f"有网络/对齐/跨度的孔 {len(eligible)} 个；未覆盖盲埋孔专用能力", "逐孔 nets 索引映射 + 孔跨度/对齐 + 叠构 + 板厂孔壁间距",
            missing=holes_missing)

    pad_data = options.get("pad_geometry", {})
    if not isinstance(pad_data, dict):
        raise ValueError("pad_geometry 必须为对象")
    pads = pad_data.get("pads", [])
    if not isinstance(pads, list):
        raise ValueError("pad_geometry.pads 必须为逐焊盘数组")
    aligned = (pad_data.get("unit") == "mm" and pad_data.get("coordinate_system") == "gerber"
               and pad_data.get("alignment_confirmed") is True)
    mask_limit = _limit(options, "pad_to_mask_clearance_mm")
    pad_checked = pad_missing = contacts = 0
    seen, net_contacts = set(), {}
    mask_indices = {}
    for role in ("mask_top", "mask_bottom"):
        if role in available:
            pieces = polygons(available[role]["geometry"])
            mask_indices[role] = (pieces, STRtree(pieces))
    if pads and not aligned:
        errors.append("逐焊盘资料未明确 mm / Gerber 坐标系 / 对齐确认，未进行焊盘几何比较")
    for obj in pads if aligned else []:
        if not isinstance(obj, dict):
            raise ValueError("逐焊盘记录必须为对象")
        ident, index = str(obj.get("id", "")).strip(), obj.get("layer")
        if not ident or isinstance(index, bool) or not isinstance(index, int) or index < 1 or (ident, index) in seen:
            raise ValueError("焊盘需要唯一 id/正整数 layer")
        seen.add((ident, index))
        try:
            coords = obj["outline"]
            if any(len(p) != 2 or any(isinstance(v, bool) or not isfinite(float(v)) for v in p) for p in coords):
                raise ValueError("轮廓坐标无效")
            shape = Polygon(coords)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"焊盘 {ident}: 需要有效毫米 outline 多边形") from exc
        if shape.is_empty or not shape.is_valid or shape.area <= 0:
            raise ValueError(f"焊盘 {ident}: outline 多边形无效")
        if index not in indexed:
            pad_missing += 1
            continue
        film, pieces, tree = indexed[index]
        # Never buffer the entire board for every pad: clip indexed neighbors first.
        x0, y0, x1, y1 = shape.bounds
        window = box(x0-.01, y0-.01, x1+.01, y1+.01)
        local = unary_union([pieces[int(j)].intersection(window) for j in tree.query(window)])
        if not local.buffer(.002).covers(shape):
            pad_missing += 1
            errors.append(f"焊盘 {ident}/L{index}: 轮廓与最终铜箔不一致，检查版本/极性/坐标，未判定")
            continue
        if obj.get("net"):
            for j in tree.query(shape, predicate="intersects"):
                contacts += 1
                if contacts > limits.max_candidates:
                    errors.append("焊盘网络连通候选超过扫描预算，检查未完成")
                    covered("pad_net_contacts_budget", "incomplete", "候选预算耗尽", "提高扫描预算后重新分析")
                    break
                if pieces[int(j)].intersection(shape).area > .002 ** 2:
                    net_contacts.setdefault((index, int(j)), {}).setdefault(str(obj["net"]), []).append(ident)
            if contacts > limits.max_candidates:
                break
        outer = next((r for r in layers if r["index"] == index), None)
        if not outer or not outer["outer"]:
            continue
        if obj.get("mask_required") is not True:
            pad_missing += 1  # 不推测 SMD/NSMD、覆盖盘或不需开窗焊盘。
            continue
        mask = available.get("mask_top" if index == 1 else "mask_bottom")
        if mask is None or mask_limit is None:
            pad_missing += 1
            continue
        pad_checked += 1
        openings, mask_tree = mask_indices[mask["role"]]
        margin = max(mask_limit * 2 + TOLERANCE, .05)
        mask_window = box(x0-margin, y0-margin, x1+margin, y1+margin)
        local_mask = unary_union([openings[int(j)].intersection(mask_window) for j in mask_tree.query(mask_window)])
        # Far-away clipped boundaries give a safe lower bound above the rule limit.
        fully_open = local_mask.covers(shape)
        actual = shape.distance(local_mask.boundary) if fully_open else 0
        if mask_limit > 0:
            minimum(film, shape.representative_point(), "逐焊盘到阻焊开窗边界", "gerber_pad_mask_clearance",
                    actual, mask_limit, f"焊盘 {ident}；仅明确要求扩大开窗的焊盘；实际外边界到同面开窗边界", padstack=ident)
        # Zero configured allowance still cannot excuse a physically covered required pad.
        if mask_limit == 0 and not fully_open:
            emit(film, shape.representative_point(), "逐焊盘阻焊覆盖候选", "gerber_pad_mask_covered", 0, 0,
                 "warn_limit", f"焊盘 {ident} 未完全开窗，SMD/NSMD 及近似边界需复核", padstack=ident)
    for (index, j), nets in net_contacts.items():
        if len(nets) > 1:
            film, pieces, _ = indexed[index]
            emit(film, pieces[j].representative_point(), "不同网络焊盘铜连通候选", "gerber_pad_net_contact", 0, 0,
                 "warn_limit", "同一连通铜块接触多个显式网络焊盘；需排除 net-tie/有意短接及资料错误；网络: "
                 + ", ".join(list(nets)[:8]))
    covered("pad_to_mask_clearance", "measured" if pad_checked and not pad_missing and pad_data.get("complete") is True
            else "partial" if pad_checked else "missing", f"已测量 {pad_checked} 个外层焊盘；未判定 {pad_missing} 个；"
            "逐焊盘完整性需明确确认", "pad_geometry: mm/Gerber 对齐、逐盘 outline/layer/mask_required、complete + limits.pad_to_mask_clearance_mm")
    covered("pad_net_contacts", "partial" if net_contacts else "missing",
            "仅同层连通铜块的逐焊盘网络冲突候选；不证明全网络开短路、跨层连通或 net-tie 合法性",
            "逐焊盘 net/轮廓/层号 + 完整网表/焊盘栈/跨层连接 + net-tie 例外")
    covered("assembly_and_test_access", "missing", "PCB 本模块不推测器件体积、高度、测试探针可达性；需 SMT 对象检查",
            "BOM、器件实体/禁布轮廓、面别/旋转/高度、测试点与治具能力；使用 SMT 对象资料入口")
    # New object rules use exact copper polygons, not pad bounding boxes or paste.
    from src.domain.pcb_objects import scan_object_checks
    objects = []
    for obj in pads if aligned else []:
        layer = next((film["role"] for index, (film, _, _) in indexed.items()
                      if index == obj["layer"]), None)
        if layer:
            objects.append(dict(obj, layer=layer, geometry=Polygon(obj["outline"]),
                                mask_policy=obj.get("mask_policy") or ("open" if obj.get("mask_required") is True else "")))
    vias = []
    via_data = options.get("via_geometry", {})
    if not isinstance(via_data, dict):
        raise ValueError("via_geometry 必须为对象")
    if via_data.get("unit") == "mm" and via_data.get("coordinate_system") == "gerber" and via_data.get("alignment_confirmed") is True:
        for obj in via_data.get("vias", []):
            layer = indexed.get(obj.get("layer"))
            if not layer or not isinstance(obj.get("id"), str) or not obj["id"].strip():
                raise ValueError("过孔需要有效 id 和铜层 layer")
            shape = Polygon(obj["outline"])
            if any(not isfinite(float(v)) or isinstance(v, bool) for p in obj["outline"] for v in p) or shape.is_empty or not shape.is_valid or shape.area <= 0:
                raise ValueError("过孔铜盘轮廓无效")
            via = dict(obj, layer=layer[0]["role"], geometry=shape)
            if obj.get("hole_outline"):
                hole = Polygon(obj["hole_outline"])
                if any(not isfinite(float(v)) or isinstance(v, bool) for p in obj["hole_outline"] for v in p) or hole.is_empty or not hole.is_valid or hole.area <= 0:
                    raise ValueError("过孔孔壁轮廓无效")
                via["hole_geometry"] = hole
            vias.append(via)
    elif via_data.get("vias"):
        errors.append("逐过孔资料未确认 Gerber 毫米对齐，未进行对象检查")
    extra = scan_object_checks(available, objects, vias, limits, options.get("object_rules"),
                               complete=pad_data.get("complete") is True, rule_bundle=object_rule_bundle)
    for row in extra["checks"]:
        emit(available[row["layer"]], Point(row["x_mm"], row["y_mm"]), row["item"], row["field"],
             row["measured"], row["capability"], row["status"], row["note"], unit=row["unit"],
             **{key: row[key] for key in ("refdes", "other_refdes", "pin", "other_pin", "net", "other_net",
                                         "object_id", "other_object_id", "padstack", "rule_source", "rule_sha256")})
    coverage.extend(extra["coverage"])
    errors.extend(extra["errors"])

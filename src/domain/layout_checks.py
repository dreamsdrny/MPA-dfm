"""共享制造数据库几何规则编排；所有缺数据/预算耗尽保留在覆盖记录。"""
from math import isfinite

from shapely.geometry import Point
from shapely.strtree import STRtree

from src.domain.pcb_rules import _to_mm
from src.domain.layout_geometry import (ScanBudgetExceeded, annular_ring, copper_density,
                                 nearby_pairs, polygons, topology_candidates)


def scan_layout_checks(folder, films, ambiguous, copper_checks, mask_checks, outline,
                       capability, layers, options, limits, *, drills=(), drill_errors=(), object_rule_bundle):
    checks, errors, stats, coverage, densities, balance, drill_layers = [], [], [], [], [], [], []
    truncated = False

    def emit(film, point, item, field, measured, required, status, note, unit="mm", **attrs):
        nonlocal truncated
        if len(checks) >= limits.max_findings:
            if not truncated:
                errors.append("制造几何问题数量超过预算，结果截断，检查未完成")
                truncated = True
            return
        checks.append(dict(item=item, field=field, measured=round(measured, 6),
                           capability=required, status=status,
                           severity="高" if status == "fail" else "中", unit=unit,
                           x_mm=round(point.x, 6), y_mm=round(point.y, 6), layer=film["role"],
                           source=film["path"], brd_alignment=film["brd_alignment"],
                           note=note + "; 轮廓离散近似误差 ≤0.002mm/边", **attrs))

    def pairs(shapes, distance, rule):
        scan = dict(rule=rule)
        stats.append(scan)
        try:
            yield from nearby_pairs(shapes, distance, limits, scan)
        except ScanBudgetExceeded as exc:
            errors.append(f"{rule}: {exc}")

    available = {film["role"]: film for film in films if film["role"] not in ambiguous}
    rows = {row["layer"]: row for row in copper_checks}
    mask_rows = {row["layer"]: row for row in mask_checks}
    for role, film in available.items():
        copper = role.startswith("copper")
        mask = role.startswith("mask")
        if not copper and not mask:
            continue
        row = rows.get(role, {}) if copper else mask_rows.get(role, {})
        width = row.get("width_limit_mm") if copper else None
        gap = row.get("spacing_limit_mm") if copper else row.get("capability")
        scan = dict(layer=role, rule="boundary_topology", complete=False)
        stats.append(scan)
        if width or gap:
            try:
                for candidate in topology_candidates(film["geometry"], width, gap, limits=limits, stats=scan):
                    is_width = candidate["kind"] == "width"
                    field = ("gerber_copper_neck" if is_width else "gerber_copper_slot") if copper else "gerber_mask_web"
                    item = ("Gerber 铜皮窄颈候选" if is_width else "Gerber 铜皮窄槽候选") if copper else "Gerber 连通开窗阻焊桥候选"
                    emit(film, candidate["point"], item, field, candidate["measured"],
                         candidate["required"], "warn_limit",
                         "实际相向边界距离；包含同一连通区域凹槽/孔洞；非完整骨架测量，需 CAM 复核")
            except ScanBudgetExceeded as exc:
                errors.append(f"{role}: {exc}")
        coverage.append(dict(rule="boundary_topology", layer=role,
                             status="partial" if scan["complete"] else "incomplete",
                             note="已扫描边界候选，非完整最小线宽/槽宽证明"))

    density_options = options.get("copper_balance", {})
    if not isinstance(density_options, dict):
        raise ValueError("copper_balance 配置必须为对象")
    cell_mm = float(density_options.get("cell_mm", 10))
    max_delta = density_options.get("max_delta_percent")
    if not isfinite(cell_mm) or cell_mm <= 0:
        raise ValueError("cell_mm 必须为正有限数")
    if max_delta is not None:
        max_delta = float(max_delta)
        if not isfinite(max_delta) or not 0 <= max_delta <= 100:
            raise ValueError("max_delta_percent 必须在 0～100（百分点）")
    for role, film in available.items():
        if not role.startswith("copper"):
            continue
        if outline.is_empty:
            coverage.append(dict(rule="copper_density", layer=role, status="missing",
                                 note="缺有效封闭板框，未使用铜包围盒估算"))
            continue
        try:
            density = copper_density(film["geometry"], outline, cell_mm, limits)
            density.update(layer=role, index=film.get("copper_index") or rows.get(role, {}).get("index"),
                           source=film["path"])
            densities.append(density)
            coverage.append(dict(rule="copper_density", layer=role, status="measured",
                                 note="总面积/网格实际布尔裁切；铜厚/压合翘曲模型不在本规则内"))
        except ScanBudgetExceeded as exc:
            errors.append(f"{role} 铜平衡: {exc}")
            coverage.append(dict(rule="copper_density", layer=role, status="incomplete", note=str(exc)))
    by_index = {row["index"]: row for row in densities if row["index"]}
    # Require the stackup to establish mirror pairs. Do not infer from imported file count.
    if layers:
        count = max(row["index"] for row in layers)
        for i in range(1, count // 2 + 1):
            j = count + 1 - i
            first, second = by_index.get(i), by_index.get(j)
            if not first or not second:
                errors.append(f"L{i}/L{j}: 镜像铜层密度缺失，铜平衡未完成")
                continue
            delta = abs(first["density_percent"] - second["density_percent"])
            second_cells = {(c["ix"], c["iy"]): c for c in second["cells"]}
            cell_deltas = []
            for cell in first["cells"]:
                other = second_cells[(cell["ix"], cell["iy"])]
                value = abs(cell["density_percent"] - other["density_percent"])
                cell_deltas.append(value)
                if max_delta is not None and value > max_delta:
                    emit(available[first["layer"]], Point(cell["x_mm"], cell["y_mm"]),
                         "Gerber 镜像层局部铜平衡", "gerber_copper_balance", value, max_delta, "warn_limit",
                         f"L{i}/L{j} 同一网格铺铜率差值超过显式配置；需板厂评估翘曲，不自行设通用下限",
                         unit="百分点", comparison="max")
            balance.append(dict(first_layer=first["layer"], second_layer=second["layer"],
                                delta_percent=delta, max_cell_delta_percent=max(cell_deltas, default=0),
                                capability=max_delta, status="measured" if max_delta is None else
                                "warn_limit" if max([delta, *cell_deltas]) > max_delta else "pass"))
            if max_delta is not None and delta > max_delta:
                emit(available[first["layer"]], outline.representative_point(), "Gerber 镜像层总铜平衡",
                     "gerber_copper_balance_total", delta, max_delta, "warn_limit",
                     f"L{i}/L{j} 总铺铜率差值；按显式配置评估，不能替代压合/翘曲模型",
                     unit="百分点", comparison="max")
    coverage.append(dict(rule="copper_balance", status="measured" if balance else "missing",
                         note="未配置 max_delta_percent 仅统计，不作制造 PASS/FAIL；需板厂确认"))

    metadata = options.get("drills", {})
    if not isinstance(metadata, dict):
        raise ValueError("drills 配置必须为文件相对路径映射")
    hole_records = []
    errors.extend(drill_errors)
    for drill in drills:
        drill_layers.append(dict(path=drill["path"], role="drill", objects=len(drill["holes"]), warnings=drill["warnings"]))
        if not drill["holes"]:
            errors.append(f"{drill['path']}: 钻孔文件为空，不能确认钻孔覆盖")
        hole_records.extend((drill, hole) for hole in drill["holes"])
    if not hole_records:
        errors.append("缺可用 Excellon 孔图形，孔环/钻孔重叠未检查；BRD Artwork 不自动包含 NC Drill")
        coverage.append(dict(rule="drill_geometry", status="missing", note="需要同版本 Excellon 及孔元数据"))
    else:
        coverage.append(dict(rule="drill_geometry", status="partial", note="钻孔图形已读取；跨层和对齐按显式元数据"))
    shapes = [hole["geometry"] for _, hole in hole_records]
    for i, j in pairs(shapes, .004, "drill_overlap"):
        first, a = hole_records[i]
        second, b = hole_records[j]
        if first["path"] != second["path"] and not (a["alignment_confirmed"] and b["alignment_confirmed"]):
            continue  # No unverified cross-file coordinate comparisons.
        spans_known = all(hole["start_layer"] is not None for hole in (a, b))
        if spans_known and max(a["start_layer"], b["start_layer"]) >= min(a["end_layer"], b["end_layer"]):
            continue  # Stacked blind vias meeting at a layer are not overlapping drill volumes.
        if not a["geometry"].intersects(b["geometry"]):
            continue
        duplicate = a["geometry"].equals(b["geometry"])
        overlap = a["geometry"].intersection(b["geometry"])
        fail = spans_known and not duplicate and overlap.area > .004 ** 2
        emit(first, overlap.representative_point(), "Excellon 钻孔重叠" if not duplicate else "Excellon 重复孔候选",
             "gerber_drill_overlap", 0, 0, "fail" if fail else "warn_limit",
             f"实际圆孔/槽孔轮廓相交；孔索引 {a['index']}/{b['index']}；"
             + ("跨层范围相交" if spans_known else "跨层未知，只作候选")
             + "; 重复孔/工艺重钻需要 CAM 复核", other_source=second["path"])

    copper_by_index = {}
    for role, film in available.items():
        if role.startswith("copper"):
            index = film.get("copper_index") or rows.get(role, {}).get("index")
            if index:
                pieces = polygons(film["geometry"])
                copper_by_index[index] = (film, pieces, STRtree(pieces))
    unknown, processed = 0, 0
    for drill, hole in hole_records:
        if hole["plated"] is False:
            continue
        if (hole["plated"] is not True or hole["process"] is None or hole["start_layer"] is None
                or not hole["alignment_confirmed"]):
            unknown += 1
            continue
        required = _to_mm(capability.get("drill", {}).get(
            "annular_ring_mech" if hole["process"] == "mechanical" else "annular_ring_laser"))
        if required is None:
            unknown += 1
            continue
        # Inner non-functional pads can be deliberately removed. Without pad/net
        # attribution only the drill-span endpoint pads are mandatory here.
        for index in (hole["start_layer"], hole["end_layer"]):
            if index not in copper_by_index:
                unknown += 1
                continue
            film, pieces, tree = copper_by_index[index]
            measured, point = annular_ring(hole["geometry"], film["geometry"], required,
                                           tree=tree, pieces=pieces)
            processed += 1
            if measured < required + .004:
                fail = measured < required - .004
                emit(film, point, "Gerber/Excellon 实际单边孔环", "gerber_annular_ring", measured,
                     required, "fail" if fail else "warn_limit",
                     f"孔壁到最终铜边界；孔索引 {hole['index']}；钻孔跨度端点 L{index}；"
                     "采用工具孔径（非成品孔推算），不计制造位置公差", drill_source=drill["path"])
    if unknown:
        errors.append(f"{unknown} 个孔/端点缺镀孔属性、工艺、跨层、对齐确认或铜层/能力，孔环未检查")
    if any(not hole["alignment_confirmed"] for _, hole in hole_records):
        errors.append("部分 Excellon 未确认坐标对齐，跨文件钻孔重叠未检查")
    coverage.append(dict(rule="annular_ring", status="partial" if processed else "missing",
                         measured_endpoints=processed, missing=unknown,
                          note="仅明确镀孔跨度端点；内层功能盘/非功能盘依赖焊盘栈和网络属性"))
    from src.domain.pcb_geometry import supplemental_checks
    supplemental_checks(available, hole_records, outline, capability, layers, options, limits,
                        emit, pairs, coverage, errors, object_rule_bundle=object_rule_bundle)
    if truncated:
        coverage.append(dict(rule="finding_budget", status="incomplete", note="问题报告截断，不能自动放行"))
    return dict(checks=checks, errors=errors, scan_stats=stats, coverage=coverage,
                copper_density=densities, copper_balance=balance, drill_layers=drill_layers)

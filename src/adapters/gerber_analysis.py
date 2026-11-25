"""Gerber/Excellon 制造几何检查，保留极性、宏与区域。
Gerbonara 负责解析 RS-274X/X2 格式；Shapely 则用于测量最终底片上的特征。
几何图形的近似精度为 0.002 毫米；凡触及边界的情况，均会被列为待审查对象。
"""
import re
import warnings
import json
import math
from time import perf_counter
from pathlib import Path

from gerbonara import GerberFile
from gerbonara.utils import MM
from gerbonara.graphic_objects import Line, Arc
from shapely.geometry import Polygon, LineString, Point, GeometryCollection
from shapely.ops import unary_union, nearest_points

from src.gerber_export import gerber_files
from src.dfm_engine import (load_capability, _to_mm, normalize_copper_um,
                            get_sm_bridge_capability, get_line_spacing_capability)
from src.dfm_stackup import copper_layers, layer_number, match_layer
from src.layout_geometry import (ScanLimits, ScanBudgetExceeded, UnionAccumulator,
                                 nearby_pairs, board_area)

TOLERANCE = 0.002


def identify_layer(path, header):
    name = Path(path).stem.upper()
    suffix = Path(path).suffix.lower()
    explicit = re.search(r"%TF\.FileFunction,([^*]+)", header, re.IGNORECASE)
    content = (explicit.group(1) if explicit else name).upper()
    if explicit:
        fields = [field.strip() for field in content.split(",")]
        if fields[0] in ("PROFILE", "OUTLINE"):
            return "outline"
        if fields[0] in ("LEGEND", "SILKSCREEN"):
            return "silkscreen_top" if "TOP" in fields else "silkscreen_bottom" if "BOT" in fields or "BOTTOM" in fields else "unknown"
        if fields[0] == "COPPER":
            number = next((int(field[1:]) for field in fields if re.fullmatch(r"L\d+", field)), None)
            if "TOP" in fields:
                return "copper_top"
            if "BOT" in fields or "BOTTOM" in fields:
                return "copper_bottom"
            return f"copper_L{number}" if number else "unknown"
        if fields[0] == "SOLDERMASK":
            if "TOP" in fields:
                return "mask_top"
            if "BOT" in fields or "BOTTOM" in fields:
                return "mask_bottom"
            return "unknown"  # 内层/面别不明的阻焊不套到顶层
        if fields[0] == "PASTE":
            return "paste_top" if "TOP" in fields else "paste_bottom" if "BOT" in fields or "BOTTOM" in fields else "unknown"
    side = "bottom" if (re.search(r"(?:^|[_ ,.-])(?:BOTTOM|BOT|B)(?:$|[_ ,.-])", content) or
                        re.search(r"(?:^|[-_])B[-_](?:CU|MASK|SILKS|PASTE)", name)) else "top"
    extensions = {".gto":"silkscreen_top", ".gbo":"silkscreen_bottom", ".gts":"mask_top",
                   ".gbs":"mask_bottom", ".gtl":"copper_top", ".gbl":"copper_bottom",
                   ".gtp":"paste_top", ".gbp":"paste_bottom",
                  ".gko":"outline", ".gm1":"outline"}
    if suffix in extensions:
        return extensions[suffix]
    for extension, mapped in extensions.items():
        if name == extension[1:].upper() or name.endswith("-" + extension[1:].upper()):
            return mapped
    if any(t in content for t in ("SILK", "LEGEND")):
        return "silkscreen_" + side
    if any(t in content for t in ("PASTE", "PMASK", "SPT", "SPB")):
        return "paste_" + ("bottom" if content == "SPB" else side)
    if any(t in content for t in ("SMASK", "SOLDERMASK", "SOLDER_MASK")):
        if re.search(r"(?:^|[_ ,])L\d+(?:$|[_ ,])", content) and not any(t in content for t in ("TOP", "BOT")):
            return "unknown"
        return "mask_" + side
    if any(t in content for t in ("PROFILE", "OUTLINE")) or name in ("PANEL", "GKO", "OL"):
        return "outline"
    if name in ("TOP", "BOTTOM", "GTL", "GBL") or "COPPER" in content:
        return "copper_" + side
    # Common Cadence / KiCad RS-274X names without X2 attributes.
    if "ETCH" in name:
        inner = re.search(r"(?:^|_)L(\d+)(?:_|$)", name)
        if inner:
            return f"copper_L{int(inner[1])}"
        if "TOP" in name or "BOTTOM" in name or "BOT" in name:
            return "copper_" + side
    kicad = re.search(r"(?:^|[-_])(F|B|IN\d+)[-_](CU|MASK|SILKS|PASTE)(?:$|[-_])", name)
    if kicad:
        face, kind = kicad.groups()
        if kind == "CU":
            return f"copper_L{int(face[2:]) + 1}" if face.startswith("IN") else "copper_top" if face == "F" else "copper_bottom"
        return {"MASK": "mask", "SILKS": "silkscreen", "PASTE": "paste"}[kind] + ("_top" if face == "F" else "_bottom")
    if name.endswith("EDGE_CUTS"):
        return "outline"
    # Infer conventional Cadence copper inner films without calling drawings copper.
    numeric = re.fullmatch(r"(?:GND|POWER|LAYER|L|IN)[_-]?(\d+)(?:[_-].*)?", name)
    if numeric:
        return f"copper_L{int(numeric[1])}"
    numeric_suffix = re.fullmatch(r"\.g(\d+)", suffix)
    if numeric_suffix:
        return f"copper_L{int(numeric_suffix[1])}"
    return "unknown"


def _arc_points(cx, cy, x1, y1, x2, y2, clockwise):
    """Approximate in mm without dividing by a zero/rounded-away radius.

    Cadence zero-width strokes produce zero-radius end caps. They have no
    area, but an Outline stroke must still retain its actual centerline.
    Equal endpoints with a nonzero radius are a full circle, not a point.
    """
    if not all(math.isfinite(v) for v in (cx, cy, x1, y1, x2, y2)):
        raise ValueError("Gerber 圆弧包含非有限坐标，不能还原图形")
    radius = math.hypot(x1 - cx, y1 - cy)
    if radius == 0:
        if (x1, y1) != (x2, y2):
            raise ValueError("Gerber 圆弧半径为零但端点不同，不能猜测为直线")
        return [(x1, y1), (x2, y2)]
    from gerbonara.utils import sweep_angle
    sweep = sweep_angle(cx, cy, x1, y1, x2, y2, clockwise)
    # Stable sagitta calculation: r**2 - (r-error)**2 loses precision for
    # large radii. This conservative step keeps the existing 0.002 mm bound.
    error_ratio = min(TOLERANCE / radius, 0.4588038998538031)
    step = 2 * math.asin(math.sqrt(error_ratio / 2))
    if step == 0 or not math.isfinite(radius):
        raise ValueError("Gerber 圆弧半径超出可解析范围")
    count = max(1, math.ceil(sweep / step))
    if count > 100_000:
        raise ValueError("Gerber 单段圆弧离散点超过 100000，图形未完整解析")
    start = math.atan2(y1 - cy, x1 - cx)
    direction = -1 if clockwise else 1
    points = [(x1, y1)]
    for i in range(1, count):
        angle = start + direction * sweep * i / count
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    points.append((x2, y2))
    return points


def _primitive_polygon(primitive):
    # Zero-width strokes / zero-diameter flashes have no filled area. Do not
    # create tiny floating-point slivers from coincident reversed arc edges.
    if getattr(primitive, "width", None) == 0 or getattr(primitive, "r", None) == 0:
        return GeometryCollection()
    arc_poly = primitive.to_arc_poly()
    if not arc_poly.outline:
        return GeometryCollection()
    outline = []
    for (x1, y1), (x2, y2), (clockwise, (cx, cy)) in arc_poly.segments:
        if clockwise is None:
            outline.append((x1, y1))
        else:
            outline.extend(_arc_points(cx, cy, x1, y1, x2, y2, clockwise)[:-1])
    if len(outline) < 3:
        return GeometryCollection()
    polygon = Polygon(outline)
    return polygon if polygon.is_valid else polygon.buffer(0)


def _centerline(obj):
    if isinstance(obj, Line):
        return LineString([(obj.x1, obj.y1), (obj.x2, obj.y2)])
    if isinstance(obj, Arc):
        return LineString(_arc_points(obj.cx+obj.x1, obj.cy+obj.y1, obj.x1, obj.y1, obj.x2, obj.y2,
                                      obj.clockwise))
    return None


def read_film(path):
    path = Path(path)
    with path.open(encoding="ascii", errors="replace") as stream:
        header = stream.read(20000)
    if not re.search(r"%(?:FS|ADD|AM)", header, re.IGNORECASE):
        raise ValueError("不是有效的 RS-274X/X2 Gerber（缺坐标格式/孔径定义）；仅修改扩展名不能导入")
    role = identify_layer(path, header)
    with warnings.catch_warnings(record=True) as notices:
        warnings.simplefilter("always")
        gerber = GerberFile.open(path)
        objects = []
        film = GeometryCollection()
        pending, previous_polarity = UnionAccumulator(), None

        def commit():
            nonlocal film, pending
            if pending.pending or any(shape is not None for shape in pending.levels):
                group = pending.finish()
                film = film.union(group) if previous_polarity else film.difference(group)
                pending = UnionAccumulator()

        for index, original in enumerate(gerber.objects):
            obj = original.converted(MM) if isinstance(original, (Line, Arc)) else original
            pieces = GeometryCollection()
            # Aperture macro clear primitives subtract only within this aperture.
            for primitive in obj.to_primitives(unit=MM):
                poly = _primitive_polygon(primitive)
                same_as_object = bool(primitive.polarity_dark) == bool(obj.polarity_dark)
                pieces = pieces.union(poly) if same_as_object else pieces.difference(poly)
            if pieces.is_empty:
                if role == "outline" and isinstance(obj, (Line, Arc)):
                    objects.append(dict(index=index, geometry=pieces, width=0, dark=bool(obj.polarity_dark),
                                        centerline=_centerline(obj)))
                continue
            polarity = bool(obj.polarity_dark)
            if previous_polarity is not None and previous_polarity != polarity:
                commit()
            previous_polarity = polarity
            pending.add(pieces)
            width = None
            if isinstance(obj, (Line, Arc)):
                aperture = getattr(obj, "aperture", None)
                diameter = getattr(aperture, "diameter", None)
                if diameter is not None:
                    width = float(MM(diameter, aperture.unit))
            objects.append(dict(index=index, geometry=pieces, width=width, dark=polarity,
                                centerline=_centerline(obj)))
        commit()
    aligned = bool(re.search(r"G04\s+File Origin:\s*Cadence Allegro", header, re.IGNORECASE))
    aligned &= not bool(re.search(r"G04\s+Mirror:\s*(?:Yes|True)", header, re.IGNORECASE))
    aligned &= not bool(re.search(r"%LM|%LR|%LS|%OF|%SF", header, re.IGNORECASE))
    offset = re.search(r"G04\s+Offset:\s*\(\s*([-\d.]+)\s+([-\d.]+)\s*\)", header, re.IGNORECASE)
    rotation = re.search(r"G04\s+Rotation:\s*([-\d.]+)", header, re.IGNORECASE)
    if offset:
        aligned &= abs(float(offset[1])) < 1e-9 and abs(float(offset[2])) < 1e-9
    if rotation:
        aligned &= abs(float(rotation[1])) < 1e-9
    if "%IPNEG" in header.upper():
        raise ValueError("负片需明确图幅裁切范围，暂不作为位置判定依据")
    if any("unknown" in str(n.message).lower() or "unsupported" in str(n.message).lower() for n in notices):
        raise ValueError("Gerber 有未识别命令，不能完整还原图形")
    function = re.search(r"%TF\.FileFunction,Copper,L(\d+),", header, re.IGNORECASE)
    copper_index = int(function[1]) if function else layer_number(role.removeprefix("copper_"))
    return dict(path=str(path.resolve()), role=role, geometry=film, objects=objects,
                copper_index=copper_index,
                warnings=[str(n.message) for n in notices], brd_alignment=bool(aligned))


def _points(geometry):
    if geometry.is_empty:
        return []
    if hasattr(geometry, "geoms"):
        return [g for g in geometry.geoms if not g.is_empty]
    return [geometry]


def analyze_gerbers(folder, factory="hongban", board_type="rigid", level="standard", copper_um=35.0,
                    stackup=None, layer_count=None, geometry_options=None):
    """制造图形扫描；几何覆盖独立于参数能力比对，不因无缺陷自动放行。

    可选目录文件 dfm_geometry.json：drills 元数据、铜平衡规则及扫描预算。
    geometry_options（字典）覆盖该配置。元数据缺失不从文件名猜跨层/孔型。
    """
    started = perf_counter()
    from src.dfm_engine import capability_fingerprint
    rule_sha256 = capability_fingerprint(factory)
    options = {}
    manifest = Path(folder) / "dfm_geometry.json"
    if manifest.is_file():
        from src.resource_limits import read_bounded
        options = json.loads(read_bounded(manifest, 16 * 1024 * 1024).decode("utf-8-sig"))
        if not isinstance(options, dict):
            raise ValueError("dfm_geometry.json 必须为对象")
    if geometry_options is not None:
        if not isinstance(geometry_options, dict):
            raise ValueError("geometry_options 必须为对象")
        options.update(geometry_options)
    if options.get("drill_directory"):
        directory = Path(options["drill_directory"])
        if not directory.is_absolute():
            options["drill_directory"] = str((Path(folder) / directory).resolve())
    limits = ScanLimits(**options.get("scan_limits", {}))
    scan_stats, coverage, densities = [], [], []
    copper_um = normalize_copper_um(copper_um)
    capability = load_capability(board_type, level, factory)
    layers = copper_layers(dict(stackup=stackup or [], layer_count=layer_count or 0), copper_um)
    silk_cap = capability.get("silkscreen", {})
    films, errors, results = [], [], []
    mask_bridge_checks = []
    copper_layer_checks = []
    for path in gerber_files(folder):
        try:
            with path.open(encoding="ascii", errors="replace") as stream:
                header = stream.read(20000)
            if identify_layer(path, header) == "unknown":
                coverage.append(dict(rule="layer_identification", layer=path.name, status="missing",
                                     note="图层未识别，未参与判定", required_data="明确 X2 FileFunction 或可识别图层文件名"))
                continue
            film = read_film(path)
            if film["role"] != "unknown":
                films.append(film)
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    if not films:
        raise ValueError("未读到可识别的 Gerber 图层。请确认 RS-274X/X2、图层名称与单位。\n" + "\n".join(errors[:5]))
    by_role = {}
    for film in films:
        by_role.setdefault(film["role"], []).append(film)
    copper_by_index = {}
    for film in films:
        if film["role"].startswith("copper"):
            index = film.get("copper_index") or layer_number(film["role"].removeprefix("copper_"), len(layers) or None)
            if index:
                copper_by_index.setdefault(index, []).append(film)
    # Multiple same-role films may be different boards/revisions: do not merge silently.
    ambiguous = {r for r, values in by_role.items() if len(values) > 1}
    for index, values in copper_by_index.items():
        if len(values) > 1:
            ambiguous.update(film["role"] for film in values)
            errors.append(f"L{index}: 多个文件映射到同一铜层，未检查；请保留同一版本的一套 Gerber")
    for role in ambiguous:
        errors.append(f"{role}: 多个同类图层，请在独立目录保留同一版本的一套 Gerber")
    edge_films = by_role.get("outline", []) if "outline" not in ambiguous else []
    centerlines = [o["centerline"] for f in edge_films for o in f["objects"]
                   if o["dark"] and o["centerline"] is not None]
    edges = unary_union(centerlines) if centerlines else GeometryCollection()
    outline, outline_error = board_area(centerlines)
    edge_is_authoritative = not outline.is_empty and not outline_error
    coverage.append(dict(rule="board_outline", status="measured" if edge_is_authoritative else "missing",
                         note="封闭板框包含挖空；不使用包围盒推测", required_data="封闭且无悬挂线的 Profile/Outline Gerber"))
    if outline_error:
        errors.append(outline_error)

    def add(film, shape, item, field, measured, required, status, note, unit="mm", **attrs):
        if len(results) >= limits.max_findings:
            message = f"问题位置超过 {limits.max_findings}，报告已截断，扫描未完成"
            if message not in errors:
                errors.append(message)
            return
        point = shape if isinstance(shape, Point) else shape.representative_point()
        results.append(dict(item=item, field=field, measured=round(measured, 6), capability=required,
            status=status, severity="高" if status == "fail" else "中", unit=unit,
            x_mm=round(point.x, 6), y_mm=round(point.y, 6), layer=film["role"],
            source=film["path"], note=note + f"；Gerber 图形近似误差 ≤{TOLERANCE}mm",
            brd_alignment=film["brd_alignment"], **attrs))

    def pairs(pieces, distance, role, rule):
        stats = dict(layer=role, rule=rule)
        scan_stats.append(stats)
        try:
            yield from nearby_pairs(pieces, distance, limits, stats)
        except ScanBudgetExceeded as exc:
            errors.append(f"{role} {rule}: {exc}")

    for film in films:
        role, solid = film["role"], film["geometry"]
        if role in ambiguous:
            continue
        if role.startswith("silkscreen"):
            side = role.split("_")[-1]
            width_limit = _to_mm(silk_cap.get("min_line_width"))
            for obj in film["objects"]:
                if obj["dark"] and obj["width"] and width_limit and obj["width"] < width_limit - TOLERANCE:
                    rendered = obj["geometry"].intersection(solid)
                    line = obj["centerline"]
                    if not rendered.is_empty and line is not None and not solid.covers(line.buffer((width_limit-TOLERANCE)/2)):
                        add(film, rendered, "Gerber 丝印线宽", "gerber_silk_width", obj["width"], width_limit,
                            "fail", "圆孔径实际描画线宽低于能力；已应用图层极性")
            mask_role = "mask_" + side
            mask_films = by_role.get(mask_role, []) if mask_role not in ambiguous else []
            mask_limit = _to_mm(silk_cap.get("min_to_solder_mask"))
            if mask_films and mask_limit:
                openings = mask_films[0]["geometry"]
                for piece in _points(solid.intersection(openings.buffer(mask_limit))):
                    gap = piece.distance(openings)
                    if gap < mask_limit - TOLERANCE:
                        marker = nearest_points(piece, openings)[0]
                        add(film, marker, "Gerber 丝印到阻焊开窗", "gerber_silk_mask", gap, mask_limit,
                            "fail", "实际丝印与同面阻焊开窗重叠/间距不足")
            elif not mask_films:
                errors.append(f"{role}: 缺少同面阻焊层，未检查丝印到阻焊开窗")
            edge_limit = _to_mm(silk_cap.get("min_to_outline"))
            if edge_is_authoritative and edge_limit:
                for piece in _points(solid.intersection(edges.buffer(edge_limit))):
                    gap = piece.distance(edges)
                    if gap < edge_limit - TOLERANCE:
                        add(film, nearest_points(piece, edges)[0], "Gerber 丝印到板边", "gerber_silk_edge", gap, edge_limit,
                            "fail", "丝印图形到外形中心线间距不足")
        if role in ("mask_top", "mask_bottom"):
            outer_row = (next((row for row in layers if row["index"] == 1), None) if role == "mask_top"
                         else next((row for row in reversed(layers) if row["outer"]), None))
            thickness = outer_row["copper_um"] if outer_row else copper_um
            bridge = get_sm_bridge_capability(capability, thickness)
            pieces = _points(solid)
            if bridge is None:
                errors.append(f"{role}: 外层成品铜厚 {thickness:g}um 无阻焊桥能力数据（缺表或超出铜厚范围），需板厂评估")
            elif not pieces:
                errors.append(f"{role}: 阻焊图形为空，不能确认开窗/阻焊桥")
            mask_bridge_checks.append(dict(layer=role, capability=bridge, copper_um=thickness,
                                           copper_layer=outer_row["name"] if outer_row else role,
                                           opening_count=len(pieces), source=film["path"],
                                           checked=bridge is not None and bool(pieces)))
            if bridge and pieces:
                for i, j in pairs(pieces, bridge + 2 * TOLERANCE, role, "mask_bridge"):
                    distance = pieces[i].distance(pieces[j])
                    # 两个开窗轮廓各有近似误差；很窄的桥也要报。
                    if 0 < distance < bridge + 2 * TOLERANCE:
                        a, b = nearest_points(pieces[i], pieces[j])
                        failed = distance < bridge - 2 * TOLERANCE
                        note = ("两个独立阻焊开窗之间桥宽低于下限，不能按当前制程能力生产" if failed
                                else "桥宽接近制程下限且处于几何近似误差范围，需 CAM 复核")
                        add(film, Point((a.x+b.x)/2, (a.y+b.y)/2), "Gerber 阻焊桥", "gerber_mask_bridge",
                            distance, bridge, "fail" if failed else "warn_limit",
                            f"{note}（外层成品铜厚 {thickness:g}um；能力按铜厚向上选档）")
        if role.startswith("copper"):
            index = film.get("copper_index") or layer_number(role.removeprefix("copper_"), len(layers) or None)
            layer = match_layer(layers, name=Path(film["path"]).stem, index=index)
            if layers and layer is None:
                errors.append(f"{role}: 无法映射到当前叠构铜层，线宽/线距未检查")
                copper_layer_checks.append(dict(layer=role, index=index, copper_um=None, checked=False))
                continue
            thickness = layer["copper_um"] if layer else (copper_um if role in ("copper_top", "copper_bottom") else None)
            if thickness is None:
                errors.append(f"{role}: 未取得该层叠构铜厚，线宽/线距未检查（不套用外层 copper_um）")
                copper_layer_checks.append(dict(layer=role, index=index, copper_um=None, checked=False))
                continue
            width_um, spacing_um = get_line_spacing_capability(capability, thickness)
            width_limit = width_um / 1000 if width_um is not None else None
            spacing_limit = spacing_um / 1000 if spacing_um is not None else None
            copper_layer_checks.append(dict(layer=role, index=index, copper_um=thickness,
                checked=width_limit is not None and spacing_limit is not None,
                width_limit_mm=width_limit, spacing_limit_mm=spacing_limit,
                source=film["path"], scope="描画线宽、独立铜块距离及相向边界窄颈/窄槽候选；非完整骨架/网络 DRC"))
            if width_limit is None or spacing_limit is None:
                errors.append(f"{role}: 铜厚 {thickness:g}um 无线宽/线距能力数据")
            if solid.is_empty:
                errors.append(f"{role}: 铜层图形为空，不能确认线宽/线距；如为无铜层需人工确认")
                copper_layer_checks[-1]["checked"] = False
            for obj in film["objects"]:
                if obj["dark"] and obj["width"] and width_limit and obj["width"] < width_limit - TOLERANCE:
                    # An individual thin primitive can be merged into a plane; only
                    # flag it if its surrounding required width is not all copper.
                    line = obj["centerline"]
                    if line is not None and not solid.covers(line.buffer((width_limit-TOLERANCE)/2)):
                        rendered = obj["geometry"].intersection(solid)
                        if not rendered.is_empty:
                            add(film, rendered, "Gerber 铜层描画线宽候选", "gerber_copper_width", obj["width"], width_limit,
                                "warn_limit", f"铜厚 {thickness:g}um；局部描画线宽不足候选；不含负片/完整颈宽拓扑，需复核")
            pieces = _points(solid)
            if spacing_limit and pieces:
                for i, j in pairs(pieces, spacing_limit + 2 * TOLERANCE, role, "copper_spacing"):
                    gap = pieces[i].distance(pieces[j])
                    if 0 < gap < spacing_limit + 2 * TOLERANCE:
                        a, b = nearest_points(pieces[i], pieces[j])
                        failed = gap < spacing_limit - 2 * TOLERANCE
                        add(film, Point((a.x+b.x)/2, (a.y+b.y)/2), "Gerber 铜层线距", "gerber_copper_spacing",
                            gap, spacing_limit, "fail" if failed else "warn_limit",
                            f"铜厚 {thickness:g}um；独立铜图形间距，不含网络归属；"
                            + ("低于当前板厂制程能力" if failed else "误差范围内需 CAM 复核"))
            edge_rule = capability.get("trace", {}).get("trace_to_board_edge", {})
            edge_value = _to_mm(dict(value=edge_rule.get(
                "outer" if (layer and layer["outer"]) or role in ("copper_top", "copper_bottom") else "inner"),
                unit=edge_rule.get("unit")))
            if edge_is_authoritative and edge_value is not None:
                for piece in _points(solid.intersection(edges.buffer(edge_value))):
                    gap = piece.distance(edges)
                    if gap < edge_value - TOLERANCE:
                        add(film, nearest_points(piece, edges)[0], "Gerber 铜箔到板边", "gerber_copper_edge", gap,
                            edge_value, "fail", "实际铜箔图形到外形中心线距离不足")
    if not edge_is_authoritative:
        errors.append("缺少可识别的外形中心线，未检查板边距离")
    for role in ("mask_top", "mask_bottom"):
        if role not in by_role:
            errors.append(f"{role}: 缺少外层阻焊 Gerber，未检查该面的阻焊桥")
    for layer in layers:
        if not any(row.get("index") == layer["index"] for row in copper_layer_checks):
            errors.append(f"L{layer['index']} {layer['name']}: 缺少该铜层 Gerber，线宽/线距未检查")
        row = next((r for r in copper_layer_checks if r.get("index") == layer["index"]), {})
        coverage.append(dict(rule="copper_width_spacing", layer=f"L{layer['index']} {layer['name']}",
                             status="partial" if row.get("checked") else "missing",
                             note="实际铜图形描画/独立铜块间距及边界候选；不是完整最小宽度证明",
                             required_data="该层 Gerber、叠构成品铜厚、板厂线宽/线距能力；完整最小宽度算法仍需补齐"))
    if not layers:
        errors.append("未提供逐层叠构/层数，无法确认全部铜层覆盖和内层铜厚")
        coverage.append(dict(rule="stackup", status="missing", note="无法确认全部铜层覆盖",
                             required_data="layer_count 与板厂确认的逐铜层 stackup/copper_um"))
    for role in ("mask_top", "mask_bottom"):
        row = next((r for r in mask_bridge_checks if r["layer"] == role), {})
        coverage.append(dict(rule="solder_mask_bridge", layer=role,
                             status="partial" if row.get("checked") else "missing",
                             note="独立/连通开窗间窄桥候选；合并开窗不能证明逐引脚有桥",
                             required_data="同面阻焊 Gerber、外层铜厚和阻焊能力；逐器件/焊盘语义"))
    for role in ("silkscreen_top", "silkscreen_bottom"):
        coverage.append(dict(rule="silkscreen_geometry", layer=role,
                             status="partial" if role in by_role and role not in ambiguous else "missing",
                             note="圆孔径描画线宽及同面开窗/板边距离；未证明字符尺寸与完整最小笔画",
                             required_data="同面丝印、阻焊、封闭板框 Gerber、字符/字体资料与板厂丝印能力"))
    from src.layout_checks import scan_layout_checks
    extra = scan_layout_checks(folder, films, ambiguous, copper_layer_checks,
                               mask_bridge_checks, outline, capability, layers, options, limits)
    for row in extra["checks"]:
        if len(results) < limits.max_findings:
            results.append(row)
        elif "问题位置报告已截断，几何检查未完成" not in errors:
            errors.append("问题位置报告已截断，几何检查未完成")
    errors.extend(extra["errors"])
    scan_stats.extend(extra["scan_stats"])
    coverage.extend(extra["coverage"])
    densities.extend(extra["copper_density"])
    # Candidates are not proof of full minimum-width, net, or pad semantics coverage.
    coverage.append(dict(rule="full_topology_and_net_drc", status="partial",
                          note="边界候选扫描非完整骨架 DRC；Gerber 无完整网络/器件/非功能盘属性",
                          required_data="完整网络/焊盘栈/层连接及 net-tie 例外；需网络 DRC 与完整最小宽度算法，不仅是补数据"))
    for scan in scan_stats:
        if not scan.get("complete"):
            coverage.append(dict(rule=scan.get("rule", "scan_budget"), layer=scan.get("layer", ""),
                                 status="incomplete", note="扫描未完成或未取得能力下限，不能视为 PASS",
                                 required_data="补齐对应规则数据/能力；预算耗尽时提高预算并重新分析"))
    for error in errors:
        coverage.append(dict(rule="input_or_scan_error", status="incomplete", note=error,
                             required_data="按此错误修复资料/元数据/扫描预算后重新分析"))
    requirements = [dict(rule=row["rule"], layer=row.get("layer", ""), status=row["status"],
                         required_data=row.get("required_data") or row.get("note", ""),
                         note=row.get("note", ""))
                    for row in coverage if row["status"] in ("missing", "incomplete", "partial")]
    if capability_fingerprint(factory) != rule_sha256:
        raise ValueError("PCB 能力 YAML 在 Gerber 分析期间发生变化，请重新分析")
    return dict(checks=results, films=films, errors=errors, tolerance_mm=TOLERANCE,
                rule_sha256=rule_sha256,
                copper_um=copper_um, mask_bridge_checks=mask_bridge_checks,
                copper_layer_checks=copper_layer_checks, stackup=layers,
                  parsed_layers=[dict(path=f["path"], role=f["role"], objects=len(f["objects"]), warnings=f["warnings"],
                                      bounds_mm=list(f["geometry"].bounds) if not f["geometry"].is_empty else [])
                                for f in films] + extra["drill_layers"], folder=str(Path(folder).resolve()),
                 geometry_coverage=coverage, copper_density=densities,
                 copper_balance=extra["copper_balance"], scan_stats=scan_stats,
                  geometry_complete=False, geometry_requirements=requirements,
                  geometry_data_source=options.get("data_source", str(manifest) if manifest.is_file() else ""),
                  elapsed_seconds=round(perf_counter() - started, 3))


def attach_gerber_result(pcb, bundle, design_unit=""):
    from copy import deepcopy
    from src.pcb_locations import _point, normalize_unit
    from src.dfm_rules import make_issue
    result = deepcopy(pcb)
    label = bundle.get("source_label", "Gerber")
    if label == "Gerber" and result.get("rule_sha256") and bundle.get("rule_sha256") and bundle["rule_sha256"] != result["rule_sha256"]:
        raise ValueError("参数分析和 Gerber 分析使用的 PCB 能力 YAML 版本不一致，请重新分析")
    if label == "Gerber" and result.get("rule_sha256") and not bundle.get("rule_sha256"):
        result.setdefault("release_blockers", []).append("Gerber 检查缺少能力 YAML 版本指纹，不能核实规则一致性")
    unit = normalize_unit(design_unit)
    groups = {}
    for check in bundle["checks"]:
        row = _point(dict(check, brd_unit=unit or "未知"), check, unit)
        if not check.get("brd_alignment"):
            row.pop("command", None)
            row["note"] += "；制造文件到 BRD 的坐标对齐未验证，不生成 Allegro 跳转命令"
        result.setdefault("problem_locations", []).append(row)
        key = (check["field"], check["layer"], check["capability"], check["source"])
        groups.setdefault(key, []).append(check)
    for i, checks in enumerate(groups.values(), 1):
        failures = [check for check in checks if check["status"] == "fail"]
        first = (max if checks[0].get("comparison") == "max" else min)(
            failures or checks, key=lambda c: c["measured"])
        message = f"{first['item']}: {len(checks)} 个问题位置"
        failed = any(c["status"] == "fail" for c in checks)
        result["items"].append(dict(category=label, item=first["item"], field=first["field"],
            design=first["measured"], capability=first["capability"], unit=first.get("unit", "mm"), status="fail",
            message=message, source=label, source_detail=first["source"], layer=first["layer"], note=first["note"]))
        result["items"][-1]["status"] = "fail" if failed else "warn_limit"
        result.setdefault("issues", []).append(make_issue(f"{'ODB' if label == 'ODB++' else 'NC' if label == 'NC Drill' else 'GBR'}-{i:02d}", label, first["item"], "高" if failed else "中",
            message, measured=first["measured"], required=first["capability"], unit=first.get("unit", "mm"),
            count=len(checks), x=first["x_mm"], y=first["y_mm"], layer=first["layer"], note=first["note"]))
    result["error_count"] = sum(c["status"] == "fail" for c in result["items"])
    result["warning_count"] = sum(c["status"] == "warn_limit" for c in result["items"])
    result["errors"] = [c.get("message", "") for c in result["items"] if c["status"] == "fail"]
    result["warnings"] = [c.get("message", "") for c in result["items"] if c["status"] == "warn_limit"]
    result["geometry_review_count"] = sum(
        it.get("status") == "warn_limit" and it.get("source") in ("Gerber", "ODB++", "NC Drill")
        for it in result["items"])
    result["gerber_layers"] = bundle["parsed_layers"]
    result["gerber_errors"] = list(bundle["errors"])
    result["gerber_folder"] = bundle["folder"]
    if result.get("copper_um") is not None and result["copper_um"] != bundle["copper_um"]:
        result["gerber_errors"].append("PCB 参数与 Gerber 分析的外层手工铜厚不一致，请使用同一输入重新分析")
    result["gerber_copper_um"] = bundle["copper_um"]
    result["gerber_mask_bridge_checks"] = bundle["mask_bridge_checks"]
    result["gerber_copper_layer_checks"] = bundle["copper_layer_checks"]
    result["geometry_coverage"] = bundle.get("geometry_coverage", [])
    result["geometry_requirements"] = bundle.get("geometry_requirements", [])
    result["geometry_data_source"] = bundle.get("geometry_data_source", "")
    result["geometry_executed"] = True
    result["geometry_coverage_summary"] = {
        status: sum(row.get("status") == status for row in result["geometry_coverage"])
        for status in ("measured", "partial", "missing", "incomplete")}
    result["geometry_complete"] = bundle.get("geometry_complete", False)
    result["copper_density"] = bundle.get("copper_density", [])
    result["copper_balance"] = bundle.get("copper_balance", [])
    result["geometry_scan_stats"] = bundle.get("scan_stats", [])
    result["geometry_elapsed_seconds"] = bundle.get("elapsed_seconds")
    for copper in bundle["copper_layer_checks"]:
        result["items"].append(dict(category="Gerber 逐铜层覆盖", item=f"{copper['layer']} 线宽/线距扫描",
            field="gerber_copper_scan", design=None, capability=None, unit="mm",
            status="info", source="Gerber", layer=copper["layer"],
            message=(f"{copper['layer']}：铜厚 {copper['copper_um']}um；"
                     + (f"扫描下限：线宽 {copper['width_limit_mm']}mm，线距 {copper['spacing_limit_mm']}mm；非合格证明"
                        if copper["checked"] else "未完成检查，见缺数据提示")),
            note=copper.get("scope", "未取得该层叠构铜厚或设备能力")))
    for mask in bundle["mask_bridge_checks"]:
        if mask["checked"]:
            result["items"].append(dict(category="Gerber 覆盖", item=f"{mask['copper_layer']} 外层阻焊桥扫描",
                field="gerber_mask_bridge_scan", design=None, capability=mask["capability"], unit="mm",
                status="info", source="Gerber", layer=mask["layer"],
                message=f"{mask['copper_layer']}：已按铜厚 {mask['copper_um']:g}um 扫描独立开窗间桥宽",
                note="仅外层；另扫描连通开窗内部窄桥候选；未验证每个器件焊盘都保留桥"))
    summary = result.setdefault("issue_summary", {})
    summary["高"] = sum(c.get("severity") == "高" for c in result.get("issues", []))
    summary["中"] = sum(c.get("severity") == "中" for c in result.get("issues", []))
    summary["总计"] = len(result.get("issues", []))
    from src.verdict import finalize_verdict
    finalize_verdict(result, geometry=bundle)
    return result


def write_preview(bundle, path):
    """Standalone real Gerber SVG with numbered issue markers and coordinate table."""
    import html
    bounds = [f["geometry"].bounds for f in bundle["films"] if not f["geometry"].is_empty]
    if not bounds:
        return ""
    # Bounds do not require a second all-layer boolean union on large designs.
    x0, y0 = min(b[0] for b in bounds), min(b[1] for b in bounds)
    x1, y1 = max(b[2] for b in bounds), max(b[3] for b in bounds)
    w, h = max(x1-x0, 1), max(y1-y0, 1)
    margin = max(w, h) * .03
    colors = {"silkscreen":"#bba9ff", "mask":"#42c998", "paste":"#cccccc", "copper":"#cf9446", "outline":"#e0e6ef"}
    drawings = []
    for film in bundle["films"]:
        if not film["geometry"].is_empty:
            color = colors.get(film["role"].split("_")[0], "#aaa")
            # Shapely Polygon uses fill_color, LineString uses stroke_color,
            # and GeometryCollection uses color. All accept color positionally.
            drawings.append(f'<g opacity="0.45">{film["geometry"].svg(.1, color)}</g>')
    markers, rows = [], []
    for index, check in enumerate(bundle["checks"], 1):
        x, y = check["x_mm"], check["y_mm"]
        markers.append(f'<circle cx="{x}" cy="{y}" r="{max(w,h)/250}" fill="red"><title>{index}: {html.escape(check["item"])} X={x} Y={y}</title></circle>')
        rows.append(f'<tr><td>{index}</td><td>{html.escape(check["item"])}</td><td>{html.escape(check["layer"])}</td>'
                    f'<td>{html.escape(str(x))}</td><td>{html.escape(str(y))}</td><td>{html.escape(str(check["measured"]))}</td><td>{html.escape(str(check["capability"]))}</td>'
                    f'<td>{html.escape(check.get("unit", "mm"))}</td></tr>')
    text = f'''<!doctype html><meta charset="utf-8"><title>Evan Gerber DFM</title>
    <style>body{{background:#151b28;color:#eee;font:14px Segoe UI}}svg{{width:100%;height:70vh;background:#10141e}}td,th{{padding:6px;border:1px solid #657}}table{{border-collapse:collapse}}</style>
    <h2>Gerber DFM · 问题坐标 / Issue coordinates (mm)</h2><p>{html.escape('; '.join(bundle['errors']))}</p>
    <svg viewBox="{x0-margin} {-y1-margin} {w+margin*2} {h+margin*2}" xmlns="http://www.w3.org/2000/svg"><g transform="scale(1,-1)">{''.join(drawings+markers)}</g></svg>
    <table><tr><th>#</th><th>问题/Issue</th><th>Layer</th><th>X mm</th><th>Y mm</th><th>Measured</th><th>Required</th><th>Unit</th></tr>{''.join(rows)}</table>'''
    Path(path).write_text(text, encoding="utf-8")
    return str(Path(path).resolve())

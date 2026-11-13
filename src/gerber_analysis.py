"""Gerber manufacturing checks using rendered polarity/macro/region geometry.

Gerbonara handles RS-274X/X2 parsing; Shapely measures final film features.
Geometry is approximated to 0.002mm; boundary hits are review candidates.
"""
import re
import warnings
from pathlib import Path

from gerbonara import GerberFile
from gerbonara.utils import MM
from gerbonara.graphic_objects import Line, Arc
from shapely.geometry import Polygon, LineString, Point, GeometryCollection
from shapely.ops import unary_union, nearest_points
from shapely.strtree import STRtree

from src.gerber_export import gerber_files
from src.dfm_engine import load_capability, _to_mm

TOLERANCE = 0.002


def identify_layer(path, header):
    name = Path(path).stem.upper()
    suffix = Path(path).suffix.lower()
    explicit = re.search(r"%TF\.FileFunction,([^*]+)", header, re.IGNORECASE)
    content = (explicit.group(1) if explicit else name).upper()
    side = "bottom" if any(t in content for t in ("BOTTOM", "BOT", ",B", "_B")) else "top"
    extensions = {".gto":"silkscreen_top", ".gbo":"silkscreen_bottom", ".gts":"mask_top",
                  ".gbs":"mask_bottom", ".gtl":"copper_top", ".gbl":"copper_bottom",
                  ".gko":"outline", ".gm1":"outline"}
    if suffix in extensions:
        return extensions[suffix]
    for extension, mapped in extensions.items():
        if name == extension[1:].upper() or name.endswith("-" + extension[1:].upper()):
            return mapped
    if any(t in content for t in ("SILK", "LEGEND")):
        return "silkscreen_" + side
    if any(t in content for t in ("SMASK", "SOLDERMASK", "SOLDER_MASK")):
        return "mask_" + side
    if any(t in content for t in ("PROFILE", "OUTLINE")) or name in ("PANEL", "GKO", "OL"):
        return "outline"
    if name in ("TOP", "BOTTOM", "GTL", "GBL") or "COPPER" in content:
        return "copper_" + side
    # Infer conventional Cadence copper inner films without calling drawings copper.
    if re.fullmatch(r"(?:GND|POWER|LAYER|L|IN)[_-]?\d+", name):
        return "copper_inner"
    return "unknown"


def _primitive_polygon(primitive):
    arc_poly = primitive.to_arc_poly().approximate_arcs(max_error=TOLERANCE)
    if len(arc_poly.outline) < 3:
        return GeometryCollection()
    polygon = Polygon(arc_poly.outline)
    return polygon if polygon.is_valid else polygon.buffer(0)


def _centerline(obj):
    if isinstance(obj, Line):
        return LineString([(obj.x1, obj.y1), (obj.x2, obj.y2)])
    if isinstance(obj, Arc):
        from gerbonara.utils import approximate_arc
        return LineString(list(approximate_arc(obj.cx+obj.x1, obj.cy+obj.y1, obj.x1, obj.y1, obj.x2, obj.y2,
                                              obj.clockwise, max_error=TOLERANCE)))
    return None


def read_film(path):
    path = Path(path)
    header = path.read_text(encoding="ascii", errors="replace")[:20000]
    role = identify_layer(path, header)
    with warnings.catch_warnings(record=True) as notices:
        warnings.simplefilter("always")
        gerber = GerberFile.open(path)
        objects = []
        film = GeometryCollection()
        pending, previous_polarity = [], None

        def commit():
            nonlocal film, pending
            if pending:
                group = unary_union(pending)
                film = film.union(group) if previous_polarity else film.difference(group)
                pending = []

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
            pending.append(pieces)
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
    return dict(path=str(path.resolve()), role=role, geometry=film, objects=objects,
                warnings=[str(n.message) for n in notices], brd_alignment=bool(aligned))


def _points(geometry):
    if geometry.is_empty:
        return []
    if hasattr(geometry, "geoms"):
        return [g for g in geometry.geoms if not g.is_empty]
    return [geometry]


def analyze_gerbers(folder, factory="hongban", board_type="rigid", level="standard", copper_oz=1.0):
    capability = load_capability(board_type, level, factory)
    silk_cap = capability.get("silkscreen", {})
    films, errors, results = [], [], []
    for path in gerber_files(folder):
        try:
            header = path.read_text(encoding="ascii", errors="replace")[:20000]
            if identify_layer(path, header) == "unknown":
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
    # Multiple same-role films may be different boards/revisions: do not merge silently.
    ambiguous = {r for r, values in by_role.items() if len(values) > 1}
    for role in ambiguous:
        errors.append(f"{role}: 多个同类图层，请在独立目录保留同一版本的一套 Gerber")
    edge_films = by_role.get("outline", []) if "outline" not in ambiguous else []
    centerlines = [o["centerline"] for f in edge_films for o in f["objects"]
                   if o["dark"] and o["centerline"] is not None]
    edges = unary_union(centerlines) if centerlines else GeometryCollection()
    edge_is_authoritative = bool(centerlines)

    def add(film, shape, item, field, measured, required, status, note):
        point = shape if isinstance(shape, Point) else shape.representative_point()
        results.append(dict(item=item, field=field, measured=round(measured, 6), capability=required,
            status=status, severity="高" if status == "fail" else "中", unit="mm",
            x_mm=round(point.x, 6), y_mm=round(point.y, 6), layer=film["role"],
            source=film["path"], note=note + f"；Gerber 图形近似误差 ≤{TOLERANCE}mm",
            brd_alignment=film["brd_alignment"]))

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
        if role.startswith("mask"):
            from src.dfm_engine import get_sm_bridge_capability
            bridge = get_sm_bridge_capability(capability, float(copper_oz or 1.0))
            pieces = _points(solid)
            if bridge and pieces:
                tree = STRtree(pieces)
                for i, part in enumerate(pieces):
                    for j in tree.query(part.buffer(bridge)):
                        j = int(j)
                        if j <= i:
                            continue
                        distance = part.distance(pieces[j])
                        if TOLERANCE < distance < bridge - TOLERANCE:
                            a, b = nearest_points(part, pieces[j])
                            add(film, Point((a.x+b.x)/2, (a.y+b.y)/2), "Gerber 阻焊桥", "gerber_mask_bridge",
                                distance, bridge, "fail", f"两个独立阻焊开窗之间桥宽不足（铜厚 {copper_oz}oz）")
        if role.startswith("copper"):
            from src.dfm_engine import get_line_spacing_capability
            width_um, _ = get_line_spacing_capability(capability, float(copper_oz or 1.0) * 35)
            width_limit = width_um / 1000 if width_um is not None else None
            for obj in film["objects"]:
                if obj["dark"] and obj["width"] and width_limit and obj["width"] < width_limit - TOLERANCE:
                    # An individual thin primitive can be merged into a plane; only
                    # flag it if its surrounding required width is not all copper.
                    line = obj["centerline"]
                    if line is not None and not solid.covers(line.buffer((width_limit-TOLERANCE)/2)):
                        rendered = obj["geometry"].intersection(solid)
                        if not rendered.is_empty:
                            add(film, rendered, "Gerber 铜层描画线宽候选", "gerber_copper_width", obj["width"], width_limit,
                                "warn_limit", "局部描画线宽不足候选；不含负片/完整颈宽拓扑，需复核")
            edge_value = capability.get("trace", {}).get("trace_to_board_edge", {}).get(
                "inner" if role == "copper_inner" else "outer")
            if edge_is_authoritative and isinstance(edge_value, (int, float)):
                for piece in _points(solid.intersection(edges.buffer(edge_value))):
                    gap = piece.distance(edges)
                    if gap < edge_value - TOLERANCE:
                        add(film, nearest_points(piece, edges)[0], "Gerber 铜箔到板边", "gerber_copper_edge", gap,
                            edge_value, "fail", "实际铜箔图形到外形中心线距离不足")
    if not edge_is_authoritative:
        errors.append("缺少可识别的外形中心线，未检查板边距离")
    return dict(checks=results, films=films, errors=errors, tolerance_mm=TOLERANCE,
                parsed_layers=[dict(path=f["path"], role=f["role"], objects=len(f["objects"]), warnings=f["warnings"])
                               for f in films], folder=str(Path(folder).resolve()))


def attach_gerber_result(pcb, bundle, design_unit=""):
    from copy import deepcopy
    from src.pcb_locations import _point, normalize_unit
    from src.dfm_rules import make_issue
    result = deepcopy(pcb)
    unit = normalize_unit(design_unit)
    groups = {}
    for check in bundle["checks"]:
        row = _point(dict(check, brd_unit=unit or "未知"), check, unit)
        if not check.get("brd_alignment"):
            row.pop("command", None)
            row["note"] += "；Gerber 含偏移/镜像/旋转，需确认与 BRD 坐标对齐"
        result.setdefault("problem_locations", []).append(row)
        groups.setdefault(check["field"], []).append(check)
    for i, checks in enumerate(groups.values(), 1):
        first = checks[0]
        message = f"{first['item']}: {len(checks)} 个问题位置"
        failed = any(c["status"] == "fail" for c in checks)
        result["items"].append(dict(category="Gerber", item=first["item"], field=first["field"],
            design=min(c["measured"] for c in checks), capability=first["capability"], unit="mm", status="fail",
            message=message, source="Gerber", note=first["note"]))
        result["items"][-1]["status"] = "fail" if failed else "warn_limit"
        result.setdefault("issues", []).append(make_issue(f"GBR-{i:02d}", "Gerber", first["item"], "高" if failed else "中",
            message, measured=first["measured"], required=first["capability"], unit="mm",
            count=len(checks), x=first["x_mm"], y=first["y_mm"], layer=first["layer"], note=first["note"]))
    result["error_count"] = sum(c["status"] == "fail" for c in result["items"])
    result["warning_count"] = sum(c["status"] == "warn_limit" for c in result["items"])
    result["errors"] = [c.get("message", "") for c in result["items"] if c["status"] == "fail"]
    result["warnings"] = [c.get("message", "") for c in result["items"] if c["status"] == "warn_limit"]
    if result["error_count"]:
        result.update(passed=False, releasable=False, verdict="不通过（含 Gerber 制造图形检查）")
    elif groups:
        result.update(releasable=False, verdict="Gerber 局部宽度候选需复核，不能直接放行")
    if bundle["errors"]:
        result["releasable"] = False
        if not groups and not result.get("error_count"):
            result["verdict"] = "Gerber 图层/解析检查不完整，需复核"
    result["gerber_layers"] = bundle["parsed_layers"]
    result["gerber_errors"] = bundle["errors"]
    result["gerber_folder"] = bundle["folder"]
    summary = result.setdefault("issue_summary", {})
    summary["高"] = sum(c.get("severity") == "高" for c in result.get("issues", []))
    summary["中"] = sum(c.get("severity") == "中" for c in result.get("issues", []))
    summary["总计"] = len(result.get("issues", []))
    if result.get("risk") and not result["releasable"]:
        result["risk"]["verdict"] = result["verdict"]
    return result


def write_preview(bundle, path):
    """Standalone real Gerber SVG with numbered issue markers and coordinate table."""
    import html
    geometry = unary_union([f["geometry"] for f in bundle["films"] if not f["geometry"].is_empty])
    if geometry.is_empty:
        return ""
    x0, y0, x1, y1 = geometry.bounds
    w, h = max(x1-x0, 1), max(y1-y0, 1)
    margin = max(w, h) * .03
    colors = {"silkscreen":"#bba9ff", "mask":"#42c998", "copper":"#cf9446", "outline":"#e0e6ef"}
    drawings = []
    for film in bundle["films"]:
        if not film["geometry"].is_empty:
            color = colors.get(film["role"].split("_")[0], "#aaa")
            drawings.append(f'<g opacity="0.45">{film["geometry"].svg(scale_factor=.1, fill_color=color)}</g>')
    markers, rows = [], []
    for index, check in enumerate(bundle["checks"], 1):
        x, y = check["x_mm"], check["y_mm"]
        markers.append(f'<circle cx="{x}" cy="{y}" r="{max(w,h)/250}" fill="red"><title>{index}: {html.escape(check["item"])} X={x} Y={y}</title></circle>')
        rows.append(f'<tr><td>{index}</td><td>{html.escape(check["item"])}</td><td>{html.escape(check["layer"])}</td>'
                    f'<td>{x}</td><td>{y}</td><td>{check["measured"]}</td><td>{check["capability"]}</td></tr>')
    text = f'''<!doctype html><meta charset="utf-8"><title>Evan Gerber DFM</title>
    <style>body{{background:#151b28;color:#eee;font:14px Segoe UI}}svg{{width:100%;height:70vh;background:#10141e}}td,th{{padding:6px;border:1px solid #657}}table{{border-collapse:collapse}}</style>
    <h2>Gerber DFM · 问题坐标 / Issue coordinates (mm)</h2><p>{html.escape('; '.join(bundle['errors']))}</p>
    <svg viewBox="{x0-margin} {-y1-margin} {w+margin*2} {h+margin*2}" xmlns="http://www.w3.org/2000/svg"><g transform="scale(1,-1)">{''.join(drawings+markers)}</g></svg>
    <table><tr><th>#</th><th>问题/Issue</th><th>Layer</th><th>X mm</th><th>Y mm</th><th>Measured</th><th>Required</th></tr>{''.join(rows)}</table>'''
    Path(path).write_text(text, encoding="utf-8")
    return str(Path(path).resolve())

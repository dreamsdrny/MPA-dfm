"""查看器图形模型：把真实底片/ODB++ 图形转成可绘制环，并如实记录简化与截断。

这里不做任何 DFM 判定，也不把简化后的显示图形当实测尺寸；渲染层只画这些环。
"""
from math import isfinite

from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

MAX_POINTS_PER_LAYER = 400_000
POLYGON_ROLES = ("copper", "mask", "paste", "silkscreen", "outline", "drill")


def _finite(value):
    return value if isinstance(value, (int, float)) and isfinite(value) else None


def _bounds(shape):
    if shape is None or shape.is_empty:
        return None
    return list(shape.bounds)


def _rings(shape, tolerance, budget):
    """返回 (多边形[外环+孔], 线串) 与是否超预算；坐标为毫米元组列表。"""
    polygons, lines, points, truncated = [], [], 0, False

    def ring(coordinates):
        nonlocal points
        if points >= budget:
            return None
        step = max(1, int(tolerance * 40)) if tolerance else 1
        kept = []
        for index, (x, y) in enumerate(coordinates):
            # 近似后仍按像素级抽稀，避免超大区域把界面渲染拖死。
            if index % step and 0 < index < len(coordinates) - 1:
                continue
            kept.append((x, y))
        points += len(kept)
        return kept

    def walk(part):
        nonlocal truncated
        if part is None or part.is_empty:
            return
        kind = part.geom_type
        if kind == "Polygon":
            outer = ring(part.exterior.coords)
            if outer is None:
                truncated = True
                return
            holes = []
            for interior in part.interiors:
                inner = ring(interior.coords)
                if inner is None:
                    truncated = True
                    break
                holes.append(inner)
            polygons.append([outer] + holes)
        elif kind in ("LineString", "LinearRing"):
            line = ring(part.coords)
            if line is None:
                truncated = True
            else:
                lines.append(line)
        elif hasattr(part, "geoms"):
            for child in part.geoms:
                walk(child)

    walk(shape)
    return polygons, lines, points, truncated


def layer_entry(name, role, geometry, side="", source="", features=None):
    """把一层 shapely 图形转成查看器条目；无效/空图形返回 None。"""
    if not isinstance(geometry, BaseGeometry) or geometry.is_empty:
        return None
    bounds = _bounds(geometry)
    if bounds is None or any(_finite(value) is None for value in bounds):
        return None
    width = max(bounds[2] - bounds[0], 1e-6)
    height = max(bounds[3] - bounds[1], 1e-6)
    tolerance = max(width, height) / 1600
    simplified = geometry.simplify(tolerance, preserve_topology=True)
    polygons, lines, points, truncated = _rings(simplified if not simplified.is_empty else geometry,
                                                tolerance, MAX_POINTS_PER_LAYER)
    return dict(name=str(name or "LAYER"), role=str(role or "unknown"), side=str(side or "").upper(),
                source=str(source or ""), bounds=bounds, visible=True,
                polygons=polygons, lines=lines, points=points, simplified_mm=tolerance,
                truncated=truncated, features=int(features) if features is not None else len(polygons) + len(lines))


def build_view_model(layers, issues=(), *, source="", warnings=(), notes=()):
    entries = []
    for layer in layers or []:
        if isinstance(layer, BaseGeometry) or isinstance(layer, dict) and "geometry" in layer:
            geometry = layer["geometry"] if isinstance(layer, dict) else layer
            row = layer_entry(layer.get("name") if isinstance(layer, dict) else "",
                              layer.get("role") if isinstance(layer, dict) else "",
                              geometry, side=layer.get("side", "") if isinstance(layer, dict) else "",
                              source=layer.get("path", layer.get("source", "")) if isinstance(layer, dict) else "",
                              features=layer.get("objects") if isinstance(layer, dict) else None)
        else:
            row = dict(layer)
            row.setdefault("visible", True)
            row.setdefault("polygons", [])
            row.setdefault("lines", [])
            row.setdefault("points", 0)
        if row is not None:
            entries.append(row)
    markers = []
    for index, issue in enumerate(issues or [], 1):
        x, y = _finite(issue.get("x_mm")), _finite(issue.get("y_mm"))
        if x is None or y is None:
            continue
        markers.append(dict(index=index, x_mm=x, y_mm=y, label=str(issue.get("item") or issue.get("label") or ""),
                            layer=str(issue.get("layer") or issue.get("side") or ""),
                            status=str(issue.get("status") or ""), measured=issue.get("measured"),
                            required=issue.get("required"), unit=str(issue.get("unit") or "mm")))
    bounds = [row["bounds"] for row in entries if row.get("bounds")]
    extent = None
    if bounds:
        extent = [min(b[0] for b in bounds), min(b[1] for b in bounds),
                  max(b[2] for b in bounds), max(b[3] for b in bounds)]
    notices = list(notes or [])
    if not entries:
        notices.append("未读到可绘制图形；查看器不判定制程能力")
    truncated = [row["name"] for row in entries if row.get("truncated")]
    if truncated:
        notices.append("图形点数超预算，已截断显示：" + "、".join(truncated[:6]) + "；判定仍以分析结果为准")
    return dict(schema=1, source=str(source or ""), layers=entries, issues=markers, bounds=extent,
                warnings=list(warnings or []), notes=notices,
                display_note="显示图形已按视野像素级简化，不能用于量测尺寸")


def viewport(model, area=None, padding=.03):
    """返回绘制视野 (x0, y0, x1, y1)；无图形时返回 None。"""
    if area:
        x0, y0, x1, y1 = area
    else:
        bounds = (model or {}).get("bounds")
        if not bounds:
            return None
        x0, y0, x1, y1 = bounds
    if not all(_finite(value) is not None for value in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
        return None
    margin = max(x1 - x0, y1 - y0) * padding
    return [x0 - margin, y0 - margin, x1 + margin, y1 + margin]


def zoom_viewport(box, factor, anchor=None):
    """以 anchor（毫米）为中心缩放；factor>1 放大。"""
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    if factor <= 0 or width <= 0 or height <= 0:
        raise ValueError("缩放参数无效")
    anchor = anchor or ((x0 + x1) / 2, (y0 + y1) / 2)
    new_width, new_height = width / factor, height / factor
    ratio_x = (anchor[0] - x0) / width
    ratio_y = (anchor[1] - y0) / height
    return [anchor[0] - new_width * ratio_x, anchor[1] - new_height * ratio_y,
            anchor[0] + new_width * (1 - ratio_x), anchor[1] + new_height * (1 - ratio_y)]


def pan_viewport(box, delta_mm):
    return [box[0] + delta_mm[0], box[1] + delta_mm[1], box[2] + delta_mm[0], box[3] + delta_mm[1]]


def visible_layers(model, names=None):
    layers = (model or {}).get("layers", [])
    if names is None:
        return [row for row in layers if row.get("visible")]
    wanted = {str(name) for name in names}
    return [row for row in layers if row.get("name") in wanted]


def clip_area(area, margin=0):
    """给渲染层用的裁剪矩形（毫米）。"""
    return box(area[0] - margin, area[1] - margin, area[2] + margin, area[3] + margin)

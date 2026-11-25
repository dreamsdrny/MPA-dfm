"""ODB++ 常用 ASCII features 子集。未知符号/命令显式标 partial。

支持常用标准符号、线、弧、岛与孔表面，用户符号通过受控解析器回调；
不支持全部符号、文字、条码、resize 或负片图层，非完整 ODB DFM 解析器。
"""
import math
import re

from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, Point, Polygon, box

from src.layout_geometry import UnionAccumulator

TOLERANCE = .002
MAX_ARC_SEGMENTS = 100_000


def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("ODB++ 数值必须有限")
    return result


def unit_scale(text, fallback=None):
    declarations = re.findall(r"(?im)^\s*(?:UNITS\s*=\s*|U\s+)(MM|INCH)\s*$", text)
    if len(set(value.upper() for value in declarations)) > 1:
        raise ValueError("ODB++ 单位声明冲突")
    if not declarations and fallback is not None:
        return fallback
    if not declarations:
        raise ValueError("ODB++ 缺 UNITS=MM/INCH，不能猜坐标单位")
    return 1.0 if declarations[0].upper() == "MM" else 25.4


def _disk(radius):
    if radius <= 0:
        raise ValueError("符号尺寸必须大于零")
    error = min(TOLERANCE, radius / 2)
    segments = max(8, math.ceil(math.pi / (4 * math.acos(1 - error / radius))))
    if segments > MAX_ARC_SEGMENTS:
        raise ValueError("圆符号超过离散预算")
    return Point(0, 0).buffer(radius, quad_segs=segments)


def symbol_shape(name, scale):
    # Standard symbol dimensions use microns for MM files, mils for INCH files.
    single = re.fullmatch(r"([rs])(\d+(?:\.\d+)?)", name.lower())
    if single:
        size = number(single[2]) * scale / 1000
        if size <= 0:
            raise ValueError("符号尺寸必须大于零")
        return (_disk(size / 2) if single[1] == "r" else box(-size/2, -size/2, size/2, size/2)), size
    pair = re.fullmatch(r"(rect|oval)(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", name.lower())
    if pair:
        w, h = [number(v) * scale / 1000 for v in pair.groups()[1:]]
        if min(w, h) <= 0:
            raise ValueError("符号尺寸必须大于零")
        if pair[1] == "rect":
            return box(-w/2, -h/2, w/2, h/2), None
        radius = min(w, h) / 2
        center = LineString([(-(w-h)/2, 0), ((w-h)/2, 0)]) if w >= h else LineString([(0, -(h-w)/2), (0, (h-w)/2)])
        return center.buffer(radius, quad_segs=32), None
    rounded = re.fullmatch(r"rect(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)xr(\d+(?:\.\d+)?)(?:x([1-4]+))?", name.lower())
    if rounded:
        w, h, radius = [number(v) * scale / 1000 for v in rounded.groups()[:3]]
        if min(w, h, radius) <= 0 or 2 * radius > min(w, h):
            raise ValueError("圆角矩形符号尺寸无效")
        shape = box(-w/2, -h/2, w/2, h/2)
        for corner in rounded[4] or "1234":
            sx, sy = {"1": (1, 1), "2": (-1, 1), "3": (-1, -1), "4": (1, -1)}[corner]
            x, y = sx*w/2, sy*h/2
            cell = box(min(x, x-sx*radius), min(y, y-sy*radius), max(x, x-sx*radius), max(y, y-sy*radius))
            disk = Point(x-sx*radius, y-sy*radius).buffer(radius, quad_segs=32)
            shape = shape.difference(cell.difference(disk))
        return shape, None
    diamond = re.fullmatch(r"di(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", name.lower())
    if diamond:
        w, h = [number(v) * scale / 1000 for v in diamond.groups()]
        if min(w, h) <= 0:
            raise ValueError("菱形符号尺寸无效")
        return Polygon([(w/2, 0), (0, h/2), (-w/2, 0), (0, -h/2)]), None
    donut = re.fullmatch(r"donut_r(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", name.lower())
    if donut:
        outer, inner = [number(v) * scale / 1000 for v in donut.groups()]
        if not 0 < inner < outer:
            raise ValueError("圆环符号内外径无效")
        return _disk(outer/2).difference(_disk(inner/2)), None
    chamfer = re.fullmatch(r"rect(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)xc(\d+(?:\.\d+)?)(?:x([1-4]+))?", name.lower())
    if chamfer:
        w, h, cut = [number(v) * scale / 1000 for v in chamfer.groups()[:3]]
        if min(w, h, cut) <= 0 or 2 * cut > min(w, h):
            raise ValueError("倒角矩形符号尺寸无效")
        corners = chamfer[4] or "1234"
        shape = box(-w/2, -h/2, w/2, h/2)
        for corner in corners:
            sx, sy = {"1": (1, 1), "2": (-1, 1), "3": (-1, -1), "4": (1, -1)}[corner]
            x, y = sx*w/2, sy*h/2
            shape = shape.difference(Polygon([(x, y), (x-sx*cut, y), (x, y-sy*cut)]))
        return shape, None
    raise ValueError("未支持 ODB++ 符号: " + name)


def arc_points(start, end, center, clockwise):
    radius = math.dist(start, center)
    if radius <= 0 or abs(math.dist(end, center) - radius) > 2 * TOLERANCE:
        raise ValueError("ODB++ 圆弧半径不一致")
    first = math.atan2(start[1] - center[1], start[0] - center[0])
    last = math.atan2(end[1] - center[1], end[0] - center[0])
    sweep = -((first-last) % (2*math.pi)) if clockwise else (last-first) % (2*math.pi)
    if abs(sweep) < 1e-12:
        sweep = -2*math.pi if clockwise else 2*math.pi
    step = 2 * math.acos(max(-1, 1 - min(TOLERANCE, radius/2) / radius))
    count = max(2, math.ceil(abs(sweep)/step))
    if count > MAX_ARC_SEGMENTS:
        raise ValueError("圆弧超过离散预算")
    points = [(center[0] + radius*math.cos(first+sweep*i/count),
               center[1] + radius*math.sin(first+sweep*i/count)) for i in range(count+1)]
    # Source endpoints are authoritative; rounded center/radius must not leave
    # microscopic gaps at the last point of a closed surface.
    points[0], points[-1] = start, end
    return points


def parse_features(text, profile=False, symbols_external=None):
    scale = unit_scale(text)
    symbols, warnings = {}, []
    counts = {}
    solid = GeometryCollection()
    group, polarity = UnionAccumulator(), None
    surface, ring, rings = None, None, []
    feature_count = 0
    records, attributes, strings = [], {}, {}
    surface_attributes = {}
    surface_index, surface_invalid = None, False

    def attr_values(line):
        result = {}
        tail = line.partition(";")[2]
        for token in tail.split(","):
            key, _, value = token.strip().partition("=")
            if key in attributes:
                name = attributes[key]
                result[name] = strings.get(value, value) if name in (".geometry", ".string") else value or True
        return result

    def commit():
        nonlocal solid, group
        geometry = group.finish()
        solid = solid.union(geometry) if polarity == "P" else solid.difference(geometry)
        group = UnionAccumulator()

    def add(shape, pol, kind="S", attrs=None, source_index=None, **meta):
        nonlocal polarity, feature_count
        if pol not in ("P", "N"):
            raise ValueError("ODB++ 极性不是 P/N")
        if polarity is not None and pol != polarity:
            commit()
        polarity = pol
        group.add(shape)
        records.append(dict(index=feature_count if source_index is None else source_index, kind=kind, polarity=pol,
                            geometry_wkb=shape.wkb_hex, attributes=attrs or {}, **meta))
        if source_index is None:
            feature_count += 1

    def close_surface():
        nonlocal surface, rings, surface_index, surface_invalid, ring
        if surface is None:
            return
        if ring is not None:
            warnings.append("ODB++ 表面含未结束轮廓")
            surface_invalid = True
            ring = None
        geometry = GeometryCollection()
        for shape, kind in rings:
            if kind == "I":
                geometry = geometry.union(shape)
            else:
                geometry = geometry.difference(shape)
        if surface_invalid:
            records.append(dict(index=surface_index, kind="S", polarity=None, geometry_wkb=None,
                                attributes=surface_attributes, error="表面轮廓不完整"))
        else:
            add(geometry, surface, attrs=surface_attributes, source_index=surface_index)
        surface, rings = None, []
        surface_index, surface_invalid = None, False

    for index, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith(("@", "&")):
            key, _, value = line[1:].partition(" ")
            (attributes if line[0] == "@" else strings)[key] = value.strip()
            continue
        if not line or line.startswith(("#", "UNITS=", "U ", "F ")) or re.fullmatch(r"ID=\d+", line):
            continue
        if line.startswith("$"):
            fields = line[1:].split()
            if len(fields) >= 2:
                factor = scale if len(fields) < 3 else 25.4 if fields[2] == "I" else 1 if fields[2] == "M" else None
                if factor is None:
                    warnings.append(f"行{index}: 未知符号单位")
                else:
                    symbols[fields[0]] = (fields[1], factor)
            continue
        tokens = line.split(";", 1)[0].split()
        kind = tokens[0]
        counts[kind] = counts.get(kind, 0) + 1
        try:
            if kind not in ("OB", "OS", "OC", "OE"):
                close_surface()
            if kind == "S":
                surface = tokens[1]
                surface_attributes = attr_values(line)
                surface_index = feature_count
                feature_count += 1
                surface_invalid = False
            elif kind == "OB":
                if ring is not None:
                    raise ValueError("未闭合轮廓")
                ring = ([(number(tokens[1])*scale, number(tokens[2])*scale)], tokens[3])
                if ring[1] not in ("I", "H"):
                    raise ValueError("未知岛/孔类型")
                if profile and surface is None:
                    surface = "P"
                    surface_index = feature_count
                    feature_count += 1
            elif kind == "OS":
                ring[0].append((number(tokens[1])*scale, number(tokens[2])*scale))
            elif kind == "OC":
                end = (number(tokens[1])*scale, number(tokens[2])*scale)
                center = (number(tokens[3])*scale, number(tokens[4])*scale)
                if tokens[5] not in ("Y", "N"):
                    raise ValueError("未知圆弧方向")
                ring[0].extend(arc_points(ring[0][-1], end, center, tokens[5] == "Y")[1:])
            elif kind == "OE":
                if len(ring[0]) < 4 or math.dist(ring[0][0], ring[0][-1]) > 1e-8:
                    raise ValueError("ODB++ 轮廓未闭合")
                shape = Polygon(ring[0])
                if not shape.is_valid:
                    raise ValueError("ODB++ 轮廓无效")
                rings.append((shape, ring[1]))
                ring = None
            elif kind == "SE":
                pass  # close_surface() already commits the surface.
            elif kind in ("P", "L", "A"):
                symbol_index = 3 if kind == "P" else 5 if kind == "L" else 7
                definition = symbols[tokens[symbol_index]]
                try:
                    shape, width = symbol_shape(*definition)
                except ValueError:
                    if symbols_external is None:
                        raise
                    shape, width = symbols_external(definition[0]), None
                pol = tokens[symbol_index + 1]
                if kind == "P":
                    # Standard records include a dcode before orientation. The
                    # historical six-token fixtures omit it; preserve that subset.
                    oi = 6 if len(tokens) >= 7 else 5
                    orient = int(tokens[oi])
                    angle = number(tokens[oi+1]) if orient in (8, 9) else (orient % 4) * 90
                    if orient not in range(10):
                        raise ValueError("未知焊盘方向")
                    expected = oi + (2 if orient in (8, 9) else 1)
                    if len(tokens) > expected:
                        raise ValueError("resize 焊盘未支持")
                    shape = affinity.rotate(shape, -angle, origin=(0, 0))
                    # ODB applies clockwise rotation first, then X-coordinate mirror.
                    if orient in (4, 5, 6, 7, 9):
                        shape = affinity.scale(shape, xfact=-1, yfact=1, origin=(0, 0))
                    shape = affinity.translate(shape, number(tokens[1])*scale, number(tokens[2])*scale)
                else:
                    if not definition[0].lower().startswith("r") or width is None:
                        raise ValueError("只支持圆符号描画线/弧")
                    start, end = [(number(tokens[i])*scale, number(tokens[i+1])*scale) for i in (1, 3)]
                    if kind == "A":
                        center = (number(tokens[5])*scale, number(tokens[6])*scale)
                        direction = tokens[10] if len(tokens) > 10 else tokens[9]
                        if direction not in ("Y", "N"):
                            raise ValueError("未知圆弧方向")
                        coords = arc_points(start, end, center, direction == "Y")
                    else:
                        coords = [start, end]
                    shape = LineString(coords).buffer(width/2, quad_segs=32)
                add(shape, pol, kind=kind, attrs=attr_values(line),
                    **(dict(x_mm=number(tokens[1])*scale, y_mm=number(tokens[2])*scale,
                            symbol=definition[0]) if kind == "P" else dict(width_mm=width)))
            else:
                raise ValueError("未支持 features 命令: " + kind)
        except (ValueError, IndexError, KeyError, TypeError, ZeroDivisionError, OverflowError) as exc:
            warnings.append(f"行{index}: {exc}")
            if kind in ("P", "L", "A"):
                # Preserve source FID indices even when a primitive is unsupported.
                records.append(dict(index=feature_count, kind=kind, polarity=None,
                                    geometry_wkb=None, attributes=attr_values(line), error=str(exc)))
                feature_count += 1
            if kind in ("OB", "OS", "OC", "OE"):
                ring = None
                surface_invalid = True
    if ring is not None:
        warnings.append("ODB++ 轮廓未结束")
        surface_invalid = True
    close_surface()
    commit()
    declared = re.findall(r"(?m)^\s*F\s+(\d+)\s*$", text)
    if declared and (len(set(declared)) != 1 or int(declared[0]) != feature_count):
        warnings.append(f"ODB++ F 图元数声明与实读不一致: {declared} / {feature_count}")
    return dict(geometry=solid, feature_count=feature_count, records=records, record_counts=counts,
                status="partial" if warnings else "parsed", warnings=warnings)

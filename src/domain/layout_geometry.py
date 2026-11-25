"""制造版图的公共几何内核（mm），不依赖 BRD 约束值。

STRtree 分批筛选邻域；精确距离/布尔运算只处理候选。拓扑扫描是
边界对候选检测，不是完整 medial-axis DRC，不能据此自动放行。
"""
from dataclasses import dataclass
from math import ceil

import numpy as np
from shapely import get_coordinates, linestrings, orient_polygons, prepare
from shapely.geometry import GeometryCollection, LineString, Point, Polygon, box
from shapely.ops import nearest_points, polygonize_full, unary_union
from shapely.strtree import STRtree


@dataclass(frozen=True)
class ScanLimits:
    batch_size: int = 256
    max_candidates: int = 2_000_000
    max_findings: int = 10_000
    max_density_cells: int = 20_000

    def __post_init__(self):
        for value in vars(self).values():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("几何扫描预算必须为正整数")


class ScanBudgetExceeded(ValueError):
    """调用者必须把预算耗尽记录为未完成检查，不能作 PASS。"""


def polygons(geometry):
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    return [part for child in getattr(geometry, "geoms", ()) for part in polygons(child)]


class UnionAccumulator:
    """分批、树形归并同极性图元，避免逐图元 union 和巨大单次输入列表。"""

    def __init__(self, batch_size=4096):
        self.batch_size = batch_size
        self.pending = []
        self.levels = []

    def add(self, geometry):
        self.pending.append(geometry)
        if len(self.pending) >= self.batch_size:
            self._flush()

    def _flush(self):
        if not self.pending:
            return
        shape = unary_union(self.pending)
        self.pending.clear()
        level = 0
        while level < len(self.levels) and self.levels[level] is not None:
            shape = self.levels[level].union(shape)
            self.levels[level] = None
            level += 1
        if level == len(self.levels):
            self.levels.append(shape)
        else:
            self.levels[level] = shape

    def finish(self):
        self._flush()
        return unary_union([shape for shape in self.levels if shape is not None])


def nearby_pairs(shapes, distance, limits=None, stats=None):
    """每个无序邻域对只返回一次，不构造全板 N×N 距离矩阵。

    单条查询控制在一个图元；批次用于进度/预算计数。极密集邻域仍可能
    二次增长，因此显式停止，而不是漏掉后续问题并宣称检查完成。
    """
    limits = limits or ScanLimits()
    stats = stats if stats is not None else {}
    stats.update(objects=len(shapes), candidates=0, complete=False)
    if not len(shapes):
        stats["complete"] = True
        return
    tree = STRtree(shapes)
    for start in range(0, len(shapes), limits.batch_size):
        for i in range(start, min(start + limits.batch_size, len(shapes))):
            for j in tree.query(shapes[i], predicate="dwithin", distance=distance):
                j = int(j)
                if j <= i:
                    continue
                stats["candidates"] += 1
                if stats["candidates"] > limits.max_candidates:
                    raise ScanBudgetExceeded(f"邻域候选超过 {limits.max_candidates}，扫描未完成")
                yield i, j
    stats["complete"] = True


def boundary_segments(solid):
    blocks, ring_blocks, position_blocks, sizes = [], [], [], []
    for part in polygons(orient_polygons(solid)):
        for ring in (part.exterior, *part.interiors):
            points = get_coordinates(ring)
            segments = np.stack((points[:-1], points[1:]), axis=1)
            segments = segments[np.linalg.norm(segments[:, 1] - segments[:, 0], axis=1) > 1e-12]
            number = len(sizes)
            sizes.append(len(segments))
            blocks.append(segments)
            ring_blocks.append(np.full(len(segments), number, dtype=np.int64))
            position_blocks.append(np.arange(len(segments), dtype=np.int64))
    if not blocks:
        return [], np.empty((0, 2, 2)), [], [], sizes
    coords = np.concatenate(blocks)
    return linestrings(coords), coords, np.concatenate(ring_blocks), np.concatenate(position_blocks), sizes


def _witness(first, second, a, b):
    """平行边取重叠投影中点，避免 nearest_points 总落在局部拐角。"""
    u, v = a[1] - a[0], b[1] - b[0]
    u, v = u / np.linalg.norm(u), v / np.linalg.norm(v)
    if abs(float(np.dot(u, v))) > 1 - 1e-10:
        lo, hi = sorted(float(np.dot(p - a[0], u)) for p in b)
        left, right = max(0, lo), min(float(np.linalg.norm(a[1] - a[0])), hi)
        if right > left + 1e-12:
            p = a[0] + u * ((left + right) / 2)
            q = b[0] + v * float(np.dot(p - b[0], v))
            return Point(p), Point(q)
    return nearest_points(first, second)


def topology_candidates(solid, width_limit=None, gap_limit=None, tolerance=.002,
                        limits=None, stats=None):
    """测量相向边界：铜内连接线为窄颈候选，铜外为窄槽候选。

    含同一连通铜皮的凹槽/孔洞；不替代网络短路或完整骨架宽度分析。
    所有结果作为 REVIEW。圆角/离散误差可能形成多个局部候选。
    """
    limits = limits or ScanLimits()
    stats = stats if stats is not None else {}
    segments, coords, rings, positions, sizes = boundary_segments(solid)
    prepare(solid)
    threshold = max(width_limit or 0, gap_limit or 0)
    if threshold <= 0:
        stats.update(objects=len(segments), candidates=0, complete=False)
        return
    findings = 0
    for i, j in nearby_pairs(segments, threshold + 2 * tolerance, limits, stats):
        if rings[i] == rings[j]:
            delta = abs(positions[i] - positions[j])
            if min(delta, sizes[rings[i]] - delta) <= 1:
                continue
        p, q = _witness(segments[i], segments[j], coords[i], coords[j])
        distance = p.distance(q)
        if distance <= 1e-10:
            continue
        direction = np.array([q.x - p.x, q.y - p.y]) / distance
        normals = []
        for index in (i, j):
            tangent = coords[index, 1] - coords[index, 0]
            normals.append(np.array([-tangent[1], tangent[0]]) / np.linalg.norm(tangent))
        facing = [float(np.dot(normals[0], direction)), float(np.dot(normals[1], -direction))]
        # Reject neighboring, similarly directed edges of discretized smooth arcs.
        if min(facing) > .5:
            kind, required = "width", width_limit
        elif max(facing) < -.5:
            kind, required = "gap", gap_limit
        else:
            continue
        if not required or distance >= required + 2 * tolerance:
            continue
        start = (p.x * .9 + q.x * .1, p.y * .9 + q.y * .1)
        end = (p.x * .1 + q.x * .9, p.y * .1 + q.y * .9)
        connector = LineString([start, end])
        if kind == "width" and not solid.covers(connector):
            continue
        if kind == "gap" and not solid.disjoint(connector):
            continue
        findings += 1
        if findings > limits.max_findings:
            stats["complete"] = False
            raise ScanBudgetExceeded(f"拓扑候选超过 {limits.max_findings}，扫描未完成")
        yield dict(kind=kind, measured=distance, required=required,
                   point=Point((p.x + q.x) / 2, (p.y + q.y) / 2))


def board_area(centerlines):
    """只接受封闭且无悬挂线的外形；嵌套环按奇偶规则扣除挖空。"""
    if not centerlines:
        return GeometryCollection(), "缺少封闭外形，不能用图元包围盒代替板面积"
    faces, cuts, dangles, invalid = polygonize_full(unary_union(centerlines))
    if any(not shape.is_empty for shape in (cuts, dangles, invalid)) or faces.is_empty:
        return GeometryCollection(), "外形未封闭或存在悬挂/无效线，铜平衡未检查"
    area = GeometryCollection()
    for face in polygons(faces):
        area = area.symmetric_difference(Polygon(face.exterior))
    return area, ""


def copper_density(solid, outline, cell_mm=10.0, limits=None):
    """实际封闭板面积上的总铺铜率及固定 mm 网格局部铺铜率。"""
    limits = limits or ScanLimits()
    if outline.is_empty or outline.area <= 0:
        raise ValueError("铜密度需要有效封闭板框")
    if not np.isfinite(cell_mm) or cell_mm <= 0:
        raise ValueError("铜平衡网格尺寸必须为正有限数")
    x0, y0, x1, y1 = outline.bounds
    nx, ny = ceil((x1 - x0) / cell_mm), ceil((y1 - y0) / cell_mm)
    if nx * ny > limits.max_density_cells:
        raise ScanBudgetExceeded(f"铜密度网格超过 {limits.max_density_cells}，请增大 cell_mm")
    pieces = polygons(solid)
    tree = STRtree(pieces)
    rows, copper_area = [], 0.0
    for ix in range(nx):
        for iy in range(ny):
            tile = outline.intersection(box(x0 + ix * cell_mm, y0 + iy * cell_mm,
                                            min(x1, x0 + (ix + 1) * cell_mm),
                                            min(y1, y0 + (iy + 1) * cell_mm)))
            if tile.area <= 0:
                continue
            area = sum(pieces[int(j)].intersection(tile).area for j in tree.query(tile))
            copper_area += area
            point = tile.representative_point()
            rows.append(dict(ix=ix, iy=iy, board_area_mm2=tile.area, copper_area_mm2=area,
                             density_percent=100 * area / tile.area, x_mm=point.x, y_mm=point.y))
    return dict(board_area_mm2=outline.area, copper_area_mm2=copper_area,
                density_percent=100 * copper_area / outline.area, cell_mm=cell_mm, cells=rows)


def annular_ring(hole, copper, required, tolerance=.002, tree=None, pieces=None):
    """实际孔壁到铜外边界；同时支持实心盘和 Gerber 已扣孔的环形盘。

    裁切窗口大于能力值，只返回缺陷附近的距离；合格值是局部下界，
    不作为全板最小孔环的精确统计。NPTH/非功能盘必须由调用者排除。
    """
    margin = max(required * 2 + 4 * tolerance, .5)
    x0, y0, x1, y1 = hole.bounds
    window = box(x0 - margin, y0 - margin, x1 + margin, y1 + margin)
    if tree is not None:
        local = unary_union([pieces[int(j)].intersection(window) for j in tree.query(window)])
    else:
        local = copper.intersection(window)
    filled = local.union(hole)
    measured = hole.distance(filled.boundary)
    a, b = nearest_points(hole, filled.boundary)
    return measured, Point((a.x + b.x) / 2, (a.y + b.y) / 2)

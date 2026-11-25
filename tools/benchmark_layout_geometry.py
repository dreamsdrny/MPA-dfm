"""合成空间索引基准；不代表百万 Gerber 解析/布尔运算验收。

运行: python tools/benchmark_layout_geometry.py --objects 1000000
最后两图元人为靠近，验证扫描尾部没有遗漏；仅产生一个邻域候选。
"""
import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

import numpy as np
from shapely import points

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.layout_geometry import nearby_pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objects", type=int, default=100_000)
    args = parser.parse_args()
    if args.objects < 2:
        parser.error("objects 必须 ≥2")
    started = perf_counter()
    coords = np.column_stack((np.arange(args.objects, dtype=float) * 2,
                              np.zeros(args.objects)))
    coords[-1] = coords[-2] + [.05, 0]
    shapes = points(coords)
    geometry_seconds = perf_counter() - started
    stats = {}
    started = perf_counter()
    pairs = list(nearby_pairs(shapes, .1, stats=stats))
    seconds = perf_counter() - started
    expected = [(args.objects - 2, args.objects - 1)]
    if pairs != expected or not stats["complete"]:
        raise AssertionError(f"扫描结果与预期不符: {pairs}")
    print(json.dumps(dict(objects=args.objects, build_geometry_seconds=round(geometry_seconds, 3),
                          index_and_scan_seconds=round(seconds, 3), candidates=stats["candidates"],
                          complete=stats["complete"], tail_pair_found=True,
                          scope="synthetic sparse points STRtree only"), indent=2))


if __name__ == "__main__":
    main()

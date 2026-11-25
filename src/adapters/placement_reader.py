"""器件坐标 CSV 合同：明确单位，一行一个位号；不猜原点/镜像。"""
import csv
import math
from pathlib import Path

from src.adapters.bom_reader import _normalize_side


def read_placements(path):
    with open(path, encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        names = {name.strip().lower(): name for name in reader.fieldnames or []}
        if "refdes" not in names:
            raise ValueError("器件坐标 CSV 必须有 refdes 列")
        millimeters = "x_mm" in names and "y_mm" in names
        if not millimeters and not {"x", "y", "unit"} <= names.keys():
            raise ValueError("器件坐标 CSV 必须有 x_mm/y_mm 或 x/y/unit 列")
        rows, seen = [], set()
        for line, raw in enumerate(reader, 2):
            if not any(str(value or "").strip() for value in raw.values()):
                continue
            row = {key: str(raw.get(original) or "").strip() for key, original in names.items()}
            ref = row["refdes"].upper()
            if not ref or ref in seen:
                raise ValueError(f"器件坐标行 {line}: 位号空白/重复 {ref}")
            if millimeters:
                scale, x, y = 1, row["x_mm"], row["y_mm"]
                if row.get("unit", "mm").lower() != "mm":
                    raise ValueError(f"器件坐标行 {line}: x_mm/y_mm 与 unit 冲突")
            else:
                scale = {"mm": 1, "inch": 25.4, "in": 25.4, "mil": .0254}.get(row["unit"].lower())
                if scale is None:
                    raise ValueError(f"器件坐标行 {line}: 未支持单位 {row['unit']}")
                x, y = row["x"], row["y"]
            try:
                x, y, rotation = float(x)*scale, float(y)*scale, float(row.get("rotation") or 0)
            except ValueError as exc:
                raise ValueError(f"器件坐标行 {line}: 数值无效") from exc
            if not all(math.isfinite(value) for value in (x, y, rotation)):
                raise ValueError(f"器件坐标行 {line}: 数值必须有限")
            side = _normalize_side(row.get("side"))
            if side not in ("TOP", "BOTTOM"):
                raise ValueError(f"器件坐标行 {line}: side 必须为 TOP/BOTTOM")
            seen.add(ref)
            rows.append(dict(refdes=ref, x_mm=x, y_mm=y, rotation=rotation, side=side,
                             package=row.get("footprint") or row.get("package", ""),
                             source=str(Path(path).resolve()), coordinate_kind="component_origin",
                             rotation_convention="placement CSV as supplied", pins=[]))
    if not rows:
        raise ValueError("器件坐标 CSV 没有记录")
    return dict(status="parsed", records=rows, warnings=[])

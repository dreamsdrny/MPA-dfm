"""Attach reproducible PCB problem coordinates to an analysis snapshot.

Coordinate origin is preserved, never translated to the outline corner. Only
geometry with known units is used; board-level constraints have no unique point.
"""
import math
import os
from copy import deepcopy

from src import pick
from src.net_locate import parse_traces_csv, list_geometry_files

LOCATION_HEADERS = ["检查项", "字段", "状态", "定位状态", "实测值", "能力值", "测量单位",
                    "X(mm)", "Y(mm)", "X(BRD)", "Y(BRD)", "BRD坐标单位", "层",
                    "网络", "位号", "焊盘栈", "坐标来源", "Allegro命令", "说明"]
LOCATION_KEYS = ["item", "field", "status", "location_status", "measured", "capability", "unit",
                 "x_mm", "y_mm", "x_brd", "y_brd", "brd_unit", "layer", "net", "refdes",
                 "padstack", "source", "command", "note"]
COMPONENT_HEADERS = ["位号", "X(mm)", "Y(mm)", "X(BRD)", "Y(BRD)", "BRD坐标单位", "面", "旋转", "坐标来源", "Allegro命令"]
COMPONENT_KEYS = ["refdes", "x_mm", "y_mm", "x_brd", "y_brd", "brd_unit", "side", "rotation", "source", "command"]


def component_values(component):
    return [component.get(key, "") for key in COMPONENT_KEYS]


def component_coordinates(inventory, design_unit=""):
    """True placement origins, not averages of a component's pads."""
    unit = normalize_unit(design_unit)
    coordinates = []
    for component in inventory.get("components", []):
        row = _point(dict(brd_unit=unit or "未知", note="器件放置原点，保留数据库原点"), component, unit)
        row["x_mm"] = round(row["x_mm"], 6)
        row["y_mm"] = round(row["y_mm"], 6)
        row.update(item="器件 " + component.get("refdes", ""),
                   side=component.get("side", ""), rotation=component.get("rotation", ""))
        coordinates.append(row)
    return coordinates


def location_values(row):
    return [row.get(key, "") for key in LOCATION_KEYS]


def normalize_unit(unit):
    key = str(unit or "").strip().lower()
    return {"millimeters": "mm", "millimeter": "mm", "mils": "mil",
            "inches": "inch", "microns": "um", "micron": "um"}.get(key, key)


def _point(row, coordinate, unit):
    row = dict(row)
    row.update({key: coordinate.get(key, "") for key in
                ("x_mm", "y_mm", "layer", "net", "refdes", "padstack", "source")})
    row["location_status"] = "可定位（按来源核对）"
    factor = pick._UNIT_MM.get(unit)
    if factor:
        row["x_brd"] = round(row["x_mm"] / factor, 6)
        row["y_brd"] = round(row["y_mm"] / factor, 6)
        row["command"] = f"x {row['x_brd']:.6f} {row['y_brd']:.6f}"
    else:
        row["note"] += "；BRD 设计单位未知，请先在 Allegro 设为 mm 再用 X(mm)/Y(mm)"
    return row


def attach_locations(result, brd_path="", files=(), design_unit="", extra_dirs=()):
    """Return a copy; all source parsing happens in the analysis worker."""
    result = deepcopy(result)
    unit = normalize_unit(design_unit)
    search_dirs = list(extra_dirs or ())
    if brd_path:
        search_dirs.append(os.path.join(os.path.dirname(os.path.abspath(brd_path)), "定位清单"))
    if search_dirs and not files:
        # Explicit GUI source directory takes precedence over auto-discovery.
        explicit_dirs = list(extra_dirs or ())
        if explicit_dirs:
            files = [source["path"] for source in pick.discover_sources("", explicit_dirs)]
            files += [p for p in list_geometry_files("", dirs=explicit_dirs)
                      if "trace" in os.path.basename(p).lower()]
    inventory_files = [p for p in files if "trace" not in os.path.basename(p).lower()]
    inventory = pick.build_inventory("" if extra_dirs else brd_path, files=inventory_files or None,
                                     extra_dirs=list(extra_dirs) if extra_dirs else search_dirs)
    trace_paths = ([os.path.abspath(p) for p in files if "trace" in os.path.basename(p).lower()]
                   if files else [p for p in list_geometry_files("" if extra_dirs else brd_path,
                                                              dirs=list(extra_dirs) if extra_dirs else search_dirs)
                                  if "trace" in os.path.basename(p).lower()])
    traces = []
    for path in dict.fromkeys(trace_paths):
        parsed = parse_traces_csv(path)
        for trace in parsed.get("items", []):
            trace["source"] = os.path.abspath(path)
            traces.append(trace)
        if not parsed.get("ok"):
            inventory["errors"].append(f"{path}: {parsed.get('error', '没有走线数据')}")
    located = {row["field"]: row for row in pick.locate_failures(result, inventory)}
    rows = []
    from src.dfm_engine import load_capability
    from src.silk_position import load_position_checks
    directories = list(extra_dirs) if extra_dirs else search_dirs + ([os.path.dirname(os.path.abspath(brd_path))] if brd_path else [])
    position_checks, position_meta = load_position_checks(directories, inventory,
        load_capability(result.get("board_type", "rigid"), result.get("capability_level", "standard"), result.get("factory", "hongban")))
    for position in position_checks:
        rows.append(_point(dict(position, brd_unit=unit or "未知"), position, unit))
    if position_checks:
        # Object-level failures must affect the verdict, not merely the location tab.
        from src.dfm_rules import make_issue
        groups = {}
        for position in position_checks:
            groups.setdefault(position["field"], []).append(position)
        for index, points in enumerate(groups.values(), 1):
            first = points[0]
            failed = any(p["status"] == "fail" for p in points)
            description = f"{first['item']}：{len(points)} 个位置" + ("不符合制程能力" if failed else "需复核")
            result.setdefault("items", []).append(dict(category="丝印", item=first["item"], field=first["field"],
                design=min(p["measured"] for p in points), capability=first["capability"], unit="mm",
                status="fail" if failed else "warn_limit", source="Allegro", message=description,
                note=first["note"]))
            result.setdefault("issues", []).append(make_issue(f"SILK-XY-{index:02d}", "丝印", first["item"],
                "高" if failed else "中", description, measured=first["measured"], required=first["capability"],
                unit="mm", layer=first.get("layer", ""), x=first["x_mm"], y=first["y_mm"],
                count=len(points), note=first["note"]))
        result["error_count"] = sum(i.get("status") == "fail" for i in result["items"])
        result["warning_count"] = sum(i.get("status") == "warn_limit" for i in result["items"])
        result["errors"] = [i.get("message", "") for i in result["items"] if i.get("status") == "fail"]
        result["warnings"] = [i.get("message", "") for i in result["items"] if i.get("status") == "warn_limit"]
        if result["error_count"]:
            result.update(passed=False, releasable=False, verdict="不通过（含对象级丝印位置检查）")
            if result.get("risk"):
                result["risk"]["verdict"] = result["verdict"]
        elif any(p["status"] == "warn_limit" for p in position_checks):
            result["releasable"] = False
            result["verdict"] = "丝印位置候选需复核，不能直接放行"
            if result.get("risk"):
                result["risk"]["verdict"] = result["verdict"]
        summary = result.setdefault("issue_summary", {})
        for severity in ("高", "中"):
            summary[severity] = sum(i.get("severity") == severity for i in result.get("issues", []))
        summary["总计"] = len(result.get("issues", []))
    for item in result.get("items", []):
        if item.get("status") not in ("fail", "warn_limit"):
            continue
        field = item.get("field", "")
        if item.get("source") == "Allegro" and any(p["item"] == item.get("item") for p in position_checks):
            continue
        base = dict(item=item.get("item", ""), field=field, status=item["status"],
                    location_status="待补几何/坐标", measured=item.get("design"),
                    capability=item.get("capability"), unit=item.get("unit", ""),
                    brd_unit=unit or "未知", note="板级参数/约束不能指向唯一对象；需 Allegro DRC/CAM 几何核对")
        match = located.get(field, {})
        points = match.get("coords", [])
        if points:
            base["note"] = match["note"] + "；保留导出原点；焊盘栈名推算尺寸需核对实际 padstack"
            for coordinate in points:
                base["measured"] = coordinate.get("ring_mm") if field.startswith("min_annular_ring") else coordinate.get("drill_mm")
                rows.append(_point(base, coordinate, unit))
            continue
        if field in ("min_line_width", "min_inner_line_width", "min_neck_width"):
            cap = item.get("capability")
            candidates = traces
            if field == "min_inner_line_width":
                candidates = [t for t in traces if str(t.get("layer", "")).upper().split("/")[-1]
                              not in ("TOP", "BOTTOM", "UNKNOWN", "")]
            bad = [t for t in candidates if isinstance(cap, (int, float))
                   and t.get("width_mm") is not None and math.isfinite(t["width_mm"])
                   and t["width_mm"] < cap - 1e-9]
            if bad:
                base["note"] = "真实走线宽度低于板厂能力；坐标为导出端点中点（圆弧需结合对象查看）；保持数据库原点"
                for trace in bad:
                    base["measured"] = trace["width_mm"]
                    rows.append(_point(base, trace, unit))
                continue
        base["note"] = match.get("note") or base["note"]
        if inventory["errors"]:
            base["note"] += "；来源解析问题见坐标来源记录"
        rows.append(base)
    # Additional assembly/process issues can exist outside the capability items.
    known = {row["item"] for row in rows}
    for issue in result.get("issues", []):
        if issue.get("rule") in known:
            continue
        rows.append(dict(item=issue.get("rule", ""), field=issue.get("field", ""),
                         status=issue.get("status", ""), location_status="待补几何/坐标",
                         measured=issue.get("measured"), capability=issue.get("required"),
                         unit=issue.get("unit", ""), brd_unit=unit or "未知",
                         note="需对象级几何/DRC 数据，不能由板级约束确定坐标"))
    result["problem_locations"] = rows
    result["coordinate_unit"] = unit or "未知"
    result["coordinate_sources"] = inventory["sources"]
    result["coordinate_errors"] = inventory["errors"]
    result["position_checks"] = position_checks
    result["position_geometry"] = position_meta
    result["component_coordinates"] = component_coordinates(inventory, unit)
    result["coordinate_note"] = ("X/Y 保持来源文件的原点与轴向，不平移到板框角；在同一版本 BRD 中，"
                                 "将设计单位设为 BRD坐标单位，命令窗口输入 x X Y 定位。"
                                 "NC Drill/Placement 如使用辅助原点/镜像必须先确认与 BRD 一致。")
    # Fill the existing issue summary's mm fields with the first verified point.
    for issue in result.get("issues", []):
        points = [row for row in rows if row["item"] == issue.get("rule") and "x_mm" in row]
        if points:
            issue.update(x=points[0]["x_mm"], y=points[0]["y_mm"], count=len(points),
                         layer=points[0].get("layer", ""))
    return result

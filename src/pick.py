"""坐标清单与"不符合项定位"引擎 (无 tkinter 依赖)。

.brd 数据库包含器件/孔位，但当前离线解析器不能解码这些对象坐标。要回答
"0.199mm 的孔有几个、在哪" 和 "R100 在什么坐标", 必须有坐标来源:

    1) SKILL 导出 (推荐): PCB Editor 里跑 src/allegro_skill.py 生成的脚本,
       得到 evan_components.csv / evan_holes.csv —— 元件与孔位全量坐标
    2) Placement 导出: File > Export > Placement 的 place_txt.txt —— 只有元件
    3) Excellon 钻孔文件 ( Manufacture > NC > NC Drill ): 全部孔的尺寸+坐标

本模块只做"读 + 核对 + 输出", 不做任何猜测: 没有坐标来源就明确写
"数量已知, 坐标需导出", 绝不编造位置。

输出:
    定位清单.xlsx   汇总 / 元件坐标 / 超能力孔位 / 全部孔位 / 数据来源
    定位清单.csv    同上汇总的精简版
    定位清单.txt    纯文本摘要
    locate.scr      Allegro 脚本: 依次 zoom 到每个不符合位置 (可选)
"""
import csv
import math
import os
import re
from typing import Dict, Iterable, List, Optional

try:  # 出 Excel 需要 openpyxl, 缺失时降级为 csv/txt
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
except Exception:  # noqa: BLE001
    Workbook = None
    Alignment = Font = PatternFill = None

MM_PER_MIL = 0.0254

# SKILL 导出的 CSV 头
_SKILL_UNITS = "UNITS"
_BRIDGE_DIRS = ("allegro_bridge", "bridge", "evan_pick", "坐标导出")

# 单位 -> mm
_UNIT_MM = {
    "millimeters": 1.0, "millimeter": 1.0, "mm": 1.0,
    "microns": 0.001, "micron": 0.001, "um": 0.001,
    "centimeters": 10.0, "cm": 10.0,
    "mils": MM_PER_MIL, "mil": MM_PER_MIL,
    "inches": 25.4, "inch": 25.4,
}

# 需要"孔尺寸"来定位的 DFM 字段
_DRILL_FIELDS = ("min_mech_drill", "max_mech_drill", "min_laser_via")


def _to_mm(value: Optional[float], unit: str, dbu_per_unit: Optional[float],
           is_dbu: bool = False) -> Optional[float]:
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    factor = _UNIT_MM.get((unit or "").strip().lower())
    if factor is None:
        return None
    if is_dbu:
        if not dbu_per_unit or not math.isfinite(dbu_per_unit) or dbu_per_unit <= 0:
            return None
        return value / float(dbu_per_unit) * factor
    return value * factor


# --------------------------------------------------------------------------- 读 SKILL
def parse_skill_csv(path: str) -> Dict[str, object]:
    """读 evan_components.csv / evan_holes.csv / evan_pads.csv (本工具生成的 SKILL 导出格式)。

    头两行形如:
        UNITS,millimeters,3,1000
        REFDES,X_DBU,Y_DBU,ROTATION,SIDE
        KIND,PADSTACK,X_DBU,Y_DBU,W_DBU,H_DBU,LAYER,NET,REFDES,PIN      (焊盘外框)
    """
    unit, dbu, header = "millimeters", 1000.0, []
    rows: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith(_SKILL_UNITS + ","):
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 2 and parts[1]:
                        unit = parts[1]
                    if len(parts) >= 4:
                        try:
                            dbu = float(parts[3])
                        except ValueError:
                            pass
                    continue
                if not header:
                    header = [h.strip().upper() for h in line.split(",")]
                    continue
                vals = [v.strip() for v in next(csv.reader([line]))]
                rows.append(dict(zip(header, vals)))
    except OSError:
        return {"ok": False, "error": f"读不到 {os.path.basename(path)}"}

    if not rows:
        return {"ok": False, "error": f"{os.path.basename(path)} 里没有数据行"}

    # 焊盘外框优先判定: 它同时带 PADSTACK 和 W_DBU/H_DBU, 不能当孔位读
    is_pads = ("W_DBU" in header and "H_DBU" in header) or ("W_UU" in header and "H_UU" in header)
    is_holes = (not is_pads) and "PADSTACK" in header
    items: List[dict] = []
    for r in rows:
        try:
            x = float(r.get("X_UU", r.get("X_DBU", "")))
            y = float(r.get("Y_UU", r.get("Y_DBU", "")))
        except (TypeError, ValueError):
            continue
        entry = {
            "x_mm": _to_mm(x, unit, dbu, is_dbu="X_UU" not in header),
            "y_mm": _to_mm(y, unit, dbu, is_dbu="Y_UU" not in header),
            "source": f"SKILL 导出 ({os.path.basename(path)})",
        }
        if entry["x_mm"] is None or entry["y_mm"] is None:
            continue
        if is_pads:
            try:
                w = float(r.get("W_UU", r.get("W_DBU", "")))
                h = float(r.get("H_UU", r.get("H_DBU", "")))
            except (TypeError, ValueError):
                continue
            w_mm = _to_mm(w, unit, dbu, is_dbu="W_UU" not in header)
            h_mm = _to_mm(h, unit, dbu, is_dbu="H_UU" not in header)
            if not w_mm or not h_mm:
                continue
            entry.update({
                "padstack": r.get("PADSTACK", ""),
                "pin": r.get("PIN", ""),
                "net": r.get("NET", ""),
                "refdes": r.get("REFDES", ""),
                "layer": r.get("LAYER", ""),
                "side": (r.get("SIDE", "") or "").upper(),
                "w_mm": round(w_mm, 4), "h_mm": round(h_mm, 4),
                "kind": "PIN",
            })
        elif is_holes:
            try:
                drill = _to_mm(float(r.get("DRILL_UU", "")), unit, dbu)
            except (ValueError, TypeError):
                drill = None
            entry.update({
                "padstack": r.get("PADSTACK", ""),
                "net": r.get("NET", ""),
                "drill_mm": drill if drill and drill > 0 else None,
                "drill_source": "Allegro padstack drillDiameter" if drill else "",
                "is_through": {"YES": True, "NO": False}.get(r.get("IS_THROUGH", "").upper()),
                "kind": r.get("KIND", "VIA") or "VIA",
                "refdes": r.get("REFDES", ""),
            })
        else:
            entry.update({
                "refdes": r.get("REFDES", ""),
                "rotation": r.get("ROTATION", ""),
                "side": r.get("SIDE", ""),
            })
        items.append(entry)
    kind = "pads" if is_pads else ("holes" if is_holes else "components")
    return {"ok": bool(items), "kind": kind,
            "items": items, "unit": unit, "dbu_per_unit": dbu,
            "source": os.path.basename(path)}


# --------------------------------------------------------------------------- 读 Placement
_PLACE_RE = re.compile(r"^\s*(\S+)\s+(\S+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(TOP|BOTTOM)\s*$",
                       re.IGNORECASE)


def parse_placement_txt(path: str) -> Dict[str, object]:
    """读 Allegro 的 place_txt.txt (File > Export > Placement)。

    格式随版本略有差异, 这里按"位号 封装 X Y 旋转 面"的宽松规则解析,
    单位从文件头注释里找 (mils / millimeters), 找不到按 mils 处理并标注低置信。
    """
    unit = "mils"
    unit_from_file = False
    comp_xy = False
    warnings = []
    items: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                declaration = re.match(r"(?:UUNITS|UNITS)\s*=\s*(\S+)", line, re.IGNORECASE)
                if declaration:
                    unit = declaration.group(1).lower()
                    unit_from_file = True
                    comp_xy = True
                    continue
                if "!" in line and not line.startswith(("#", ";")):
                    values = [v.strip() for v in line.split("!")]
                    if len(values) >= 6:
                        try:
                            if not unit_from_file:
                                raise ValueError("COMP_XY 必须声明 UUNITS")
                            ref, sx, sy, rot, mirror, footprint = values[:6]
                            x, y = _to_mm(float(sx), unit, None), _to_mm(float(sy), unit, None)
                            rotation = float(rot)
                            if x is None or y is None or not math.isfinite(rotation):
                                raise ValueError("未知单位或非有限数值")
                            items.append(dict(refdes=ref, footprint=footprint, x_mm=x, y_mm=y,
                                              rotation=rot, side="BOTTOM" if mirror.lower() in ("m", "yes", "true", "1") else "TOP",
                                              embedded_layer=values[6] if len(values) > 6 else "",
                                              source=f"COMP_XY ({os.path.basename(path)})"))
                        except ValueError:
                            warnings.append("无效坐标行: " + line[:100])
                    continue
                if line.startswith("#") or line.startswith(";"):
                    low = line.lower()
                    for key, u in ((r"\bmils?\b", "mils"), (r"\b(?:millimeters?|mm)\b", "millimeters"),
                                   (r"\b(?:inches|inch)\b", "inches"), (r"\b(?:um|microns?)\b", "um")):
                        if re.search(key, low) and not unit_from_file:
                            unit, unit_from_file = u, True
                    continue
                m = _PLACE_RE.match(line)
                if not m:
                    continue
                ref, _footprint, sx, sy, rot, side = m.groups()
                try:
                    x = _to_mm(float(sx), unit, None)
                    y = _to_mm(float(sy), unit, None)
                    if x is None or y is None:
                        continue
                except ValueError:
                    continue
                items.append({"refdes": ref, "footprint": _footprint,
                              "x_mm": x, "y_mm": y,
                              "rotation": rot, "side": side.upper(),
                              "source": f"Placement 导出 ({os.path.basename(path)})"})
    except OSError:
        return {"ok": False, "error": f"读不到 {os.path.basename(path)}"}

    if not items:
        return {"ok": False, "error": "没有解析到元件行 (检查是否为 Allegro 的 place_txt.txt)"}
    return {"ok": True, "kind": "components", "items": items, "unit": unit,
            "unit_from_file": unit_from_file, "source": os.path.basename(path),
            "format": "COMP_XY" if comp_xy else "placement", "warnings": warnings}


# --------------------------------------------------------------------------- 读 Excellon
_EXC_TOOL_RE = re.compile(r"^T(\d+)(?:F[\d.]+|S[\d.]+)*C([\d.]+)", re.IGNORECASE)
_EXC_XY_RE = re.compile(r"^X(-?[\d.]+)Y(-?[\d.]+)", re.IGNORECASE)


def parse_excellon(path: str) -> Dict[str, object]:
    """读 Excellon 钻孔文件, 返回 {holes:[{x_mm,y_mm,drill_mm,tool}], tools:{...}}。

    支持 METRIC / M71 / INCH / M72 与 FILE_FORMAT=2:5 这类整数格式。
    """
    unit = "inches"
    decimals = None         # Unknown integer format must never be guessed.
    integer_digits = None
    suppression = "L"
    tools: Dict[int, float] = {}
    holes: List[dict] = []
    tool_no = None
    x = y = None
    errors = []

    def coordinate(value):
        if "." in value:
            return float(value)
        if decimals is None:
            raise ValueError("整数坐标缺少 FILE_FORMAT 或单位行中的 000.000 格式声明")
        sign = -1 if value.startswith("-") else 1
        digits = value.lstrip("+-")
        if suppression == "T" and integer_digits is not None:
            digits = digits.ljust(integer_digits + decimals, "0")
        return sign * int(digits) / 10.0 ** decimals
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                up = line.upper()
                m = re.search(r"FILE_FORMAT\s*=\s*(\d+)\s*:\s*(\d+)", up)
                if m:
                    integer_digits, decimals = int(m.group(1)), int(m.group(2))
                    continue
                if up.startswith(";"):
                    continue
                if up in ("G91", "ICI,ON"):
                    return {"ok": False, "error": "暂不支持增量钻孔坐标，需导出绝对坐标"}
                if up.startswith("M48"):
                    continue
                if up.startswith("%") or up.startswith("M95"):
                    continue
                if up in ("M71", "M72", "METRIC", "INCH") or up.startswith(("METRIC,", "INCH,")):
                    unit = "millimeters" if up.startswith("METRIC") or up == "M71" else "inches"
                    suppression = "T" if ",TZ" in up else "L"
                    fmt = re.search(r"(0+)\.(0+)", up)
                    if fmt:
                        integer_digits, decimals = len(fmt.group(1)), len(fmt.group(2))
                    continue
                if up.startswith("M30") or up.startswith("M00"):
                    break
                m = _EXC_TOOL_RE.match(up)
                if m:
                    try:
                        tools[int(m.group(1))] = _to_mm(float(m.group(2)), unit, None)
                    except ValueError:
                        pass
                    continue
                if up.startswith("T") and up[1:].strip().isdigit():
                    tool_no = int(up[1:].strip())
                    continue
                m = re.fullmatch(r"(?:G0?5)?(?:X([+-]?[\d.]+))?(?:Y([+-]?[\d.]+))?", up)
                if not m:
                    continue
                try:
                    xs, ys = m.group(1), m.group(2)
                    nx = coordinate(xs) if xs is not None else x
                    ny = coordinate(ys) if ys is not None else y
                    x, y = nx, ny
                    if x is None or y is None or tool_no not in tools:
                        errors.append("坐标或钻孔刀具未初始化")
                        continue
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
                x_mm = _to_mm(x, unit, None)
                y_mm = _to_mm(y, unit, None)
                if x_mm is None or y_mm is None:
                    continue
                holes.append({"x_mm": x_mm, "y_mm": y_mm, "tool": tool_no,
                              "drill_mm": tools.get(tool_no),
                              "net": "", "padstack": f"T{tool_no}" if tool_no else "",
                              "kind": "DRILL", "refdes": ""})
    except OSError:
        return {"ok": False, "error": f"读不到 {os.path.basename(path)}"}

    if not holes:
        return {"ok": False, "error": "; ".join(dict.fromkeys(errors)) or "没有解析到孔坐标 (检查是否为 Excellon .drl)"}
    return {"ok": True, "kind": "holes", "items": holes, "unit": unit,
            "tools_mm": {k: round(v, 4) for k, v in tools.items() if v},
            "source": os.path.basename(path), "format_decimals": decimals,
            "warnings": list(dict.fromkeys(errors))}


# --------------------------------------------------------------------------- 发现与汇总
def discover_sources(brd_path: str, extra_dirs: Iterable[str] = ()) -> List[dict]:
    """在 BRD 同目录/输出目录里找可用的坐标来源。"""
    found: List[dict] = []
    seen = set()
    dirs = []
    if brd_path:
        dirs.append(os.path.dirname(os.path.abspath(brd_path)))
    dirs.extend(d for d in extra_dirs if d and os.path.isdir(d))
    for d in dirs:
        if d in seen:
            continue
        seen.add(d)
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        # SKILL 脚本默认输出到 <输出目录>\allegro_bridge, 顺手扫一层子目录
        for name in names:
            sub = os.path.join(d, name)
            if os.path.isdir(sub) and name.lower() in _BRIDGE_DIRS and sub not in seen:
                seen.add(sub)
                try:
                    names += [os.path.join(name, n) for n in sorted(os.listdir(sub))]
                except OSError:
                    pass
        for name in names:
            full = os.path.join(d, name)
            if not os.path.isfile(full):
                continue
            low = os.path.basename(name).lower()
            if low in ("evan_components.csv", "evan_holes.csv", "evan_pads.csv"):
                found.append({"path": full, "kind": "skill"})
            elif low in ("place_txt.txt", "place_txt.csv") or low.startswith(("place_txt", "comp_xy")):
                found.append({"path": full, "kind": "placement"})
            elif low.endswith((".drl", ".nc", ".xln", ".txt")) and _looks_like_drill(full):
                found.append({"path": full, "kind": "excellon"})
    return found


def _looks_like_drill(path: str) -> bool:
    """粗判: 前 2KB 里出现 M48 / FILE_FORMAT / T..C.. 就是钻孔文件。"""
    try:
        with open(path, "rb") as f:
            head = f.read(2048).decode("latin-1", "replace").upper()
    except OSError:
        return False
    if "M48" in head or "FILE_FORMAT" in head:
        return True
    return bool(re.search(r"\bT\d+C[\d.]+", head)) and "X" in head


def build_inventory(brd_path: str, files: Optional[Iterable[str]] = None,
                    extra_dirs: Iterable[str] = ()) -> Dict[str, object]:
    """汇总坐标来源: components / holes / pads, 并记录每个来源的读取结果。"""
    comps: List[dict] = []
    holes: List[dict] = []
    pads: List[dict] = []
    used: List[dict] = []
    errors: List[str] = []

    if files:
        candidates = [{"path": os.path.abspath(p), "kind": "auto"} for p in files if p]
    else:
        candidates = discover_sources(brd_path, extra_dirs)

    for cand in candidates:
        path = cand["path"]
        low = os.path.basename(path).lower()
        parsers = []
        if low.endswith((".drl", ".nc", ".xln")) or _looks_like_drill(path):
            parsers.append(parse_excellon)
        if low.startswith("evan_") or low.endswith(".csv"):
            parsers.append(parse_skill_csv)
        if low.startswith("place_txt") or low.endswith(".txt"):
            parsers.append(parse_placement_txt)
        if not parsers:
            continue
        got = None
        for parser in parsers:
            res = parser(path)
            if res.get("ok"):
                got = res
                break
        if not got:
            errors.append(f"{os.path.basename(path)}: 解析失败")
            continue
        for warning in got.get("warnings", []):
            errors.append(f"{os.path.basename(path)}: {warning}")
        if got.get("kind") == "components" and got.get("unit_from_file") is False:
            errors.append(f"{os.path.basename(path)}: 未声明坐标单位，Placement 按 mil 读取，需核对")
        for entry in got["items"]:
            entry["source"] = os.path.abspath(path)
        if got["kind"] == "components":
            comps.extend(got["items"])
        elif got["kind"] == "pads":
            pads.extend(got["items"])
        else:
            holes.extend(got["items"])
        used.append({"file": os.path.basename(path), "kind": got["kind"],
                     "count": len(got["items"]), "unit": got.get("unit", ""),
                      "error": ""})

    # Prefer database SKILL exports; a COMP_XY file remains a valid standalone input.
    comps.sort(key=lambda c: (0 if os.path.basename(c.get("source", "")).lower() == "evan_components.csv" else 1))
    by_ref = {}
    for component in comps:
        key = str(component.get("refdes", "")).upper()
        old = by_ref.get(key)
        if old and (abs(old["x_mm"] - component["x_mm"]) > 0.01 or abs(old["y_mm"] - component["y_mm"]) > 0.01):
            errors.append(f"{key}: 多份坐标来源不一致，优先使用 SKILL 导出")
        by_ref.setdefault(key, component)
    comps = list(by_ref.values())

    # 孔位: 用焊盘栈名解析钻孔直径, 与 Excellon 的 tool 直径互为补充
    try:
        from src.brd_raw import parse_via_name

        for h in holes:
            if h.get("drill_mm"):
                h["drill_source"] = h.get("drill_source") or "钻孔文件刀具表"
            parsed = parse_via_name(h.get("padstack") or "")
            if parsed and parsed.get("drill_mil"):
                if not h.get("drill_mm"):
                    h["drill_mm"] = round(parsed["drill_mil"] * MM_PER_MIL, 4)
                h["pad_mm"] = round(parsed["pad_mil"] * MM_PER_MIL, 4)
                h["drill_source"] = h.get("drill_source") or "焊盘栈名"
                h["span"] = parsed.get("span")
            else:
                h["drill_source"] = h.get("drill_source") or ""
    except Exception:  # noqa: BLE001
        pass

    def deduplicate(items, keys):
        unique = {}
        for item in items:
            key = tuple(item.get(k) for k in keys)
            unique.setdefault(key, item)
        return list(unique.values())

    comps = deduplicate(comps, ("refdes", "x_mm", "y_mm", "side"))
    holes = deduplicate(holes, ("x_mm", "y_mm", "drill_mm", "span"))

    return {"components": comps, "holes": holes, "pads": pads, "sources": used,
            "errors": errors,
            "has_components": bool(comps), "has_holes": bool(holes),
            "has_pads": bool(pads)}


# --------------------------------------------------------------------------- 定位
def _size_tolerance(drill_mm: float) -> float:
    return max(0.003, drill_mm * 0.02)


def locate_failures(result: dict, inventory: dict, limit: Optional[int] = None) -> List[dict]:
    """把 DFM 不符合项翻译成"数量 + 坐标"。

    只有和孔径直接相关的项 (最小/最大机械孔、激光孔) 才能精确匹配孔位;
    其它项 (线宽/孔环/阻焊...) 需要图形几何, 这里如实标注"需 CAM/图纸"。
    """
    holes = inventory.get("holes") or []
    out: List[dict] = []
    for it in (result or {}).get("items") or []:
        if it.get("status") not in ("fail", "warn_limit"):
            continue
        field = it.get("field") or ""
        row = {
            "item": it.get("item", ""), "category": it.get("category", ""),
            "status": it.get("status"), "design": it.get("design"),
            "capability": it.get("capability"), "unit": it.get("unit", "mm"),
            "message": it.get("message", ""), "source": it.get("source", ""),
            "count": None, "coords": [], "located": False, "note": "",
            "field": field,
        }
        if field.startswith("min_annular_ring") and holes:
            cap = it.get("capability")
            sized = [h for h in holes if h.get("pad_mm") and h.get("drill_mm")]
            if field == "min_annular_ring":
                sized = [h for h in sized if not h.get("span") and h.get("is_through") is not False]
            elif field == "min_annular_ring_laser":
                sized = [h for h in sized if h.get("technology") == "laser"]
            matched = [h for h in sized
                       if (h["pad_mm"] - h["drill_mm"]) / 2.0 < cap - 1e-9]
            row["count"] = len(matched) if sized else None
            row["coords"] = [{"x_mm": round(h["x_mm"], 6), "y_mm": round(h["y_mm"], 6),
                              "drill_mm": h.get("drill_mm"),
                               "ring_mm": round((h["pad_mm"] - h["drill_mm"]) / 2.0, 4),
                               "padstack": h.get("padstack", ""), "net": h.get("net", ""),
                               "refdes": h.get("refdes", ""), "source": h.get("source", "")}
                              for h in matched[:limit]]
            row["located"] = bool(sized)
            if not sized:
                row["note"] = "有孔位但焊盘直径未知 (焊盘栈名无法解析), 不能判断孔环"
            else:
                row["note"] = (f"单边孔环 < {cap}mm 的孔; 可算孔环的孔共 {len(sized)} 个")
        elif field in _DRILL_FIELDS and holes:
            cap = it.get("capability")
            design = it.get("design")
            known = [h for h in holes if h.get("drill_mm")]
            if field in ("min_mech_drill", "max_mech_drill"):
                known = [h for h in known if not h.get("span") and h.get("is_through") is not False]
            elif field == "min_laser_via":
                # Size or padstack spelling alone cannot prove laser technology.
                known = [h for h in known if h.get("technology") == "laser"]
            unknown = len(holes) - len(known)
            if field == "max_mech_drill" and isinstance(cap, (int, float)):
                matched = [h for h in known
                            if h["drill_mm"] > cap + 1e-9]
                row["note"] = f"孔径 > 板厂能力 {cap}mm 的孔"
            elif isinstance(design, (int, float)):
                # 先按"违反能力值的那个孔径"精确匹配: 这就是用户问的 0.199mm 有几个、在哪
                tol = _size_tolerance(design)
                exact = [h for h in known if abs(h["drill_mm"] - design) <= tol]
                if it.get("status") == "fail" and isinstance(cap, (int, float)):
                    matched = [h for h in known if h["drill_mm"] < cap - 1e-9]
                    row["note"] = f"孔径 < 板厂能力 {cap}mm 的全部孔"
                elif exact:
                    matched = exact
                    row["note"] = f"孔径 = {design}mm 的孔"
                elif isinstance(cap, (int, float)):
                    matched = [h for h in known
                                if h["drill_mm"] < cap - 1e-9]
                    row["note"] = f"孔径 < {cap}mm 的孔 (没有与设计值 {design}mm 完全一致的孔)"
                else:
                    matched = []
                    row["note"] = "孔径未知, 无法匹配"
            else:
                matched = []
                row["note"] = "设计值缺失, 无法匹配"
            if unknown:
                row["note"] += f"; 另有 {unknown} 个孔孔径未解析 (焊盘栈名无法换算)"
            row["count"] = len(matched) if known else None
            row["coords"] = [{"x_mm": round(h["x_mm"], 6), "y_mm": round(h["y_mm"], 6),
                              "drill_mm": h.get("drill_mm"),
                              "ring_mm": (round((h["pad_mm"] - h["drill_mm"]) / 2.0, 4)
                                          if h.get("pad_mm") and h.get("drill_mm") else None),
                               "padstack": h.get("padstack", ""), "net": h.get("net", ""),
                               "refdes": h.get("refdes", ""), "source": h.get("source", "")}
                             for h in matched[:limit]]
            row["located"] = bool(matched)
            if matched:
                sizes = sorted({round(h["drill_mm"], 3) for h in matched if h.get("drill_mm")})
                row["note"] += f", 涉及孔径 {sizes[:6]}" + (" 等" if len(sizes) > 6 else "")
        else:
            if not holes and field in _DRILL_FIELDS:
                row["note"] = "没有孔位数据: 请在 PCB Editor 导出 SKILL 坐标或 Excellon 钻孔文件"
            elif field:
                row["note"] = "需要图形几何 (CAM/Gerber), 本清单不提供坐标"
            else:
                row["note"] = "该检查项不由坐标数据判定"
        out.append(row)
    return out


def locate_components(result: dict, inventory: dict) -> List[dict]:
    """元件坐标表: 带上与最近不符合孔位的距离, 方便先看问题区。"""
    comps = inventory.get("components") or []
    if not comps:
        return []
    rows = []
    for c in comps:
        rows.append({"refdes": c.get("refdes", ""),
                     "x_mm": round(c.get("x_mm") or 0, 3),
                     "y_mm": round(c.get("y_mm") or 0, 3),
                     "rotation": c.get("rotation", ""), "side": c.get("side", ""),
                     "source": c.get("source", "")})
    rows.sort(key=lambda r: (str(r["refdes"])))
    return rows


def hole_size_summary(inventory: dict) -> List[dict]:
    """按孔径汇总: 每个尺寸多少个孔 (与制程能力表对照时最有用的表)。"""
    from collections import defaultdict

    buckets = defaultdict(list)
    for h in inventory.get("holes") or []:
        d = h.get("drill_mm")
        if not d:
            continue
        buckets[round(d, 3)].append(h)
    rows = []
    for dia in sorted(buckets):
        holes = buckets[dia]
        xs = [h["x_mm"] for h in holes]
        ys = [h["y_mm"] for h in holes]
        rows.append({"drill_mm": dia, "count": len(holes),
                     "x_min": round(min(xs), 2), "x_max": round(max(xs), 2),
                     "y_min": round(min(ys), 2), "y_max": round(max(ys), 2),
                     "sample": [(round(h["x_mm"], 2), round(h["y_mm"], 2)) for h in holes[:5]]})
    return rows


# --------------------------------------------------------------------------- 输出
def _write_scr(path: str, located: List[dict], unit_hint: str = "mm") -> Optional[str]:
    """生成 Allegro 脚本: 依次把视图移到每个不符合位置。

    Allegro 的 zoom 命令吃的是设计用户单位, 所以 mm 要换算回设计单位。
    """
    factor = 1.0 / MM_PER_MIL if unit_hint == "mil" else (1.0 / 25.4 if unit_hint == "inch" else 1.0)
    lines = ["# Evan_BRD 定位脚本: 在 PCB Editor 里 Run Script 即可逐个跳转",
             f"# 坐标单位: {unit_hint} (按 .brd 设计单位换算)",
             "setwindow pcb"]
    n = 0
    for row in located:
        for c in row.get("coords") or []:
            lines.append(f"zoom center {c['x_mm'] * factor:.3f} {c['y_mm'] * factor:.3f}")
            lines.append("zoom 4")
            n += 1
    if not n:
        return None
    lines.append("zoom 4")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        return None
    return path


def write_report(out_dir: str, base_name: str, result: dict, inventory: dict,
                 located: Optional[List[dict]] = None,
                 extras: Optional[Dict[str, object]] = None,
                 design_unit: Optional[str] = None) -> Dict[str, object]:
    """写 xlsx + csv + txt + locate.scr, 返回各文件路径。"""
    located = located if located is not None else locate_failures(result, inventory)
    os.makedirs(out_dir, exist_ok=True)
    paths: Dict[str, object] = {}

    xlsx = os.path.join(out_dir, f"{base_name}.xlsx")
    try:
        if Workbook is None:
            raise RuntimeError("未安装 openpyxl")
        wb = Workbook()
        head_font = Font(bold=True, color="FFFFFF")
        head_fill = PatternFill("solid", fgColor="1F4E79")
        title_font = Font(bold=True, size=13)

        def sheet(name, headers, rows, widths=None):
            ws = wb.create_sheet(name)
            for c, h in enumerate(headers, 1):
                cell = ws.cell(row=1, column=c, value=h)
                cell.font = head_font
                cell.fill = head_fill
                cell.alignment = Alignment(horizontal="center")
            for r, row in enumerate(rows, 2):
                for c, v in enumerate(row, 1):
                    ws.cell(row=r, column=c, value=v)
            for i, w in enumerate(widths or [14] * len(headers), 1):
                ws.column_dimensions[chr(64 + i)].width = w
            ws.freeze_panes = "A2"
            return ws

        # 汇总
        ws = wb.active
        ws.title = "汇总"
        ws["A1"] = "DFM 不符合项定位清单"
        ws["A1"].font = title_font
        info = list((extras or {}).items()) + [
            ("结论", (result or {}).get("verdict", "")),
            ("元件坐标来源", _src_desc(inventory, "components")),
            ("孔位坐标来源", _src_desc(inventory, "holes")),
            ("元件数", len(inventory.get("components") or [])),
            ("孔数", len(inventory.get("holes") or [])),
        ]
        for i, (k, v) in enumerate(info, 3):
            ws.cell(row=i, column=1, value=k).font = Font(bold=True)
            ws.cell(row=i, column=2, value=v)
        ws.column_dimensions["A"].width = 16
        ws.column_dimensions["B"].width = 46

        rows = []
        for row in located:
            rows.append([row["category"], row["item"],
                         "FAIL" if row["status"] == "fail" else "WARN",
                         row["design"], row["capability"], row["source"],
                         row["count"] if row["count"] is not None else "",
                         "可定位" if row["located"] else "无坐标",
                         row["note"]])
        sheet("不符合项定位", ["类别", "检查项", "结论", "设计值", "能力值", "取值来源",
                              "孔数", "坐标", "说明"], rows,
              [12, 22, 8, 10, 10, 10, 8, 10, 52])

        _write_sheet_sizes(wb, inventory)
        comps = locate_components(result, inventory)
        if comps:
            sheet("元件坐标", ["位号", "X(mm)", "Y(mm)", "旋转", "面", "来源"],
                  [[c["refdes"], c["x_mm"], c["y_mm"], c["rotation"], c["side"], c["source"]]
                   for c in comps], [12, 12, 12, 10, 8, 30])
        sheet("数据来源", ["文件", "类型", "条数", "单位"],
              [[s["file"], "元件" if s["kind"] == "components" else "孔位",
                s["count"], s["unit"]] for s in inventory.get("sources") or []],
              [40, 10, 10, 14])
        wb.save(xlsx)
        paths["xlsx"] = xlsx
    except Exception as exc:  # noqa: BLE001
        paths["xlsx_error"] = str(exc)

    # CSV 摘要
    csv_path = os.path.join(out_dir, f"{base_name}.csv")
    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["类别", "检查项", "结论", "设计值", "能力值", "孔数", "坐标数", "说明"])
            for row in located:
                w.writerow([row["category"], row["item"],
                            "FAIL" if row["status"] == "fail" else "WARN",
                            row["design"], row["capability"], row["count"] or "",
                            len(row["coords"]), row["note"]])
        paths["csv"] = csv_path
    except OSError as exc:
        paths["csv_error"] = str(exc)

    # 文本摘要
    txt_path = os.path.join(out_dir, f"{base_name}.txt")
    try:
        lines = ["=" * 72, "  DFM 不符合项定位清单", "=" * 72,
                 f"  元件坐标来源: {_src_desc(inventory, 'components')}",
                 f"  孔位坐标来源: {_src_desc(inventory, 'holes')}", ""]
        for row in located:
            lines.append(f"[{'FAIL' if row['status'] == 'fail' else 'WARN'}] "
                         f"{row['category']} {row['item']}")
            lines.append(f"    设计 {row['design']} / 能力 {row['capability']}")
            if row["count"] is not None:
                lines.append(f"    命中 {row['count']} 个孔 ({row['note']})")
            if row["coords"]:
                for c in row["coords"][:10]:
                    extra = f"  孔环={c['ring_mm']}" if c.get("ring_mm") is not None else ""
                    lines.append(f"      X={c['x_mm']:.3f}  Y={c['y_mm']:.3f}  "
                                 f"钻={c['drill_mm']}{extra}  焊盘={c['padstack'] or '-'}")
                if len(row["coords"]) > 10:
                    lines.append(f"      ... 其余 {len(row['coords']) - 10} 个见 Excel")
            elif row["note"]:
                lines.append(f"    {row['note']}")
            lines.append("")
        sizes = hole_size_summary(inventory)
        if sizes:
            lines.append("按孔径汇总:")
            for s in sizes:
                lines.append(f"    Ø{s['drill_mm']}mm  x {s['count']}")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        paths["txt"] = txt_path
    except OSError as exc:
        paths["txt_error"] = str(exc)

    unit_hint = "mm"
    design_unit = str(design_unit or "").strip().lower()
    if design_unit in ("mil", "mils", "inch", "inches", "mm", "millimeter", "millimeters"):
        unit_hint = "mil" if design_unit.startswith("mil") else (
            "inch" if design_unit.startswith("inch") else "mm")
    # A drill file's unit does not imply the BRD user unit. Do not guess.
    scr = (_write_scr(os.path.join(out_dir, f"{base_name}_locate.scr"), located, unit_hint)
           if design_unit else None)
    if scr:
        paths["scr"] = scr
    return paths


def _write_sheet_sizes(wb, inventory: dict) -> bool:
    sizes = hole_size_summary(inventory)
    if not sizes:
        return False
    ws = wb.create_sheet("孔位尺寸汇总")
    headers = ["孔径(mm)", "数量", "X最小", "X最大", "Y最小", "Y最大", "前5个坐标"]
    for c, h in enumerate(headers, 1):
        ws.cell(row=1, column=c, value=h).font = Font(bold=True)
    for r, s in enumerate(sizes, 2):
        ws.cell(row=r, column=1, value=s["drill_mm"])
        ws.cell(row=r, column=2, value=s["count"])
        ws.cell(row=r, column=3, value=s["x_min"])
        ws.cell(row=r, column=4, value=s["x_max"])
        ws.cell(row=r, column=5, value=s["y_min"])
        ws.cell(row=r, column=6, value=s["y_max"])
        ws.cell(row=r, column=7, value="; ".join(f"({x},{y})" for x, y in s["sample"]))
    for i, w in enumerate([12, 8, 10, 10, 10, 10, 52], 1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A2"
    # 全部孔位
    holes = inventory.get("holes") or []
    if holes:
        ws2 = wb.create_sheet("全部孔位")
        for c, h in enumerate(["#", "X(mm)", "Y(mm)", "孔径(mm)", "焊盘(mm)", "孔环(mm)",
                               "焊盘栈", "网络", "来源"], 1):
            ws2.cell(row=1, column=c, value=h).font = Font(bold=True)
        for i, hole in enumerate(holes, 2):
            pad = hole.get("pad_mm")
            ring = round((pad - hole["drill_mm"]) / 2.0, 4) if pad and hole.get("drill_mm") else None
            ws2.cell(row=i, column=1, value=i - 1)
            ws2.cell(row=i, column=2, value=round(hole["x_mm"], 3))
            ws2.cell(row=i, column=3, value=round(hole["y_mm"], 3))
            ws2.cell(row=i, column=4, value=hole.get("drill_mm"))
            ws2.cell(row=i, column=5, value=pad)
            ws2.cell(row=i, column=6, value=ring)
            ws2.cell(row=i, column=7, value=hole.get("padstack", ""))
            ws2.cell(row=i, column=8, value=hole.get("net", ""))
            ws2.cell(row=i, column=9, value=hole.get("source", ""))
        for i, w in enumerate([6, 12, 12, 12, 12, 12, 22, 24, 30], 1):
            ws2.column_dimensions[chr(64 + i)].width = w
    return True


def _src_desc(inventory: dict, kind: str) -> str:
    hits = [s for s in inventory.get("sources") or [] if s["kind"] == kind]
    if not hits:
        return "无 (需 SKILL 导出 / Placement 导出 / Excellon 钻孔文件)"
    return "; ".join(f"{s['file']}({s['count']})" for s in hits)


# --------------------------------------------------------------------------- 一站式
def analyze_and_locate(brd_path: str, files: Optional[Iterable[str]] = None,
                       out_dir: Optional[str] = None, board_type: str = "rigid",
                       level: str = "standard", factory: str = "hongban",
                       base_name: str = "定位清单",
                       result: Optional[dict] = None) -> Dict[str, object]:
    """分析 .brd -> 读坐标来源 -> 定位不符合项 -> 出 xlsx/csv/txt/scr。

    CLI 与 GUI 共用这一个入口, 保证两条路径产出一致。
    """
    from src.brd_design import extract_brd_design, to_design_params
    from src.dfm_engine import analyze_design

    extracted = extract_brd_design(brd_path)
    if result is None:
        design = to_design_params(extracted)
        result = analyze_design(design, board_type=board_type,
                                capability_level=level, factory=factory)
    if not result.get("board_name"):
        result = dict(result)
        result["board_name"] = os.path.splitext(os.path.basename(brd_path))[0]
    out_dir = out_dir or os.path.join(os.path.dirname(os.path.abspath(brd_path)), "定位清单")
    inventory = build_inventory(brd_path, files=files, extra_dirs=[out_dir])
    located = locate_failures(result, inventory)
    size = extracted.get("board_size_mm") or None
    extras = {
        "板名": result.get("board_name") or os.path.basename(brd_path),
        "板尺寸": f"{size[0]} x {size[1]} mm" if size else "未读到",
        "层数": extracted.get("layer_count") or "未读到",
    }
    paths = write_report(out_dir, base_name, result, inventory, located, extras=extras,
                         design_unit=extracted.get("units"))
    return {"result": result, "inventory": inventory, "located": located,
            "paths": paths, "out_dir": out_dir, "extracted": extracted}


# --------------------------------------------------------------------------- 命令行
def find_sources(brd_path: str, out_dir: str = "") -> List[str]:
    """自动找可用的坐标来源 (BRD 同目录 + 输出目录 + allegro_bridge 子目录)。"""
    return [s["path"] for s in discover_sources(brd_path, [out_dir] if out_dir else [])]


def print_summary(bundle: Dict[str, object]) -> None:
    """命令行摘要: 输出路径 + 每条不符合项的命中数量/坐标。"""
    print("=" * 60)
    print("定位清单:", bundle.get("out_dir"))
    for key, path in (bundle.get("paths") or {}).items():
        print(f"  {key}: {path}")
    inv = bundle.get("inventory") or {}
    print(f"元件坐标 {len(inv.get('components') or [])} 条 / 孔位 {len(inv.get('holes') or [])} 条")
    for row in bundle.get("located") or []:
        tag = "FAIL" if row["status"] == "fail" else "WARN"
        head = f"[{tag}] {row['item']}"
        if row["count"] is not None:
            print(f"  {head}  命中 {row['count']} 个孔 ({row['note']})")
            for c in (row["coords"] or [])[:5]:
                print(f"      X={c['x_mm']:.3f} Y={c['y_mm']:.3f} 钻={c['drill_mm']}")
        else:
            print(f"  {head}  {row['note']}")
    if not (inv.get("components") or inv.get("holes")):
        print("提示: 没有坐标来源。用 --skill 生成 SKILL 脚本, 或在 PCB Editor 导出"
              " Placement / NC Drill 文件。")


def main(argv: Optional[List[str]] = None) -> int:
    """独立命令行: python -m src.pick --brd x.brd [--files ...]"""
    import argparse

    ap = argparse.ArgumentParser(description="DFM 不符合项定位清单 (元件/孔位坐标)")
    ap.add_argument("--brd", required=True, help="Allegro .brd 文件")
    ap.add_argument("--files", nargs="*", default=None,
                    help="坐标来源: SKILL 导出的 csv / place_txt.txt / Excellon .drl")
    ap.add_argument("--out", default=None, help="输出目录")
    ap.add_argument("--factory", default="hongban", choices=["hongban", "sihui"])
    ap.add_argument("--level", default="standard",
                    choices=["standard", "automotive", "extreme", "advanced"])
    ap.add_argument("--board-type", default="rigid", choices=["rigid", "flex"])
    ap.add_argument("--skill", action="store_true",
                    help="只在输出目录生成 Allegro SKILL 导出脚本, 不做分析")
    args = ap.parse_args(argv)

    if args.skill:
        from src.allegro_skill import write_bundle

        out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.brd)), "allegro_bridge")
        paths = write_bundle(out)
        print("已生成坐标导出脚本:")
        for k in ("il", "scr", "readme"):
            print(f"  {paths[k]}")
        print("下一步: PCB Editor 里执行  skill load(\"%s\")  然后  evan_pick_all(\"%s\")"
              % (paths["il"].replace("\\", "/"), out.replace("\\", "/")))
        return 0

    files = args.files or find_sources(args.brd, args.out or "")
    bundle = analyze_and_locate(args.brd, files=files, out_dir=args.out,
                                board_type=args.board_type, level=args.level,
                                factory=args.factory)
    print_summary(bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

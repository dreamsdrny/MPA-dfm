"""网络定位: 按网络名给出坐标 / 位置 / 外框尺寸.

为什么需要 Allegro 导出
----------------------
`.brd` 内嵌的设计快照只有图形 (板框 / ETCH / 丝印 / ROUTE KEEPIN), **没有网络归属**
—— 快照里的每一段走线都读不出属于哪个网络 (78 块样本板逐一确认)。所以"按网络名
在 .brd 里定位"必须借助带 `NET` 列的 Allegro 导出文件:

    evan_pads.csv     SKILL 导出: 每个引脚带 NET + 焊盘外框 (W/H)
    evan_holes.csv    SKILL 导出: 过孔 / 通孔引脚带 NET + 坐标
    evan_traces.csv   SKILL 导出 (新版): 每段走线带 NET + 层 + 线宽 + 两端坐标

没有这些文件时, 本模块仍然能列出 `.brd` 里全部网络名 (objects/Net.xml), 但不会
编造坐标; 返回结果里 `has_geometry=False` 并说明缺什么。

坐标口径
--------
* 全部换算成 mm, 保持 Allegro 数据库原点 (不一定是板左下)，不平移/镜像。
* `bbox` 是该网络所有引脚/过孔/走线的包络框; `w_mm/h_mm` 是包络宽高 (长宽),
  `center` 是包络中心; `trace_length_mm` 是走线总长; `width_*_mm` 是线宽范围。
* 引脚按焊盘外框计入包络 (中心 ± 宽/高一半), 过孔按中心点, 走线按两端点。
"""
import csv
import fnmatch
import math
import os
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = ["load_net_inventory", "find_nets", "export_net_csv",
           "parse_traces_csv", "list_geometry_files"]

_UNIT_MM = {
    "millimeters": 1.0, "millimeter": 1.0, "mm": 1.0,
    "microns": 0.001, "micron": 0.001, "um": 0.001,
    "centimeters": 10.0, "cm": 10.0,
    "mils": 0.0254, "mil": 0.0254,
    "inches": 25.4, "inch": 25.4,
}

# 已知的 SKILL 导出文件名 -> 类型
_KNOWN_FILES = {
    "evan_pads.csv": "pads",
    "evan_holes.csv": "holes",
    "evan_traces.csv": "traces",
}
_TRACE_COLUMNS = ("X1_DBU", "Y1_DBU", "X2_DBU", "Y2_DBU")


# --------------------------------------------------------------------------
# 基础换算
# --------------------------------------------------------------------------
def _to_mm(value, unit: str, dbu_per_unit, is_dbu: bool = False):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    factor = _UNIT_MM.get((unit or "").strip().lower())
    if factor is None:
        return None
    if is_dbu:
        if not dbu_per_unit or not math.isfinite(float(dbu_per_unit)) or float(dbu_per_unit) <= 0:
            return None
        return num / float(dbu_per_unit) * factor
    return num * factor


def _round(value, digits=4):
    return None if value is None else round(float(value), digits)


# --------------------------------------------------------------------------
# 带网络坐标的导出文件
# --------------------------------------------------------------------------
def parse_traces_csv(path: str) -> dict:
    """读新版 SKILL 导出的 evan_traces.csv。

    格式:
        UNITS,millimeters,3,1000
        KIND,LAYER,WIDTH_DBU,X1_DBU,Y1_DBU,X2_DBU,Y2_DBU,NET
        TRACE,TOP,500,1000,2000,3000,2000,GND
    """
    unit, dbu = "millimeters", 1000.0
    header: List[str] = []
    items: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.upper().startswith("UNITS,"):
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
                user_units = "X1_UU" in header
                columns = tuple(col.replace("_DBU", "_UU") for col in _TRACE_COLUMNS) if user_units else _TRACE_COLUMNS
                if not all(col in header for col in columns):
                    continue
                vals = next(csv.reader([line]))
                row = dict(zip(header, (v.strip() for v in vals)))
                x1, y1, x2, y2 = [_to_mm(row.get(col), unit, dbu, not user_units) for col in columns]
                if None in (x1, y1, x2, y2):
                    continue
                width = _to_mm(row.get("WIDTH_UU" if user_units else "WIDTH_DBU"), unit, dbu, not user_units)
                net = (row.get("NET") or "").strip()
                if not net:
                    continue
                items.append({
                    "kind": "TRACE",
                    "net": net,
                    "layer": (row.get("LAYER") or "").strip(),
                    "width_mm": _round(width, 4),
                    "x1_mm": _round(x1, 4), "y1_mm": _round(y1, 4),
                    "x2_mm": _round(x2, 4), "y2_mm": _round(y2, 4),
                    "length_mm": _round(math.hypot(x2 - x1, y2 - y1), 4),
                    "x_mm": _round((x1 + x2) / 2.0, 4),
                    "y_mm": _round((y1 + y2) / 2.0, 4),
                })
    except OSError:
        return {"ok": False, "kind": "traces", "items": [],
                "error": f"读不到 {os.path.basename(path)}",
                "source": os.path.basename(path)}

    return {
        "ok": bool(items),
        "kind": "traces",
        "items": items,
        "count": len(items),
        "nets": len({it["net"] for it in items}),
        "source": os.path.basename(path),
        "path": os.path.abspath(path),
        "error": "" if items else "文件里没有带 NET 的走线段",
    }


def _detect_kind(path: str) -> Optional[str]:
    """按文件头判断类型 (kinds: pads / holes / traces); 认不出返回 None。"""
    name = os.path.basename(path).lower()
    if name in _KNOWN_FILES:
        return _KNOWN_FILES[name]
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            for _ in range(8):
                line = f.readline()
                if not line:
                    break
                upper = line.upper()
                if upper.startswith("UNITS,"):
                    continue
                if not line.strip():
                    continue
                cols = [c.strip().upper() for c in line.split(",")]
                if all(col in cols for col in _TRACE_COLUMNS) or all(
                         col.replace("_DBU", "_UU") in cols for col in _TRACE_COLUMNS):
                    return "traces"
                if ("W_DBU" in cols and "H_DBU" in cols) or ("W_UU" in cols and "H_UU" in cols):
                    return "pads"
                if "PADSTACK" in cols:
                    return "holes"
                if "REFDES" in cols and "ROTATION" in cols:
                    return None
    except OSError:
        return None
    return None


def _parse_pads_holes(path: str, kind: str) -> dict:
    from src.pick import parse_skill_csv

    res = parse_skill_csv(path)
    if not res.get("ok"):
        return {"ok": False, "kind": kind, "items": [],
                "error": res.get("error") or f"{os.path.basename(path)} 没有数据行",
                "source": os.path.basename(path)}
    items = []
    for raw in res.get("items") or []:
        net = (raw.get("net") or "").strip()
        if not net:
            continue
        item = {
            "kind": raw.get("kind") or ("PIN" if kind == "pads" else "VIA"),
            "net": net,
            "layer": raw.get("layer") or "",
            "side": raw.get("side") or "",
            "refdes": raw.get("refdes") or "",
            "pin": raw.get("pin") or "",
            "padstack": raw.get("padstack") or "",
            "x_mm": _round(raw.get("x_mm"), 4),
            "y_mm": _round(raw.get("y_mm"), 4),
        }
        if kind == "pads":
            item["w_mm"] = _round(raw.get("w_mm"), 4)
            item["h_mm"] = _round(raw.get("h_mm"), 4)
        items.append(item)
    return {
        "ok": bool(items),
        "kind": kind,
        "items": items,
        "count": len(items),
        "nets": len({it["net"] for it in items}),
        "source": os.path.basename(path),
        "path": os.path.abspath(path),
        "error": "" if items else
                 (f"{os.path.basename(path)} 的 NET 列全为空, 无法归属到网络"
                  if res.get("items") else "文件里没有数据行"),
    }


def list_geometry_files(brd_path: str, files: Iterable[str] = (),
                        dirs: Iterable[str] = ()) -> List[str]:
    """收集候选的带网络坐标文件 (显式给的 + 板目录/输出目录里发现的)。"""
    found: List[str] = []
    for path in files or ():
        if path and os.path.isfile(path) and path not in found:
            found.append(os.path.abspath(path))

    search: List[str] = []
    if brd_path:
        search.append(os.path.dirname(os.path.abspath(brd_path)))
    search.extend(d for d in (dirs or ()) if d)
    extra = []
    for base in search:
        for sub in ("", "定位清单", "allegro_bridge",
                    os.path.join("定位清单", "allegro_bridge"), "output", "exports"):
            folder = os.path.join(base, sub) if sub else base
            if os.path.isdir(folder) and folder not in extra:
                extra.append(folder)
    for folder in extra:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            if name.lower() not in _KNOWN_FILES:
                continue
            path = os.path.abspath(os.path.join(folder, name))
            if os.path.isfile(path) and path not in found:
                found.append(path)
    return found


def _load_geometry(paths: Sequence[str]) -> Tuple[List[dict], List[dict], List[str]]:
    items: List[dict] = []
    sources: List[dict] = []
    errors: List[str] = []
    for path in paths:
        kind = _detect_kind(path)
        if kind is None:
            errors.append(f"{os.path.basename(path)}: 认不出是焊盘/过孔/走线导出, 已跳过")
            continue
        if kind == "traces":
            res = parse_traces_csv(path)
        else:
            res = _parse_pads_holes(path, kind)
        if not res.get("ok"):
            errors.append(f"{res.get('source', path)}: {res.get('error') or '没有可用数据'}")
            sources.append({"file": res.get("source", os.path.basename(path)),
                            "kind": kind, "count": res.get("count", 0),
                            "nets": res.get("nets", 0), "error": res.get("error", "")})
            continue
        items.extend(res.get("items") or [])
        sources.append({"file": res.get("source", os.path.basename(path)),
                        "kind": kind, "count": res.get("count", 0),
                        "nets": res.get("nets", 0), "error": ""})
    # 同一个引脚会同时出现在 evan_holes.csv (SKILL 把所有引脚当孔导出) 和
    # evan_pads.csv (带焊盘外框) 里: 保留带 W/H 的那条, 否则引脚数会翻倍。
    # 过孔按 网络 + 坐标 去重 (重复文件 / 多目录副本)。
    best: Dict[tuple, dict] = {}
    order: List[tuple] = []
    for item in items:
        if item.get("kind") == "PIN":
            # 用位号 + 坐标做键: evan_holes.csv 的 PIN 行没有引脚号, 但坐标一致;
            # 这样同一引脚在 pads/holes 两份文件里只算一次 (保留带 W/H 的)。
            key = ("PIN", item.get("net"), item.get("refdes", ""),
                   round(item.get("x_mm") or 0.0, 3),
                   round(item.get("y_mm") or 0.0, 3))
        elif item.get("kind") == "TRACE":
            key = ("TRACE", item.get("net"), item.get("layer", ""),
                   item.get("x1_mm"), item.get("y1_mm"),
                   item.get("x2_mm"), item.get("y2_mm"))
        else:
            key = ("VIA", item.get("net"),
                   round(item.get("x_mm") or 0.0, 3),
                   round(item.get("y_mm") or 0.0, 3))
        prev = best.get(key)
        if prev is None:
            best[key] = item
            order.append(key)
        elif item.get("w_mm") is not None and prev.get("w_mm") is None:
            best[key] = item
    items = [best[k] for k in order]
    return items, sources, errors


# --------------------------------------------------------------------------
# .brd 网络名 / 网表兜底
# --------------------------------------------------------------------------
def _archive_net_names(brd_path: str) -> Tuple[List[str], List[str]]:
    """从 .brd 归档 objects/Net.xml 读网络名 + DiffPair.xml 差分对名。"""
    if not brd_path or not os.path.isfile(brd_path):
        return [], []
    try:
        from src.brd_design import read_brd_objects
        objects, _info = read_brd_objects(brd_path)
    except Exception:  # noqa: BLE001  16.x 纯二进制 / 归档损坏
        return [], []
    from xml.etree import ElementTree as ET

    def names_of(entry: str) -> List[str]:
        data = objects.get(entry)
        if not data:
            return []
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            return []
        return [n for n in ((node.get("N") or "").strip() for node in root.findall("o")) if n]

    return sorted(set(names_of("objects/Net.xml"))), sorted(set(names_of("objects/DiffPair.xml")))


def _netlist_names(path: str) -> List[str]:
    try:
        from src.brd_analysis.imports import parse_netlist

        res = parse_netlist(path)
    except Exception:  # noqa: BLE001
        return []
    if not res.get("ok"):
        return []
    return sorted((res.get("nets") or {}).keys())


def _netlist_candidates(paths: Iterable[str]) -> List[str]:
    out = []
    for path in paths:
        low = os.path.basename(path).lower()
        if "pstxnet" in low or "netlist" in low or low.endswith(".net"):
            out.append(path)
    return out


# --------------------------------------------------------------------------
# 聚合
# --------------------------------------------------------------------------
def _pair_names(names: Sequence[str]) -> Dict[str, str]:
    """按 _P/_N 后缀推断差分对 (仅用于展示; DiffPair.xml 优先)。"""
    by_base: Dict[str, Dict[str, str]] = {}
    for name in names:
        upper = name.upper()
        if upper.endswith("_P"):
            by_base.setdefault(name[:-2], {})["p"] = name
        elif upper.endswith("_N"):
            by_base.setdefault(name[:-2], {})["n"] = name
    pairs: Dict[str, str] = {}
    for entry in by_base.values():
        if "p" in entry and "n" in entry:
            pairs[entry["p"]] = entry["n"]
            pairs[entry["n"]] = entry["p"]
    return pairs


def _signal_info(name: str) -> dict:
    try:
        from src.signal_classifier import classify_signal

        sig = classify_signal(name)
        return {"class": sig.name or "DEFAULT", "impedance_ohm": sig.impedance_target,
                "is_diff_pair": bool(sig.is_diff_pair)}
    except Exception:  # noqa: BLE001
        return {"class": "DEFAULT", "impedance_ohm": None, "is_diff_pair": False}


def _aggregate(names: Sequence[str], items: Sequence[dict],
               diff_pairs: Sequence[str]) -> List[dict]:
    by_net: Dict[str, dict] = {}

    def bucket(name: str) -> dict:
        return by_net.setdefault(name, {
            "name": name, "items": [], "pins": 0, "vias": 0, "traces": 0,
            "layers": set(), "sides": set(), "widths": [],
            "trace_length_mm": 0.0,
            "x_min": None, "y_min": None, "x_max": None, "y_max": None,
        })

    for name in names:
        bucket(name)

    for item in items:
        net = (item.get("net") or "").strip()
        if not net:
            continue
        rec = bucket(net)
        rec["items"].append(item)
        kind = item.get("kind")
        xs, ys = [], []
        if kind == "TRACE":
            rec["traces"] += 1
            xs = [item.get("x1_mm"), item.get("x2_mm")]
            ys = [item.get("y1_mm"), item.get("y2_mm")]
            rec["trace_length_mm"] += float(item.get("length_mm") or 0.0)
            if item.get("width_mm") is not None:
                rec["widths"].append(float(item["width_mm"]))
            if item.get("layer"):
                rec["layers"].add(item["layer"])
        elif kind == "PIN":
            rec["pins"] += 1
            x, y = item.get("x_mm"), item.get("y_mm")
            w, h = item.get("w_mm"), item.get("h_mm")
            if x is not None and y is not None:
                xs = [x - (w or 0) / 2.0, x + (w or 0) / 2.0]
                ys = [y - (h or 0) / 2.0, y + (h or 0) / 2.0]
            if item.get("side"):
                rec["sides"].add(item["side"])
        else:  # VIA 或没有外框的引脚
            rec["vias"] += 1
            xs = [item.get("x_mm")]
            ys = [item.get("y_mm")]
            if item.get("layer"):
                rec["layers"].add(item["layer"])

        xs = [v for v in xs if v is not None]
        ys = [v for v in ys if v is not None]
        if xs and ys:
            rec["x_min"] = xs[0] if rec["x_min"] is None else min(rec["x_min"], *xs)
            rec["x_max"] = xs[0] if rec["x_max"] is None else max(rec["x_max"], *xs)
            rec["y_min"] = ys[0] if rec["y_min"] is None else min(rec["y_min"], *ys)
            rec["y_max"] = ys[0] if rec["y_max"] is None else max(rec["y_max"], *ys)

    pair_map = _pair_names(sorted(by_net))
    for pair in diff_pairs:
        p, n = _split_pair_name(pair)
        if p in by_net and n in by_net:
            pair_map[p] = n
            pair_map[n] = p

    nets: List[dict] = []
    for name, rec in by_net.items():
        sig = _signal_info(name)
        bbox = None
        center = None
        if None not in (rec["x_min"], rec["y_min"], rec["x_max"], rec["y_max"]):
            bbox = {
                "x_min": _round(rec["x_min"]), "y_min": _round(rec["y_min"]),
                "x_max": _round(rec["x_max"]), "y_max": _round(rec["y_max"]),
                "w_mm": _round(rec["x_max"] - rec["x_min"]),
                "h_mm": _round(rec["y_max"] - rec["y_min"]),
            }
            center = {"x": _round((rec["x_min"] + rec["x_max"]) / 2.0),
                      "y": _round((rec["y_min"] + rec["y_max"]) / 2.0)}
        widths = rec["widths"]
        nets.append({
            "name": name,
            "class": sig["class"],
            "impedance_ohm": sig["impedance_ohm"],
            "is_diff_pair": sig["is_diff_pair"],
            "pair": pair_map.get(name, ""),
            "pins": rec["pins"],
            "vias": rec["vias"],
            "traces": rec["traces"],
            "layers": sorted(rec["layers"]) or sorted(rec["sides"]),
            "sides": sorted(rec["sides"]),
            "trace_length_mm": _round(rec["trace_length_mm"]),
            "width_min_mm": _round(min(widths)) if widths else None,
            "width_max_mm": _round(max(widths)) if widths else None,
            "bbox": bbox,
            "center": center,
            "has_geometry": bool(rec["items"]),
            "items": rec["items"],
        })

    nets.sort(key=lambda n: (not n["has_geometry"], n["name"].upper()))
    return nets


def _split_pair_name(name: str) -> Tuple[str, str]:
    """DiffPair.xml 的节点名可能直接是 "ETH_TX" (差分对名), 也可能写成
    "ETH_TX_P / ETH_TX_N"。两种都尽量拆成 (P 名, N 名); 拆不出返回 ("","")。"""
    text = (name or "").strip()
    if "/" in text:
        left, right = [p.strip() for p in text.split("/", 1)]
        if left.upper().endswith(("_P", "_N")) or right.upper().endswith(("_P", "_N")):
            return left, right
        return f"{left}_P", f"{right}_N"
    return f"{text}_P", f"{text}_N"


# --------------------------------------------------------------------------
# 板框 (画布上下文, 可选)
# --------------------------------------------------------------------------
def _board_info(brd_path: str) -> dict:
    try:
        from src.brd_analysis import facts
        from src.brd_analysis.context import Context

        ctx = Context(brd_path)
        lines = facts.outline_polylines(ctx)
        outline = ctx.outline or {}
        return {
            "ok": bool(lines),
            "name": ctx.board_name,
            "width_mm": outline.get("width_mm"),
            "height_mm": outline.get("height_mm"),
            "outline": [[[round(x, 4), round(y, 4)] for x, y in line] for line in lines],
            "source": "内嵌设计快照 BOARD GEOMETRY/OUTLINE (只作画布参照, 不含网络)",
        }
    except Exception as exc:  # noqa: BLE001  没有快照/标定失败都不影响网络清单
        return {"ok": False, "reason": f"板框不可用: {exc}", "outline": []}


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def load_net_inventory(brd_path: str, files: Iterable[str] = (),
                       dirs: Iterable[str] = (), with_outline: bool = True) -> dict:
    """汇总网络名与带网络归属的坐标。

    返回:
        {"ok", "brd_path", "nets": [...], "sources": [...], "warnings": [...],
         "board": {...}, "has_geometry", "name_source"}
    """
    names, diff_pairs = _archive_net_names(brd_path)
    warnings: List[str] = []
    name_source = "objects/Net.xml"

    candidate_files = list_geometry_files(brd_path, files, dirs)
    items, sources, errors = _load_geometry(candidate_files)
    if not names:
        netlists = _netlist_candidates(list(files or ()) + candidate_files)
        if not netlists and brd_path:
            base = os.path.dirname(os.path.abspath(brd_path))
            try:
                for name in os.listdir(base):
                    if "pstxnet" in name.lower() or name.lower().endswith(".net"):
                        netlists.append(os.path.join(base, name))
            except OSError:
                pass
        if netlists:
            names = _netlist_names(netlists[0])
            name_source = f"网表 {os.path.basename(netlists[0])} (归档里没有 Net.xml)"
            warnings.append(
                "该 .brd 归档里没有 objects/Net.xml (可能是 16.x 纯二进制), 网络名来自导入的网表; "
                "网表只有连接关系, 没有坐标。")
        elif items:
            # 归档里没有 Net.xml (例如只存约束/物理信息的库板), 但 SKILL 导出的
            # NET 列本身就能给出网络名 + 坐标 —— 这是最后的、也是最可靠的兜底。
            names = sorted({it["net"] for it in items if it.get("net")})
            name_source = "坐标文件的 NET 列 (归档里没有 Net.xml)"
            warnings.append(
                "该 .brd 归档里没有 objects/Net.xml, 网络名取自导入的坐标文件; "
                "没出现在坐标文件里的网络不会列出来。")
        else:
            name_source = "(未读到)"
            warnings.append(
                "读不到网络名: .brd 归档里没有 objects/Net.xml, 也没有带 NET 的坐标文件。"
                "17.x 请确认归档完整; 16.x 请提供 pstxnet.dat 网表, "
                "或先用「生成 SKILL 脚本」导出 evan_pads.csv / evan_holes.csv / evan_traces.csv。")

    if not names:
        return {"ok": False, "brd_path": os.path.abspath(brd_path) if brd_path else "",
                "nets": [], "sources": sources, "warnings": warnings,
                "board": {"ok": False}, "has_geometry": False,
                "name_source": name_source, "net_count": 0, "geometry_net_count": 0,
                "error": "没有可用的网络名"}

    warnings.extend(errors)
    if not items:
        warnings.append(
            "没有带 NET 列的坐标导出文件, 只能列出网络名, 定位不了坐标/长宽。"
            "请用「生成 SKILL 脚本」在 Allegro 里导出 evan_pads.csv / evan_holes.csv / "
            "evan_traces.csv (NET 列不能为空)。")

    on_board = _board_info(brd_path) if with_outline else {"ok": False, "outline": []}
    nets = _aggregate(names, items, diff_pairs)

    known = {n["name"] for n in nets}
    extra = sorted({it["net"] for it in items if it["net"] not in known})
    if extra:
        warnings.append("坐标文件里有 %d 个网络不在 .brd 网络清单里: %s"
                        % (len(extra), "、".join(extra[:8]) + (" 等" if len(extra) > 8 else "")))

    return {
        "ok": True,
        "brd_path": os.path.abspath(brd_path) if brd_path else "",
        "nets": nets,
        "sources": sources,
        "warnings": warnings,
        "board": on_board,
        "has_geometry": bool(items),
        "name_source": name_source,
        "net_count": len(nets),
        "geometry_net_count": sum(1 for n in nets if n["has_geometry"]),
    }


def find_nets(inventory: dict, query: str = "", only_geometry: bool = False,
              diff_only: bool = False) -> List[dict]:
    """按名称筛选 (大小写不敏感, 支持 * ? 通配; 不含通配符时按子串匹配)。"""
    nets = (inventory or {}).get("nets") or []
    text = (query or "").strip()
    pattern = text if any(ch in text for ch in "*?") else f"*{text}*"
    out = []
    for net in nets:
        if only_geometry and not net.get("has_geometry"):
            continue
        if diff_only and not net.get("pair"):
            continue
        if text and not fnmatch.fnmatch(net["name"].upper(), pattern.upper()):
            continue
        out.append(net)
    return out


def export_net_csv(net: dict, path: str) -> str:
    """把选中网络导出成 CSV (概要 + 明细), 返回路径。"""
    if not net:
        raise ValueError("没有选中的网络")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        bbox = net.get("bbox") or {}
        center = net.get("center") or {}
        writer.writerow(["网络", net.get("name", "")])
        writer.writerow(["分类", net.get("class", "")])
        writer.writerow(["阻抗目标 (ohm)", net.get("impedance_ohm") or ""])
        writer.writerow(["差分对", net.get("pair", "")])
        writer.writerow(["外框 X 范围 (mm)", bbox.get("x_min"), bbox.get("x_max")])
        writer.writerow(["外框 Y 范围 (mm)", bbox.get("y_min"), bbox.get("y_max")])
        writer.writerow(["外框 宽 x 高 (mm)", bbox.get("w_mm"), bbox.get("h_mm")])
        writer.writerow(["中心 (mm)", center.get("x"), center.get("y")])
        writer.writerow(["走线总长 (mm)", net.get("trace_length_mm")])
        writer.writerow(["线宽范围 (mm)", net.get("width_min_mm"), net.get("width_max_mm")])
        writer.writerow(["层", " / ".join(net.get("layers") or [])])
        writer.writerow([])
        writer.writerow(["类型", "层/面", "X(mm)", "Y(mm)", "宽(mm)", "高(mm)",
                         "长度(mm)", "位号", "引脚", "焊盘栈", "网络"])
        for item in net.get("items") or []:
            kind = item.get("kind", "")
            if kind == "TRACE":
                writer.writerow(["TRACE", item.get("layer", ""),
                                 item.get("x1_mm"), item.get("y1_mm"),
                                 item.get("width_mm"), "", item.get("length_mm"),
                                 "", "", "", net.get("name", "")])
            else:
                writer.writerow([kind, item.get("layer") or item.get("side", ""),
                                 item.get("x_mm"), item.get("y_mm"),
                                 item.get("w_mm", ""), item.get("h_mm", ""), "",
                                 item.get("refdes", ""), item.get("pin", ""),
                                 item.get("padstack", ""), net.get("name", "")])
    return path

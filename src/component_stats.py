# -*- coding: utf-8 -*-
"""器件分类统计: 从 .brd 位号 + 导出文件，按位号前缀统计器件数量与明细。

数据来源（按可信度/丰富度）
-------------------------
1. `.brd` 归档 `objects/Part.xml` 的位号节点 —— 这是"设计里有几颗器件"的
   权威清单；封装名再从 `objects/PartDefn.xml` / `objects/Gate.xml` 防御式
   探测（只有明确带封装字段的节点才用）。
2. `evan_components.csv`（SKILL 导出）—— 位号 + X/Y + 旋转 + 面别；
3. `evan_pads.csv`（SKILL 导出）—— 位号 + 引脚号 + 焊盘栈，可算引脚数；
   没有元件坐标时用焊盘中心推算，并明确标注"推算"；
4. Allegro Placement 导出 `place_txt.txt` —— 位号 + 封装 + X/Y + 旋转 + 面。

`Part.xml` 只有位号，**没有值/料号**；FAB 拼板图通常连 Part.xml 都没有。
没有数据时本模块如实返回 `ok=False`，不拿丝印文字猜器件。

分类口径
--------
按位号前缀（R/C/L/D/Q/U/IC/LED/J/SW/F/X/Y/T/B/BT…）归类，表在
`src/brd_components.py::_PREFIX_CATEGORY`。未知前缀归到 `未分类(前缀)`，
不静默并入"其它器件"。
"""
import os
from collections import Counter, OrderedDict
from typing import Dict, Iterable, List, Optional, Tuple

from src import brd_components, pick
from src.brd_design import read_brd_objects

try:  # 封装映射复用分析模块的防御式实现；打包时静态可见，缺失也不影响位号统计
    from src.brd_analysis.cat_database import _package_map as _impl_package_map
except Exception:  # noqa: BLE001
    _impl_package_map = None

__all__ = ["load_component_inventory", "find_components", "component_files",
           "export_component_csv", "export_component_excel"]

_CATEGORY = brd_components._PREFIX_CATEGORY
_prefix_of = brd_components._prefix_of
_natural_key = brd_components._natural_key


# --------------------------------------------------------------------------
# 来源发现
# --------------------------------------------------------------------------
def _search_dirs(brd_path: str, dirs: Iterable[str]) -> List[str]:
    # 初始化用于存放所有候选目录的列表
    out: List[str] = []
    
    # 如果传入了有效的 brd 文件路径
    if brd_path:
        # 获取 brd 文件所在目录的绝对路径作为基准目录
        base = os.path.dirname(os.path.abspath(brd_path))
        # 将 brd 所在目录及其常见子目录加入候选列表
        out.extend([base, os.path.join(base, "定位清单"),
                    os.path.join(base, "定位清单", "allegro_bridge"),
                    os.path.join(base, "allegro_bridge"),
                    os.path.join(base, "统计清单"), os.path.join(base, "output"),
                    os.path.join(base, "exports")])
    
    # 将用户传入的目录列表追加到候选列表中（如果 dirs 为 None 则追加空列表）
    out.extend(dirs or [])
    
    # 初始化集合用于记录已添加的目录（用于去重），列表用于保持添加顺序
    seen, uniq = set(), []
    
    # 遍历所有候选目录
    for d in out:
        # 将目录路径标准化（统一大小写）并转为绝对路径，作为去重键
        key = os.path.normcase(os.path.abspath(d))
        # 如果目录真实存在且未被添加过
        if os.path.isdir(d) and key not in seen:
            # 记录该目录键，避免重复
            seen.add(key)
            # 将原始目录路径加入结果列表
            uniq.append(d)
    
    # 返回去重后且真实存在的目录列表
    return uniq


def component_files(brd_path: str = "", files: Iterable[str] = (),
                    dirs: Iterable[str] = ()) -> List[Tuple[str, str]]:
    """找出可用的器件来源，返回 [(路径, 类型)]；类型: partxml 不在这里。"""
    # 初始化结果列表，存放 (文件绝对路径, 文件类型) 元组
    found: List[Tuple[str, str]] = []
    # 初始化集合，用于记录已处理的文件，防止重复添加
    seen = set()

    def add(path: str):
        """内部函数：将给定的文件路径加入结果列表（如果有效且未重复）"""
        # 路径为空或文件不存在则跳过
        if not path or not os.path.isfile(path):
            return
        # 将路径标准化（统一大小写）并转为绝对路径，作为去重键
        key = os.path.normcase(os.path.abspath(path))
        # 如果已经处理过该文件则跳过
        if key in seen:
            return
        # 记录该文件，避免重复
        seen.add(key)
        # 嗅探文件类型
        kind = _sniff_kind(path)
        # 如果识别出有效类型，则将 (绝对路径, 类型) 加入结果列表
        if kind:
            found.append((os.path.abspath(path), kind))

    # 遍历用户直接传入的文件路径列表，逐个添加
    for path in files or ():
        add(path)

    # 遍历通过 _search_dirs 生成的候选搜索目录
    for folder in _search_dirs(brd_path, dirs):
        # 检查目录中是否存在指定的固定名称文件，并添加
        for name in ("evan_components.csv", "evan_pads.csv",
                     "place_txt.txt", "placement.txt"):
            add(os.path.join(folder, name))
        # 尝试列出目录中的所有文件名
        try:
            names = os.listdir(folder)
        except OSError:
            # 如果无法列出目录则跳过
            continue
        # 遍历目录中的每个文件名
        for name in names:
            low = name.lower()
            # 筛选扩展名为 .csv 或 .txt，且文件名包含 place/component/pick 的文件
            if low.endswith((".csv", ".txt")) and (
                    "place" in low or "component" in low or "pick" in low or "comp_xy" in low):
                add(os.path.join(folder, name))

    # 返回最终找到的 (文件路径, 文件类型) 列表
    return found


def _sniff_kind(path: str) -> str:
    if not os.path.exists(path):
        return ""
        
    low_path = path.lower()
    if not (low_path.endswith(".csv") or low_path.endswith(".txt")):
        return ""

    # 1. 尝试多种常见编码（兼容 GBK/GB18030 编码的中文 EDA 报表）
    head_text = ""
    for enc in ("utf-8-sig", "gbk", "gb18030", "latin1"):
        try:
            with open(path, "r", encoding=enc, errors="ignore") as f:
                # 扩大读取范围至前 8 行，防止表头位置偏下
                head_lines = [next(f, "") for _ in range(8)]
            head_text = "".join(head_lines).upper()
            if head_text.strip():
                break
        except (OSError, StopIteration):
            continue

    # 2. 基于文件内容的关键词加权评分（内容优先，更具鲁棒性）
    if "UUNITS" in head_text and "SYMBOL_X" in head_text:
        return "placement"
    # Placement's textual header also says REFDES/PACKAGE. Only CSV coordinate
    # columns identify the SKILL format; keyword scores misroute Placement.
    if "X_DBU" in head_text or "X_UU" in head_text:
        if "W_DBU" in head_text or "W_UU" in head_text:
            return "pads"
        return "holes" if "PADSTACK" in head_text else "components"
    if "PLACE" in os.path.basename(path).upper() or any(
            pick._PLACE_RE.match(line.strip()) for line in head_text.splitlines()):
        return "placement"
    pads_score = 0
    comp_score = 0
    place_score = 0

    # Pads 特征关键词及权重
    if "PADSTACK" in head_text: pads_score += 3
    if "W_DBU" in head_text: pads_score += 2
    if "PAD" in head_text: pads_score += 1

    # Components / Placement 特征关键词及权重
    if "REFDES" in head_text or "位号" in head_text: comp_score += 3
    if "DEVICE" in head_text or "PACKAGE" in head_text or "元件" in head_text: comp_score += 2
    if "PLACE" in head_text or ("X" in head_text and "Y" in head_text): place_score += 1

    # 内容判定阈值
    if pads_score > comp_score and pads_score >= 2:
        return "pads"
    if comp_score >= 2:
        return "components"
    if place_score >= 2:
        return "placement"

    # 3. 若内容特征不明显，再回退到文件名匹配作为辅助兜底
    base_name = os.path.basename(path).lower()
    if "pad" in base_name and "comp" not in base_name:
        return "pads"
    if "component" in base_name or "comp" in base_name:
        return "components"
    if "place" in base_name:
        return "placement"

    return ""


def _read_source(path: str, kind: str) -> dict:
    if kind == "placement":
        return pick.parse_placement_txt(path)
    return pick.parse_skill_csv(path)


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------
def _norm_refdes(text) -> str:
    return str(text or "").strip()


def _side(value) -> str:
    up = str(value or "").strip().upper()
    if up in ("T", "TOP", "TOP SIDE"):
        return "TOP"
    if up in ("B", "BOTTOM", "BOT SIDE", "BOT", "BTM"):
        return "BOTTOM"
    return "未知" if up else ""


def _package_map(objects: Dict[str, bytes], refdes: List[str]) -> Dict[str, str]:
    """PartDefn/Gate 的"位号 -> 封装"防御式映射；失败就空表，不猜。"""
    if not objects or not refdes:
        return {}
    try:
        if _impl_package_map is None:
            return {}
        got = _impl_package_map(objects, refdes)
        return {str(k).upper(): str((v or {}).get("封装") or "")
                for k, v in (got.get("by_refdes") or {}).items()}
    except Exception:  # noqa: BLE001
        return {}


def load_component_inventory(brd_path: str = "", files: Iterable[str] = (),
                             dirs: Iterable[str] = ()) -> dict:
    """读器件清单并分类统计。

    返回:
        ok / components / stats / sources / warnings / has_partxml / brd_path
    每个 component:
        refdes, prefix, category, package, side, x_mm, y_mm, rotation,
        pin_count, pins, padstacks, sources, has_coords, estimated_coord
    """
    warnings: List[str] = []
    objects: Dict[str, bytes] = {}
    if brd_path:
        if not os.path.isfile(brd_path):
            return _failed(f"文件不存在: {brd_path}")
        try:
            objects, _ = read_brd_objects(brd_path)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"读 .brd 归档失败: {exc}")

    part = brd_components.extract_components(objects)
    has_partxml = bool(part.get("ok"))
    if not has_partxml and part.get("reason"):
        warnings.append(part["reason"])

    src_files = component_files(brd_path, files, dirs)
    if not has_partxml and not src_files:
        return _failed(
            "没有器件数据: .brd 归档里没有 objects/Part.xml，也没有导入 "
            "evan_components.csv / evan_pads.csv / place_txt.txt。"
            "FAB 拼板图常见，仅凭图形无法知道有几颗器件。", warnings, brd_path)

    records: "OrderedDict[str, dict]" = OrderedDict()

    def rec_for(ref: str, source: str = "") -> dict:
        key = ref.upper()
        rec = records.get(key)
        if rec is None:
            rec = {"refdes": ref, "in_partxml": False,
                   "x_mm": None, "y_mm": None, "coord_rank": 0,
                   "coord_source": "", "side": "", "rotation": "",
                   "pins": set(), "padstacks": set(), "sources": set(),
                   "pad_xs": [], "pad_ys": [], "coord_conflict": False,
                   "placement_pkg": ""}
            records[key] = rec
        if source:
            rec["sources"].add(source)
        return rec

    if has_partxml:
        for ref in part.get("refdes") or []:
            rec = rec_for(ref)
            rec["in_partxml"] = True

    sources: List[dict] = []
    if has_partxml:
        sources.append({"file": "objects/Part.xml", "kind": "partxml",
                        "count": part.get("count", 0)})

    for path, kind in src_files:
        res = _read_source(path, kind)
        name = os.path.basename(path)
        if not res.get("ok"):
            warnings.append(f"{name}: {res.get('error') or '没有解析到器件行'}")
            continue
        items = res.get("items") or []
        if kind == "holes":
            warnings.append(f"{name}: 孔/引脚坐标不能代替器件原点，请使用元件或 Placement 导出")
            continue
        if kind == "pads":
            label = "焊盘导出"
        elif kind == "placement":
            label = "Placement"
        else:
            label = "元件坐标导出"
        sources.append({"file": name, "kind": kind, "count": len(items)})

        for item in items:
            ref = _norm_refdes(item.get("refdes"))
            if not ref:
                continue
            rec = rec_for(ref, label)
            x, y = item.get("x_mm"), item.get("y_mm")
            if kind == "pads":
                pin = str(item.get("pin") or "").strip()
                rec["pins"].add(pin or f"{x},{y}")
                pad = str(item.get("padstack") or "").strip()
                if pad:
                    rec["padstacks"].add(pad)
                if x is not None and y is not None:
                    rec["pad_xs"].append(float(x))
                    rec["pad_ys"].append(float(y))
                side = _side(item.get("side"))
                if side and not rec["side"]:
                    rec["side"] = side
                continue

            rank = 3 if kind == "components" else 2
            side = _side(item.get("side"))
            if side and not rec["side"]:
                rec["side"] = side
            rotation = str(item.get("rotation") or "").strip()
            if rotation and not rec["rotation"]:
                rec["rotation"] = rotation
            footprint = str(item.get("footprint") or "").strip()
            if footprint and not rec["placement_pkg"]:
                rec["placement_pkg"] = footprint
            if x is None or y is None:
                continue
            x, y = float(x), float(y)
            if rec["x_mm"] is None or rank > rec["coord_rank"]:
                rec["x_mm"], rec["y_mm"] = x, y
                rec["coord_rank"] = rank
                rec["coord_source"] = ("元件坐标导出" if kind == "components"
                                       else "Placement 导出")
            elif rank == rec["coord_rank"] and (
                    abs(rec["x_mm"] - x) > 0.01 or abs(rec["y_mm"] - y) > 0.01):
                rec["coord_conflict"] = True

    components: List[dict] = []
    package_map = _package_map(objects, [r["refdes"] for r in records.values()])
    for key, rec in records.items():
        ref = rec["refdes"]
        x, y = rec["x_mm"], rec["y_mm"]
        estimated = False
        if x is None and rec["pad_xs"]:
            x = sum(rec["pad_xs"]) / len(rec["pad_xs"])
            y = sum(rec["pad_ys"]) / len(rec["pad_ys"])
            rec["coord_source"] = "焊盘中心推算"
            estimated = True
        prefix = _prefix_of(ref)
        category = _CATEGORY.get(prefix) or (
            f"未分类({prefix})" if prefix else "未分类(无前缀)")
        pins = sorted((p for p in rec["pins"] if p), key=_natural_key)
        pkg = package_map.get(key, "") or rec["placement_pkg"]
        pkg_source = ("objects/PartDefn.xml" if package_map.get(key)
                      else ("Placement 封装" if rec["placement_pkg"] else ""))
        components.append({
            "refdes": ref,
            "prefix": prefix or "(无前缀)",
            "category": category,
            "package": pkg,
            "package_source": pkg_source,
            "side": rec["side"] or "未知",
            "x_mm": round(x, 4) if x is not None else None,
            "y_mm": round(y, 4) if y is not None else None,
            "rotation": rec["rotation"],
            "pin_count": len(pins) if pins else None,
            "pins": pins,
            "padstacks": sorted(rec["padstacks"]),
            "sources": sorted(rec["sources"]),
            "has_coords": x is not None,
            "estimated_coord": estimated,
            "coord_source": rec["coord_source"],
            "has_coord_conflict": rec["coord_conflict"],
            "in_partxml": rec["in_partxml"],
        })

    components.sort(key=lambda c: _natural_key(c["refdes"]))
    stats = _build_stats(components, sources)

    if not has_partxml:
        warnings.append(
            "归档里没有 objects/Part.xml；器件清单来自导出文件，只能统计导出到的位号。"
            "FAB 拼板图常见，若要全量清单请提供真实设计的 .brd 或用 SKILL 导出元件坐标。")
    if has_partxml and not any(v > 0 for v in stats["sides"].values()):
        warnings.append("Part.xml 只有位号，没有面别/坐标；导入 evan_components.csv "
                        "或 Placement 可补 X/Y/面别/旋转，导入 evan_pads.csv 可补引脚数。")
    if has_partxml and stats["export_only"]:
        warnings.append(
            f"{len(stats['export_only'])} 个位号只在导出文件里、不在 Part.xml 里 "
            f"({_sample(stats['export_only'])}); 已合并统计，请确认是测试点/拼板标记还是导出差异。")
    if stats["part_only"]:
        warnings.append(
            f"{len(stats['part_only'])} 个 Part.xml 位号在导出文件里找不到 "
            f"({_sample(stats['part_only'])}); 可能是未贴装/未导出，坐标与引脚列为空。")
    if stats["coord_conflicts"]:
        warnings.append(f"{stats['coord_conflicts']} 个位号在多个来源里的坐标不一致，"
                        "已保留优先来源（元件坐标导出 > Placement），请看明细的「来源」列。")

    return {"ok": True, "brd_path": os.path.abspath(brd_path) if brd_path else "",
            "components": components, "stats": stats, "sources": sources,
            "warnings": warnings, "has_partxml": has_partxml}


def _failed(reason: str, warnings: Optional[List[str]] = None,
            brd_path: str = "") -> dict:
    return {"ok": False, "brd_path": os.path.abspath(brd_path) if brd_path else "",
            "components": [], "stats": _build_stats([], []),
            "sources": [], "warnings": list(warnings or []) + [reason],
            "has_partxml": False, "error": reason}


def _sample(items: List[str], limit: int = 8) -> str:
    shown = "、".join(items[:limit])
    return shown + (f" …共 {len(items)} 个" if len(items) > limit else "")


def _build_stats(components: List[dict], sources: List[dict]) -> dict:
    total = len(components)
    cat_counter = Counter(c["category"] for c in components)
    prefix_counter = Counter(c["prefix"] for c in components)
    categories: List[dict] = []
    for name, count in sorted(cat_counter.items(), key=lambda kv: (-kv[1], kv[0])):
        members = [c for c in components if c["category"] == name]
        prefixes = sorted({c["prefix"] for c in members},
                          key=lambda p: (-prefix_counter.get(p, 0), p))
        categories.append({
            "name": name,
            "count": count,
            "share": round(count * 100.0 / total, 1) if total else 0.0,
            "prefixes": prefixes,
            "examples": [c["refdes"] for c in members[:8]],
        })
    sides = Counter(c["side"] for c in components)
    pins_known = [c for c in components if c["pin_count"] is not None]
    unclassified = sorted({c["prefix"] for c in components
                           if c["category"].startswith("未分类")},
                          key=_natural_key)
    export_only = [c["refdes"] for c in components if not c["in_partxml"]]
    part_only = [c["refdes"] for c in components
                 if c["in_partxml"] and not c["sources"]]
    return {
        "total": total,
        "category_count": len(categories),
        "by_category": dict(cat_counter),
        "by_prefix": dict(prefix_counter),
        "categories": categories,
        "sides": {k: sides.get(k, 0) for k in ("TOP", "BOTTOM", "未知")},
        "with_coords": sum(1 for c in components if c["has_coords"]),
        "estimated_coords": sum(1 for c in components if c["estimated_coord"]),
        "with_package": sum(1 for c in components if c["package"]),
        "pins_known": len(pins_known),
        "total_pins": sum(c["pin_count"] for c in pins_known),
        "unclassified": unclassified,
        "coord_conflicts": sum(1 for c in components if c["has_coord_conflict"]),
        "export_only": sorted(export_only, key=_natural_key),
        "part_only": sorted(part_only, key=_natural_key),
        "sources": list(sources),
    }


# --------------------------------------------------------------------------
# 查询 / 导出
# --------------------------------------------------------------------------
def find_components(inv: dict, query: str = "", category: str = "") -> List[dict]:
    """按位号/类别/封装/前缀过滤；query 支持 * ? 通配。"""
    rows = list((inv or {}).get("components") or [])
    if category and category not in ("全部", "所有"):
        rows = [c for c in rows if c["category"] == category]
    q = (query or "").strip()
    if not q:
        return rows
    import fnmatch

    def hit(text: str) -> bool:
        text = (text or "").upper()
        pat = q.upper()
        if "*" in pat or "?" in pat:
            return fnmatch.fnmatch(text, pat)
        return pat in text

    return [c for c in rows if hit(c["refdes"]) or hit(c["category"])
            or hit(c["package"]) or hit(c["prefix"])]


def _detail_rows(inv: dict) -> List[list]:
    rows = []
    for c in (inv or {}).get("components") or []:
        rows.append([
            c["refdes"], c["category"], c["prefix"], c["package"] or "",
            c["side"], c["x_mm"] if c["x_mm"] is not None else "",
            c["y_mm"] if c["y_mm"] is not None else "", c["rotation"] or "",
            c["coord_source"] or ("无坐标" if not c["has_coords"] else ""),
            c["pin_count"] if c["pin_count"] is not None else "",
            "、".join(c["padstacks"][:3]),
            "、".join(c["sources"]),
        ])
    return rows


_DETAIL_HEADERS = ["位号", "类别", "前缀", "封装", "面别", "X(mm)", "Y(mm)",
                   "旋转", "坐标来源", "引脚数", "焊盘栈", "来源"]


def export_component_csv(inv: dict, path: str) -> str:
    """导出明细 CSV (UTF-8 BOM, Excel 直接打开)。"""
    import csv

    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_DETAIL_HEADERS)
        writer.writerows(_detail_rows(inv))
    return path


def export_component_excel(inv: dict, path: str) -> str:
    """导出 Excel: 汇总 / 分类 / 明细 三个页签。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    stats = (inv or {}).get("stats") or _build_stats([], [])
    wb = Workbook()
    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="DCE6F1")

    def head(ws, cols):
        ws.append(cols)
        for cell in ws[1]:
            cell.font = bold
            cell.fill = head_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.freeze_panes = "A2"

    def widths(ws, sizes):
        for i, w in enumerate(sizes, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

    ws = wb.active
    ws.title = "汇总"
    ws.append(["器件分类统计"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    summary = [
        ("器件总数", stats["total"], "个", "去重后的位号数"),
        ("类别数", stats["category_count"], "类", "按位号前缀归类"),
        ("IC芯片", stats["by_category"].get("IC芯片", 0), "个", "U/IC 前缀"),
        ("有坐标", stats["with_coords"], "个",
         f"其中焊盘中心推算 {stats['estimated_coords']} 个"),
        ("有封装", stats["with_package"], "个", "PartDefn/Gate 或 Placement"),
        ("已知引脚数", stats["pins_known"], "个器件", "来自 evan_pads.csv"),
        ("引脚总数", stats["total_pins"], "个", "仅统计已知引脚"),
        ("TOP", stats["sides"].get("TOP", 0), "个", ""),
        ("BOTTOM", stats["sides"].get("BOTTOM", 0), "个", ""),
        ("面别未知", stats["sides"].get("未知", 0), "个", ""),
        ("坐标冲突", stats["coord_conflicts"], "个", "多来源坐标不一致"),
        ("未分类前缀", "、".join(stats["unclassified"]) or "无", "", ""),
    ]
    ws.append(["指标", "值", "单位", "说明"])
    for cell in ws[3]:
        cell.font = bold
        cell.fill = head_fill
    for row in summary:
        ws.append(list(row))
    widths(ws, [16, 30, 10, 56])
    ws.append([])
    if (inv or {}).get("sources"):
        ws.append(["数据来源", ""])
        ws.cell(ws.max_row, 1).font = bold
        for src in inv["sources"]:
            ws.append([src.get("file", ""), src.get("kind", ""), src.get("count", "")])

    ws2 = wb.create_sheet("分类")
    head(ws2, ["类别", "数量", "占比(%)", "前缀", "示例位号"])
    for item in stats["categories"]:
        ws2.append([item["name"], item["count"], item["share"],
                    "、".join(item["prefixes"]), "、".join(item["examples"])])
    widths(ws2, [18, 8, 10, 28, 60])

    ws3 = wb.create_sheet("明细")
    head(ws3, _DETAIL_HEADERS)
    for row in _detail_rows(inv):
        ws3.append(row)
    widths(ws3, [10, 16, 8, 22, 8, 12, 12, 8, 16, 8, 28, 18])
    wb.save(path)
    return path

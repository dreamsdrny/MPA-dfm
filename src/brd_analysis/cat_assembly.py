"""第 6 类: SMT 贴装与装配分析 (给 SMT 厂 / PCBA 制造商交付数据的视角)。

对应 PCB Editor 的: Component Placement (XY/旋转) / Reference Designator /
BOM Export / Assembly Drawing / Silkscreen / Testpoint / Variant。

这一类的数据现实 (决定了哪些能算、哪些只能如实说缺)
--------------------------------------------------
  * `.brd` 的 `objects/Part.xml` **只有位号**, 没有 Value / Footprint / MPN;
  * **器件 XY 坐标、旋转角、面别不在 .brd 二进制里** (二进制与内嵌快照都没有),
    它们只存在于 Allegro 的设计数据库, 必须由 File > Export > Placement 导出,
    或用 `--export-skill` / `src.allegro_skill.write_bundle()` 生成的 SKILL 取出来;
  * 装配图上的位号文字、Variant (DNP / 替代料) 数据同样只能在 Allegro 里导出。

所以本模块的口径是:
  1. **能算的算到底** —— 位号清单、BOM 分组、丝印几何、装配图落盘、坐标三项校验,
     全部给出可核对的结果与产物文件;
  2. **算不了一律 `UNAVAILABLE`**, 并写清「缺什么 + 怎么补 (Allegro 菜单路径)」,
     绝不编造器件位置 (编出来的坐标会让 SMT 贴错件, 比没有坐标糟糕得多);
  3. 「有数量、无坐标」这种中间状态要单独说清楚 (例如 IPC-356 有测试点记录但
     没有可校定的坐标), 不能混成"通过"。
"""
import csv
import os
import re
import tempfile
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as _xml_escape

from src.brd_analysis import facts
from src.brd_analysis.context import Context
from src.brd_analysis.model import (INFO, PASS, UNAVAILABLE, WARN, Capability,
                                    Result, unavailable)

# 位号前缀 -> 中文类别: 复用 brd_components 的那一份表, 保证报告里"类别"口径一致。
try:
    from src.brd_components import _PREFIX_CATEGORY as _REFDES_CATEGORY
    from src.brd_components import _prefix_of as _refdes_prefix
except Exception:  # noqa: BLE001  单独跑本模块 (依赖缺失) 时也要能工作
    _REFDES_CATEGORY = {}

    def _refdes_prefix(refdes: str) -> str:
        m = re.match(r"^([A-Za-z]+)\d", refdes or "")
        return m.group(1).upper() if m else ""


# --------------------------------------------------------------------------
# 通用小工具 (目录 / CSV / 数值 / 自然序)
# --------------------------------------------------------------------------
_SMT_SUBDIR = "smt"


def _out_root(ctx: Context) -> str:
    """输出根目录: ctx.out_dir -> .brd 所在目录 -> 系统临时目录。"""
    if ctx.out_dir:
        return ctx.out_dir
    if ctx.brd_path:
        parent = os.path.dirname(os.path.abspath(ctx.brd_path))
        if parent:
            return parent
    return tempfile.gettempdir()


def _smt_dir(ctx: Context) -> str:
    """本类所有产物的目录: <out>/smt/。"""
    return os.path.join(_out_root(ctx), _SMT_SUBDIR)


def _ensure_dir(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError:  # 目录建不出来时由调用方降级说明, 不抛异常打断整轮分析
        return False


def _write_csv(path: str, columns: List[str], rows: List[dict]) -> Optional[str]:
    """写 CSV (utf-8-sig, Excel 双击直接正确显示中文); 失败返回 None。"""
    if not _ensure_dir(os.path.dirname(path)):
        return None
    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(columns), extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({c: ("" if row.get(c) is None else row.get(c))
                                 for c in columns})
    except OSError:
        return None
    return path


def _write_text(path: str, text: str) -> Optional[str]:
    """写文本产物 (SVG 等), 统一 LF 换行, UTF-8。"""
    if not _ensure_dir(os.path.dirname(path)):
        return None
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except OSError:
        return None
    return path


def _num(value) -> Optional[float]:
    """宽松取数: 支持 float/str/"90.0°"; 取不到返回 None (不当成 0)。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("°", "").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int(value, default: int = 0) -> int:
    got = _num(value)
    return int(got) if got is not None else default


def _natural_key(refdes: str):
    """位号自然序: R2 < R10 < R100 (与 brd_components 的排序口径一致)。"""
    parts = re.split(r"(\d+)", refdes or "")
    key = []
    for part in parts:
        if part.isdigit():
            key.append((1, int(part), ""))
        elif part:
            key.append((0, 0, part.upper()))
    return key


def _category_of(refdes: str) -> str:
    prefix = _refdes_prefix(refdes or "")
    return _REFDES_CATEGORY.get(prefix, prefix or "未知前缀")


def _sample(refs: List[str], limit: int = 8) -> str:
    """"R1、R2、R3 …等 12 个" 这样的位号示例 (明细表里不塞几百个位号)。"""
    refs = [r for r in refs if r]
    if not refs:
        return ""
    head = "、".join(refs[:limit])
    return head if len(refs) <= limit else f"{head} 等 {len(refs)} 个"


def _angle_bucket(value) -> Tuple[str, Optional[float]]:
    """旋转角归桶: 0/90/180/270 归一桶, 非 90° 倍数单独成桶 (要 SMT 确认)。"""
    ang = _num(value)
    if ang is None:
        return ("未知", None)
    a = ang % 360.0
    if a < 0:
        a += 360.0
    base = (int(round(a / 90.0)) % 4) * 90
    if abs(a - base) <= 0.05 or abs(a - base - 360.0) <= 0.05:
        return (f"{base}°", a)
    return (f"非正交 {a:.1f}°", a)


# --------------------------------------------------------------------------
# 6.1 Component Placement —— 器件 XY 坐标 + 三项实用校验
# --------------------------------------------------------------------------
def _placement_rows(comps: List[dict]) -> List[dict]:
    rows = []
    for c in comps:
        refdes = str(c.get("refdes") or "").strip()
        rows.append({
            "位号": refdes,
            "X_mm": _num(c.get("x_mm")),
            "Y_mm": _num(c.get("y_mm")),
            "旋转": ("" if c.get("rotation") in (None, "") else c.get("rotation")),
            "面别": (c.get("side") or "").strip().upper(),
            "来源": c.get("source", ""),
        })
    rows.sort(key=lambda r: _natural_key(r["位号"]))
    return rows


_PLACEMENT_COLS = ["位号", "X_mm", "Y_mm", "旋转", "面别", "来源"]


def cap_placement(ctx: Context) -> Result:
    cap = CATALOG_MAP["smt.placement"]
    comps = list(ctx.exports.get("components") or [])
    if not comps:
        res = unavailable(
            cap,
            "没有器件 XY 坐标: .brd 二进制与内嵌快照都不含器件位置, "
            "坐标只能来自 Allegro 的 Placement 导出或 SKILL 脚本",
            ctx.howto_components_xy())
        res.need("器件 XY 坐标 / 旋转角 / 面别 (place_txt.txt 或 evan_components.csv)",
                 "生成导出脚本: python -m src.pick --brd <板.brd> --skill --out <输出目录> "
                 "(或调用 src.allegro_skill.write_bundle(out_dir)), 得到 evan_pick.il / "
                 "evan_pick.scr / README_坐标导出.txt 后, 在 PCB Editor 里执行一次再回来分析")
        res.extra["skill_bundle"] = ["evan_pick.il", "evan_pick.scr", "README_坐标导出.txt"]
        res.extra["skill_generator"] = "src.allegro_skill.write_bundle(out_dir)"
        comp = facts.components(ctx)
        if comp.get("ok"):
            res.metric("Part.xml 位号数", comp.get("count", 0), "个",
                       comp.get("source", "objects/Part.xml"), "high")
            res.summary = (f"Part.xml 里有 {comp.get('count', 0)} 个位号, "
                           f"但没有任何器件的 XY 坐标, 贴装坐标表与三项校验都做不了")
        else:
            res.summary = "没有器件 XY 坐标, 也没有位号清单, 贴装数据全缺"
        res.source("objects/Part.xml (只有位号) / 无 Placement 导出")
        return res

    rows = _placement_rows(comps)
    res = Result(cap, PASS, "")
    res.add_all(rows, _PLACEMENT_COLS)

    xs = [r["X_mm"] for r in rows if r["X_mm"] is not None]
    ys = [r["Y_mm"] for r in rows if r["Y_mm"] is not None]
    sides: Dict[str, int] = {}
    for row in rows:
        side = row["面别"] or "未标注"
        sides[side] = sides.get(side, 0) + 1
    res.metric("有坐标的器件", len(rows), "个", "导入的 Placement/SKILL 坐标文件", "high")
    if xs and ys:
        res.metric("布局范围 X", f"{min(xs):.3f} ~ {max(xs):.3f}", "mm", "导入的坐标文件")
        res.metric("布局范围 Y", f"{min(ys):.3f} ~ {max(ys):.3f}", "mm", "导入的坐标文件")
        res.metric("布局跨度", f"{max(xs) - min(xs):.3f} × {max(ys) - min(ys):.3f}",
                   "mm", "导入的坐标文件")
    for side, count in sorted(sides.items()):
        res.metric(f"{side} 面器件", count, "个", "坐标文件的 side 列")

    issues: List[str] = []

    # (a) 器件是否落在板框 bbox 之外 (板框来自内嵌快照, 同一设计坐标系才可比)
    area = facts.region_areas(ctx)
    outside: List[str] = []
    if area.get("ok"):
        x0, y0, x1, y1 = area["bbox_mm"]
        res.metric("板框 bbox", f"X {x0}~{x1} / Y {y0}~{y1}", "mm",
                   "内嵌快照 BOARD GEOMETRY/OUTLINE", "high" if ctx.scale_confident else "low")
        for row in rows:
            if row["X_mm"] is None or row["Y_mm"] is None:
                continue
            if (row["X_mm"] < x0 or row["X_mm"] > x1
                    or row["Y_mm"] < y0 or row["Y_mm"] > y1):
                outside.append(row["位号"])
    else:
        res.need("板框几何 (判器件是否落在板框外要用)",
                 f"{area.get('reason', '没有可用的板框折线')}; "
                 "确保 .brd 有 BOARD GEOMETRY/OUTLINE, 或提供板框 DXF")
    res.metric("落在板框外的器件", len(outside), "个", "坐标 vs 板框 bbox",
               "medium" if area.get("ok") else "low")
    if outside:
        ratio = len(outside) / max(1, len(rows))
        if ratio >= 0.3:
            # 大比例"越界"通常不是真的放错了, 而是两个来源的原点不同 —— 如实说, 不乱判
            issues.append(f"{len(outside)} 个器件的坐标在板框 bbox 之外 (占 {ratio:.0%}), "
                          f"比例过高, 更像是坐标文件与快照的原点/单位不一致, "
                          f"请核对两者是否同一坐标系; 位号: {_sample(outside, 12)}")
        else:
            issues.append(f"{len(outside)} 个器件落在板框外, 请核对是否允许板边/板外器件; "
                          f"位号: {_sample(outside, 12)}")

    # (b) 坐标重复: 同坐标不同位号, 多半是重叠摆放
    buckets: Dict[Tuple[float, float], List[str]] = {}
    for row in rows:
        if row["X_mm"] is None or row["Y_mm"] is None:
            continue
        buckets.setdefault((round(row["X_mm"], 3), round(row["Y_mm"], 3)), []).append(row["位号"])
    duplicates = {k: v for k, v in buckets.items() if len(v) > 1}
    dup_refs = [r for refs in duplicates.values() for r in refs]
    res.metric("坐标重复的器件", len(dup_refs), "个", "按 (X,Y) 取整到 0.001mm 分组")
    res.metric("重复坐标组数", len(duplicates), "组", "同上")
    if duplicates:
        detail = "; ".join(f"({k[0]},{k[1]}) {'、'.join(v)}"
                           for k, v in list(duplicates.items())[:8])
        issues.append(f"{len(duplicates)} 组器件坐标完全相同 (可能重叠摆放), 需要核对: {detail}")

    # (c) 坐标缺失或为 0
    missing = [r["位号"] for r in rows if r["X_mm"] is None or r["Y_mm"] is None]
    zeros = [r["位号"] for r in rows
             if r["X_mm"] == 0 and r["Y_mm"] == 0]
    res.metric("X/Y 缺失的器件", len(missing), "个", "坐标文件的 X/Y 列")
    res.metric("坐标 (0,0) 的器件", len(zeros), "个", "坐标文件的 X/Y 列")
    if missing:
        issues.append(f"{len(missing)} 个器件的 X 或 Y 缺失 (坐标文件没读到), "
                      f"位号: {_sample(missing, 12)}")
    if zeros:
        issues.append(f"{len(zeros)} 个器件的坐标是 (0,0): 可能真的是原点器件, "
                      f"也可能是导出失败, 需要人工确认; 位号: {_sample(zeros, 12)}")

    # 产物: 坐标明细 CSV
    path = _write_csv(os.path.join(_smt_dir(ctx), "placement.csv"), _PLACEMENT_COLS, rows)
    if path:
        res.artifact("csv", path, "器件贴装坐标明细 (位号/X/Y/旋转/面别)")
    else:
        res.warn("坐标明细 CSV 写盘失败 (输出目录不可写), 结果只在报告里")

    # 与 Part.xml 位号清单交叉核对
    comp = facts.components(ctx)
    if comp.get("ok"):
        known = {r.upper() for r in comp.get("refdes") or []}
        got = {r["位号"].upper() for r in rows if r["位号"]}
        only_xy = sorted(got - known, key=_natural_key)
        only_xml = sorted(known - got, key=_natural_key)
        if only_xy:
            issues.append(f"坐标文件里有 {len(only_xy)} 个位号不在 Part.xml 中: "
                          f"{_sample(only_xy, 10)}")
        if only_xml:
            issues.append(f"Part.xml 里有 {len(only_xml)} 个位号没有坐标 (未摆放或未导出): "
                          f"{_sample(only_xml, 10)}")
        res.metric("Part.xml 位号数", comp.get("count", 0), "个",
                   comp.get("source", "objects/Part.xml"), "high")

    res.status = WARN if issues else PASS
    for text in issues:
        res.warn(text)
    res.extra["checks"] = {"outside_board": outside, "duplicate_coords": dup_refs,
                           "missing_xy": missing, "zero_xy": zeros}
    res.summary = (
        f"{len(rows)} 个器件有坐标"
        + ("; " + " / ".join(f"{k} {v}" for k, v in sorted(sides.items())) if sides else "")
        + f"; 校验: 板框外 {len(outside)} 个 / 重复坐标 {len(duplicates)} 组 / "
          f"坐标缺失 {len(missing)} 个 / 坐标(0,0) {len(zeros)} 个"
        + ("" if not issues else f"; {len(issues)} 项需关注"))
    res.source("导入的 Placement/SKILL 坐标文件" + (" + objects/Part.xml" if comp.get("ok") else ""))
    return res


# --------------------------------------------------------------------------
# 6.2 Rotation —— 元件旋转角度分布
# --------------------------------------------------------------------------
def cap_rotation(ctx: Context) -> Result:
    cap = CATALOG_MAP["smt.rotation"]
    comps = list(ctx.exports.get("components") or [])
    if not comps:
        res = unavailable(
            cap,
            "没有器件旋转角: 旋转角与 XY 坐标一起由 Allegro 的 Placement 导出, "
            ".brd 二进制与内嵌快照里都没有",
            ctx.howto_components_xy())
        res.need("每个器件的旋转角 (含面别: 底面器件的角度是镜像后的值)",
                 "Allegro: File > Export > Placement 导出 place_txt.txt; "
                 "或用 SKILL (--export-skill) 导出的 evan_components.csv 的 ROTATION 列")
        res.summary = "缺器件旋转角, 无法统计贴装角度分布"
        res.source("无 Placement/SKILL 坐标文件")
        return res

    buckets: Dict[str, List[str]] = {}
    unknown: List[str] = []
    angles: List[float] = []
    for c in sorted(comps, key=lambda c: _natural_key(str(c.get("refdes") or ""))):
        refdes = str(c.get("refdes") or "").strip()
        label, ang = _angle_bucket(c.get("rotation"))
        if ang is None:
            unknown.append(refdes)
            continue
        angles.append(ang)
        buckets.setdefault(label, []).append(refdes)

    rows = []
    for label, refs in sorted(buckets.items(),
                              key=lambda kv: (_num(kv[0].rstrip("°")) if kv[0][0].isdigit()
                                              else 9999, kv[0])):
        rows.append({"角度": label, "器件数": len(refs), "位号示例": _sample(refs, 8)})
    if unknown:
        rows.append({"角度": "未知 (坐标文件没写旋转角)", "器件数": len(unknown),
                     "位号示例": _sample(unknown, 8)})

    res = Result(cap, PASS, "")
    res.add_all(rows, ["角度", "器件数", "位号示例"])
    orthogonal = sum(len(v) for k, v in buckets.items() if not k.startswith("非正交"))
    weird = sum(len(v) for k, v in buckets.items() if k.startswith("非正交"))
    res.metric("有旋转角的器件", len(angles), "个", "坐标文件的 ROTATION 列", "high")
    res.metric("正交角度器件 (0/90/180/270)", orthogonal, "个", "按 90° 倍数额定",
               "high")
    res.metric("非正交角度器件", weird, "个", "按 90° 倍数额定", "high")
    for label, refs in sorted(buckets.items()):
        if not label.startswith("非正交"):
            res.metric(f"旋转 {label}", len(refs), "个", "坐标文件的 ROTATION 列")
    if weird:
        odd = sorted({k for k in buckets if k.startswith("非正交")})
        res.warn(f"{weird} 个器件的旋转角不是 90° 的整数倍 ({'、'.join(odd)}): "
                 f"这类角度要 SMT 确认 (贴片机角度/封装角度定义是否一致, "
                 f"是否需要改为 90° 台阶或特殊吸嘴)")
        res.need("非正交角度的确认结论 (是设计有意为之, 还是导出/封装角度偏差)",
                 "与 SMT 厂一起核对这几个位号的贴装角度; 必要时用 Allegro 的 "
                 "Placement 报告复核原设计角度")
        res.status = WARN
    if unknown:
        res.warn(f"{len(unknown)} 个器件的旋转角没读到, 这些器件的贴装角度未知")
        res.status = WARN

    path = _write_csv(os.path.join(_smt_dir(ctx), "rotation.csv"),
                      ["角度", "器件数", "位号示例"], rows)
    if path:
        res.artifact("csv", path, "贴装角度分布 (0/90/180/270 归桶, 非正交角单列)")
    res.summary = (f"{len(angles)} 个器件有旋转角: 正交 {orthogonal} 个, "
                   f"非正交 {weird} 个"
                   + (f", 未知 {len(unknown)} 个" if unknown else "")
                   + ("; 非正交角度要 SMT 确认" if weird else ""))
    res.source("导入的 Placement/SKILL 坐标文件 (ROTATION 列)")
    return res


# --------------------------------------------------------------------------
# 6.3 Reference Designator —— 元件位号清单
# --------------------------------------------------------------------------
def cap_refdes(ctx: Context) -> Result:
    cap = CATALOG_MAP["smt.refdes"]
    comp = facts.components(ctx)
    if not comp.get("ok"):
        res = unavailable(cap, comp.get("reason") or "BRD 归档里没有 objects/Part.xml",
                          "确认提供的是真实 PCB 设计的 .brd (归档里有 objects/Part.xml); "
                          "FAB 拼板图/纯板框图纸没有器件清单。也可用 Allegro: "
                          "Tools > Quick Reports > Component Report 导出位号清单后交给工具")
        res.source("objects/Part.xml (缺失或解析失败)")
        return res

    refdes = list(comp.get("refdes") or [])
    rows = [{"位号": r, "前缀": _refdes_prefix(r) or "（无数字前缀）", "类别": _category_of(r)}
            for r in sorted(refdes, key=_natural_key)]

    res = Result(cap, INFO, "")
    res.add_all(rows, ["位号", "前缀", "类别"])
    res.metric("位号总数", len(rows), "个", comp.get("source", "objects/Part.xml"), "high")
    by_prefix = comp.get("by_prefix") or {}
    by_category = comp.get("by_category") or {}
    res.metric("前缀种类", len(by_prefix), "种", "位号前缀拆分")
    res.metric("类别数量", len(by_category), "类", "位号前缀归类")
    for prefix, count in list(by_prefix.items())[:12]:
        res.metric(f"{prefix} 器件", count, "个", "位号前缀拆分")
    if len(by_prefix) > 12:
        res.metric("其余前缀", sum(v for k, v in list(by_prefix.items())[12:]), "个",
                   "位号前缀拆分")

    # 与坐标文件交叉核对: 清单对不对得上, SMT 最关心
    comps_xy = list(ctx.exports.get("components") or [])
    if comps_xy:
        known = {r.upper() for r in refdes}
        got = {str(c.get("refdes") or "").strip().upper()
               for c in comps_xy if c.get("refdes")}
        only_xy = sorted(got - known, key=_natural_key)
        only_xml = sorted(known - got, key=_natural_key)
        res.metric("坐标文件里的位号", len(got), "个", "导入的 Placement/SKILL 坐标文件")
        if only_xy or only_xml:
            res.warn(f"位号清单与坐标文件不一致: 只在坐标文件里 {len(only_xy)} 个 "
                     f"({_sample(only_xy, 8)}), 只在 Part.xml 里 {len(only_xml)} 个 "
                     f"({_sample(only_xml, 8)}); 交付 SMT 前必须对齐")
        else:
            res.metric("与坐标文件的位号差异", 0, "个", "Part.xml vs 坐标文件", "high")

    path = _write_csv(os.path.join(_smt_dir(ctx), "refdes.csv"),
                      ["位号", "前缀", "类别"], rows)
    if path:
        res.artifact("csv", path, "元件位号清单 (自然序, 位号/前缀/类别)")
    else:
        res.warn("位号清单 CSV 写盘失败 (输出目录不可写)")
    res.summary = (f"位号 {len(rows)} 个, {len(by_prefix)} 种前缀 / {len(by_category)} 类; "
                   + "、".join(f"{k} {v}" for k, v in list(by_prefix.items())[:6]))
    if not comps_xy:
        res.need("器件 XY 坐标 (把位号清单变成可贴装的坐标表)",
                 ctx.howto_components_xy())
    res.source(comp.get("source", "objects/Part.xml"))
    return res


# --------------------------------------------------------------------------
# 6.4 BOM Export —— 物料清单 (贴装视角)
# --------------------------------------------------------------------------
_BOM_COLS = ["位号前缀", "类别", "面别", "数量", "位号示例",
             "Value", "Footprint", "MPN", "来源"]
_BOM_REPORT_KEYS = ("REFDES", "COMPONENT", "QUANTITY")
_BOM_COL_HINTS = {
    "refdes": ("REFDES", "REFERENCE", "SYMBOL", "COMPONENT", "PART"),
    "value": ("VALUE", "VAL"),
    "footprint": ("FOOTPRINT", "PACKAGE", "PKG", "DEVICE"),
    "mpn": ("MPN", "PART NUMBER", "PART_NUMBER", "PARTNUMBER", "MANUFACTURER"),
    "qty": ("QUANTITY", "QTY", "COUNT"),
}
_NO_REFDES = "（报告未给位号）"
_UNKNOWN_SIDE = "未知（需 Placement 导出）"


def _pick_col(columns: List[str], keys) -> Optional[str]:
    for col in columns or []:
        up = str(col).upper()
        if any(k in up for k in keys):
            return col
    return None


def _split_refs(cell) -> List[str]:
    return [p for p in re.split(r"[,;/|\s]+", str(cell or "")) if p]


def _bom_from_reports(ctx: Context) -> List[dict]:
    """从导入的报告里找表头含 REFDES/COMPONENT/QUANTITY 的段, 取出可合并的 BOM 行。

    只认"定宽表格段" (imports.parse_allegro_report 解析出来的 sections);
    列名按关键字模糊匹配, 匹配不到就留空, 不做位置猜测。
    """
    out: List[dict] = []
    for rep in ctx.exports.get("reports") or []:
        source = rep.get("source") or "导入报告"
        for sec in rep.get("sections") or []:
            columns = list(sec.get("columns") or [])
            header = (sec.get("header") or " ".join(columns)).upper()
            if not any(k in header for k in _BOM_REPORT_KEYS):
                continue
            col_ref = _pick_col(columns, _BOM_COL_HINTS["refdes"])
            col_value = _pick_col(columns, _BOM_COL_HINTS["value"])
            col_fp = _pick_col(columns, _BOM_COL_HINTS["footprint"])
            col_mpn = _pick_col(columns, _BOM_COL_HINTS["mpn"])
            col_qty = _pick_col(columns, _BOM_COL_HINTS["qty"])

            def cell(row, col):
                return str(row.get(col, "") or "").strip() if col else ""

            for raw in sec.get("rows") or []:
                if not isinstance(raw, dict):
                    continue
                refs = _split_refs(cell(raw, col_ref)) if col_ref else []
                qty = _int(cell(raw, col_qty), 0) if col_qty else 0
                if qty <= 0:
                    qty = max(1, len(refs))
                out.append({
                    "source": source,
                    "header": sec.get("header") or " ".join(columns),
                    "refs": refs,
                    "qty": qty,
                    "value": cell(raw, col_value),
                    "footprint": cell(raw, col_fp),
                    "mpn": cell(raw, col_mpn),
                })
                if len(out) >= 5000:  # 极端大的报告: 截断并如实说明
                    return out
    return out


def cap_bom(ctx: Context) -> Result:
    cap = CATALOG_MAP["smt.bom"]
    comp = facts.components(ctx)
    base_ok = bool(comp.get("ok"))
    report_rows = _bom_from_reports(ctx)
    if not base_ok and not report_rows:
        res = unavailable(
            cap,
            comp.get("reason") or "BRD 归档里没有 objects/Part.xml, 也没有可用的 BOM 报告",
            "提供真实 PCB 设计的 .brd (带 objects/Part.xml); 或在 Allegro 里 "
            "Tools > Reports > Bill of Materials 导出 BOM 报告 (.rpt) 交给工具")
        res.need("位号清单 (BOM 的基本盘) 或 Allegro BOM 报告",
                 "Allegro: Tools > Reports > Bill of Materials (含 REFDES/COMPONENT/"
                 "QUANTITY 表头), 导出后作为 --exports 文件传给本工具")
        res.summary = "没有 Part.xml 也没有 BOM 报告, 无法生成贴装视角 BOM"
        res.source("objects/Part.xml (缺失) / 无 BOM 报告")
        return res

    # 面别来自坐标文件的 side 列; 没有坐标文件就如实写"未知"
    side_of: Dict[str, str] = {}
    for c in ctx.exports.get("components") or []:
        ref = str(c.get("refdes") or "").strip().upper()
        if ref:
            side_of[ref] = (c.get("side") or "").strip().upper()

    groups: Dict[Tuple[str, str, str], dict] = {}
    attrs: Dict[str, dict] = {}

    def touch(prefix: str, category: str, side: str, refs: List[str],
              qty: int, source: str) -> None:
        key = (prefix, category, side)
        g = groups.setdefault(key, {"refs": [], "qty": 0, "sources": []})
        g["qty"] += qty
        for r in refs:
            if r and r not in g["refs"] and len(g["refs"]) < 2000:
                g["refs"].append(r)
        if source not in g["sources"]:
            g["sources"].append(source)

    # 已经计过数的位号: Part.xml 与导入报告可能重复列同一颗器件, 只算一次,
    # 报告里多出来的信息 (Value/Footprint/MPN) 照收 —— 数量虚高会让 SMT 多备料。
    counted: Dict[str, None] = {}
    if base_ok:
        for ref in comp.get("refdes") or []:
            side = side_of.get(ref.upper()) or _UNKNOWN_SIDE
            touch(_refdes_prefix(ref) or "（无数字前缀）", _category_of(ref), side,
                  [ref], 1, comp.get("source", "objects/Part.xml"))
            counted[ref.upper()] = None

    report_sources: List[str] = []
    report_dup = 0
    for rr in report_rows:
        source = f"导入报告 {rr['source']}"
        if source not in report_sources:
            report_sources.append(source)
        refs = rr["refs"]
        if refs:
            for ref in refs:
                key_ref = ref.upper()
                # 属性 (Value/Footprint/MPN) 无论是否重复都收
                for field, col in (("value", "Value"), ("footprint", "Footprint"),
                                   ("mpn", "MPN")):
                    if rr.get(field):
                        attrs.setdefault(key_ref, {})[col] = rr[field]
                if key_ref in counted:
                    report_dup += 1
                    continue
                counted[key_ref] = None
                side = side_of.get(key_ref) or _UNKNOWN_SIDE
                touch(_refdes_prefix(ref) or "（无数字前缀）", _category_of(ref), side,
                      [ref], 1, source)
        else:
            # 报告只给了汇总数量 (没有位号): 单列一组, 不与 Part.xml 的数量混算
            touch(_NO_REFDES, _NO_REFDES, _UNKNOWN_SIDE, [], rr["qty"], source)

    def attr_join(refs: List[str], key: str) -> str:
        vals = []
        for ref in refs:
            got = (attrs.get(ref.upper()) or {}).get(key)
            if got and got not in vals:
                vals.append(got)
        if not vals:
            return ""
        return "、".join(vals[:3]) + (" 等" if len(vals) > 3 else "")

    rows = []
    for (prefix, category, side), g in sorted(
            groups.items(), key=lambda kv: (_natural_key(kv[0][0]), kv[0][1], kv[0][2])):
        rows.append({
            "位号前缀": prefix,
            "类别": category,
            "面别": side,
            "数量": g["qty"],
            "位号示例": _sample(sorted(g["refs"], key=_natural_key), 10),
            "Value": attr_join(g["refs"], "Value"),
            "Footprint": attr_join(g["refs"], "Footprint"),
            "MPN": attr_join(g["refs"], "MPN"),
            "来源": " 与 ".join(g["sources"]),
        })

    res = Result(cap, INFO, "")
    res.add_all(rows, _BOM_COLS)
    total = sum(r["数量"] for r in rows)
    res.metric("BOM 分组", len(rows), "组", "按 位号前缀/类别/面别 分组")
    res.metric("贴装数量合计", total, "个", "Part.xml 位号数" if base_ok else "导入报告数量列")
    if base_ok:
        res.metric("Part.xml 位号数", comp.get("count", 0), "个",
                   comp.get("source", "objects/Part.xml"), "high")
    unknown_side = sum(r["数量"] for r in rows if r["面别"] == _UNKNOWN_SIDE)
    res.metric("面别未知的数量", unknown_side, "个",
               "坐标文件的 side 列" if side_of else "没有坐标文件")
    if report_rows:
        res.metric("合并的导入报告行", len(report_rows), "行", "、".join(report_sources))
        if report_dup:
            res.metric("报告与 Part.xml 重复的位号", report_dup, "个",
                       "只计一次, 报告仅用于补 Value/Footprint/MPN")
            res.warn(f"导入报告里有 {report_dup} 个位号与 Part.xml 重复, 数量只算一次 "
                     f"(报告只用来补 Value/Footprint/MPN), 避免 SMT 重复备料")

    if not side_of and base_ok:
        res.need("器件面别 (Top/Bottom) —— BOM 要按面别分开投料/分线",
                 ctx.howto_components_xy())
        res.warn("没有坐标文件, 所有器件的面别都记为「未知（需 Placement 导出）」; "
                 "BOM 只能按 前缀/类别 看, 不能按面别拆")
    if not report_rows:
        res.warn("Value / Footprint / MPN 三列保留但为空: .brd 的 Part.xml 只有位号; "
                 "型号/物料编码需要 Allegro 的 BOM 报告或 ERP 数据")
        res.need("Value / Footprint / MPN (料号、封装、物料编码)",
                 "Allegro: Tools > Reports > Bill of Materials (勾选 Value/Footprint/"
                 "Part Number) 导出报告后作为 --exports 传给本工具; 或让 SMT 厂按 ERP "
                 "BOM 补全")
    else:
        res.source(" + ".join([s for s in
                               (["objects/Part.xml"] if base_ok else []) + report_sources
                               if s]))
    if base_ok:
        res.source(comp.get("source", "objects/Part.xml"))
    if side_of:
        res.source("导入的 Placement/SKILL 坐标文件 (side 列)")

    path = _write_csv(os.path.join(_smt_dir(ctx), "bom.csv"), _BOM_COLS, rows)
    if path:
        res.artifact("csv", path, "贴装视角 BOM (按 前缀/类别/面别 分组)")
    else:
        res.warn("BOM CSV 写盘失败 (输出目录不可写)")
    res.extra["report_rows"] = report_rows[:500]
    res.extra["bom_groups"] = rows
    res.summary = (f"{len(rows)} 组 / {total} 个贴装位号; 面别未知 {unknown_side} 个"
                   + (f"; 已合并 {len(report_rows)} 行导入报告" if report_rows else "")
                   + "; Value/Footprint/MPN "
                   + ("取自导入报告" if report_rows else "留空 (Part.xml 只有位号)"))
    return res


# --------------------------------------------------------------------------
# 6.5 Assembly Drawing —— 装配图 (SVG, 毫米坐标, Y 轴翻转)
# --------------------------------------------------------------------------
_SVG_COLORS = {"assembly": "#B03A2E", "silk": "#1F6FEB"}
_SVG_DEFAULT_WIDTH_MM = 0.15


def _svg_bbox(segs: List[dict]) -> Optional[Tuple[float, float, float, float]]:
    xs: List[float] = []
    ys: List[float] = []
    for seg in segs:
        for x, y in seg.get("pts") or []:
            xs.append(x)
            ys.append(y)
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _svg_polyline(seg: dict) -> str:
    """一段图形 -> <polyline>; Y 取负实现翻转, 线宽用快照的 width_mm。"""
    pts = seg.get("pts") or []
    points = " ".join(f"{x:.4f},{-y:.4f}" for x, y in pts)
    if seg.get("has_width") and seg.get("width_mm"):
        style = f' style="stroke-width: {float(seg["width_mm"]):.3f}"'
    else:
        style = ""
    return f'    <polyline points="{points}"{style}/>'


def _write_assembly_svg(path: str, layers: List[Tuple[str, List[dict]]],
                        outline: List[dict], title: str, desc: str) -> Optional[str]:
    """写装配图 SVG。

    坐标系: 毫米, viewBox 用「板框 bbox + 2mm 边距」; Allegro 的 +Y 向上,
    SVG 的 +Y 向下, 所以 viewBox 的 Y 用负值、几何点的 Y 取负 —— 上下不镜像。
    """
    drawn = [s for _kind, segs in layers for s in segs]
    box = _svg_bbox(list(outline) + drawn)
    if box is None:
        return None
    minx, miny, maxx, maxy = box
    pad = 2.0
    vb_x = minx - pad
    vb_y = -(maxy + pad)
    vb_w = (maxx - minx) + 2.0 * pad
    vb_h = (maxy - miny) + 2.0 * pad

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1"',
        f'     width="{vb_w:.3f}mm" height="{vb_h:.3f}mm"',
        f'     viewBox="{vb_x:.3f} {vb_y:.3f} {vb_w:.3f} {vb_h:.3f}"'
        f' preserveAspectRatio="xMidYMid meet">',
        f'  <title>{_xml_escape(title)}</title>',
        f'  <desc>{_xml_escape(desc)}</desc>',
        '  <style>',
        '    .bg { fill: #F7F9FB; }',
        '    .k-outline { fill: none; stroke: #9AA0A6; stroke-width: 0.150; }',
        '    .k-assembly { fill: none; stroke: #B03A2E; stroke-width: 0.200; }',
        '    .k-silk { fill: none; stroke: #1F6FEB; stroke-width: 0.150; opacity: 0.55; }',
        '    .k-other { fill: none; stroke: #6B7280; stroke-width: 0.150; }',
        '  </style>',
        '  <!-- 坐标系: 毫米; Y 轴已翻转 (viewBox 用负 Y), 与 Allegro 的 +Y 向上一致 -->',
        f'  <rect class="bg" x="{vb_x:.3f}" y="{vb_y:.3f}"'
        f' width="{vb_w:.3f}" height="{vb_h:.3f}"/>',
        '  <!-- 板框底层 (淡灰) -->',
        '  <g id="outline" class="k-outline">',
    ]
    lines.extend(_svg_polyline(seg) for seg in outline)
    lines.append('  </g>')
    for kind, segs in layers:
        css = kind if kind in _SVG_COLORS else "other"
        lines.append(f'  <!-- {kind}: {len(segs)} 段 -->')
        lines.append(f'  <g id="{kind}" class="k-{css}">')
        lines.extend(_svg_polyline(seg) for seg in segs)
        lines.append('  </g>')
    lines.append('</svg>')
    return _write_text(path, "\n".join(lines) + "\n")


def cap_assembly_drawing(ctx: Context) -> Result:
    cap = CATALOG_MAP["smt.assembly_drawing"]
    if not ctx.has_snapshot:
        return unavailable(cap, "该 .brd 没有可解析的内嵌设计快照, 画不出装配图",
                           "确认 .brd 是 PCB Editor 保存的设计 (带内嵌快照); "
                           "或提供 Allegro 导出的装配图 PDF/DXF")
    if not ctx.has_scale:
        return unavailable(cap,
                           "板框标定失败 (快照坐标无法换算成 mm), 装配图尺寸不可信",
                           "提供含 BOARD GEOMETRY/OUTLINE 的 .brd 或板框 DXF, "
                           "以便确定 mm 比例")

    assembly = ctx.segments(["assembly"])
    silk = ctx.segments(["silk"])
    outline = ctx.segments(["outline"])
    if not assembly and not silk:
        return unavailable(cap,
                           "内嵌快照里既没有装配 (ASSEMBLY/PLACE_BOUND) 也没有丝印 "
                           "(SILKSCREEN) 图形, 没有可画的内容",
                           "Allegro: Manufacture > Create Assembly Drawing "
                           "(选 Assembly 层, 含器件外框与位号文字), 导出 PDF/DXF 交付 SMT")

    res = Result(cap, PASS, "")
    layers: List[Tuple[str, List[dict]]] = []
    if silk:
        layers.append(("silk", silk))       # 丝印先画 (淡), 装配轮廓压在上面
    if assembly:
        layers.append(("assembly", assembly))

    title = f"{ctx.board_name} 装配图 (Assembly Drawing)"
    desc = ("由 Evan_BRD 从 .brd 内嵌设计快照生成; 单位 mm; 线宽取快照 width 字段; "
            "不含丝印/装配层文字与焊盘图形")
    path = _write_assembly_svg(os.path.join(_smt_dir(ctx), "assembly_drawing.svg"),
                               layers, outline, title, desc)
    if path:
        res.artifact("svg", path,
                     "装配图 SVG (mm 坐标, Y 轴翻转, 板框 + "
                     + " + ".join(k for k, _v in layers) + ")")
    else:
        res.warn("装配图 SVG 写盘失败 (输出目录不可写)")

    total = sum(len(segs) for _k, segs in layers)
    res.metric("装配图形段数", len(assembly), "段", "内嵌快照 ASSEMBLY/PLACE_BOUND 图形")
    res.metric("丝印图形段数", len(silk), "段", "内嵌快照 SILKSCREEN 图形")
    res.metric("图中图形段数", total, "段", "装配 + 丝印")
    res.metric("板框图形段数", len(outline), "段", "内嵌快照 BOARD GEOMETRY/OUTLINE")
    box = _svg_bbox(list(outline) + [s for _k, segs in layers for s in segs])
    if box:
        res.metric("图幅 (含 2mm 边距)",
                   f"{round(box[2] - box[0] + 4.0, 2)} × {round(box[3] - box[1] + 4.0, 2)}",
                   "mm", "图形 bbox + 2mm 边距")
        res.metric("viewBox",
                   f"{round(box[0] - 2.0, 2)} {round(-(box[3] + 2.0), 2)} "
                   f"{round(box[2] - box[0] + 4.0, 2)} {round(box[3] - box[1] + 4.0, 2)}",
                   "mm (Y 取负 = 屏幕向下)")
    no_width = sum(1 for _k, segs in layers for s in segs if not s.get("has_width"))
    res.metric("未记录线宽的图形", no_width, "段",
               f"按默认 {_SVG_DEFAULT_WIDTH_MM}mm 画 (CSS 里的 stroke-width)", "low")

    if not assembly:
        res.status = WARN
        res.warn("快照里没有装配 (ASSEMBLY/PLACE_BOUND) 图形, 本图用的是丝印轮廓; "
                 "位号文字需要 Allegro 导出 (Manufacture > Create Assembly Drawing)")
    res.warn("快照只有图形轮廓, 没有位号文字与焊盘: 装配图上的位号/器件外框需要 "
             "Allegro 的 Create Assembly Drawing 导出件补充")
    res.need("带位号文字与器件外框的装配图 (Allegro 原生输出)",
             "Allegro: Manufacture > Create Assembly Drawing (Assembly_Top/Bottom 层, "
             "勾选 RefDes 文字), 或 File > Export > DXF/PDF 后一起交付 SMT")
    if no_width:
        res.warn(f"{no_width} 段图形在快照里 width=0 (FAB 图纸导出常见), "
                 f"图上按默认 {_SVG_DEFAULT_WIDTH_MM}mm 画, 不代表设计线宽")
    if not ctx.scale_confident:
        res.warn("快照坐标是用经验系数换算的 (未取到 .brd 的 Draw Size), "
                 "图面尺寸需人工核对")
    res.summary = (f"装配图已生成: {total} 段图形"
                   + (f" (装配 {len(assembly)} + 丝印 {len(silk)})" if assembly and silk
                      else (" (仅装配)" if assembly else " (仅丝印, 非装配层)"))
                   + f"; 板框底 {len(outline)} 段; 输出 assembly_drawing.svg")
    res.source("内嵌设计快照 ASSEMBLY / SILKSCREEN / BOARD GEOMETRY 图形")
    return res


# --------------------------------------------------------------------------
# 6.6 Silkscreen —— 丝印信息 (复用 src/silkscreen.py)
# --------------------------------------------------------------------------
def cap_silkscreen(ctx: Context) -> Result:
    cap = CATALOG_MAP["smt.silkscreen"]
    doc = ctx.snapshot
    scale = ctx.scale_mm_per_unit
    if doc is None:
        return unavailable(cap, "该 .brd 没有可解析的内嵌设计快照, 丝印几何读不到",
                           "确认 .brd 是 PCB Editor 保存的设计 (含内嵌快照); "
                           "或提供 Allegro 导出的丝印层 Gerber/PDF")
    if not scale:
        return unavailable(cap,
                           "板框标定失败 (scale_mm_per_unit=0), 丝印线宽与距离无法换算成 mm",
                           "提供含 BOARD GEOMETRY/OUTLINE 的 .brd, 以便确定 mm 比例")

    from src.silkscreen import silkscreen_stats, silkscreen_to_outline, silk_to_component

    stats = silkscreen_stats(doc, scale)
    if not stats.get("ok"):
        res = unavailable(cap, stats.get("reason") or "快照里没有丝印图形",
                          "确认设计里有 SILKSCREEN 层图形 (Package/Board Geometry); "
                          "SMT 的丝印要求需要丝印层数据才能核对")
        res.need(stats.get("reason") or "丝印图形", "提供含丝印图形的 .brd 或丝印层 Gerber")
        res.summary = "没有丝印图形, 丝印信息不可用"
        return res

    to_outline = silkscreen_to_outline(doc, scale)
    to_component = silk_to_component(doc, scale)

    res = Result(cap, INFO, "")
    by_class = stats.get("by_class") or {}
    res.add_all([{"丝印层/子类": k, "图形数": v} for k, v in by_class.items()],
                ["丝印层/子类", "图形数"])
    res.metric("丝印图形段数", stats.get("segments", 0), "段",
               stats.get("source", "内嵌快照 SILKSCREEN 图形"), "high")
    res.metric("丝印层/子类数", len(by_class), "个", "class/subclass 分组")
    if stats.get("has_widths"):
        res.metric("最小丝印线宽", stats.get("min_width_mm"), "mm",
                   "快照 width 字段 (乘 mm 系数)", "medium")
        res.metric("最大丝印线宽", stats.get("max_width_mm"), "mm", "同上", "medium")
        res.metric("线宽种类", len(stats.get("widths_mm") or []), "种", "同上", "medium")
    else:
        res.need("丝印实际线宽 (SMT 关心最小丝印线宽, 常见要求 ≥0.15mm)",
                 "快照的 width 字段全是 0, 需要 Allegro 的丝印层 Gerber 或 "
                 "Artwork 参数来量实际线宽")
        res.warn("丝印图形的 width 字段全是 0 (快照未记录线宽), "
                 "最小丝印线宽不可用 —— 不能因此认为线宽合格")

    missing: List[str] = []
    if to_outline.get("ok"):
        res.metric("丝印到板框最小距离", to_outline.get("min_mm"), "mm",
                   to_outline.get("source", "几何最短距离"), "medium")
        res.metric("参与距离计算的丝印段", to_outline.get("silk_segments"), "段",
                   "丝印折线拆分")
    else:
        missing.append(to_outline.get("reason") or "丝印到板框距离算不出来")
        res.need(to_outline.get("reason") or "丝印到板框的距离",
                 "需要 BOARD GEOMETRY/OUTLINE 与丝印图形的同一坐标系几何")
    if to_component.get("ok"):
        res.metric("丝印到器件本体最小距离", to_component.get("min_mm"), "mm",
                   to_component.get("source", "几何最短距离"), "medium")
        res.metric("参与距离计算的器件本体段", to_component.get("body_segments"), "段",
                   "PACKAGE GEOMETRY/PLACE_BOUND·ASSEMBLY 轮廓")
    else:
        missing.append(to_component.get("reason") or "丝印到器件本体的距离算不出来")
        res.need(to_component.get("reason") or "丝印到器件本体的距离",
                 "器件本体在快照里用 PLACE_BOUND/ASSEMBLY 轮廓代替; "
                 "没有这些轮廓时需 Allegro 的元件外框报告")

    res.extra["silkscreen_stats"] = stats
    res.extra["to_outline"] = to_outline
    res.extra["to_component"] = to_component
    res.extra["unavailable_parts"] = missing

    if missing:  # 缺哪一项都不算"通过": 状态按数据不足处理, 能给的指标照给
        res.status = UNAVAILABLE
        res.summary = (f"丝印 {stats.get('segments', 0)} 段已读到, 但 "
                       f"{len(missing)} 项距离/几何数据不足: "
                       + "; ".join(missing[:2]))
    else:
        res.summary = (f"丝印 {stats.get('segments', 0)} 段"
                       + (f", 最小线宽 {stats.get('min_width_mm')}mm"
                          if stats.get("has_widths") else ", 线宽未记录")
                       + f"; 到板框最小距离 {to_outline.get('min_mm')}mm"
                       + f", 到器件本体最小距离 {to_component.get('min_mm')}mm")
    if to_component.get("ok") and to_component.get("note"):
        res.warn(to_component["note"])
    res.source("内嵌设计快照 SILKSCREEN 图形 + 几何最短距离计算")
    return res


# --------------------------------------------------------------------------
# 6.7 Testpoint —— 测试点坐标
# --------------------------------------------------------------------------
_TESTPOINT_COLS = ["来源", "网络", "类型", "位号", "X_mm", "Y_mm", "直径mm",
                   "层", "参考数值", "原始记录"]


def _pack_rows(sample: List) -> str:
    """把 IPC-356 记录里的数字扫描拼成一行文本 (可能是 str, 统一按数格式化)。"""
    vals = []
    for v in sample or []:
        got = _num(v)
        vals.append(f"{got:g}" if got is not None else str(v))
    return " ".join(vals)


def cap_testpoint(ctx: Context) -> Result:
    cap = CATALOG_MAP["smt.testpoint"]
    ipc = ctx.exports.get("ipc356")
    ipc_ok = bool(ipc and ipc.get("ok"))

    # (a) IPC-356 测试文件
    rows: List[dict] = []
    ipc_points = list((ipc or {}).get("points") or []) if ipc_ok else []
    ipc_nets = dict((ipc or {}).get("nets") or {}) if ipc_ok else {}
    for pt in ipc_points[:5000]:
        if not isinstance(pt, dict):
            continue
        rows.append({
            "来源": f"IPC-356 ({ipc.get('source', '')})",
            "网络": pt.get("net", ""),
            "类型": "连通性测试点" if pt.get("type") == "continuity" else "绝缘测试点",
            "位号": pt.get("refdes", ""),
            "X_mm": None, "Y_mm": None, "直径mm": None, "层": "",
            "参考数值": _pack_rows(list(pt.get("nums") or [])[:6]),
            "原始记录": pt.get("tail", ""),
        })

    # (b) 快照焊盘 -> 测试点 (同一套判定: src/pads.py)
    pads_summary: dict = {"ok": False, "reason": "未读取焊盘 (快照或标定缺失)"}
    detected: dict = {"ok": False, "count": 0, "reason": "未读取焊盘"}
    coord_rows: List[dict] = []
    try:
        from src import pads as pads_mod

        pads_summary = pads_mod.extract_pads(ctx.snapshot, ctx.scale_mm_per_unit)
        detected = pads_mod.detect_test_points(pads_summary)
        doc = ctx.snapshot
        scale = ctx.scale_mm_per_unit
        if doc and scale:
            # extract_pads 只回汇总, 坐标明细用同一套判定函数逐条取出
            for entry in pads_mod._entries(doc):
                if not pads_mod._looks_like_pad(entry):
                    continue
                pad = pads_mod._circle_of(entry, scale)
                if pad:
                    coord_rows.append(pad)
    except Exception as exc:  # noqa: BLE001  焊盘模块不可用时只走 IPC-356 这条路
        pads_summary = {"ok": False, "reason": f"焊盘识别失败: {exc}"}
        detected = {"ok": False, "count": 0, "reason": str(exc)}
        coord_rows = []

    stencil = [p for p in coord_rows if p.get("stencil")]
    for pad in stencil:
        rows.append({
            "来源": "内嵌快照钢网层圆焊盘 (PASTEMASK/PASTE)",
            "网络": "", "类型": "测试点 (钢网层圆焊盘)", "位号": "",
            "X_mm": round(pad["x"], 4), "Y_mm": round(pad["y"], 4),
            "直径mm": round(pad["d"], 4), "层": pad.get("layer", ""),
            "参考数值": "", "原始记录": "",
        })

    if not ipc_ok and not detected.get("ok"):
        res = unavailable(
            cap,
            "两路都没有测试点数据: " + (pads_summary.get("reason")
                                       or "快照里没有焊盘图形")
            + (f"; IPC-356: {ipc.get('reason')}" if ipc and not ipc_ok else
               "; 也没有提供 IPC-356 测试文件"),
            "测试点要钢网层圆焊盘 (需 Gerber 或 Allegro 的焊盘坐标) "
            "或测试厂的 IPC-356 网表文件; 两者给一个即可")
        res.need("测试点清单 (网络 / 位号 / 坐标 / 直径)",
                 "让测试厂提供 IPC-D-356 (.ipc) 文件, 或用 Allegro: "
                 "File > Export > IPC356 导出后作为 --exports 传给本工具")
        res.source("无 IPC-356 / 快照无焊盘图形")
        return res

    res = Result(cap, PASS, "")
    res.add_all(rows, _TESTPOINT_COLS)

    with_coords = [r for r in rows if r["X_mm"] is not None and r["Y_mm"] is not None]
    ipc_count = len(ipc_points)
    pads_count = _int(detected.get("count"), 0)
    if ipc_ok:
        res.metric("IPC-356 网络数", len(ipc_nets), "个",
                   f"导入的 IPC-356 ({ipc.get('source', '')})", "high")
        res.metric("IPC-356 测试点记录", ipc_count, "条", "327/367 记录行", "high")
        res.metric("连通性测试点 (327)", sum(1 for p in ipc_points
                                            if p.get("type") == "continuity"), "条",
                   "IPC-356 记录类型")
        res.metric("绝缘测试点 (367)", sum(1 for p in ipc_points
                                           if p.get("type") == "isolation"), "条",
                   "IPC-356 记录类型")
        res.source(f"IPC-356 测试文件: {ipc.get('source', '')}")
    if pads_summary.get("ok"):
        res.metric("快照圆焊盘", pads_summary.get("pads"), "个",
                   pads_summary.get("source", "内嵌快照焊盘图形"), "medium")
        res.metric("其中钢网层圆焊盘", pads_summary.get("stencil_pads"), "个",
                   "subclass 含 PASTEMASK/PASTE", "medium")
        res.metric("焊盘直径范围",
                   f"{pads_summary.get('min_d_mm')} ~ {pads_summary.get('max_d_mm')}",
                   "mm", "快照 radius 字段")
    res.metric("有坐标的测试点", len(with_coords), "个", "钢网层圆焊盘坐标")

    if not with_coords:
        # 「有数量、无坐标」必须与「没有测试点」区分开
        res.status = WARN
        res.warn(f"有测试点数量、但没有可用坐标: IPC-356 记录 {ipc_count} 条 (记录里只有"
                 f"原始文本, 没有可校定的坐标字段), 快照钢网层圆焊盘 {pads_count} 个"
                 f" (没有逐个坐标明细); 「参考数值」列只是原始记录里的数字扫描, 不可当坐标用")
        res.need("测试点坐标 (X/Y, 用于排测试针床/工装)",
                 "钢网层圆焊盘的坐标需要 Gerber 或 Allegro 的焊盘坐标导出; "
                 "IPC-356 请用含坐标字段的完整格式 (定宽 X/Y 列) 导出")
    elif ipc_ok:
        res.warn("测试点坐标来自内嵌快照的钢网层圆焊盘; IPC-356 的记录只有原始文本, "
                 "两者数量不一致时以坐标明细为准")
    if ipc_ok and not coord_rows:
        res.need("每个测试点的坐标 (IPC-356 本次只解析出记录, 未得到坐标列)",
                 "用含 X/Y 定宽列的标准 IPC-D-356 导出, 或提供 Gerber 钢网层")
    if pads_summary.get("ok") and pads_count == 0:
        res.warn("快照里有圆焊盘, 但没有钢网层 (PASTEMASK/PASTE) 的圆焊盘, "
                 "按口径不算测试点")

    res.extra["ipc356_points"] = ipc_points[:500]
    res.extra["pad_rows"] = coord_rows[:2000]
    res.summary = (f"测试点: IPC-356 记录 {ipc_count} 条 / 快照钢网层圆焊盘 {pads_count} 个; "
                   f"有坐标 {len(with_coords)} 个"
                   + ("" if with_coords else " (有数量、无坐标)"))
    if not ipc_ok:
        res.source("仅内嵌快照焊盘图形 (无 IPC-356)")
    return res


# --------------------------------------------------------------------------
# 6.8 Variant Output —— 不同产品配置的装配数据
# --------------------------------------------------------------------------
def cap_variant(ctx: Context) -> Result:
    cap = CATALOG_MAP["smt.variant"]
    res = unavailable(
        cap,
        "产品变体 (哪些器件不贴 DNP / 换什么替代料) 只存在于 Allegro 的 "
        "Variant/Assembly 变体数据里, .brd 二进制与内嵌快照都读不到",
        "Allegro: Setup > Variants（或 Assembly Variant Manager）里导出变体报告，"
        "再把报告传给工具")
    res.need("变体清单（每个变体里 DNP/替代料的位号）",
             "Allegro: Setup > Variants（或 Assembly Variant Manager）里导出变体报告，"
             "再把报告传给工具")

    # 实事: 先出一份基础 BOM 作为比对基准
    comp = facts.components(ctx)
    base_rows: List[dict] = []
    if comp.get("ok"):
        for ref in sorted(comp.get("refdes") or [], key=_natural_key):
            base_rows.append({"位号": ref, "前缀": _refdes_prefix(ref) or "（无数字前缀）",
                              "类别": _category_of(ref), "是否贴装": "是（基础 BOM）",
                              "面别": "未知（需 Placement 导出）", "变体": "基础 BOM"})
        path = _write_csv(os.path.join(_smt_dir(ctx), "variant_base_bom.csv"),
                          ["位号", "前缀", "类别", "是否贴装", "面别", "变体"], base_rows)
        if path:
            res.artifact("csv", path, "基础 BOM（变体比对的基准，来自 objects/Part.xml）")
        else:
            res.warn("基础 BOM 写盘失败 (输出目录不可写)")
        res.metric("基础 BOM 位号数", len(base_rows), "个",
                   comp.get("source", "objects/Part.xml"), "high")
    else:
        res.need("基础 BOM（比对基准）: " + (comp.get("reason") or "BRD 无 objects/Part.xml"),
                 "提供真实 PCB 设计的 .brd (带 objects/Part.xml), "
                 "或 Allegro 导出的 BOM 报告")

    # 说清"有了变体报告以后本工具能比对出什么" —— 让用户知道补数据值不值
    res.add_all([
        {"比对项": "DNP（不贴）位号",
         "能得出什么": "变体报告里标记为不贴的位号清单, 与基础 BOM 的差集",
         "需要的字段": "变体名 / 位号 / 是否贴装 (DNP)"},
        {"比对项": "替代料（换料）",
         "能得出什么": "同一位置在不同变体里 MPN/Value 不同的清单, 及影响的贴装工位",
         "需要的字段": "变体名 / 位号 / 替代 MPN 或 Value"},
        {"比对项": "数量差异",
         "能得出什么": "各变体贴装数量与基础 BOM 数量的增减 (含差异占比)",
         "需要的字段": "变体名 / 位号 / 数量"},
        {"比对项": "只在该变体出现的位号",
         "能得出什么": "变体报告里有、基础 BOM 里没有的位号 (选配器件)",
         "需要的字段": "变体名 / 位号"},
        {"比对项": "按变体出贴装清单",
         "能得出什么": "有了坐标文件后, 按变体生成 位号/坐标/面别/旋转 的贴装表",
         "需要的字段": "变体清单 + Placement 坐标文件"},
    ], ["比对项", "能得出什么", "需要的字段"])

    res.extra["base_bom_rows"] = len(base_rows)
    res.extra["variant_report_expects"] = ["变体名", "位号", "是否贴装(DNP)",
                                           "替代料 MPN/Value", "数量"]
    res.summary = ("变体数据读不到 (需 Allegro Variant 导出); "
                   + (f"已输出基础 BOM {len(base_rows)} 个位号作为比对基准"
                      if base_rows else "也没有 Part.xml, 连比对基准都缺"))
    if base_rows:
        res.source("objects/Part.xml (基础 BOM 基准) / 无 Allegro 变体数据")
    else:
        res.source("objects/Part.xml (缺失) / 无 Allegro 变体数据")
    return res


# --------------------------------------------------------------------------
# 能力注册表 (category = assembly)
# --------------------------------------------------------------------------
CATALOG: List[Capability] = [
    Capability("smt.placement", "assembly", "Component Placement", "元件贴装坐标",
               "器件 XY 坐标明细, 并校验板框外器件、坐标重复、坐标缺失",
               cap_placement, "export",
               ["Placement 导出 place_txt.txt 或 SKILL evan_components.csv", "objects/Part.xml"],
               ["placement.csv", "坐标校验结论"]),
    Capability("smt.rotation", "assembly", "Rotation", "元件旋转角度",
               "旋转角按 0/90/180/270 归桶, 非正交角单列并要求 SMT 确认",
               cap_rotation, "export",
               ["Placement/SKILL 坐标文件的 ROTATION 列"],
               ["rotation.csv", "角度分布"]),
    Capability("smt.refdes", "assembly", "Reference Designator", "元件位号清单",
               "位号清单 (位号/前缀/类别) 自然序输出, 并与坐标文件核对",
               cap_refdes, "brd", ["objects/Part.xml"], ["refdes.csv", "前缀/类别汇总"]),
    Capability("smt.bom", "assembly", "BOM Export", "物料清单 (贴装视角)",
               "按位号前缀/类别/面别分组统计贴装数量; Value/Footprint/MPN 需 BOM 报告",
               cap_bom, "brd",
               ["objects/Part.xml", "Placement 坐标文件的 side 列", "Allegro BOM 报告"],
               ["bom.csv", "分组数量汇总"]),
    Capability("smt.assembly_drawing", "assembly", "Assembly Drawing", "装配图",
               "从快照装配/丝印图形生成 mm 坐标系 SVG (Y 轴翻转, 2mm 边距)",
               cap_assembly_drawing, "snapshot",
               ["内嵌快照 ASSEMBLY / SILKSCREEN / BOARD GEOMETRY 图形"],
               ["assembly_drawing.svg"]),
    Capability("smt.silkscreen", "assembly", "Silkscreen", "丝印信息",
               "丝印段数、线宽、到板框与器件本体的最小距离",
               cap_silkscreen, "snapshot",
               ["内嵌快照 SILKSCREEN 图形", "BOARD GEOMETRY/OUTLINE", "器件本体轮廓"],
               ["丝印指标与分层明细"]),
    Capability("smt.testpoint", "assembly", "Testpoint", "测试点坐标",
               "测试点清单与坐标: IPC-356 测试文件 或 钢网层圆焊盘",
               cap_testpoint, "export",
               ["IPC-356 (.ipc) 测试文件", "内嵌快照钢网层圆焊盘 (PAD/PASTEMASK)"],
               ["测试点清单", "坐标/数量区分说明"]),
    Capability("smt.variant", "assembly", "Variant Output", "产品变体装配数据",
               "不同产品配置 (DNP/替代料) 的装配数据; 只能由 Allegro Variant 导出",
               cap_variant, "external",
               ["Allegro Variant/Assembly Variant 报告", "objects/Part.xml (比对基准)"],
               ["variant_base_bom.csv", "可比对内容说明"]),
]
CATALOG_MAP: Dict[str, Capability] = {c.id: c for c in CATALOG}

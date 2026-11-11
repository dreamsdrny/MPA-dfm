"""第 4 类: 制造文件输出 (Manufacturing Output)。

对应 PCB Editor 的 Manufacture 菜单: Artwork (Gerber) / NC Drill / NC Route /
IPC-2581 / ODB++ / IPC-D-356 / Pick & Place / Bill of Materials。

这一类的纪律比其它类别更硬, 写代码前先把话说清楚:

  * **能真生成的必须真生成文件** —— Gerber、Excellon 钻孔、铣外形路径、
    Pick & Place、BOM 都是真的写盘, 不是"你自己去 Allegro 里导一下";
  * **生成不了的必须给出 Allegro 导出方案** —— IPC-2581 / ODB++ 少了 Allegro
    授权就产不出合法文件, 这时状态是 `unavailable`, 同时给出菜单路径/命令和一张
    "导出准备度清单"(逐项说清 .brd 里读得到什么、还缺什么);
  * **缺数据 != 通过** —— 坐标标定不可信 (快照坐标是经验系数换算的) 时绝不输出
    Gerber/铣外形路径, 因为尺寸一定是错的; 没有孔坐标时只出参数表并写清缺什么;
  * 任何"从内嵌快照反推"的文件都在 warning 里写明: 只能用于检查/预览/沟通,
    正式投产必须用 Allegro 自己的 Manufacture 导出。

坐标系与格式口径 (全模块统一):
  * Gerber: RS-274X, `%FSLAX46Y46*%` + `%MOMM*%`, 坐标 = mm x 1e6 取整;
  * Excellon: `METRIC,TZ`, 坐标 mm 保留 3 位小数 (带符号);
  * 只画快照里真实存在的图形, 不补线、不补焊盘、不猜网络。
"""
import csv
import math
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from src.brd_analysis import facts
from src.brd_analysis.context import Context
from src.brd_analysis.model import (INFO, PASS, WARN, Capability, Result,
                                    unavailable)

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
#: 快照里可反推成 Gerber 的图形类别 (与 geom.classify 的取值一致)
_KINDS: Tuple[str, ...] = ("etch", "outline", "silk", "mask", "paste")

#: 类别 -> 文件名里的英文标签, 例如 ETCH_TOP.gbr / SILKSCREEN_TOP.gbr
_KIND_FILE: Dict[str, str] = {
    "etch": "ETCH", "outline": "OUTLINE", "silk": "SILKSCREEN",
    "mask": "SOLDERMASK", "paste": "PASTEMASK",
}

#: 类别中文名 (结论与明细表里用)
_KIND_CN: Dict[str, str] = {
    "etch": "铜箔/走线", "outline": "板框", "silk": "丝印",
    "mask": "阻焊", "paste": "锡膏",
}

#: 没有任何线宽信息时使用的孔径 (mm)。真实线宽未知, 取一个常见最小线宽。
_MIN_APERTURE_MM = 0.10

#: 铣外形路径的默认铣刀直径 (mm)。**模块默认值**, 必须与板厂确认。
_ROUTE_TOOL_MM = 2.00

#: Gerber 4.6 格式: 坐标 = mm x 1e6
_GERBER_SCALE = 1000000

#: 孔坐标出界的判定容差 (mm), 避免刚好压在板边的器件被误判
_OUTSIDE_TOL_MM = 0.05

#: IPC-D-356 记录条数上限 (防止异常大的网表把内存/文件撑爆)
_IPC356_MAX_POINTS = 200000

# --------------------------------------------------------------------------
# IPC-D-356A 常用列位 (手工实现, 上机前必须用测试厂的校验工具过一遍)
#   列 1-3    记录类型: 317 = 网络名, 327 = 连通性测试点
#   列 4      空格
#   列 5-19   网络名 (左对齐, 超长截断)
#   列 20     空格
#   列 21-27  位号 (327 记录, 左对齐)
#   列 28     "-" (位号与引脚的分隔)
#   列 29-32  引脚号 (327 记录, 左对齐)
#   列 33     空格
#   列 34-42  X 坐标 (mm, 保留 3 位小数, 带符号, 右对齐)
#   列 43     空格
#   列 44-52  Y 坐标 (同上)
#   列 53     空格
#   列 54-56  面别 (TOP/BOT)
# --------------------------------------------------------------------------
_IPC356_NET_W = 15
_IPC356_REF_W = 7
_IPC356_PIN_W = 4
_IPC356_XY_W = 9
_IPC356_SIDE_W = 3

#: 位号前缀 -> 类别。口径与 src/brd_components.py 的 _PREFIX_CATEGORY 保持一致,
#: 但本模块自带一份, 免得依赖那边的私有名字。
_CATEGORY_OF_PREFIX: Dict[str, str] = {
    "R": "电阻", "RN": "电阻排", "RP": "电阻排", "VR": "可调电阻",
    "C": "电容", "CP": "钽电容",
    "L": "电感", "FB": "磁珠", "F": "保险丝", "T": "变压器",
    "U": "IC芯片", "IC": "IC芯片", "Q": "三极管/MOS管",
    "D": "二极管", "LED": "发光二极管",
    "X": "晶振", "Y": "晶振", "OSC": "晶振",
    "J": "连接器/接插件", "P": "连接器/接插件",
    "CN": "连接器/接插件", "CON": "连接器/接插件",
    "SW": "开关", "K": "继电器", "S": "开关",
    "TP": "测试点", "MH": "安装孔", "H": "安装孔", "FH": "安装孔",
    "B": "电池", "BT": "电池", "BZ": "蜂鸣器", "M": "电机", "MOV": "压敏电阻",
}

#: 工艺报告里常见的物料列名 (用来判断导入的 BOM 报告能不能补上 Value/MPN)
_VALUE_COLS = ("VALUE", "VAL", "阻值", "容值", "规格")
_MPN_COLS = ("MPN", "PART NUMBER", "PART_NUMBER", "PARTNUM", "MANUFACTURER PART", "型号", "料号")
_FOOTPRINT_COLS = ("FOOTPRINT", "PACKAGE", "DECAL", "封装")
_REFDES_COLS = ("REFDES", "REF DES", "DESIGNATOR", "COMPONENT", "SYMBOL", "位号")
_QTY_COLS = ("QUANTITY", "QTY", "COUNT", "数量")


# --------------------------------------------------------------------------
# 通用小工具 (产物目录 / 文件写出 / 命名)
# --------------------------------------------------------------------------
def _target_dir(ctx: Context) -> str:
    """产物目录: 优先 ctx.out_dir; 否则用 .brd 所在目录。

    只有 .brd 真实存在时才从它派生目录 —— 路径不存在还往它旁边写文件, 只会在
    磁盘根目录里留垃圾。目录建不出来时返回空串, 调用方据此跳过写文件并如实说明。
    """
    if ctx.out_dir:
        target = ctx.out_dir
    elif ctx.brd_path and os.path.exists(ctx.brd_path):
        target = os.path.dirname(ctx.brd_path)
    else:
        return ""
    try:
        os.makedirs(target, exist_ok=True)
    except OSError:
        return ""
    return target


def _write_text(path: str, text: str) -> Optional[str]:
    """写文本文件 (UTF-8, LF 换行); 成功返回 None, 失败返回原因。"""
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return None
    except OSError as exc:
        return f"{os.path.basename(path)} 写入失败: {exc}"


def _write_csv(path: str, columns: Sequence[str], rows: Sequence[dict]) -> Optional[str]:
    """写 CSV (UTF-8); 成功返回 None, 失败返回原因。"""
    try:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns),
                                    extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: ("" if row.get(key) is None else row.get(key))
                                 for key in columns})
        return None
    except OSError as exc:
        return f"{os.path.basename(path)} 写入失败: {exc}"


def _safe_name(text: str, fallback: str) -> str:
    """把板名/层名清成能当文件名的标签 (保留中文与字母数字)。"""
    name = re.sub(r"[^\w\-.]+", "_", str(text or "").strip(), flags=re.UNICODE)
    name = name.strip("_.") or fallback
    return name[:60]


def _board_tag(ctx: Context) -> str:
    return _safe_name(ctx.board_name, "BOARD")


def _layer_tag(layer: str) -> str:
    """层名 -> 文件名标签: 'PACKAGE GEOMETRY/SILKSCREEN_TOP' -> 'SILKSCREEN_TOP'。"""
    name = str(layer or "").split("/")[-1].strip() or str(layer or "")
    tag = re.sub(r"[^0-9A-Za-z_\-\+]", "_", name.upper())
    tag = re.sub(r"_+", "_", tag).strip("_")
    return tag or "LAYER"


def _unique_name(base: str, used: set) -> str:
    """同名文件加 _2/_3 后缀, 保证一层一个文件不互相覆盖。"""
    if base not in used:
        used.add(base)
        return base
    stem, ext = os.path.splitext(base)
    index = 2
    while f"{stem}_{index}{ext}" in used:
        index += 1
    name = f"{stem}_{index}{ext}"
    used.add(name)
    return name


def _gerber_filename(board: str, kind: str, layer: str, used: set) -> str:
    """Gerber 文件名: <board>_ETCH_TOP.gbr / <board>_SILKSCREEN_TOP.gbr / <board>_OUTLINE.gbr。

    层名里常常已经带了类别 (PACKAGE GEOMETRY/SILKSCREEN_TOP), 这时不再重复前缀,
    免得出现 SILKSCREEN_SILKSCREEN_TOP 这种名字。
    """
    label = _KIND_FILE[kind]
    if kind == "outline":
        base = f"{board}_OUTLINE.gbr"
    else:
        tag = _layer_tag(layer)
        if tag == label or tag.startswith(label + "_"):
            base = f"{board}_{tag}.gbr"
        else:
            base = f"{board}_{label}_{tag}.gbr"
    return _unique_name(base, used)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _num(value) -> Optional[float]:
    """尽力转 float; 转不了返回 None (不拿 0 充数)。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _natural_key(refdes: str):
    """位号自然排序: R2 排在 R10 前面。"""
    key = []
    for part in re.split(r"(\d+)", refdes or ""):
        if part.isdigit():
            key.append((1, int(part), ""))
        elif part:
            key.append((0, 0, part.upper()))
    return key


def _prefix_of(refdes: str) -> str:
    """R3438 -> R; FB44 -> FB; 没有数字时返回空串。"""
    match = re.match(r"^([A-Za-z]+)\d", refdes or "")
    return match.group(1).upper() if match else ""


def _pad(text: str, width: int) -> str:
    """左对齐定宽 (超长截断), 用于 IPC-D-356 的定宽列。"""
    value = str(text or "")
    return value[:width].ljust(width)


def _pick_column(row: dict, hints: Sequence[str]) -> str:
    """在导入报告的一行里, 按关键字找到对应列的值 (找不到返回空串)。"""
    for key, value in row.items():
        up = str(key or "").upper()
        for hint in hints:
            if hint.upper() in up:
                return str(value or "").strip()
    return ""


# --------------------------------------------------------------------------
# Gerber (RS-274X) 生成器
# --------------------------------------------------------------------------
def _gc(value: float) -> str:
    """毫米 -> Gerber 4.6 坐标整数文本 (mm x 1e6 四舍五入)。"""
    return str(int(round(float(value) * _GERBER_SCALE)))


def _circumcenter(p1, p2, p3) -> Optional[Tuple[float, float]]:
    """三点外接圆心; 三点共线时返回 None。"""
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    det = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(det) < 1e-12:
        return None
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / det
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / det
    return (ux, uy)


def _arc_geometry(pts: List[Tuple[float, float]]) -> Optional[dict]:
    """从快照圆弧的折线取样点还原圆心/方向。

    快照圆弧经过 geom.normalize 之后只剩 9 个取样点 (没有圆心字段), 这里用
    "起点 + 中点 + 终点" 三点定圆求圆心, 再用 **起点->圆心->终点** 的向量叉积
    判定方向:
        叉积 > 0 -> 逆时针 (Gerber G03)
        叉积 < 0 -> 顺时针 (Gerber G02)
    三点不在同一圆上 (说明这条"圆弧"其实是折线) 时返回 None, 由调用方退回折线。
    """
    if len(pts) < 3:
        return None
    start, mid, end = pts[0], pts[len(pts) // 2], pts[-1]
    center = _circumcenter(start, mid, end)
    if center is None:
        return None
    cx, cy = center
    sx, sy = start
    ex, ey = end
    radius = math.hypot(sx - cx, sy - cy)
    if radius <= 1e-9:
        return None
    if abs(math.hypot(mid[0] - cx, mid[1] - cy) - radius) > max(0.002, radius * 0.02):
        return None     # 取样点不在同一个圆上: 不是圆弧
    basis = "起点->圆心->终点叉积"
    cross = (sx - cx) * (ey - cy) - (sy - cy) * (ex - cx)
    if math.hypot(ex - sx, ey - sy) <= 1e-9 or abs(cross) < 1e-9:
        quarter = pts[len(pts) // 4]
        cross = (sx - cx) * (quarter[1] - cy) - (sy - cy) * (quarter[0] - cx)
        basis = "起终点重合, 用起点->圆心->1/4 点叉积"
    if abs(cross) < 1e-9:
        return None
    return {"center": (cx, cy), "start": (sx, sy), "end": (ex, ey),
            "radius": round(radius, 5), "ccw": cross > 0,
            "cross": round(cross, 6), "basis": basis}


def _emit_polyline(out: List[str], pts: List[Tuple[float, float]]) -> int:
    """按 "先 D02 定位, 再逐点 D01" 输出折线; 返回真正画出的线段数。"""
    out.append(f"X{_gc(pts[0][0])}Y{_gc(pts[0][1])}D02*")
    drawn = 0
    previous = pts[0]
    for point in pts[1:]:
        if abs(point[0] - previous[0]) < 1e-9 and abs(point[1] - previous[1]) < 1e-9:
            continue    # 零长度段: 不产生 D01
        out.append(f"X{_gc(point[0])}Y{_gc(point[1])}D01*")
        previous = point
        drawn += 1
    return drawn


def _build_gerber(board: str, kind: str, layer: str, segs: List[dict]) -> dict:
    """把一层的一组毫米线段渲染成 RS-274X 文本。

    返回 {text, apertures, lines, arcs, zero_width, warnings}
      apertures  [(D 码, 孔径 mm)]  D10 起递增
      lines/arcs 真正输出的直线段数 / 圆弧数
      zero_width 没有线宽字段的图形条数 (已按最小孔径输出)
    """
    warnings: List[str] = []
    usable = [s for s in segs if len(s.get("pts") or []) >= 2]
    widths = sorted({round(float(s["width_mm"]), 4) for s in usable
                     if s.get("has_width") and float(s["width_mm"]) > 0})
    zero_width = [s for s in usable
                  if not (s.get("has_width") and float(s["width_mm"]) > 0)]
    if not widths and any(not s.get("flash") for s in usable):
        widths = [_MIN_APERTURE_MM]
    aperture_of: Dict[float, int] = {w: 10 + i for i, w in enumerate(widths)}
    fallback_w = widths[0] if widths else _MIN_APERTURE_MM
    fallback_code = aperture_of.get(fallback_w)
    if zero_width:
        warnings.append(
            f"{len(zero_width)} 条图形在快照里没有线宽字段 (width=0), 已按最小孔径 "
            f"{fallback_w:.3f}mm 输出; 这些线的真实宽度未知, 不能当设计线宽")
    if len(widths) > 40:
        warnings.append(f"这层有 {len(widths)} 种线宽, 生成了同样多的圆孔径, "
                        f"请确认快照线宽字段没有被异常值污染")

    # 圆形图案 (焊盘/圆盘) 用 D03 曝光: 它的"孔径"是直径, 和走线线宽是两套数,
    # 所以单独编号, 从走线孔径之后接着排。没有这一段, 阻焊/钢网层会一条图形都输不出。
    flashes = [s for s in usable
               if s.get("flash") and float(s.get("diameter_mm") or 0) > 0]
    flash_diams = sorted({round(float(s["diameter_mm"]), 4) for s in flashes})
    next_code = 10 + len(widths)
    flash_code_of: Dict[float, int] = {d: next_code + i
                                       for i, d in enumerate(flash_diams)}
    no_diameter = [s for s in usable if s.get("flash")
                   and float(s.get("diameter_mm") or 0) <= 0]
    if no_diameter:
        warnings.append(f"{len(no_diameter)} 个圆形图案没有半径字段, 无法输出 (已跳过)")

    out: List[str] = []
    out.append(f"G04 brd_extract {board} {kind} {layer} from BRD snapshot*")
    out.append("G04 preview/check only; use Allegro Manufacture > Artwork for production*")
    out.append(f"G04 generated {_now()}*")
    out.append("%FSLAX46Y46*%")
    out.append("%MOMM*%")
    out.append("%LPD*%")
    out.append("G01*")
    for width in widths:                 # D10 起递增的圆孔径定义
        out.append(f"%ADD{aperture_of[width]}C,{width:.6f}*%")
    for dia in flash_diams:              # 圆形焊盘孔径 (D03 用)
        out.append(f"%ADD{flash_code_of[dia]}C,{dia:.6f}*%")

    current_code = None
    line_count = 0
    arc_count = 0
    flash_count = 0
    arc_fallback = 0
    for seg in usable:
        if seg.get("flash"):
            dia = round(float(seg.get("diameter_mm") or 0.0), 4)
            code = flash_code_of.get(dia)
            if code is None:
                continue
            if code != current_code:
                out.append(f"D{code}*")
                current_code = code
            fx, fy = seg["pts"][0]
            out.append(f"X{_gc(fx)}Y{_gc(fy)}D03*")
            flash_count += 1
            continue
        width = (_num(seg.get("width_mm")) or 0.0) if seg.get("has_width") else 0.0
        if width <= 0:
            code = fallback_code
        else:
            code = aperture_of.get(round(width, 4), fallback_code)
        if code != current_code:         # 换孔径时显式选刀, 不依赖模态状态
            out.append(f"D{code}*")
            current_code = code

        pts = [(round(float(x), 6), round(float(y), 6)) for x, y in seg["pts"]]
        if seg.get("arc"):
            arc = _arc_geometry(pts)
            if arc is None:
                arc_fallback += 1
                line_count += _emit_polyline(out, pts)
                continue
            (sx, sy) = arc["start"]
            (ex, ey) = arc["end"]
            (cx, cy) = arc["center"]
            out.append(f"X{_gc(sx)}Y{_gc(sy)}D02*")     # 定位到弧起点
            out.append("G75*")                          # 多象限圆弧模式
            gcode = "G03" if arc["ccw"] else "G02"      # 叉积定方向
            # I/J 是"起点 -> 圆心"的相对增量 (mm x 1e6)
            out.append(f"{gcode}X{_gc(ex)}Y{_gc(ey)}"
                       f"I{_gc(cx - sx)}J{_gc(cy - sy)}D01*")
            arc_count += 1
        else:
            line_count += _emit_polyline(out, pts)
    out.append("M02*")
    if arc_fallback:
        warnings.append(f"{arc_fallback} 条圆弧无法定出圆心/方向, 已退回折线近似")
    return {"text": "\n".join(out) + "\n",
            "apertures": ([(aperture_of[w], w) for w in widths]
                          + [(flash_code_of[d], d) for d in flash_diams]),
            "lines": line_count, "arcs": arc_count, "flashes": flash_count,
            "zero_width": len(zero_width), "warnings": warnings}


def _gerber_groups(ctx: Context) -> List[Tuple[str, str, List[dict]]]:
    """按 (类别, 层) 分组: 每层一个 Gerber 文件。"""
    groups: List[Tuple[str, str, List[dict]]] = []
    for kind in _KINDS:
        by_layer: Dict[str, List[dict]] = {}
        for seg in ctx.segments([kind]):
            by_layer.setdefault(str(seg.get("layer") or "?"), []).append(seg)
        for layer in sorted(by_layer):
            groups.append((kind, layer, by_layer[layer]))
    return groups


# --------------------------------------------------------------------------
# Excellon / NC Route 生成器
# --------------------------------------------------------------------------
def _xy(value: float) -> str:
    """Excellon 坐标: 毫米保留 3 位小数, 负号照写。"""
    return f"{float(value):.3f}"


def _excellon_content(board: str, groups: List[Tuple[int, float, List[dict]]],
                      title: str) -> str:
    """生成 Excellon 钻孔文件文本。

    groups = [(刀号, 孔径 mm, 该刀的孔 [{x_mm, y_mm}]), ...]
    """
    out = [f"; brd_extract {title}",
           f"; 板名: {board}  生成时间: {_now()}",
           "; 由导入的孔坐标文件反推; Excellon 不区分镀覆, 请按参数表分别出 PTH/NPTH",
           "; 正式投产请用 Allegro Manufacture > NC > NC Drill",
           "M48", "METRIC,TZ"]
    for tool, size, _holes in groups:
        out.append(f"T{tool}C{size:.3f}")
    out.append("%")
    for tool, _size, holes in groups:
        out.append(f"T{tool}")
        for hole in holes:
            out.append(f"X{_xy(hole['x_mm'])}Y{_xy(hole['y_mm'])}")
    out.append("M30")
    return "\n".join(out) + "\n"


def _route_content(board: str, lines: List[List[Tuple[float, float]]],
                   tool_mm: float) -> str:
    """生成铣外形路径 (Excellon route 风格)。"""
    out = [f"; brd_extract NC Route {board}",
           f"; 生成时间: {_now()}",
           f"; 铣刀直径 {tool_mm:.3f}mm 是模块默认值, 必须按板厂实际铣刀确认",
           "; 轮廓为板边名义线, 未做刀具半径补偿 (板厂通常自行补偿)",
           "M48", "METRIC,TZ", f"T1C{tool_mm:.3f}", "%", "T1"]
    for line in lines:
        pts = [(round(float(x), 3), round(float(y), 3)) for x, y in line]
        out.append(f"G00X{_xy(pts[0][0])}Y{_xy(pts[0][1])}")
        out.append("M15")                       # 下刀
        for point in pts[1:]:
            out.append(f"G01X{_xy(point[0])}Y{_xy(point[1])}")
        out.append("M16")                       # 抬刀
    out.append("M30")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# 4.1 Artwork (Gerber RS-274X)
# --------------------------------------------------------------------------
def cap_artwork(ctx: Context) -> Result:
    cap = CATALOG_MAP["mfg.artwork"]
    if not ctx.has_snapshot:
        return unavailable(cap, "该 .brd 没有可解析的内嵌设计快照, 反推不出任何图形",
                           "确认 .brd 是真实设计且内嵌 design.comps 快照完整; "
                           "或直接用 Allegro: Manufacture > Artwork 导出 Gerber")
    if not ctx.has_scale:
        return unavailable(cap, "板框标定失败 (快照坐标换算不成 mm), 无法生成 Gerber",
                           "在 Allegro 里重存一次 .brd 让 Draw Size 写入, "
                           "或用 Manufacture > Artwork 导出 Gerber")
    if not ctx.scale_confident:
        # 安全底线: 经验系数换算出来的坐标, 生成的 Gerber 尺寸一定是错的。
        return unavailable(
            cap,
            "坐标标定不可信 (未取到 .brd 的 Draw Size, 快照坐标是用经验系数换算的), "
            "生成的 Gerber 尺寸会是错的, 已拒绝生成",
            "Allegro: 打开设计另存一次让 Draw Size 写入 .brd, 再重新分析; "
            "正式投产请用 Allegro: Manufacture > Artwork 导出 Gerber")

    groups = _gerber_groups(ctx)
    if not groups:
        return unavailable(cap, "内嵌快照里没有 ETCH/OUTLINE/SILK/MASK/PASTE 图形, "
                                "没有内容可以生成 Gerber",
                           "确认设计快照导出包含这些层; 或直接用 Allegro 的 Artwork 导出")

    out_dir = _target_dir(ctx)
    board = _board_tag(ctx)
    used: set = set()
    res = Result(cap, PASS, "")
    total_segs = total_lines = total_arcs = total_zero = total_flash = 0
    written = 0
    files: List[dict] = []
    for kind, layer, segs in groups:
        built = _build_gerber(board, _KIND_FILE[kind], layer, segs)
        name = _gerber_filename(board, kind, layer, used)
        path = os.path.join(out_dir, name) if out_dir else ""
        note = (f"{_KIND_CN[kind]} {len(segs)} 条图形, "
                f"直线 {built['lines']} 段 / 圆弧 {built['arcs']} 段 / "
                f"闪盘 {built['flashes']} 个, "
                f"孔径 {len(built['apertures'])} 种")
        if out_dir:
            error = _write_text(path, built["text"])
            if error:
                res.warn(error)
                path = ""
            else:
                res.artifact("gerber", path, note)
                written += 1
        else:
            path = ""
            res.warn("没有可写的输出目录 (既没给 out_dir, .brd 也不存在), "
                     "Gerber 未落盘")
        for warning in built["warnings"]:
            res.warn(f"{_KIND_FILE[kind]}_{_layer_tag(layer)}: {warning}")
        total_segs += len(segs)
        total_lines += built["lines"]
        total_arcs += built["arcs"]
        total_flash += built["flashes"]
        total_zero += built["zero_width"]
        files.append({"层类型": _KIND_CN[kind], "层名": layer, "图形数": len(segs),
                      "直线段": built["lines"], "圆弧": built["arcs"],
                      "闪盘": built["flashes"],
                      "孔径数": len(built["apertures"]), "文件": os.path.basename(path)})
    res.add_all(files, ["层类型", "层名", "图形数", "直线段", "圆弧", "闪盘", "孔径数",
                        "文件"])
    res.columns = ["层类型", "层名", "图形数", "直线段", "圆弧", "闪盘", "孔径数", "文件"]

    res.metric("Gerber 文件数", written, "个", "内嵌快照按层分组")
    res.metric("图形总数", total_segs, "条", "内嵌快照几何")
    res.metric("直线段", total_lines, "段", "RS-274X D01")
    res.metric("圆弧", total_arcs, "段", "G75 + G02/G03 (叉积定方向)", "medium")
    res.metric("圆形闪盘 (D03)", total_flash, "个",
               "快照 radius 字段, 按直径出圆孔径", "medium")
    if total_zero:
        res.metric("无宽度图形", total_zero, "条", "快照 width=0, 已按最小孔径输出", "low")
    res.metric("圆弧方向判定依据",
               "起点->圆心->终点向量叉积: >0 逆时针 G03, <0 顺时针 G02",
               "", "模块实现口径")
    res.summary = (f"从设计快照反推生成 {written} 个 Gerber 文件, 共 {total_segs} 条图形 "
                   f"({total_lines} 段直线 / {total_arcs} 段圆弧 / {total_flash} 个圆形闪盘)"
                   + (f"; {total_zero} 条图形没有线宽, 按最小孔径输出" if total_zero else ""))
    res.warn("这是从设计快照反推的 Gerber, 用于检查/预览/沟通; 正式投产请用 Allegro 的 "
             "Artwork 导出 (Manufacture > Artwork)")
    res.warn("快照只有轮廓线 + 圆形图案: 铜皮填充、矩形/异形焊盘仍然缺失, 所以这不是"
             "完整的生产数据 (不能直接下单)")
    res.warn("阻焊/锡膏层用的是快照轮廓, 不是真实开窗; 钻孔要用 NC Drill 单独出, "
             "不在 Gerber 里")
    if out_dir:
        res.source(f"内嵌设计快照几何 (Draw Size 标定, scale={ctx.scale_mm_per_unit})")
    res.source("输出目录: " + (out_dir or "(无)"))
    if total_zero or not written:
        # 有线宽是补出来的 / 一个文件都没落盘: 都不能算"没问题"
        res.status = WARN
    return res


# --------------------------------------------------------------------------
# 4.2 NC Drill
# --------------------------------------------------------------------------
def cap_nc_drill(ctx: Context) -> Result:
    cap = CATALOG_MAP["mfg.nc_drill"]
    table = facts.drill_by_size(ctx)
    holes = list(ctx.exports.get("holes") or [])
    if not table and not holes:
        return unavailable(cap, ".brd 里没有可用的钻孔表 (DRILL CHART), 也没有导入的孔坐标文件",
                           ctx.howto_holes_xy())

    # 按孔径分刀 (钻孔坐标)
    by_size: Dict[float, List[dict]] = {}
    bad_coord = 0
    for hole in holes:
        x = _num(hole.get("x_mm"))
        y = _num(hole.get("y_mm"))
        size = _num(hole.get("drill_mm"))
        if x is None or y is None or size is None or size <= 0:
            bad_coord += 1
            continue
        by_size.setdefault(round(size, 3), []).append({"x_mm": x, "y_mm": y})

    # 孔坐标必须在板内。这条检查是必要的: 孔坐标文件的单位/原点一旦搞错
    # (例如文件头写 METRIC 而数值是 mil), 生成的 Excellon 会把每个孔都
    # 扔到板外, 而"孔径/数量"看上去完全正常 —— 只靠参数表根本发现不了。
    area = facts.region_areas(ctx)
    outside: List[str] = []
    if area.get("ok") and by_size:
        min_x, min_y, max_x, max_y = area["bbox_mm"]
        pad = 0.5                       # 允许半个毫米的边界外扩, 避免浮点擦边
        for size, items in by_size.items():
            for item in items:
                if not (min_x - pad <= item["x_mm"] <= max_x + pad
                        and min_y - pad <= item["y_mm"] <= max_y + pad):
                    outside.append(f"Ø{size:.3f}@({item['x_mm']:.2f}, {item['y_mm']:.2f})")

    out_dir = _target_dir(ctx)
    board = _board_tag(ctx)
    res = Result(cap, INFO, "")

    # (a) 参数表: 孔径 / 镀覆 / 数量 / 跨度 —— 这张表永远能报
    coord_count = {size: len(items) for size, items in by_size.items()}
    sizes = sorted(set([float(r["size_mm"]) for r in table]) | set(by_size))
    rows: List[dict] = []
    for size in sizes:
        row = next((r for r in table if abs(float(r["size_mm"]) - size) < 1e-6), None)
        rows.append({
            "孔径mm": size,
            "镀覆": (row or {}).get("plating", "未知"),
            "数量": (row or {}).get("qty", ""),
            "跨度": (row or {}).get("spans", ""),
            "坐标孔数": coord_count.get(size, 0),
            "来源": "二进制 DRILL CHART" if row else "仅导入孔坐标",
        })
    res.add_all(rows, ["孔径mm", "镀覆", "数量", "跨度", "坐标孔数", "来源"])
    res.columns = ["孔径mm", "镀覆", "数量", "跨度", "坐标孔数", "来源"]
    res.metric("孔径种类", len(sizes), "种", "二进制 DRILL CHART + 导入孔坐标")
    total_qty = sum(int(r["数量"]) for r in rows
                    if isinstance(r.get("数量"), (int, float)))
    if total_qty:
        res.metric("钻孔总数", total_qty, "个", "二进制 DRILL CHART 数量列")
    res.metric("有坐标的孔", sum(coord_count.values()), "个", "导入的 Excellon/SKILL 孔坐标")

    # 参数表 CSV
    if out_dir:
        path = os.path.join(out_dir, f"{board}_DRILL_TABLE.csv")
        error = _write_csv(path, res.columns, rows)
        if error:
            res.warn(error)
        else:
            res.artifact("csv", path, "钻孔参数表 (孔径/镀覆/数量/跨度)")
    else:
        res.warn("没有可写的输出目录, 钻孔参数表未落盘")

    # (b) 真实 Excellon: 只有拿到孔坐标才可能
    if not by_size:
        res.summary = (f"{len(sizes)} 种孔径; 只有参数表, 没有孔坐标, 无法生成 Excellon")
        res.need("每个孔的坐标", ctx.howto_holes_xy())
        if bad_coord:
            res.warn(f"{bad_coord} 条孔记录缺坐标或缺孔径, 已跳过")
        res.source("二进制 DRILL CHART")
        return res

    groups = [(index + 1, size, by_size[size]) for index, size in enumerate(sorted(by_size))]
    written = 0
    if out_dir:
        combined = os.path.join(out_dir, f"{board}_DRILL.drl")
        error = _write_text(combined, _excellon_content(board, groups, "NC Drill 钻孔文件"))
        if error:
            res.warn(error)
        else:
            res.artifact("excellon", combined,
                         f"{len(groups)} 把刀 / {sum(coord_count.values())} 个孔 (全部孔径合并)")
            written += 1
        for tool, size, items in groups:      # 再按刀各出一份 (Allegro 的习惯做法)
            path = os.path.join(out_dir, f"{board}_DRILL_{size:.3f}mm.drl")
            error = _write_text(path, _excellon_content(
                board, [(1, size, items)], f"NC Drill 单刀文件 T{size:.3f}mm"))
            if error:
                res.warn(error)
            else:
                res.artifact("excellon", path, f"Ø{size:.3f}mm 单刀, {len(items)} 个孔")
                written += 1
    else:
        res.warn("没有可写的输出目录, Excellon 钻孔文件未落盘")

    res.status = PASS if written else INFO
    res.summary = (f"{len(sizes)} 种孔径; 由 {sum(coord_count.values())} 个孔坐标生成 "
                   f"{written} 个 Excellon 文件 (含合并文件与按刀文件), "
                   f"孔径 {', '.join(f'Ø{s:.3f}' for s in sorted(by_size))}mm")
    if outside:
        # 孔跑到板外 = 坐标文件本身有问题, 生成的 Excellon 也不能用
        res.status = FAIL
        res.metric("落在板框外的孔", len(outside), "个",
                   f"与板框 {area.get('bbox_mm')} 比较", "high")
        res.warn(f"{len(outside)} 个孔落在板框之外 (取前 5 个): "
                 + "; ".join(outside[:5])
                 + " —— 孔坐标文件的单位/原点很可能与 .brd 不一致 "
                   "(常见: 文件头写 METRIC 但数值是 mil)。这段坐标不可直接投产")
        res.summary += f"; 但 {len(outside)} 个孔在板框外, 坐标可疑"
        res.need("单位与原点正确的孔坐标文件",
                 "重新从 Allegro 导出: Manufacture > NC > NC Drill (选对单位和原点), "
                 "或核对现有 .drl 文件头的 METRIC/INCH 与数值量级是否一致")
    elif area.get("ok"):
        res.metric("落在板框外的孔", 0, "个", "与板框 bbox 比较", "medium")
    if bad_coord:
        res.warn(f"{bad_coord} 条孔记录缺坐标或缺孔径, 已跳过 (没进 Excellon)")
    res.warn("Excellon 本身不区分镀覆; 镀覆/跨层信息见上面的参数表, "
             "投产时请按 PTH/NPTH 分别出文件")
    if not table:
        res.need("钻孔表 (孔径/镀覆/数量)", "Allegro: Manufacture > NC > Drill Customization / "
                                       "Drill Chart 报告, 用来核对孔数与镀覆")
    res.source("二进制 DRILL CHART + 导入的孔坐标文件 (Excellon/SKILL)")
    return res


# --------------------------------------------------------------------------
# 4.3 NC Route
# --------------------------------------------------------------------------
def cap_nc_route(ctx: Context) -> Result:
    cap = CATALOG_MAP["mfg.nc_route"]
    lines = facts.outline_polylines(ctx)
    if not lines:
        return unavailable(cap, "没有可用的板框折线, 生成不了铣外形路径",
                           "确认 .brd 有 BOARD GEOMETRY/OUTLINE 且内嵌快照完整; "
                           "或直接提供 Allegro 的 NC Route 文件")
    if not ctx.scale_confident:
        # 与 Gerber 同一条安全底线: 尺寸标定不可信就不能出机床文件
        return unavailable(
            cap,
            "坐标标定不可信 (未取到 .brd 的 Draw Size), 生成的铣外形路径尺寸会是错的, "
            "已拒绝生成",
            "Allegro: 打开设计另存一次让 Draw Size 写入 .brd, 再重新分析; "
            "或直接导出 Allegro 的 NC Route 文件 (Manufacture > NC > NC Route)")

    out_dir = _target_dir(ctx)
    board = _board_tag(ctx)
    path = os.path.join(out_dir, f"{board}.rou") if out_dir else ""
    res = Result(cap, PASS, "")
    if out_dir:
        error = _write_text(path, _route_content(board, lines, _ROUTE_TOOL_MM))
        if error:
            res.warn(error)
            path = ""
    else:
        res.warn("没有可写的输出目录, 铣外形路径未落盘")
        path = ""
    if path:
        res.artifact("excellon_route", path,
                     f"{len(lines)} 条轮廓, 铣刀 Ø{_ROUTE_TOOL_MM:.2f}mm (默认值)")

    total_len = 0.0
    for line in lines:
        pts = [(float(x), float(y)) for x, y in line]
        for index in range(len(pts) - 1):
            total_len += math.hypot(pts[index + 1][0] - pts[index][0],
                                    pts[index + 1][1] - pts[index][1])
    for index, line in enumerate(lines, start=1):
        pts = [(float(x), float(y)) for x, y in line]
        length = sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                     for i in range(len(pts) - 1))
        res.add(轮廓序号=index, 点数=len(pts), 起点X=round(pts[0][0], 3),
                起点Y=round(pts[0][1], 3), 路径长mm=round(length, 3),
                闭合="是" if math.hypot(pts[-1][0] - pts[0][0],
                                       pts[-1][1] - pts[0][1]) < 0.01 else "否")
    res.columns = ["轮廓序号", "点数", "起点X", "起点Y", "路径长mm", "闭合"]
    res.metric("轮廓条数", len(lines), "条", "板框折线 (取最外一圈)")
    res.metric("铣路径总长", round(total_len, 3), "mm", "各轮廓折线长度求和", "medium")
    res.metric("铣刀直径", _ROUTE_TOOL_MM, "mm", "模块默认值, 必须与板厂确认", "low")
    res.summary = (f"{len(lines)} 条轮廓, 路径总长 {round(total_len, 3)}mm; "
                   f"铣刀 Ø{_ROUTE_TOOL_MM:.2f}mm 是模块默认值, 需按板厂铣刀确认")
    res.warn(f"铣刀直径用的是模块默认值 Ø{_ROUTE_TOOL_MM:.2f}mm, "
             f"必须与板厂实际铣刀核对后再投线")
    res.warn("轮廓是板边名义线, 未做刀具半径补偿; 板厂一般自行补偿, 但要在下单说明里写清")
    res.warn("板框折线取自内嵌快照最外一圈, 内部开槽/异形轮廓可能不全, "
             "正式投产请用 Allegro 的 NC Route 导出")
    if not path:
        res.status = WARN     # 文件没落盘就不能算产出
    res.source("内嵌快照 BOARD GEOMETRY/OUTLINE 折线")
    return res


# --------------------------------------------------------------------------
# 4.4 / 4.5 IPC-2581 与 ODB++ (需要 Allegro 授权)
# --------------------------------------------------------------------------
def _readiness_rows(ctx: Context) -> List[dict]:
    """导出准备度清单: 逐项说明 .brd 里读得到什么、还缺什么。"""
    rows: List[dict] = []

    def item(name: str, state: str, detail: str, source: str) -> None:
        rows.append({"数据项": name, "是否齐备": state,
                     "数量/说明": detail, "来源": source})

    comp = facts.components(ctx)
    drills = facts.drill_by_size(ctx)
    area = facts.region_areas(ctx)
    nets = facts.net_names(ctx)
    netlist = ctx.exports.get("netlist") or {}
    comps_xy = ctx.exports.get("components") or []
    holes_xy = ctx.exports.get("holes") or []
    layers = ctx.layers or []
    etch_layers = ctx.etch_layers or []
    mask_segs = ctx.segments(["mask"]) if ctx.has_snapshot else []
    silk_segs = ctx.segments(["silk"]) if ctx.has_snapshot else []
    notes = (ctx.design.get("raw_note") or {}).get("notes") or {}
    thickness = ctx.design.get("board_thickness_mm") or notes.get("board_thickness_mm")
    copper_oz = notes.get("copper_oz")
    finishes = ctx.design.get("surface_finishes") or []

    item("层信息 (层数/层名)", "齐备" if layers else ("部分" if ctx.layer_count else "缺失"),
         f"{ctx.layer_count or 0} 层, 层名 {len(layers)} 个", "SubClass.xml / 预览元数据")
    item("每层铜箔图形", "齐备" if etch_layers else "缺失",
         f"{len(etch_layers)} 层有 ETCH 图形: {'、'.join(etch_layers[:8])}"
         if etch_layers else "快照里没有 ETCH 图形", "内嵌设计快照")
    item("板框 (OUTLINE)", "齐备" if area.get("ok") else "缺失",
         (f"{area.get('width_mm')} x {area.get('height_mm')} mm"
          if area.get("ok") else area.get("reason", "无")),
         "内嵌设计快照 BOARD GEOMETRY/OUTLINE")
    item("网络名清单", "齐备" if nets else "缺失",
         f"{len(nets)} 个网络" if nets else "没有 objects/Net.xml 节点名",
         "objects/Net.xml")
    item("网表连接 (网络 + 引脚)", "齐备" if netlist.get("ok") else "缺失",
         (f"{netlist.get('count')} 个网络 / {netlist.get('pin_count')} 个引脚")
         if netlist.get("ok") else "没有导入网表 (pstxnet.dat)",
         "导入的网表文件")
    item("器件位号清单", "齐备" if comp.get("ok") else "缺失",
         f"{comp.get('count')} 个位号" if comp.get("ok")
         else comp.get("reason", "无 objects/Part.xml"), "objects/Part.xml")
    item("器件坐标/旋转/面别", "齐备" if comps_xy else "缺失",
         f"{len(comps_xy)} 个器件有坐标" if comps_xy else "没有导入 Placement/SKILL 坐标",
         "导入的 Placement/SKILL 坐标文件")
    item("钻孔表 (孔径/镀覆/数量)", "齐备" if drills else "缺失",
         f"{len(drills)} 种孔径 / {sum(int(r.get('qty') or 0) for r in drills)} 个孔"
         if drills else "二进制里没有 DRILL CHART", "二进制 DRILL CHART")
    item("孔坐标", "齐备" if holes_xy else "缺失",
         f"{len(holes_xy)} 个孔有坐标" if holes_xy else "没有导入 Excellon/SKILL 孔坐标",
         "导入的孔坐标文件")
    item("叠层介质/板厚/铜厚", "部分" if (thickness or copper_oz) else "缺失",
         f"板厚 {thickness or '未知'}, 铜厚 {copper_oz or '未知'}; 介质厚度与 Dk 无",
         "制程说明文本 (置信度低)")
    item("表面处理", "齐备" if finishes else "缺失",
         "、".join(finishes) if finishes else "没有读到表面处理说明", "制程说明文本")
    item("阻焊层图形", "齐备" if mask_segs else "缺失",
         f"{len(mask_segs)} 条 SOLDERMASK 图形" if mask_segs else "快照里没有阻焊图形",
         "内嵌设计快照 (轮廓级)")
    item("丝印层图形", "齐备" if silk_segs else "缺失",
         f"{len(silk_segs)} 条 SILKSCREEN 图形" if silk_segs else "快照里没有丝印图形",
         "内嵌设计快照 (轮廓级)")
    item("铜皮填充几何", "缺失", "内嵌快照只导出轮廓线, 没有铜皮/平面填充数据",
         "—")
    item("焊盘/引脚几何与网络归属", "缺失", "快照图形没有网络号, 也没有焊盘栈几何",
         "—")
    item("器件物料信息 (Value/MPN/封装)", "缺失", "objects/Part.xml 只有位号",
         "—")
    return rows


def _readiness_result(ctx: Context, cap: Capability, fmt: str, menu: str,
                      extra_data: str, filename: str) -> Result:
    """IPC-2581 / ODB++ 的公共实现: 如实 unavailable + 导出准备度清单 CSV。"""
    res = unavailable(
        cap,
        f"没有 Allegro 授权, 本工具产不出合法的 {fmt} 文件 (该格式必须由 Allegro 的"
        f"导出模块生成, 手工拼装的文件不算数)",
        menu)
    rows = _readiness_rows(ctx)
    res.add_all(rows, ["数据项", "是否齐备", "数量/说明", "来源"])
    res.columns = ["数据项", "是否齐备", "数量/说明", "来源"]

    ready = sum(1 for r in rows if r["是否齐备"] == "齐备")
    partial = sum(1 for r in rows if r["是否齐备"] == "部分")
    missing = [r["数据项"] for r in rows if r["是否齐备"] == "缺失"]
    res.metric("导出准备度", f"{ready}/{len(rows)} 项齐备", "",
               "逐项核对 .brd 可读数据", "high")
    res.metric("部分齐备", partial, "项", "同上")
    res.metric("缺失数据项", len(missing), "项", "同上")

    out_dir = _target_dir(ctx)
    board = _board_tag(ctx)
    if out_dir:
        path = os.path.join(out_dir, filename.format(board=board))
        error = _write_csv(path, res.columns, rows)
        if error:
            res.warn(error)
        else:
            res.artifact("csv", path, f"{fmt} 导出准备度清单 ({ready}/{len(rows)} 项齐备)")
    else:
        res.warn("没有可写的输出目录, 导出准备度清单未落盘")

    res.summary = (f"{fmt} 需要 Allegro 授权才能输出; 本工具改出「导出准备度清单」: "
                   f"{ready} 项齐备 / {partial} 项部分齐备 / {len(missing)} 项缺失")
    if missing:
        res.need(f"以下数据 {fmt} 需要, 但 .brd 里读不到: {'、'.join(missing)}; "
                 f"另外 {extra_data}", menu)
    else:
        res.need(extra_data, menu)
    res.warn(f"{fmt} 由 Allegro 导出后建议再做一次比对; 本清单只能说明"
             f"「哪些数据 .brd 里有」, 不等于导出的文件一定合格")
    res.source("逐项核对: 内嵌快照 / 二进制元数据 / 导入的导出文件")
    return res


def cap_ipc2581(ctx: Context) -> Result:
    cap = CATALOG_MAP["mfg.ipc2581"]
    return _readiness_result(
        ctx, cap, "IPC-2581",
        "Allegro: File > Export > IPC-2581 (需要 Allegro 的 IPC-2581 导出选件); "
        "导出后把 .xml/.cvg 一并给回来, 本工具可以读它做校验",
        "IPC-2581 还要求 .brd 里读不到的东西: 铜皮/平面填充几何、焊盘栈与引脚到网络的"
        "归属、每层介质厚度与 Dk、表面处理与阻焊开窗、器件物料信息 (Value/MPN)",
        "{board}_IPC2581_READINESS.csv")


def cap_odbpp(ctx: Context) -> Result:
    cap = CATALOG_MAP["mfg.odbpp"]
    return _readiness_result(
        ctx, cap, "ODB++",
        "Allegro: File > Export > ODB++ (或 Manufacture > ODB++ Inside); "
        "输出目录里会有 odb 归档/tgz, 可直接给板厂",
        "ODB++ 还要求 .brd 里读不到的东西: 铜皮/平面填充几何、焊盘栈与引脚到网络的"
        "归属、叠层介质参数、钻孔的镀覆与跨层归属、器件物料信息 (Value/MPN)",
        "{board}_ODBPP_READINESS.csv")


# --------------------------------------------------------------------------
# 4.6 IPC-D-356 电气测试网表
# --------------------------------------------------------------------------
_PIN_SPLIT_RE = re.compile(r"^([A-Za-z]+\d+)\s*[.\-:/]\s*(.+)$")
_PIN_ONLY_RE = re.compile(r"^([A-Za-z]+\d+)$")


def _split_pin(pin: str) -> Tuple[str, str]:
    """把 'R12.1' / 'R12-1' / 'U3:A2' 拆成 (位号, 引脚号); 拆不出位号时返回 ('', 原文)。"""
    text = str(pin or "").strip()
    match = _PIN_SPLIT_RE.match(text)
    if match:
        return match.group(1).upper(), match.group(2).strip()
    match = _PIN_ONLY_RE.match(text)
    if match:
        return match.group(1).upper(), ""
    return "", text


def _ipc356_317(net: str) -> str:
    return "317 " + _pad(net, _IPC356_NET_W)


def _ipc356_327(net: str, ref: str, pin: str, x: float, y: float, side: str) -> str:
    return ("327 " + _pad(net, _IPC356_NET_W) + " " + _pad(ref, _IPC356_REF_W) + "-"
            + _pad(pin, _IPC356_PIN_W) + " "
            + f"{x:>{_IPC356_XY_W}.3f}" + " " + f"{y:>{_IPC356_XY_W}.3f}" + " "
            + _pad(side, _IPC356_SIDE_W))


def cap_ipc356(ctx: Context) -> Result:
    cap = CATALOG_MAP["mfg.ipc356"]
    netlist = ctx.exports.get("netlist") or {}
    if not netlist.get("ok"):
        return unavailable(
            cap,
            "没有网络表（网络名 + 每个网络的引脚/测试点）, 生成不了 IPC-D-356 测试网表",
            "Allegro: Tools > Reports > Netlist，或第三方网表 pstxnet.dat")
    comps = list(ctx.exports.get("components") or [])
    holes = list(ctx.exports.get("holes") or [])
    if not comps and not holes:
        return unavailable(cap, "有网表但没有测试点/器件坐标, 327 记录写不出来",
                           ctx.howto_components_xy())

    # 位号 -> 坐标; 孔 (带网络名的 SKILL 导出) 也能当测试点
    by_ref: Dict[str, dict] = {}
    for comp in comps:
        ref = str(comp.get("refdes") or "").strip().upper()
        if ref and _num(comp.get("x_mm")) is not None and _num(comp.get("y_mm")) is not None:
            by_ref.setdefault(ref, comp)
    holes_by_net: Dict[str, List[dict]] = {}
    for hole in holes:
        net = str(hole.get("net") or "").strip()
        if not net:
            continue
        if _num(hole.get("x_mm")) is None or _num(hole.get("y_mm")) is None:
            continue
        holes_by_net.setdefault(net, []).append(hole)

    nets: Dict[str, List[str]] = netlist.get("nets") or {}
    out: List[str] = [
        "C IPC-D-356A 电气测试网表 - 由 brd_extract 生成",
        f"C 生成时间 {_now()}, 网络 {len(nets)} 个",
        "C 列排版按 IPC-D-356A 常用列手工实现, 上机前请用测试厂的校验工具过一遍",
    ]
    points = 0
    unresolved = 0
    truncated = False
    long_net = 0
    for net in sorted(nets):
        if len(net) > _IPC356_NET_W:
            long_net += 1
        out.append(_ipc356_317(net))
        seen = set()
        for pin_text in nets.get(net) or []:
            ref, pin = _split_pin(pin_text)
            comp = by_ref.get(ref)
            if comp is None:
                unresolved += 1
                continue
            x = float(comp["x_mm"])
            y = float(comp["y_mm"])
            side = str(comp.get("side") or "").strip().upper()[:3] or "TOP"
            key = (ref, pin)
            if key in seen:
                continue
            seen.add(key)
            if points >= _IPC356_MAX_POINTS:
                truncated = True
                break
            out.append(_ipc356_327(net, ref, pin, x, y, side))
            points += 1
        for hole in holes_by_net.get(net, []):
            if points >= _IPC356_MAX_POINTS:
                truncated = True
                break
            ref = str(hole.get("padstack") or hole.get("kind") or "VIA")
            out.append(_ipc356_327(net, ref, "", float(hole["x_mm"]),
                                   float(hole["y_mm"]), "TOP"))
            points += 1
        if truncated:
            break
    out.append("999")

    out_dir = _target_dir(ctx)
    board = _board_tag(ctx)
    path = os.path.join(out_dir, f"{board}_IPC356.ipc") if out_dir else ""
    res = Result(cap, PASS if points else WARN, "")
    if out_dir:
        error = _write_text(path, "\n".join(out) + "\n")
        if error:
            res.warn(error)
            path = ""
    else:
        res.warn("没有可写的输出目录, IPC-D-356 文件未落盘")
        path = ""
    if not path:
        res.status = WARN     # 文件没落盘就不能算产出
    if path:
        res.artifact("ipc356", path,
                     f"{len(nets)} 个网络 / {points} 个测试点")

    res.add_all([{"记录类型": "317", "内容": "网络名", "条数": len(nets)},
                 {"记录类型": "327", "内容": "连通性测试点 (位号-引脚 + 坐标)",
                  "条数": points},
                 {"记录类型": "999", "内容": "文件结束", "条数": 1}],
                ["记录类型", "内容", "条数"])
    res.columns = ["记录类型", "内容", "条数"]
    res.metric("网络数", len(nets), "个", "导入的网表")
    res.metric("测试点数", points, "个", "网表引脚 x 器件坐标")
    if unresolved:
        res.metric("对不上坐标的引脚", unresolved, "个",
                   "网表引脚名在坐标文件里找不到对应位号", "low")
    res.summary = (f"{len(nets)} 个网络写出 317 记录, {points} 个测试点写出 327 记录"
                   + (f"; {unresolved} 个引脚对不上坐标" if unresolved else ""))
    res.warn("列排版按 IPC-D-356A 常用列手工实现, 上机前请用测试厂的校验工具过一遍")
    if long_net:
        res.warn(f"{long_net} 个网络名超过 {_IPC356_NET_W} 字符被截断, "
                 f"测试厂可能需要完整网络名")
    if unresolved:
        res.need("网表引脚与器件位号的对应关系",
                 f"{unresolved} 个引脚 (如 'U1.A2') 在导入的坐标文件里找不到位号; "
                 f"{ctx.howto_components_xy()}")
    if not points:
        res.need("可定位的测试点坐标",
                 "网表引脚名与坐标文件的位号对不上, 请确认两边是同一版设计")
    if truncated:
        res.warn(f"测试点超过 {_IPC356_MAX_POINTS} 条, 已截断")
    res.source(f"网表: {netlist.get('source', '导入文件')} + "
               f"坐标: {len(comps)} 个器件 / {len(holes)} 个孔")
    return res


# --------------------------------------------------------------------------
# 4.7 Pick & Place
# --------------------------------------------------------------------------
def cap_pick_place(ctx: Context) -> Result:
    cap = CATALOG_MAP["mfg.pick_place"]
    comps = list(ctx.exports.get("components") or [])
    if not comps:
        return unavailable(cap, "没有导入的 Placement/SKILL 坐标文件, "
                                "器件 XY / 旋转角 / 面别拿不到",
                           ctx.howto_components_xy())

    rows: List[dict] = []
    for comp in comps:
        x = _num(comp.get("x_mm"))
        y = _num(comp.get("y_mm"))
        rows.append({
            "位号": str(comp.get("refdes") or "").strip(),
            "X(mm)": x, "Y(mm)": y,
            "旋转角": str(comp.get("rotation") or "").strip(),
            "面别": str(comp.get("side") or "").strip().upper(),
            "来源": str(comp.get("source") or ""),
        })
    rows.sort(key=lambda r: _natural_key(r["位号"]))
    out_dir = _target_dir(ctx)
    board = _board_tag(ctx)
    res = Result(cap, PASS, "")

    # 基本校验: 有没有坐标落在板框 bbox 之外
    area = facts.region_areas(ctx)
    outside_index: set = set()
    if area.get("ok"):
        min_x, min_y, max_x, max_y = area["bbox_mm"]
        for index, row in enumerate(rows):
            x, y = row["X(mm)"], row["Y(mm)"]
            if x is None or y is None:
                continue
            if (x < min_x - _OUTSIDE_TOL_MM or x > max_x + _OUTSIDE_TOL_MM
                    or y < min_y - _OUTSIDE_TOL_MM or y > max_y + _OUTSIDE_TOL_MM):
                outside_index.add(index)
    for index, row in enumerate(rows):
        row["是否出界"] = ("是" if index in outside_index
                          else ("未知" if not area.get("ok") else "否"))
    outside = [rows[index] for index in sorted(outside_index)]
    columns = ["位号", "X(mm)", "Y(mm)", "旋转角", "面别", "是否出界", "来源"]

    # CSV: Designator,Mid X,Mid Y,Rotation,Layer
    csv_columns = ["Designator", "Mid X", "Mid Y", "Rotation", "Layer"]
    csv_rows = [{"Designator": r["位号"], "Mid X": r["X(mm)"], "Mid Y": r["Y(mm)"],
                 "Rotation": r["旋转角"], "Layer": r["面别"]} for r in rows]
    # TXT: Allegro Placement 风格 REFDES X Y ROTATION SIDE (空格对齐)
    txt_lines = [f"# brd_extract Pick & Place 坐标文件 (单位: mm), 板名 {board}",
                 f"# 生成时间 {_now()}; 来源: 导入的 Placement/SKILL 坐标文件",
                 "# REFDES        X         Y  ROTATION  SIDE"]
    for row in rows:
        x = "—" if row["X(mm)"] is None else f"{row['X(mm)']:.4f}"
        y = "—" if row["Y(mm)"] is None else f"{row['Y(mm)']:.4f}"
        txt_lines.append(f"{row['位号']:<8} {x:>10} {y:>10} "
                         f"{row['旋转角'] or '0':>8} {row['面别'] or '?':<6}")

    written = 0
    if out_dir:
        csv_path = os.path.join(out_dir, f"{board}_PICK_PLACE.csv")
        txt_path = os.path.join(out_dir, f"{board}_PICK_PLACE.txt")
        error = _write_csv(csv_path, csv_columns, csv_rows)
        if error:
            res.warn(error)
        else:
            res.artifact("csv", csv_path, f"{len(rows)} 个器件 (Designator/Mid X/Mid Y/"
                                          f"Rotation/Layer)")
            written += 1
        error = _write_text(txt_path, "\n".join(txt_lines) + "\n")
        if error:
            res.warn(error)
        else:
            res.artifact("placement_txt", txt_path, "Allegro Placement 风格 (空格对齐)")
            written += 1
    else:
        res.warn("没有可写的输出目录, 坐标文件未落盘")
    if not written:
        res.status = WARN     # 文件没落盘就不能算产出

    res.add_all(rows, columns)
    res.columns = columns
    sides: Dict[str, int] = {}
    for row in rows:
        sides[row["面别"] or "?"] = sides.get(row["面别"] or "?", 0) + 1
    res.metric("器件数", len(rows), "个", "导入的 Placement/SKILL 坐标文件", "high")
    for side, count in sorted(sides.items()):
        res.metric(f"{side} 面器件", count, "个", "同上")
    res.metric("板框外器件", len(outside), "个", "与板框 bbox 比较", "medium")
    res.summary = (f"{len(rows)} 个器件的坐标已导出 (CSV + TXT); "
                   f"{' / '.join(f'{k} {v}' for k, v in sorted(sides.items()))}")
    if outside:
        names = "、".join(str(r["位号"]) for r in outside[:20])
        res.warn(f"{len(outside)} 个器件的中心点落在板框 bbox 之外: {names}"
                 + (" 等" if len(outside) > 20 else "")
                 + " (确认是悬边/拼板还是坐标错误)")
        res.summary += f"; {len(outside)} 个器件在板框外, 需人工确认"
        res.status = WARN
    elif not area.get("ok"):
        res.warn("没有板框 bbox, 无法校验坐标是否落在板内")
        res.need("板框几何 (用来校验器件坐标是否出界)",
                 "确认 .brd 有 BOARD GEOMETRY/OUTLINE; 或人工核对坐标范围")
        res.status = WARN
    res.warn("坐标来自导入的 Placement/SKILL 文件; 若该文件单位判定有误, "
             "整份坐标都会偏, 请与 Allegro 的 Placement 报告对一次")
    res.source("导入的 Placement/SKILL 坐标文件")
    return res


# --------------------------------------------------------------------------
# 4.8 BOM
# --------------------------------------------------------------------------
def cap_bom(ctx: Context) -> Result:
    cap = CATALOG_MAP["mfg.bom"]
    comp = facts.components(ctx)
    if not comp.get("ok"):
        return unavailable(cap, comp.get("reason") or ".brd 里没有器件位号清单, 无 BOM 可出",
                           "确认 .brd 含 objects/Part.xml (真实设计); 或提供 Allegro 的 "
                           "BOM 报告 (Tools > Reports > Bill of Materials)")
    refdes = [str(r) for r in (comp.get("refdes") or [])]

    # (a) 按位号前缀/类别分组
    grouped: Dict[Tuple[str, str], List[str]] = {}
    for ref in refdes:
        prefix = _prefix_of(ref) or "(无前缀)"
        category = _CATEGORY_OF_PREFIX.get(prefix, "其它" if prefix != "(无前缀)" else "未知前缀")
        grouped.setdefault((category, prefix), []).append(ref)

    rows: List[dict] = []
    for (category, prefix), refs in sorted(grouped.items(),
                                           key=lambda kv: (-len(kv[1]), kv[0])):
        rows.append({"类别": category, "前缀": prefix, "数量": len(refs),
                     "位号列表": "、".join(sorted(refs, key=_natural_key)),
                     "Value": "", "Footprint": "", "MPN": "",
                     "来源": "objects/Part.xml 位号清单"})

    # (b) 合并导入报告里含 REFDES/COMPONENT/QUANTITY 的段
    merged = 0
    filled = 0
    reports = ctx.exports.get("reports") or []
    for report in reports:
        for section in report.get("sections") or []:
            header = str(section.get("header") or "").upper()
            if not any(key in header for key in
                       ("REFDES", "COMPONENT", "QUANTITY", "DESIGNATOR", "QTY")):
                continue
            for raw in section.get("rows") or []:
                designator = _pick_column(raw, _REFDES_COLS)
                quantity = _pick_column(raw, _QTY_COLS)
                value = _pick_column(raw, _VALUE_COLS)
                mpn = _pick_column(raw, _MPN_COLS)
                footprint = _pick_column(raw, _FOOTPRINT_COLS)
                if not (designator or quantity):
                    continue
                prefix = _prefix_of(designator) or "(无前缀)"
                rows.append({
                    "类别": "导入报告", "前缀": prefix or "(合并行)",
                    "数量": quantity or "",
                    "位号列表": designator, "Value": value, "Footprint": footprint,
                    "MPN": mpn,
                    "来源": f"{report.get('source', '导入报告')} / {section.get('header')}",
                })
                merged += 1
                if value or mpn or footprint:
                    filled += 1

    columns = ["类别", "前缀", "数量", "位号列表", "Value", "Footprint", "MPN", "来源"]
    out_dir = _target_dir(ctx)
    board = _board_tag(ctx)
    res = Result(cap, PASS if filled else WARN, "")
    written = 0
    if out_dir:
        path = os.path.join(out_dir, f"{board}_BOM.csv")
        error = _write_csv(path, columns, rows)
        if error:
            res.warn(error)
        else:
            written = 1
            res.artifact("csv", path,
                         f"{comp.get('count')} 个位号, {len(grouped)} 组"
                         + (f", 合并导入报告 {merged} 行" if merged else ""))
    else:
        res.warn("没有可写的输出目录, BOM 未落盘")
    if not written:
        res.status = WARN     # 文件没落盘就不能算产出

    res.add_all(rows, columns)
    res.columns = columns
    res.metric("器件数", comp.get("count"), "个", comp.get("source", "objects/Part.xml"),
               comp.get("confidence", "high"))
    res.metric("分组数", len(grouped), "组", "按位号前缀/类别分组")
    res.metric("类别数", len({key[0] for key in grouped}), "类", "位号前缀分类")
    by_prefix = comp.get("by_prefix") or {}
    res.metric("其中 IC芯片", sum(by_prefix.get(p, 0) for p in ("U", "IC")), "个",
               "位号前缀 U/IC")
    if merged:
        res.metric("合并的导入报告行", merged, "行", "导入的 BOM/报告段")
    res.summary = (f"{comp.get('count')} 个位号分成 {len(grouped)} 组写进 BOM; "
                   f"Value/Footprint/MPN 列保留但为空 —— .brd 的 Part.xml 只有位号, "
                   f"物料编码/型号需要 Allegro 的 BOM 报告或 ERP 数据")
    if merged:
        res.summary += f"; 已合并 {merged} 行导入报告 (来源见「来源」列), 其中 {filled} 行带物料字段"
    res.need("物料信息 (Value / Footprint / MPN)",
             "Allegro: Tools > Reports > Bill of Materials 导出带料的 BOM, 或提供 ERP/PLM 导出")
    res.warn("BOM 的物料列 (Value/Footprint/MPN) 来自 .brd 的什么数据都没有, "
             "只保留列名; 不能拿它直接下单采购")
    if not merged:
        res.warn("没有可合并的导入报告 (表头需含 REFDES/COMPONENT/QUANTITY), "
                 "所以目前只有位号分组")
    res.source(comp.get("source", "objects/Part.xml 位号清单"))
    return res


# --------------------------------------------------------------------------
# 能力注册表
# --------------------------------------------------------------------------
CATALOG: List[Capability] = [
    Capability("mfg.artwork", "manufacturing", "Artwork (Gerber)", "Artwork 光绘 (Gerber)",
               "从内嵌快照按层生成 RS-274X Gerber; 标定不可信时拒绝生成", cap_artwork,
               "snapshot", ["内嵌快照几何", "板框 Draw Size 标定"],
               ["每层 .gbr 文件", "逐层图形/孔径统计"]),
    Capability("mfg.nc_drill", "manufacturing", "NC Drill", "NC 钻孔文件",
               "钻孔参数表 + 由孔坐标生成真实 Excellon 钻孔文件", cap_nc_drill, "brd",
               ["二进制 DRILL CHART", "Excellon/SKILL 孔坐标"],
               ["钻孔参数表 CSV", "Excellon .drl 文件"]),
    Capability("mfg.nc_route", "manufacturing", "NC Route", "NC 铣外形路径",
               "由板框折线生成铣外形路径; 铣刀直径为默认值需与板厂确认", cap_nc_route,
               "snapshot", ["内嵌快照板框折线"], ["铣外形路径文件"]),
    Capability("mfg.ipc2581", "manufacturing", "IPC-2581", "IPC-2581 数字产品模型",
               "需 Allegro 授权; 本工具输出导出准备度清单", cap_ipc2581, "external",
               ["Allegro IPC-2581 导出选件"],
               ["导出准备度清单 CSV", "Allegro 导出菜单路径"]),
    Capability("mfg.odbpp", "manufacturing", "ODB++", "ODB++ 制造数据",
               "需 Allegro 授权; 本工具输出导出准备度清单", cap_odbpp, "external",
               ["Allegro ODB++ 导出能力"],
               ["导出准备度清单 CSV", "Allegro 导出菜单路径"]),
    Capability("mfg.ipc356", "manufacturing", "IPC-D-356", "IPC-D-356 测试网表",
               "由网表 + 器件/测试点坐标生成 IPC-D-356 记录", cap_ipc356, "export",
               ["网表 (pstxnet.dat)", "器件/测试点坐标"],
               ["IPC-D-356 文件 (.ipc)"]),
    Capability("mfg.pick_place", "manufacturing", "Pick & Place", "贴片坐标文件",
               "由导入的 Placement/SKILL 坐标生成 CSV 与 Allegro 风格 TXT", cap_pick_place,
               "export", ["Placement/SKILL 坐标文件"], ["CSV + TXT 坐标文件"]),
    Capability("mfg.bom", "manufacturing", "Bill of Materials", "BOM 物料清单",
               "按位号前缀/类别分组的 BOM; 物料列需 Allegro BOM 报告或 ERP", cap_bom,
               "brd", ["objects/Part.xml 位号", "导入的 BOM 报告段"],
               ["BOM CSV"]),
]
CATALOG_MAP: Dict[str, Capability] = {c.id: c for c in CATALOG}

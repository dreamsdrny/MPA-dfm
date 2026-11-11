"""生成一个**可运行的合成 .brd 样例**, 用于在没有真实 Allegro 板子时验证整条链路。

为什么需要它
------------
本工具的所有能力都建立在"从 .brd 里读出 XX"之上, 而仓库里没有真实 .brd 可以跑
测试。这个脚本按 Allegro 17.x 的实际结构合成一份最小但**格式真实**的 .brd:

    [二进制体]  ALL UNITS ARE IN MILS / DRILL CHART / VIA20D10A30 / L2_GND ...
    [zip 归档]  第 1 个条目 = 内嵌设计快照 JSON (design.comps)
                其余条目 = Header.xml, objects/PhysicalCSet.xml, Net.xml ...

合成出来的板子能被 src/brd_raw.py、src/brd_design.py、src/design_geometry.py
原样解析, 所以可以拿它跑端到端测试:
    python tools/make_demo_brd.py --out demo
    python main.py --analyze demo/DEMO_BOARD.brd --analyze-files demo/place_txt.txt demo/demo.drl demo/demo_drc.rpt demo/pstxnet.dat demo/demo.ipc

它同时还生成配套的"Allegro 导出文件"样例 (Placement / Excellon / DRC 报告 /
网表 / IPC-356), 用来验证导入解析这条路。**这些文件是测试夹具, 不是真实板子**,
不要拿去投产。
"""
import argparse
import io
import json
import os
import zipfile

MIL = 0.0254


# --------------------------------------------------------------------------
# 几何: 快照坐标用 mil (和真实 .brd 一致), 标定时 Draw Size 与之一致
# --------------------------------------------------------------------------
def _pt(x, y):
    return {"x": float(x), "y": float(y)}


def _line(cls, sub, x1, y1, x2, y2, width):
    return {"class": cls, "subclass": sub, "type": 2, "width": width,
            "points": [_pt(x1, y1), _pt(x2, y2)]}


def _arc(cls, sub, cx, cy, x1, y1, x2, y2, width):
    import math

    r = math.hypot(x1 - cx, y1 - cy)
    a0 = math.atan2(y1 - cy, x1 - cx)
    a1 = math.atan2(y2 - cy, x2 - cx)
    return {"class": cls, "subclass": sub, "type": 6, "width": width,
            "points": [_pt(cx, cy)], "endpts": [_pt(x1, y1), _pt(x2, y2)],
            "start": a0, "end": a1, "radius": r}


def _circle(cls, sub, cx, cy, r, width=0.0):
    return {"class": cls, "subclass": sub, "type": 3, "width": width,
            "points": [_pt(cx, cy)], "radius": r}


def _rect(cls, sub, x0, y0, x1, y1, width):
    return [
        _line(cls, sub, x0, y0, x1, y0, width),
        _line(cls, sub, x1, y0, x1, y1, width),
        _line(cls, sub, x1, y1, x0, y1, width),
        _line(cls, sub, x0, y1, x0, y0, width),
    ]


def _mm_mil(mm: float) -> float:
    """mm -> mil (夹具里几何用 mil 表示, 和真实 .brd 的快照一致)。"""
    return round(mm / MIL, 3)


# --------------------------------------------------------------------------
# 器件焊盘表 (测试夹具): 用于验证"从 BRD 提取焊盘大小/引脚间距" 与
# "焊盘到铜皮 / 焊盘到走线距离"。每行 = (位号, 引脚号, 中心x_mil, 中心y_mil, 宽mm, 高mm)
# --------------------------------------------------------------------------
DEMO_PADS = []
# U7: SOIC-8, 两排各 4 个焊盘; 行内引脚间距 1.27mm(50mil), 两排中心距 5.4mm
for _pin, _y in enumerate((1200, 1250, 1300, 1350), start=1):
    DEMO_PADS.append(("U7", str(_pin), 1400.0, float(_y), 1.5, 0.6))
for _pin, _y in enumerate((1200, 1250, 1300, 1350), start=5):
    DEMO_PADS.append(("U7", str(_pin), 1612.598, float(_y), 1.5, 0.6))
# R60: 0603 片式, 引脚间距 1.651mm(65mil)
DEMO_PADS.append(("R60", "1", 600.0, 2400.0, 0.95, 0.8))
DEMO_PADS.append(("R60", "2", 665.0, 2400.0, 0.95, 0.8))

# 焊盘图形 (钢网层矩形 + 阻焊层矩形: 阻焊比焊盘大 0.05mm/边, 用于验证去重)
DEMO_FOOTPRINT_GRAPHICS = []
for _ref, _pin, _cx, _cy, _w, _h in DEMO_PADS:
    _hw, _hh = _mm_mil(_w) / 2.0, _mm_mil(_h) / 2.0
    DEMO_FOOTPRINT_GRAPHICS += _rect("PACKAGE GEOMETRY", "PASTEMASK_TOP",
                                     _cx - _hw, _cy - _hh, _cx + _hw, _cy + _hh, 0)
    _mw, _mh = _mm_mil(_w + 0.1) / 2.0, _mm_mil(_h + 0.1) / 2.0
    DEMO_FOOTPRINT_GRAPHICS += _rect("PACKAGE GEOMETRY", "SOLDERMASK_TOP",
                                     _cx - _mw, _cy - _mh, _cx + _mw, _cy + _mh, 0)

# 铜箔: 走线 (细线宽) 与铜皮轮廓 (宽线宽闭合矩形) + 两个"铜皮内/外"的圆焊盘
DEMO_FOOTPRINT_GRAPHICS += [
    # 顶层走线 (8mil): 距 U7 最近一排焊盘 0.8684mm
    _line("ETCH", "TOP", 1300, 1150, 1700, 1150, 8),
    # 顶层走线 (6mil): 距 R60 焊盘 0.79375mm
    _line("ETCH", "TOP", 550, 2350, 750, 2350, 6),
    # 顶层铜皮轮廓 (40mil 宽闭合矩形): 距 U7 右侧焊盘 3.5019mm
    *_rect("ETCH", "TOP", 1800, 1150, 2050, 1400, 40),
    # 铜皮内部/外部的圆焊盘 (验证"在铜皮内"与到铜皮轮廓的距离)
    _circle("PACKAGE GEOMETRY", "PASTEMASK_TOP", 1925, 1275, 20),
    _circle("PACKAGE GEOMETRY", "PASTEMASK_TOP", 2250, 1275, 20),
]


def build_snapshot(width_mil=4000, height_mil=3150, variants=0):
    """构造设计快照 (design.comps 即全板图形)。variants>0 时生成"改动过的版本"。"""
    w, h = width_mil, height_mil
    comps = []

    # ---- 板框 (外圈 + 一个内孔, 用来验开槽识别) ----
    comps += _rect("BOARD GEOMETRY", "OUTLINE", 0, 0, w, h, 6)
    comps += _rect("BOARD GEOMETRY", "OUTLINE", 500, 500, 900, 900, 6)

    # ---- 布线允许区 ----
    comps += _rect("ROUTE KEEPIN", "ALL", 40, 40, w - 40, h - 40, 0)

    # ---- ETCH: 正常走线 ----
    comps += [
        _line("ETCH", "TOP", 200, 200, 1200, 200, 6),
        _line("ETCH", "TOP", 1200, 200, 1200, 900, 6),
        _line("ETCH", "TOP", 200, 400, 1000, 400, 8),
        _line("ETCH", "TOP", 300, 1600, 300, 2600, 10),
        _arc("ETCH", "TOP", 900, 2600, 300, 2600, 900, 2000, 6),
        _line("ETCH", "BOTTOM", 500, 1000, 3500, 1000, 8),
        _line("ETCH", "BOTTOM", 500, 1100, 3500, 1100, 8),
        # 内层平面用粗图形表示 (铜皮轮廓)
        _line("ETCH", "L2_GND", 100, 100, 3900, 100, 40),
        _line("ETCH", "L2_GND", 100, 3050, 3900, 3050, 40),
        _line("ETCH", "L3_SIG", 400, 1500, 3600, 1500, 12),
    ]
    # ---- ETCH: 一对故意过近的走线 (0.16 mil 净距, 用于触发间距违规) ----
    comps += [
        _line("ETCH", "TOP", 2000, 2000, 2000, 2800, 6),
        _line("ETCH", "TOP", 2006.3, 2000, 2006.3, 2800, 6),
    ]

    # ---- 丝印 / 装配 / 阻焊 / 钢网 ----
    comps += [
        _line("PACKAGE GEOMETRY", "SILKSCREEN_TOP", 1900, 1900, 2100, 1900, 5),
        _line("PACKAGE GEOMETRY", "SILKSCREEN_TOP", 2100, 1900, 2100, 2100, 5),
        _line("PACKAGE GEOMETRY", "SILKSCREEN_TOP", 300, 3000, 3700, 3000, 5),
        _line("PACKAGE GEOMETRY", "ASSEMBLY_TOP", 1850, 1850, 2150, 1850, 0),
        _line("PACKAGE GEOMETRY", "ASSEMBLY_TOP", 2150, 1850, 2150, 2150, 0),
        _line("PACKAGE GEOMETRY", "SILKSCREEN_BOTTOM", 500, 1500, 1500, 1500, 5),
        _circle("PACKAGE GEOMETRY", "SOLDERMASK_TOP", 700, 700, 25),
        _circle("PACKAGE GEOMETRY", "PASTEMASK_TOP", 700, 700, 22),
        _circle("PACKAGE GEOMETRY", "PASTEMASK_TOP", 1500, 700, 20),
        _circle("PACKAGE GEOMETRY", "PASTEMASK_TOP", 2300, 700, 24),
        _circle("PACKAGE GEOMETRY", "PASTEMASK_TOP", 3100, 700, 18),
    ]
    # ---- 器件焊盘 (钢网层矩形) + 铜箔: 用于验证"焊盘尺寸/引脚间距"和
    #      "焊盘到铜皮 / 焊盘到走线"的距离。数字都是按几何算好的, 便于自检:
    #        U7 (SOIC-8): 焊盘 1.5×0.6mm, 行内引脚间距 1.27mm(50mil), 两行间距 5.4mm
    #        R60 (0603):   焊盘 0.95×0.8mm, 引脚间距 1.651mm(65mil)
    #        顶层走线 (8mil) 距 U7 最近一排焊盘 0.8684mm
    #        顶层铜皮轮廓 (40mil 宽闭合矩形) 距 U7 右侧焊盘 3.5027mm
    comps += DEMO_FOOTPRINT_GRAPHICS

    # ---- 文字图形 (标注导出用) ----
    for x, text in ((300, "R1"), (900, "C1"), (1500, "U1"), (2100, "J1")):
        comps.append({"class": "REFDES", "subclass": "SILKSCREEN_TOP", "type": 1,
                      "width": 0, "text": text, "points": [_pt(x, 3200)]})

    if variants:                      # "另一版本"的板子: 改一层、改约束、删一个图形
        comps = [c for c in comps if not (c.get("subclass") == "L3_SIG")]
        comps.append(_line("ETCH", "BOTTOM", 600, 1200, 3400, 1200, 10))
        comps += _rect("BOARD GEOMETRY", "OUTLINE", 0, 0, w + 200, h, 6)

    return {
        "header": {"version": "17.4", "units": "mils"},
        "design": {"name": "DEMO_BOARD", "comps": comps},
    }


# --------------------------------------------------------------------------
# 二进制体 + zip 归档
# --------------------------------------------------------------------------
def _drill_chart():
    """DRILL CHART 段: 每个孔的 孔径/公差/镀覆/数量 用不可打印字节分隔。

    这正是 src/brd_raw._scan_drill 期望的形状: 它按"可打印连续串"切 token,
    所以 token 之间必须是 \x00 这类分隔符, 不能是空格。
    """
    def row(size_mil, tol, plate, qty):
        parts = [f"{size_mil:.4f}", tol, plate, str(qty)]
        return b"\x00".join(p.encode("ascii") for p in parts) + b"\x00"

    body = b""
    body += b"DRILL CHART: TOP to BOTTOM\x00"
    body += row(8.0, "+/-0.003/+/-0.003", "PLATED", 1200)
    body += row(10.0, "+/-0.003/+/-0.003", "PLATED", 320)
    body += row(20.0, "+/-0.004/+/-0.004", "PLATED", 64)
    body += row(125.0, "+/-0.005/+/-0.005", "NON-PLATED", 12)
    body += b"DRILL CHART: L2 to L3\x00"
    body += row(4.0, "+/-0.002/+/-0.002", "PLATED", 180)
    return body


def build_binary_body(variants=0):
    parts = [
        b"ALLEGRO SYSTEM DESIGN 17.4\x00",
        b"ALL UNITS ARE IN MILS\x00",
        b"BOARD GEOMETRY\x00OUTLINE\x00",
        b"ETCH\x00TOP\x00BOTTOM\x00L2_GND\x00L3_SIG\x00",
        b"PADSTACKS:\x00VIA20D10A30\x00VIA16D8A24\x00VIA14D8_3-10\x00VIA8-16\x00",
        _drill_chart(),
        # _QuickViewText 元数据: 单位/精度/层数/图纸范围
        ("MINX=0|MINY=0|MAXX=%d|MAXY=%d|"
         "STRING=Name: DEMO_BOARD|DRAWING MODE|"
         "Units: mils|Precision|Accuracy: 2|Grids|Layers: %d|END"
         % (4000 + (200 if variants else 0), 3150, 4)).encode("ascii"),
        b"\x00",
        b"PCB THICKNESS 1.6MM +/-10%, COPPER WEIGHT 1OZ, SURFACE FINISH ENIG, "
        b"CONTROLLED IMPEDANCE REQUIRED\x00",
        b"\x00" * 32,
    ]
    return b"".join(parts)


def _xml_cset(entries):
    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<root>"]
    for name, attrs in entries.items():
        out.append(f'  <o N="{name}">')
        for key, value in attrs.items():
            out.append(f'    <a N="{key}"><v V="{value}" G="{value}"/></a>')
        out.append("  </o>")
    out.append("</root>")
    return "\n".join(out).encode("utf-8")


def _xml_names(entry, names):
    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<root>"]
    for name in names:
        out.append(f'  <o N="{name}"/>')
    out.append("</root>")
    return "\n".join(out).encode("utf-8")


NETS = ["GND", "VCC_3V3", "VCC_5V", "CLK_25MHZ", "CLK_25MHZ_N", "RESET_N",
        "SPI_CLK", "SPI_MOSI", "SPI_MISO", "SPI_CS_N", "I2C_SDA", "I2C_SCL",
        "USB_DM", "USB_DP", "ETH_TX_P", "ETH_TX_N", "ETH_RX_P", "ETH_RX_N",
        "LED_STATUS", "TP1", "TP2", "TP3", "TP4"]
REFDES = (["R%d" % i for i in range(1, 41)] + ["C%d" % i for i in range(1, 31)]
          + ["U%d" % i for i in range(1, 6)] + ["J1", "J2", "J3", "J4", "J5", "J6"]
          + ["L1", "L2", "L3", "D1", "D2", "Q1", "Q2", "Y1", "SW1", "SW2"]
          + ["TP1", "TP2", "TP3", "TP4", "MH1", "MH2", "MH3", "MH4"])


def build_physical_cset(variants=0):
    line_width = 4 if not variants else 3
    return _xml_cset({
        "DEFAULT": {
            "MIN_LINE_WIDTH": str(line_width),
            "MIN_NECK_WIDTH": str(max(2, line_width - 1)),
            "MAX_LINE_WIDTH": "200",
            "VIA_LIST": "VIA20D10A30:VIA16D8A24:VIA14D8_3-10:VIA8-16",
        },
        "POWER": {
            "MIN_LINE_WIDTH": "10",
            "MIN_NECK_WIDTH": "8",
            "VIA_LIST": "VIA20D10A30",
        },
        "DIFF90": {
            "MIN_LINE_WIDTH": "5",
            "DIFFP_PRIMARY_GAP": "7",
            "VIA_LIST": "VIA16D8A24:VIA14D8_3-10",
        },
    })


def build_spacing_cset():
    return _xml_cset({
        "DEFAULT": {
            "LINE_TO_LINE_SPACING": "5",
            "LINE_TO_THRUVIA_SPACING": "6",
            "LINE_TO_SMDPIN_SPACING": "5",
            "SHAPE_TO_SMDPIN_SPACING": "6",
            "THRUVIA_TO_THRUVIA_SPACING": "25",
        },
        "POWER": {
            "LINE_TO_LINE_SPACING": "8",
            "LINE_TO_THRUVIA_SPACING": "8",
        },
    })


def build_sc_brd(name="DEMO_BOARD", variants=0, with_exports=True):
    """产出 (brd_bytes, {导出文件相对路径: 文本内容})。"""
    snap = build_snapshot(variants=variants)
    if variants:
        snap["design"]["name"] = name

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # ★ 第一个条目必须是设计快照 JSON: src/design_geometry.load_design_snapshot
        #   读的就是 namelist()[0]
        zf.writestr("design_1.json", json.dumps(snap))
        zf.writestr("Header.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<root physical="12" version="17.4"/>')
        zf.writestr("objects/PhysicalCSet.xml", build_physical_cset(variants))
        zf.writestr("objects/SpacingCSet.xml", build_spacing_cset())
        zf.writestr("objects/Net.xml", _xml_names("Net.xml", NETS))
        zf.writestr("objects/Part.xml", _xml_names("Part.xml", REFDES))
        zf.writestr("objects/DiffPair.xml",
                    _xml_names("DiffPair.xml", ["ETH_TX", "ETH_RX", "USB"]))
        zf.writestr("objects/SubClass.xml", _xml_names(
            "SubClass.xml",
            ["Etch/TOP", "Etch/L2_GND", "Etch/L3_SIG", "Etch/BOTTOM",
             "Board Geometry/Outline", "Package Geometry/Silkscreen_Top"]))
        zf.writestr("objects/Design.xml", _xml_names("Design.xml", [name]))

    brd = build_binary_body(variants) + buf.getvalue()
    exports = _export_files(name, variants) if with_exports else {}
    return brd, exports


# --------------------------------------------------------------------------
# 配套的 Allegro 导出文件样例
# --------------------------------------------------------------------------
_PLACEMENT = """\
# Allegro Placement Export (合成测试夹具, 非真实板子)
# Units: millimeters
# REFDES      FOOTPRINT     X          Y      ROTATION   SIDE
{rows}
"""


def _placement_rows(variants=0):
    """位号按网格摆在板内 (板 101.6 x 80.01 mm), 旋转角覆盖 0/90/180/270/45。"""
    rows = []
    cols = 11
    for idx, ref in enumerate(REFDES):
        col, row = idx % cols, idx // cols
        x = 6.0 + col * 9.0
        y = 6.0 + row * 8.0
        rot = [0.0, 90.0, 180.0, 270.0, 45.0][idx % 5]
        side = "BOTTOM" if (ref.startswith("C") and idx % 3 == 0) else "TOP"
        rows.append("%-11s %-12s %8.3f %8.3f %9.1f   %s"
                    % (ref, "PKG_%s" % ref.rstrip("0123456789").lower(), x, y, rot, side))
    return "\n".join(rows)


_DRC_RPT = """\
                          DRC REPORT
                          ----------
  Design: {name}
  Date: 2026-11-09

ERROR CLASS:  Line to Line Spacing
  Layer        : TOP
  Actual       : 0.0025
  Required     : 0.1270
  Location     : 2000.0 2000.0
  Net          : SPI_CLK
  Net          : SPI_MOSI

ERROR CLASS:  Line to Line Spacing
  Layer        : TOP
  Actual       : 0.0040
  Required     : 0.1270
  Location     : 2000.0 2400.0
  Net          : SPI_CLK
  Net          : I2C_SDA

ERROR CLASS:  Short Circuit
  Layer        : BOTTOM
  Actual       : 0.0000
  Required     : 0.0000
  Location     : 1500.0 1000.0
  Net          : VCC_5V
  Net          : GND

ERROR CLASS:  Unconnected Pin
  Layer        : TOP
  Actual       : 0.0000
  Required     : 0.0000
  Location     : 1200.0 900.0
  Net          : RESET_N
  RefDes       : U3

ERROR CLASS:  Line to Thru Via Spacing
  Layer        : L3_SIG
  Actual       : 0.0900
  Required     : 0.1524
  Location     : 900.0 1500.0
  Net          : I2C_SCL
"""

_PSTXNET = """\
$PACKAGE
'NET' 'GND'
'PIN' '1' 'U1'
'PIN' '2' 'U2'
'PIN' '1' 'C1'
'PIN' '2' 'C1'
'NET' 'VCC_3V3'
'PIN' '3' 'U1'
'PIN' '1' 'R1'
'NET' 'SPI_CLK'
'PIN' '4' 'U1'
'PIN' '1' 'R2'
'NET' 'RESET_N'
'PIN' '5' 'U1'
'PIN' '2' 'R3'
$END
"""


def _excellon():
    """Excellon 钻孔样例 (METRIC)。

    ★ 坐标必须是**毫米**: 头里写了 METRIC,TZ, 如果这里塞 mil 数值, 解析出来
    的孔全会落在板框之外 (300mm 的孔出现在 101mm 的板上) —— 夹具自己先错,
    就没法验证"孔在板外"这类检查了。
    """
    # (刀号, 孔径 mm, [(x_mm, y_mm), ...]) —— 全部落在 101.6 x 80.01mm 的板内
    tools = [
        (1, 0.2032, [(12.7, 15.24), (25.4, 25.4), (38.1, 38.1), (50.8, 50.8),
                     (63.5, 63.5), (76.2, 15.24)]),
        (2, 0.2540, [(20.0, 55.0), (30.0, 55.0), (40.0, 55.0), (50.0, 55.0)]),
        (3, 0.5080, [(60.0, 30.0), (70.0, 30.0)]),
        # 4 个安装孔 (NPTH, 3.175mm): 四角内缩 5mm
        (4, 3.1750, [(5.0, 5.0), (96.6, 5.0), (5.0, 75.0), (96.6, 75.0)]),
    ]
    lines = ["M48", "; DEMO DRILL (合成测试夹具, 单位 mm)", "METRIC,TZ"]
    for tool, size, _holes in tools:
        lines.append("T%dC%.4f" % (tool, size))
    lines += ["%", "G90", "G05"]
    for tool, _size, holes in tools:
        lines.append("T%d" % tool)
        for x, y in holes:
            lines.append("X%07.3fY%07.3f" % (x, y))
    lines += ["M30"]
    return "\n".join(lines) + "\n"


def _ipc356():
    """IPC-D-356A 样例。坐标为 0.0001 英寸定宽整数, 与板内位置对应。"""
    lines = ["C  IPC-D-356A DEMO (合成测试夹具)", "P  JOB DEMO", "P  UNITS CUST 0"]

    def u(value_mm):
        return int(round(value_mm / 0.00254))      # mm -> 0.0001 inch

    for ref, net, x_mm, y_mm in (("TP1", "GND", 12.7, 15.24),
                                 ("TP2", "VCC_3V3", 25.4, 25.4),
                                 ("TP3", "SPI_CLK", 50.8, 50.8),
                                 ("TP4", "RESET_N", 76.2, 15.24)):
        lines.append("317%-14s" % net)
        lines.append("327%-6s%+09d%+09d" % (ref, u(x_mm), u(y_mm)))
    lines.append("999")
    return "\n".join(lines) + "\n"


def _pads_csv():
    """evan_pads.csv 样例: 每个引脚的焊盘外框 (SKILL 导出格式, 坐标为 DBU 整数)。

    这是"器件焊盘尺寸与引脚间距"最可信的来源: 有真实位号, 尺寸来自设计数据库。
    夹具里只导出 U7 / R60 两颗器件 (真实脚本会导出全部引脚)。
    """
    lines = ["UNITS,millimeters,3,1000",
             "KIND,PADSTACK,X_DBU,Y_DBU,W_DBU,H_DBU,SIDE,NET,REFDES,PIN"]
    for ref, pin, cx_mil, cy_mil, w_mm, h_mm in DEMO_PADS:
        lines.append("PIN,%s_%s,%d,%d,%d,%d,TOP,,%s,%s" % (
            ref, pin,
            round(cx_mil * MIL * 1000), round(cy_mil * MIL * 1000),
            round(w_mm * 1000), round(h_mm * 1000), ref, pin))
    return "\n".join(lines) + "\n"


def _export_files(name, variants=0):
    return {
        "place_txt.txt": _PLACEMENT.format(rows=_placement_rows(variants)),
        "demo.drl": _excellon(),
        "demo_drc.rpt": _DRC_RPT.format(name=name),
        "pstxnet.dat": _PSTXNET,
        "demo.ipc": _ipc356(),
        "evan_pads.csv": _pads_csv(),
    }


# --------------------------------------------------------------------------
def write_demo(out_dir: str, variants: bool = True) -> dict:
    """写出样例 .brd 与配套导出文件, 返回 {名称: 路径}。"""
    os.makedirs(out_dir, exist_ok=True)
    paths = {}

    brd, exports = build_sc_brd("DEMO_BOARD")
    main_brd = os.path.join(out_dir, "DEMO_BOARD.brd")
    with open(main_brd, "wb") as f:
        f.write(brd)
    paths["brd"] = main_brd
    for name, text in exports.items():
        p = os.path.join(out_dir, name)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        paths[name] = p

    if variants:
        brd2, _ = build_sc_brd("DEMO_BOARD_REV_B", variants=1, with_exports=False)
        p2 = os.path.join(out_dir, "DEMO_BOARD_REV_B.brd")
        with open(p2, "wb") as f:
            f.write(brd2)
        paths["brd_rev_b"] = p2

    readme = os.path.join(out_dir, "README_样例说明.txt")
    with open(readme, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(
            "这是 tools/make_demo_brd.py 合成的测试夹具, 不是真实板子。\n"
            "用途: 在没有 Allegro 与真实 .brd 的环境里验证整条分析链路。\n\n"
            "DEMO_BOARD.brd        4 层板, 100.16 x 80.01 mm, 49 个器件, 23 个网络\n"
            "                      故意做了一对间距不足的走线 (TOP 2000/2006.3 mil)\n"
            "                      另含 U7 (SOIC-8, 焊盘 1.5x0.6mm, 引脚间距 1.27mm)\n"
            "                      与 R60 (0603) 焊盘, 以及配好的走线/铜皮轮廓\n"
            "DEMO_BOARD_REV_B.brd  改动版: 少一层走线、约束收紧、板框变宽\n"
            "place_txt.txt         Allegro Placement 导出样例 (器件坐标)\n"
            "demo.drl              Excellon 钻孔样例 (16 个孔)\n"
            "demo_drc.rpt          Allegro DRC 报告样例 (间距/短路/未连接)\n"
            "pstxnet.dat           第三方网表样例\n"
            "demo.ipc              IPC-D-356 样例 (4 个测试点)\n"
            "evan_pads.csv         SKILL 焊盘外框样例 (U7/R60 的焊盘尺寸与引脚间距)\n")
    paths["readme"] = readme
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成合成 .brd 样例 (测试夹具)")
    parser.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo"),
        help="输出目录 (默认 <项目>/demo)")
    parser.add_argument("--no-variants", action="store_true",
                        help="不生成对比版本 .brd")
    args = parser.parse_args(argv)
    paths = write_demo(args.out, variants=not args.no_variants)
    print("已生成合成样例:")
    for name, path in paths.items():
        size = os.path.getsize(path)
        print(f"  {name:<14} {path}  ({size} 字节)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""网络定位自检: 不依赖真实板 / Allegro, 用最小 .brd 归档 + CSV 验证口径。

覆盖:
  * .brd 归档 Net.xml / DiffPair.xml 读网络名与差分对
  * evan_pads / evan_holes / evan_traces 的 NET 归属、外框、走线总长、线宽
  * pads 与 holes 重复导出同一引脚时的去重 (不能把引脚数翻倍)
  * 没有 NET 列时只列网络名、不编造坐标
  * find_nets 通配筛选、export_net_csv 输出
"""
import os
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import net_locate  # noqa: E402

_pass = 0
_fail = 0


def check(label, cond, detail=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  [OK]   {label}")
    else:
        _fail += 1
        print(f"  [FAIL] {label}  {detail}")


NET_XML = ('<?xml version="1.0" encoding="utf-8"?><net>'
           '<o N="MIPI_D0_P"/><o N="MIPI_D0_N"/>'
           '<o N="MIPI_CLK_P"/><o N="MIPI_CLK_N"/>'
           '<o N="MIPI"/><o N="MIPN"/><o N="GND"/></net>')
DIFF_XML = ('<?xml version="1.0" encoding="utf-8"?><diffpair>'
            '<o N="MIPI_D0"/><o N="MIPI_CLK"/></diffpair>')

PADS = """UNITS,millimeters,3,1000
KIND,PADSTACK,X_DBU,Y_DBU,W_DBU,H_DBU,SIDE,NET,REFDES,PIN
PIN,U1_1,10000,10000,500,500,TOP,MIPI_D0_P,U1,1
PIN,U1_2,12000,10000,500,500,TOP,MIPI_D0_N,U1,2
PIN,U1_3,14000,12000,500,500,TOP,MIPI_CLK_P,U1,3
PIN,U1_4,16000,12000,500,500,TOP,MIPI_CLK_N,U1,4
PIN,U2_1,30000,20000,400,400,TOP,MIPI_D0_P,U2,1
PIN,U2_2,30000,21000,400,400,TOP,MIPI_D0_N,U2,2
PIN,U3_1,40000,30000,300,300,TOP,MIPI,U3,1
PIN,U3_2,41000,30000,300,300,TOP,MIPN,U3,2
"""

HOLES = """UNITS,millimeters,3,1000
KIND,PADSTACK,X_DBU,Y_DBU,NET,REFDES
VIA,VIA20D10A30,11000,11000,MIPI_D0_P,
VIA,VIA20D10A30,31000,21000,MIPI_D0_N,
PIN,U1_1,10000,10000,MIPI_D0_P,U1
PIN,U2_1,30000,20000,MIPI_D0_P,U2
"""

TRACES = """UNITS,millimeters,3,1000
KIND,LAYER,WIDTH_DBU,X1_DBU,Y1_DBU,X2_DBU,Y2_DBU,NET
TRACE,TOP,90,10000,10000,30000,10000,MIPI_D0_P
TRACE,TOP,90,30000,10000,30000,20000,MIPI_D0_P
TRACE,TOP,90,11000,11000,31000,21000,MIPI_D0_N
TRACE,TOP,90,12000,10000,14000,12000,MIPI_CLK_P
TRACE,TOP,90,16000,12000,18000,12000,MIPI_CLK_N
TRACE,TOP,90,40000,30000,42000,30000,MIPI
TRACE,TOP,90,41000,30000,43000,30000,MIPN
"""

EMPTY_NET_PADS = """UNITS,millimeters,3,1000
KIND,PADSTACK,X_DBU,Y_DBU,W_DBU,H_DBU,SIDE,NET,REFDES,PIN
PIN,U1_1,10000,10000,500,500,TOP,,U1,1
"""


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _mini_brd(folder, with_net=True):
    path = os.path.join(folder, "MINI.brd")
    with zipfile.ZipFile(path, "w") as z:
        if with_net:
            z.writestr("objects/Net.xml", NET_XML)
            z.writestr("objects/DiffPair.xml", DIFF_XML)
        else:
            z.writestr("Header.xml", '<?xml version="1.0"?><root/>')
    return path


def main():
    tmp = tempfile.mkdtemp(prefix="evan_net_loc_")
    try:
        brd = _mini_brd(tmp)
        _write(os.path.join(tmp, "evan_pads.csv"), PADS)
        _write(os.path.join(tmp, "evan_holes.csv"), HOLES)
        _write(os.path.join(tmp, "evan_traces.csv"), TRACES)

        inv = net_locate.load_net_inventory(brd, with_outline=False)
        check("读取网络清单", inv.get("ok") and inv.get("net_count") == 7,
              f"nets={inv.get('net_count')}")
        check("有坐标的网络数 (6/7)", inv.get("geometry_net_count") == 6,
              f"geo={inv.get('geometry_net_count')}")
        check("三个坐标文件都被识别", len(inv.get("sources") or []) == 3,
              str(inv.get("sources")))

        by_name = {n["name"]: n for n in inv.get("nets") or []}
        d0p = by_name.get("MIPI_D0_P") or {}
        check("D0_P 引脚/过孔/走线数 (2/1/2)",
              (d0p.get("pins"), d0p.get("vias"), d0p.get("traces")) == (2, 1, 2),
              f"{d0p.get('pins')}/{d0p.get('vias')}/{d0p.get('traces')}")
        check("pads/holes 重复引脚已去重 (pins=2, 不是 4)", d0p.get("pins") == 2)
        bbox = d0p.get("bbox") or {}
        check("外框 X 9.75~30.20", (bbox.get("x_min"), bbox.get("x_max")) == (9.75, 30.2),
              str(bbox))
        check("外框 Y 9.75~20.20", (bbox.get("y_min"), bbox.get("y_max")) == (9.75, 20.2),
              str(bbox))
        check("外框宽×高 20.45×10.45",
              (bbox.get("w_mm"), bbox.get("h_mm")) == (20.45, 10.45), str(bbox))
        check("中心 (19.975, 14.975)",
              d0p.get("center") == {"x": 19.975, "y": 14.975}, str(d0p.get("center")))
        check("走线总长 30.0mm", d0p.get("trace_length_mm") == 30.0,
              str(d0p.get("trace_length_mm")))
        check("线宽 0.09mm", (d0p.get("width_min_mm"), d0p.get("width_max_mm")) == (0.09, 0.09),
              f"{d0p.get('width_min_mm')}~{d0p.get('width_max_mm')}")
        check("没有 LAYER 列时用 SIDE 兜底 (TOP)", d0p.get("layers") == ["TOP"],
              str(d0p.get("layers")))
        check("差分对 D0_P ↔ D0_N", d0p.get("pair") == "MIPI_D0_N",
              str(d0p.get("pair")))
        clk = by_name.get("MIPI_CLK_P") or {}
        check("DiffPair.xml 覆盖 _P/_N 推断",
              clk.get("pair") == "MIPI_CLK_N", str(clk.get("pair")))
        mipn = by_name.get("MIPN") or {}
        check("MIPN 分类为 MIPI / 100Ω",
              mipn.get("class") == "MIPI" and mipn.get("impedance_ohm") == 100,
              f"{mipn.get('class')} {mipn.get('impedance_ohm')}")
        gnd = by_name.get("GND") or {}
        check("GND 没有坐标 (不编造)", gnd.get("has_geometry") is False and not gnd.get("bbox"))

        # 通配筛选
        check("MIPI* 命中 5 个网络",
              len(net_locate.find_nets(inv, "MIPI*")) == 5,
              str([n["name"] for n in net_locate.find_nets(inv, "MIPI*")]))
        check("*D0* 命中 2 个网络", len(net_locate.find_nets(inv, "*D0*")) == 2)
        check("只看有坐标时 GND 被排除",
              "GND" not in [n["name"] for n in net_locate.find_nets(inv, only_geometry=True)])
        check("差分对筛选只留 4 条",
              len(net_locate.find_nets(inv, diff_only=True)) == 4,
              str([n["name"] for n in net_locate.find_nets(inv, diff_only=True)]))

        # 导出
        out = os.path.join(tmp, "net.csv")
        net_locate.export_net_csv(d0p, out)
        text = open(out, encoding="utf-8-sig").read()
        check("导出 CSV 含网络名与总长",
              "MIPI_D0_P" in text and "30.0" in text and "TRACE" in text)

        # 没有 NET 列: 只列名字
        empty_dir = os.path.join(tmp, "empty")
        os.makedirs(empty_dir, exist_ok=True)
        empty_brd = _mini_brd(empty_dir)
        _write(os.path.join(empty_dir, "evan_pads.csv"), EMPTY_NET_PADS)
        inv2 = net_locate.load_net_inventory(empty_brd, with_outline=False)
        check("NET 列为空时 has_geometry=False", inv2.get("has_geometry") is False)
        check("NET 列为空时给出警告",
              any("NET" in w for w in inv2.get("warnings") or []),
              str(inv2.get("warnings")))

        # 归档里没有 Net.xml: 网络名从坐标文件的 NET 列兜底
        nonet_dir = os.path.join(tmp, "nonet")
        os.makedirs(nonet_dir, exist_ok=True)
        nonet_brd = _mini_brd(nonet_dir, with_net=False)
        for name, content in (("evan_pads.csv", PADS), ("evan_holes.csv", HOLES),
                              ("evan_traces.csv", TRACES)):
            _write(os.path.join(nonet_dir, name), content)
        inv3 = net_locate.load_net_inventory(nonet_brd, with_outline=False)
        check("没有 Net.xml 时用 NET 列列网络名",
              inv3.get("ok") and inv3.get("net_count") == 6,
              f"nets={inv3.get('net_count')}")
        check("没有 Net.xml 时来源写明坐标文件",
              "坐标文件" in (inv3.get("name_source") or ""), str(inv3.get("name_source")))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 70)
    print(f"  网络定位自检: 通过 {_pass} / {_pass + _fail}")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

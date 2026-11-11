# -*- coding: utf-8 -*-
"""器件分类统计自检 (独立夹具，不依赖 demo)。

覆盖:
  * Part.xml 位号去重 + 前缀分类 (R/C/L/D/Q/U/IC/LED/J/SW/F/X/Y/T/B/BT/未知)
  * evan_components.csv 坐标/面别、placement 封装、evan_pads.csv 引脚数
  * 无坐标器件用焊盘中心推算并标注
  * 无 Part.xml 时退回导出文件; 完全无数据时如实失败
  * Excel 三个页签能写出并读回
"""
import os
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.component_stats import (export_component_excel, find_components,
                                 load_component_inventory)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tmp_compfixture")

PASS = FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {detail}")


def make_fixture():
    if os.path.isdir(FIX):
        shutil.rmtree(FIX)
    os.makedirs(FIX)
    part_refs = ["R1", "R2", "RN1", "C1", "C2", "L1", "D1", "Q1", "U1", "IC1",
                 "LED1", "J1", "SW1", "F1", "X1", "Y1", "T1", "B1", "BT1",
                 "ZZ1", "TP1", "R1"]
    part = ('<?xml version="1.0"?><Part>'
            + "".join(f'<o N="{r}"/>' for r in part_refs) + "</Part>")
    with zipfile.ZipFile(os.path.join(FIX, "COMP.brd"), "w") as z:
        z.writestr("Header.xml", '<?xml version="1.0"?><header version="17.4"/>')
        z.writestr("objects/Part.xml", part)

    comps = [
        ("R1", 1000, 2000, 0, "TOP"), ("R2", 3000, 2000, 90, "BOTTOM"),
        ("C1", 5000, 3000, 0, "TOP"), ("U1", 10000, 10000, 0, "BOTTOM"),
        ("J1", 12000, 4000, 180, "TOP"), ("B1", 14000, 5000, 0, "TOP"),
    ]
    with open(os.path.join(FIX, "evan_components.csv"), "w", encoding="utf-8") as f:
        f.write("UNITS,millimeters,3,1000\nREFDES,X_DBU,Y_DBU,ROTATION,SIDE\n")
        for row in comps:
            f.write(",".join(str(v) for v in row) + "\n")

    pads = [
        ("R1", "1", 900, 1900), ("R1", "2", 1100, 2100),
        ("U1", "1", 9000, 9000), ("U1", "2", 11000, 9000),
        ("U1", "3", 9000, 11000), ("U1", "4", 11000, 11000),
        ("D1", "1", 5000, 6000), ("D1", "2", 6000, 6000),
    ]
    with open(os.path.join(FIX, "evan_pads.csv"), "w", encoding="utf-8") as f:
        f.write("UNITS,millimeters,3,1000\n")
        f.write("KIND,PADSTACK,X_DBU,Y_DBU,W_DBU,H_DBU,LAYER,NET,REFDES,PIN\n")
        for ref, pin, x, y in pads:
            f.write(f"PIN,R0603,{x},{y},900,450,TOP,GND,{ref},{pin}\n")

    with open(os.path.join(FIX, "place_txt.txt"), "w", encoding="utf-8") as f:
        f.write("# Units: millimeters\n")
        f.write("TP1 PKG_TP 7000 1000 0 TOP\n")

    # 无 Part.xml 的归档，放在单独目录，避免找到上面的导出文件
    nodir = os.path.join(FIX, "no_part")
    os.makedirs(nodir)
    with zipfile.ZipFile(os.path.join(nodir, "NO_PART.brd"), "w") as z:
        z.writestr("Header.xml", '<?xml version="1.0"?><header version="17.4"/>')
    shutil.copy(os.path.join(FIX, "evan_components.csv"),
                os.path.join(nodir, "evan_components.csv"))

    emptydir = os.path.join(FIX, "empty")
    os.makedirs(emptydir)
    with zipfile.ZipFile(os.path.join(emptydir, "EMPTY.brd"), "w") as z:
        z.writestr("Header.xml", '<?xml version="1.0"?><header version="17.4"/>')
    return os.path.join(FIX, "COMP.brd"), os.path.join(nodir, "NO_PART.brd"), \
        os.path.join(emptydir, "EMPTY.brd")


def main():
    brd, nobrd, emptybrd = make_fixture()
    print("== 1. Part.xml + 导出文件 ==")
    inv = load_component_inventory(brd)
    check(inv["ok"], "读取成功", inv.get("warnings"))
    st = inv["stats"]
    check(st["total"] == 21, "位号去重后 21 个", st["total"])
    expected = {
        "电阻": 2, "电阻排": 1, "电容": 2, "电感": 1, "二极管": 1,
        "三极管/MOS管": 1, "IC芯片": 2, "发光二极管": 1,
        "连接器/接插件": 1, "开关": 1, "保险丝": 1, "晶振": 2,
        "变压器": 1, "电池": 2, "测试点": 1, "未分类(ZZ)": 1,
    }
    check(st["by_category"] == expected, "分类计数完全匹配",
          f"{st['by_category']} != {expected}")
    check(st["by_prefix"].get("B") == 1 and st["by_prefix"].get("BT") == 1,
          "B/BT 都算电池")
    check(st["by_prefix"].get("U") == 1 and st["by_prefix"].get("IC") == 1,
          "U/IC 都算 IC芯片")
    check(st["sides"]["TOP"] == 5 and st["sides"]["BOTTOM"] == 2
          and st["sides"]["未知"] == 14, "面别统计", st["sides"])
    check(st["with_coords"] == 8, "有坐标 8 个 (含焊盘推算)", st["with_coords"])
    check(st["estimated_coords"] == 1, "其中推算坐标 1 个 (D1)", st["estimated_coords"])
    check(st["pins_known"] == 3 and st["total_pins"] == 8,
          "引脚数 3 个器件 / 共 8 脚", (st["pins_known"], st["total_pins"]))
    check(st["with_package"] == 1, "Placement 补到 1 个封装 (TP1)", st["with_package"])
    check(st["unclassified"] == ["ZZ"], "未分类前缀 ZZ", st["unclassified"])

    d1 = next(c for c in inv["components"] if c["refdes"] == "D1")
    check(abs(d1["x_mm"] - 5.5) < 1e-6 and d1["estimated_coord"],
          "D1 中心由焊盘推算 5.5,6.0", (d1["x_mm"], d1["y_mm"]))
    u1 = next(c for c in inv["components"] if c["refdes"] == "U1")
    check(u1["pin_count"] == 4 and len(u1["pins"]) == 4, "U1 4 个引脚")
    tp1 = next(c for c in inv["components"] if c["refdes"] == "TP1")
    check(tp1["package"] == "PKG_TP", "TP1 封装来自 Placement", tp1["package"])

    check(len(find_components(inv, "R*")) == 3, "通配 R* 命中 3 个")
    check(len(find_components(inv, "", "IC芯片")) == 2, "按类别筛选 IC芯片 2 个")

    print("== 2. 无 Part.xml，退回导出文件 ==")
    inv2 = load_component_inventory(nobrd)
    check(inv2["ok"] and not inv2["has_partxml"], "仍能统计 (来源导出)")
    check(inv2["stats"]["total"] == 6, "6 个位号", inv2["stats"]["total"])
    check(any("Part.xml" in w for w in inv2["warnings"]), "提示缺 Part.xml")

    print("== 3. 完全无器件数据 ==")
    inv3 = load_component_inventory(emptybrd)
    check(not inv3["ok"] and inv3["stats"]["total"] == 0, "如实失败, 不编造")
    check(any("Part.xml" in w for w in inv3["warnings"]), "失败原因说明")

    print("== 4. Excel 导出 ==")
    xlsx = os.path.join(FIX, "器件统计.xlsx")
    export_component_excel(inv, xlsx)
    check(os.path.isfile(xlsx) and os.path.getsize(xlsx) > 0, "xlsx 已写出")
    try:
        from openpyxl import load_workbook

        wb = load_workbook(xlsx)
        check(wb.sheetnames == ["汇总", "分类", "明细"], "三个页签", wb.sheetnames)
        ws = wb["明细"]
        check(ws.max_row == 22 and ws.max_column == 12,
              "明细 21 行 + 表头 / 12 列", (ws.max_row, ws.max_column))
    except Exception as exc:  # noqa: BLE001
        check(False, "xlsx 能读回", exc)

    print()
    print(f"  器件统计自检: 通过 {PASS} / {PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

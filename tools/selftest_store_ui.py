"""界面四 (BRD ↔ BOM 资料库联动检查) 的冒烟测试: 真把窗口建起来、填数据、导出、关掉。

只验证界面能不能用, 业务判定由 tools/test_store_check.py 覆盖:
  1. Tk 能不能起来 (无桌面环境会 skip, 不算失败)
  2. StoreCheckDialog 能否构造, 目录默认值是否指向 store-brd / store-bom
  3. 把 run_check 的结果回填后, 配对表 / 位号明细 / 详情区是否有内容
  4. 筛选"只看差异"是否只留漏项/多出/冲突
  5. 导出报告是否真的落盘, 关窗后 Tk 是否干净退出
"""
import os
import shutil
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo" / "DEMO_BOARD.brd"
FAILS = []


def check(ok, label, detail=""):
    if ok:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}  {detail}")
        FAILS.append(label)


def setup_store(root: Path):
    brd_dir, bom_dir = root / "store-brd", root / "store-bom"
    brd_dir.mkdir()
    bom_dir.mkdir()
    shutil.copy(DEMO, brd_dir / "S-860-000777_FAB_BRD_Rev_B.brd")

    from openpyxl import Workbook

    from src.design_inputs.reconcile import brd_component_records

    records = brd_component_records(str(DEMO))["records"]
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Bill Of Materials"])
    sheet.append(["Item", "IPN", "Quantity", "PCB Footprint", "Value",
                  "Part Reference", "Manufacturer", "MPN", "Description",
                  "Schematic Page"])
    sheet.append(["_____"])
    sheet.append(["PCB", "S-860-000777", 1, "", "BOARD", "PCB1", "", "", "", ""])
    for index, record in enumerate(records, start=2):
        sheet.append([index, "PN-" + record["refdes"], 1, "", "",
                      record["refdes"], "", "", "", ""])
    workbook.save(bom_dir / "S-850-000777_BOM_REVB.BOM.xlsx")
    return str(brd_dir), str(bom_dir)


def main():
    print("=" * 70)
    print("  界面四 (BRD ↔ BOM 联动检查) 冒烟测试")
    print("=" * 70)
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"  [SKIP] 当前环境没有可用显示, 跳过界面测试: {exc}")
        return 0
    root.withdraw()

    from src.design_inputs.store import run_check, store_paths
    from src.ui.store_dialog import StoreCheckDialog

    temp = tempfile.TemporaryDirectory()
    try:
        brd_dir, bom_dir = setup_store(Path(temp.name))
        out_dir = str(Path(temp.name) / "out")
        dlg = StoreCheckDialog(root, brd_dir=brd_dir, bom_dir=bom_dir,
                               out_dir=out_dir)
        root.update()
        check(True, "对话框构造成功")
        check(dlg.brd_dir.get() == brd_dir, "BRD 目录已带入")
        check(dlg.bom_dir.get() == bom_dir, "BOM 目录已带入")
        defaults = store_paths()
        check(defaults["brd"].endswith("store-brd") and defaults["bom"].endswith("store-bom"),
              "默认目录指向 store-brd / store-bom", str(defaults))

        bundle = run_check(brd_dir=brd_dir, bom_dir=bom_dir)
        dlg._apply_bundle(bundle)
        root.update()
        pairs = dlg.pair_tree.get_children()
        check(len(pairs) == 1, f"配对表 {len(pairs)} 行", str(pairs))
        values = dlg.pair_tree.item(pairs[0], "values")
        check(str(values[2]) == "通过", "配对结论显示通过", str(values))
        detail = dlg.detail.get("1.0", tk.END)
        check("位号双向核对" in detail and "料号一致" in detail,
              "详情区渲染逐项结论", detail[:80])
        rows = dlg.ref_tree.get_children()
        check(len(rows) == len(bundle["pairs"][0]["reconciliation"]["rows"]),
              f"位号明细 {len(rows)} 行")

        dlg.filter_var.set("只看差异")
        dlg._fill_refdes()
        root.update()
        check(len(dlg.ref_tree.get_children()) == 0, "只看差异时无差异行")
        dlg.filter_var.set("全部")
        dlg._fill_refdes()
        root.update()
        check(len(dlg.ref_tree.get_children()) == len(rows), "切回全部恢复所有行")

        dlg._set_detail("")
        check(dlg.detail.get("1.0", tk.END).strip() == "", "详情区可清空")

        with mock.patch("src.ui.store_dialog.messagebox.showinfo"):
            dlg._export()
        exported = [name for name in os.listdir(out_dir) if name.startswith("BRD_BOM联动检查_")]
        check(len(exported) >= 4, f"导出报告落盘 {len(exported)} 个文件", str(exported))

        dlg.close()
        root.update()
    finally:
        temp.cleanup()
        try:
            root.destroy()
        except tk.TclError:
            pass

    print("-" * 70)
    if FAILS:
        print(f"  失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("  界面冒烟测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

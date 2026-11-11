"""界面三 (BRD 分析与输出对话框) 的冒烟测试: 真的把窗口建起来再关掉。

只验证"能不能建、能不能点", 不验证业务逻辑 (那是 tools/selftest_analysis.py 的事):
  1. Tk 能不能起来 (无桌面环境会在这里 skip, 不算失败)
  2. AnalyzeDialog 能否构造: 能力库里的每一项是否都进了表格
  3. 勾选/取消勾选、全选、按结论筛选是否工作
  4. 选一行能否在下方详情区渲染出文字
  5. 关窗后 Tk 是否干净退出
"""
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "demo", "DEMO_BOARD.brd")
FAILS = []


def check(ok, label, detail=""):
    if ok:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}  {detail}")
        FAILS.append(label)


def main():
    print("=" * 70)
    print("  界面三 (BRD 分析与输出) 冒烟测试")
    print("=" * 70)
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"  [SKIP] 当前环境没有可用显示, 跳过界面测试: {exc}")
        return 0
    root.withdraw()

    from src.brd_analysis.catalog import CATALOG
    from src.ui.analyze_dialog import AnalyzeDialog

    expected = len(CATALOG.all)
    dlg = AnalyzeDialog(root, brd_path=DEMO, out_dir=os.path.join(ROOT, "output"))
    root.update()
    check(True, "对话框构造成功")
    check(len(dlg.rows) == expected, f"能力表格装载 {len(dlg.rows)} 项", f"实际 {len(dlg.rows)}")
    check(len(dlg.tree.get_children()) == expected, "表格行数与能力数一致")
    check(not dlg.catalog_errors, "无类别模块加载错误", str(dlg.catalog_errors))
    check(dlg.brd_path.get() == DEMO, "BRD 路径已带入")

    # 勾选 / 取消
    first = dlg.rows[0]["id"]
    check(first in dlg._selected, "默认全部勾选")
    dlg._toggle_id(first)
    check(first not in dlg._selected, "双击可取消勾选")
    dlg._toggle_id(first)
    check(first in dlg._selected, "再双击可重新勾选")
    # 窗口未映射 (withdrawn) 时 bbox 取不到, 双击事件不能崩
    dlg._toggle_row(None)
    dlg._toggle_row(type("E", (), {"y": 0})())

    dlg._select_all(False)
    check(not dlg._selected, "全不选")
    dlg._select_all(True)
    check(len(dlg._selected) == expected, "全选")

    # 详情区
    dlg.tree.selection_set(first)
    dlg._show_detail()
    text = dlg.detail.get("1.0", tk.END)
    check(len(text.strip()) > 20 and dlg.rows[0]["name_cn"] in text,
          "选中一行能在详情区渲染说明", repr(text[:60]))

    # 未跑分析时"按结论筛选"应给出提示而不是崩
    dlg.bundle = {"results": [{"id": first, "status": "fail"}]}
    dlg._select_problems()
    check(dlg._selected == {first}, "按结论筛选 (只留 不符合/数据不足)")

    # 关闭
    dlg.close()
    root.update()
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

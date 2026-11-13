"""Evan_DFM: 界面二 —— DFM 分析 (独立程序).

界面一 (Evan_BRD.exe) 只负责 Checklist 填写; DFM 分析在这里, 自己导入 BRD,
不从界面一取任何路径或参数。

用法:
    Evan_DFM.exe                打开窗口, 自动带出上次用过的 BRD (没有就等你选)
    Evan_DFM.exe board.brd      打开窗口并直接读这块板
"""
import os
import sys
import tkinter as tk
import traceback


def _crash_path() -> str:
    """崩溃日志写到程序旁边 (打包后是 exe 目录, 不是临时解压目录)。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "Evan_DFM_crash.log")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    brd = next((a for a in argv if a.lower().endswith(".brd")), "")

    from src.ui.dfm_dialog import DFMDialog
    from src.ui.theme import enable_dpi_awareness

    enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    from src.startup_guard import check_startup
    license_path = None
    if "--license" in argv:
        index=argv.index("--license")
        if index+1<len(argv):license_path=argv[index+1]
    if check_startup(root,license_path) is None:
        root.destroy()
        return 1
    dialog = DFMDialog(root, brd_path=brd, standalone=True)
    dialog.win.deiconify()
    dialog.win.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001  窗口版没有控制台, 至少留个日志
        try:
            with open(_crash_path(), "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except OSError:
            pass
        raise

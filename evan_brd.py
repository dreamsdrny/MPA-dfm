"""
Evan_BRD: 程序入口 (GUI)
=================================
BRD 分析工具: 读取 Cadence Allegro .brd -> 生成约束 / Checklist / DFM 校验
二级窗口: DFM 功能分析 (设计参数 -> 板厂制程能力比对)

用法:
    python evan_brd.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

APP_NAME = "Evan_MPA"
WINDOW_TITLE = "Evan_MPA - 分析BRD (Cadence Allegro)"


def resource_path(name: str) -> str:
    """
    获取资源文件的绝对路径，兼容开发环境和 PyInstaller 打包后的环境。

    :param name: 资源文件名或相对路径（如 "config.json" 或 "images/icon.png"）
    :return: 资源文件的绝对路径
    """
    # 获取 PyInstaller 打包后的临时解压目录；如果未打包，则使用当前脚本所在目录
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

    # 将基础目录与资源文件名拼接，返回完整路径
    return os.path.join(base, name)


def apply_icon(root) -> None:
    """
    为 GUI 窗口设置图标（兼容开发环境和 PyInstaller 打包环境）。
    依次尝试 .ico 和 .png 格式，并设置窗口图标。
    """

    try:
        # 尝试设置 .ico 图标（Windows 常用）
        ico = resource_path("evan.ico")

        if os.path.exists(ico):
            # 方式一：设置窗口图标（部分系统/环境有效）
            try:
                root.iconbitmap(default=ico)
            except Exception:
                pass

            # 方式二：再次尝试设置图标（兼容不同 tkinter 实现）
            try:
                root.wm_iconbitmap(ico)
            except Exception:
                pass

        # 尝试设置 .png 图标（跨平台，tkinter 原生支持）
        png = resource_path("evan.png")

        if os.path.exists(png):
            from tkinter import PhotoImage

            # 加载 PNG 图片
            img = PhotoImage(file=png)

            # 设置窗口图标（True 表示应用到所有子窗口）
            root.iconphoto(True, img)

            # 保持图片引用，防止被垃圾回收导致图标消失
            root._evan_icon = img

    except Exception:
        pass


def _crash_log_path() -> str:
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "Evan_BRD_crash.log")


def log_crash(source: str, text: str) -> None:
    """窗口版没有控制台: 任何未捕获异常都落到 crash.log, 方便用户回传。"""
    import datetime
    try:
        with open(_crash_log_path(), "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} {source} =====\n")
            f.write(text.rstrip() + "\n")
    except OSError:
        pass


def _cli_log_path() -> str:
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "Evan_BRD_cli.log")


def run_cli(argv) -> int:
    """带参数启动时走命令行 (打包版没有控制台, 输出同时写 Evan_BRD_cli.log)。"""
    import traceback

    log = open(_cli_log_path(), "a", encoding="utf-8")
    original_out, original_err = sys.stdout, sys.stderr

    class Tee:
        def write(self, text):
            log.write(text)
            log.flush()

        def flush(self):
            log.flush()

    sys.stdout = sys.stderr = Tee()
    sys.argv = [sys.argv[0]] + list(argv)
    code = 0
    try:
        import main as cli
        cli.main()
    except SystemExit as exc:
        code = int(exc.code or 0)
    except Exception:  # noqa: BLE001
        text = traceback.format_exc()
        log.write(text)
        log.flush()
        log_crash("cli", text)
        code = 1
    finally:
        # 必须先把 sys.stdout 换回去再关文件, 否则解释器退出时 flush 失败会返回 120
        try:
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        sys.stdout, sys.stderr = original_out, original_err
        try:
            log.close()
        except Exception:  # noqa: BLE001
            pass
    return code


def main():
    import threading
    import traceback

    if sys.argv[1:]:
        raise SystemExit(run_cli(sys.argv[1:]))

    from gui import BRD_AIGUI

    threading.excepthook = lambda args: log_crash(
        "thread", "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback)))

    app = BRD_AIGUI()  # 直接进入 BRD 分析界面 (无导航首页)
    try:
        app.root.title(WINDOW_TITLE)
        app.root.report_callback_exception = lambda *exc: log_crash(
            "tk-callback", "".join(traceback.format_exception(*exc)))
    except Exception:
        pass
    apply_icon(app.root)
    app.run()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        log_crash("main", traceback.format_exc())
        raise

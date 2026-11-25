"""程序目录解析: 源码运行 vs 打包(exe)运行.

打包后 ``__file__`` 指向 PyInstaller 的临时解压目录(_MEIPASS),
输出与配置若写到那里会在退出时被清理, 因此打包环境一律以 exe 所在目录为准。
"""
import os
import sys


def app_base_dir() -> str:
    """程序根目录: 源码运行时为脚本所在目录, 打包运行时为 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def default_output_dir() -> str:
    """默认输出目录: <程序根目录>/output"""
    return os.path.join(app_base_dir(), "output")


def resource_dir() -> str:
    """只读资源目录: 打包时优先 _MEIPASS, 源码时同 app_base_dir()。"""
    meipass = getattr(sys, "_MEIPASS", None)
    return meipass or app_base_dir()

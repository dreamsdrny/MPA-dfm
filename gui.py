"""Evan_BRD 图形界面入口 (兼容层).

实现已按功能拆分到 ``src/ui/``:

    src/ui/main_window.py   主窗口
    src/ui/main_panels.py   主窗口卡片与页眉/底部条
    src/ui/pipeline.py      约束生成流水线
    src/ui/dfm_export.py    BRD -> DFM 报告
    src/ui/dfm_dialog.py    DFM 功能分析窗口
    src/ui/dfm_params.py    参数表单
    src/ui/dfm_results.py   结果表格
    src/ui/theme.py         主题
    src/ui/widgets.py       通用控件
    src/ui/fields.py        参数表定义
    src/ui/prefs.py         界面偏好
    src/ui/paths.py         程序目录

本文件仅保留旧入口名, 便于 ``python gui.py`` / ``from gui import BRD_AIGUI`` 继续可用。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


__all__ = ["BRD_AIGUI", "MainWindow", "DFMDialog", "DFM_FIELDS",
            "app_base_dir", "default_output_dir"]


def __getattr__(name):
    if name in ("MainWindow", "BRD_AIGUI"):
        from src.ui.main_window import MainWindow
        return MainWindow
    if name == "DFMDialog":
        from src.ui.dfm_dialog import DFMDialog
        return DFMDialog
    if name in ("DFM_FIELDS", "_BRD_COMPONENT_CAP"):
        from src.application import pcb_inputs
        return getattr(pcb_inputs, "BRD_COMPONENT_CAP" if name == "_BRD_COMPONENT_CAP" else name)
    if name in ("app_base_dir", "default_output_dir"):
        from src.infrastructure import paths
        return getattr(paths, name)
    raise AttributeError(name)


def main():
    from src.bootstrap import prepare_startup
    prepare_startup()
    from src.ui.main_window import MainWindow
    MainWindow().run()


if __name__ == "__main__":
    main()

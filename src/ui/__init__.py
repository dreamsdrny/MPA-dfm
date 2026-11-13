"""Evan_BRD 界面层.

按功能拆分的模块, 每个文件只负责一件事:

    theme.py        配色 / 字体 / ttk 样式 / 卡片 / 日志控件 (纯外观)
    widgets.py      通用小部件: 表单行、下拉、表格、滚动区
    prefs.py        界面偏好读写 (%APPDATA%)
    paths.py        程序目录与默认输出目录 (兼容打包环境)
    fields.py       DFM 48 项参数表定义 + BRD 提取结果映射
    main_panels.py  主窗口的三张卡片与页眉/底部操作条
    main_window.py  主窗口 (MainWindow): 组装 + 生命周期 + 后台任务
      dfm_params.py   DFM 对话框左栏: 参数表单
    dfm_results.py  DFM 对话框右栏: 结果表格 (制程能力 / Layout 规则)
    dfm_dialog.py   界面二主窗口 (可独立运行, 自己导入 BRD)
    analyze_dialog.py  界面三: BRD 分析与输出 (8 大类能力)
    store_dialog.py    界面四: BRD ↔ BOM 资料库联动检查 (store-brd / store-bom)

业务逻辑不在这里: 填写 Checklist 走 ``src/checklist_pipeline.py``,
BRD/DFM 分析走 ``src/dfm_engine.py``, BRD/BOM 联动检查走
``src/design_inputs/store.py``, 界面只负责传参和展示。

对外导出采用延迟加载: 只 import ``src.ui.prefs`` 之类的子模块时,
不会连带把 tkinter 与整个界面栈拉起来。

兼容: 老的 ``gui.py`` / ``src/ui_theme.py`` 仍可 import, 内部转发到本包。
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:                                   # 仅给类型检查器看
    from src.ui.dfm_dialog import DFMDialog
    from src.ui.main_window import MainWindow

__all__ = ["MainWindow", "BRD_AIGUI", "DFMDialog", "DFM_FIELDS"]


def __getattr__(name):
    if name in ("MainWindow", "BRD_AIGUI"):
        from src.ui.main_window import MainWindow
        return MainWindow
    if name == "DFMDialog":
        from src.ui.dfm_dialog import DFMDialog
        return DFMDialog
    if name == "DFM_FIELDS":
        from src.ui.fields import DFM_FIELDS
        return DFM_FIELDS
    raise AttributeError(f"module 'src.ui' has no attribute {name!r}")

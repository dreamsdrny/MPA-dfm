"""兼容层: 界面主题已拆分到 src/ui/ 下, 此文件仅转发旧名字.

新代码请直接用:
    from src.ui.theme   import COLORS, Card, LogView, apply_theme, fonts
    from src.ui.widgets import field_row, labeled_combo, labeled_entry
    from src.ui.prefs   import load_prefs, save_prefs
"""
from src.ui.prefs import load_prefs, prefs_path, save_prefs
from src.ui.theme import (COLORS, Card, LogView, apply_theme,
                          enable_dpi_awareness, fonts)
from src.ui.widgets import field_row, labeled_combo, labeled_entry

__all__ = [
    "COLORS", "Card", "LogView", "apply_theme", "enable_dpi_awareness", "fonts",
    "field_row", "labeled_combo", "labeled_entry",
    "load_prefs", "save_prefs", "prefs_path",
]

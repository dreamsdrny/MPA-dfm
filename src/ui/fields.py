"""兼容层: DFM 字段定义与 BRD 映射已移到 src/dfm_inputs.py。

界面与命令行都用同一份映射, 这里只做再导出, 老代码 (gui.py / 测试) 仍可
`from src.ui.fields import DFM_FIELDS, brd_dfm_fields`。
"""
from src.dfm_inputs import (BRD_COMPONENT_CAP, BRD_FIELDS, CATEGORY_HINTS,
                            DFM_FIELDS, FIELD_META, brd_dfm_fields,
                            brd_design_values, brd_extra_values,
                            surface_finish_choices)

__all__ = ["BRD_COMPONENT_CAP", "BRD_FIELDS", "CATEGORY_HINTS", "DFM_FIELDS",
           "FIELD_META", "brd_dfm_fields", "brd_design_values",
           "brd_extra_values", "surface_finish_choices"]

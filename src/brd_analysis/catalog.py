"""能力清单与执行调度。

`CATALOG` 是本包唯一的能力注册表: 报告、命令行、界面都从它取列表,
所以新增一项能力 = 写一个函数 + 在 catalog 里登记一行, 不用改报告和界面。

为什么把注册表单独放一个模块: 各 cat_*.py 之间互不依赖, 只有这里 import 它们,
避免"改一个类别要重新 import 全部"的循环引用。
"""
from typing import Dict, List, Optional

from src.brd_analysis.model import CATEGORY_ORDER, Capability

# 类别模块: 每个模块导出 CATALOG = [Capability, ...]
_MODULE_BY_CATEGORY = {
    "drc": "src.brd_analysis.cat_drc",
    "physical": "src.brd_analysis.cat_physical",
    "electrical": "src.brd_analysis.cat_electrical",
    "manufacturing": "src.brd_analysis.cat_manufacturing",
    "reports": "src.brd_analysis.cat_reports",
    "assembly": "src.brd_analysis.cat_assembly",
    "mcad": "src.brd_analysis.cat_mcad",
    "database": "src.brd_analysis.cat_database",
}


def _load_module(dotted: str):
    import importlib

    return importlib.import_module(dotted)


class _Catalog:
    """惰性加载的能力表 (首次访问时才 import 各 cat_*.py)。"""

    def __init__(self):
        self._caps: Optional[List[Capability]] = None
        self._errors: List[str] = []

    def _ensure(self):
        if self._caps is not None:
            return
        caps: List[Capability] = []
        for category in CATEGORY_ORDER:
            dotted = _MODULE_BY_CATEGORY.get(category)
            if not dotted:
                continue
            try:
                module = _load_module(dotted)
            except Exception as exc:  # noqa: BLE001  一个类别坏了不能拖垮全部
                self._errors.append(f"{category}: 模块加载失败 {exc}")
                continue
            for cap in getattr(module, "CATALOG", []):
                caps.append(cap)
        self._caps = caps

    @property
    def all(self) -> List[Capability]:
        self._ensure()
        return list(self._caps or [])

    @property
    def errors(self) -> List[str]:
        self._ensure()
        return list(self._errors)

    def get(self, cap_id: str) -> Optional[Capability]:
        for cap in self.all:
            if cap.id == cap_id:
                return cap
        return None

    def by_category(self, category: str) -> List[Capability]:
        return [c for c in self.all if c.category == category]

    def ids(self) -> List[str]:
        return [c.id for c in self.all]

    def grouped(self) -> Dict[str, List[Capability]]:
        out: Dict[str, List[Capability]] = {key: [] for key in CATEGORY_ORDER}
        for cap in self.all:
            out.setdefault(cap.category, []).append(cap)
        return out


CATALOG = _Catalog()

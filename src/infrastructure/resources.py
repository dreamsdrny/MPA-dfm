"""Compatibility boundary for shared bounded file reads and parsing budgets."""
from src.infrastructure.resource_limits import (
    MAX_BRD_BYTES,
    MAX_BRD_ENTRIES,
    MAX_BRD_ENTRY_BYTES,
    MAX_BRD_UNPACKED_BYTES,
    InputBudgetExceeded,
    read_bounded,
)

__all__ = [
    "MAX_BRD_BYTES",
    "MAX_BRD_ENTRIES",
    "MAX_BRD_ENTRY_BYTES",
    "MAX_BRD_UNPACKED_BYTES",
    "InputBudgetExceeded",
    "read_bounded",
]

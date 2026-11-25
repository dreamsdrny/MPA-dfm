"""Input budgets; exceeding a limit must stop the affected analysis."""
from pathlib import Path

MAX_BRD_BYTES = 512 * 1024 * 1024
MAX_BRD_ENTRY_BYTES = 64 * 1024 * 1024
MAX_BRD_UNPACKED_BYTES = 256 * 1024 * 1024
MAX_BRD_ENTRIES = 20000


class InputBudgetExceeded(ValueError):
    """A partial parse is not a completed analysis."""


def read_bounded(path, limit=MAX_BRD_BYTES):
    path = Path(path)
    if path.stat().st_size > limit:
        raise InputBudgetExceeded(f"输入文件超过 {limit // 1024 // 1024} MiB 安全上限：{path.name}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise InputBudgetExceeded("读取期间输入超过安全上限")
    return data

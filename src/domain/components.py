"""Component summary derived only from supplied records, no parsing or I/O."""


def component_summary_line(comp: dict, limit: int = 6) -> str:
    if not comp or not comp.get("ok"):
        return comp.get("reason", "BRD 无器件数据") if comp else "未读取器件信息"
    parts = [f"{label} {count}" for label, count in list((comp.get("by_category") or {}).items())[:limit]]
    rest = len(comp.get("by_category") or {}) - limit
    if rest > 0:
        parts.append(f"其它 {rest} 类")
    return f"器件 {comp['count']} 个 (" + " / ".join(parts) + ")"

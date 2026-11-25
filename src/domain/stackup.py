"""逐铜层 DFM 输入合同：层序和铜厚必须来自叠构，不用外层值猜内层。"""
import re


def layer_number(name, layer_count=None):
    text = str(name or "").strip().upper()
    if text in ("TOP", "COPPER_TOP", "MASK_TOP"):
        return 1
    if text in ("BOTTOM", "BOT", "COPPER_BOTTOM", "MASK_BOTTOM"):
        return layer_count
    match = re.fullmatch(r"(?:L|LAYER|IN)[_-]?(\d+)(?:[_-].*)?", text)
    return int(match[1]) if match else None


def copper_layers(design, outer_copper_um=35.0):
    """stackup 是按层号的铜层表；layers/layer_count 只补待确认行，不补内层铜厚。"""
    from src.domain.pcb_rules import normalize_copper_um
    rows = design.get("stackup") or []
    if isinstance(rows, dict):
        rows = rows.get("layers") or []
    count_value = float(design.get("layer_count") or 0)
    if not count_value.is_integer() or count_value < 0:
        raise ValueError("layer_count 必须为非负整数")
    count = int(count_value)
    indexed = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("stackup 必须是含 index/name/copper_um 的铜层表")
        index_value = float(row.get("index") or layer_number(row.get("name"), count) or 0)
        if not index_value.is_integer():
            raise ValueError("叠构铜层 index 必须为整数")
        index = int(index_value)
        if index <= 0 or index in indexed:
            raise ValueError("叠构铜层 index 必须为不重复的正整数")
        indexed[index] = dict(row, index=index, name=row.get("name") or f"L{index}")
    if indexed and not count:
        count = max(indexed)
    if indexed and max(indexed) > count:
        raise ValueError("叠构铜层层号超出 layer_count")
    for name in design.get("layers") or []:
        name = name.get("name") if isinstance(name, dict) else name
        index = layer_number(name, count)
        if index and index <= count:
            indexed.setdefault(index, dict(index=index, name=name))
    for index in range(1, count + 1):
        indexed.setdefault(index, dict(index=index, name=f"L{index}"))
    result = []
    for index, row in sorted(indexed.items()):
        outer = index in (1, count)
        copper_um = row.get("copper_um")
        source = row.get("source") or "叠构输入"
        if copper_um is None and outer:
            copper_um, source = outer_copper_um, "外层手工 copper_um"
        if copper_um is not None:
            copper_um = normalize_copper_um(copper_um)
        result.append(dict(row, copper_um=copper_um, outer=outer, source=source))
    return result


def match_layer(rows, name=None, index=None):
    if index:
        return next((row for row in rows if row["index"] == index), None)
    text = str(name or "").upper()
    return next((row for row in rows if str(row["name"]).upper() == text), None)

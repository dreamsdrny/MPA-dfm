"""叠构剖面数据模型：只把已给厚度画成比例，缺失保持“未获取”，不用外层值猜内层或介质。"""
from math import isfinite

COPPER, DIELECTRIC, FINISH, MASK = "copper", "dielectric", "finish", "mask"
KINDS = dict(copper=COPPER, dielectric=DIELECTRIC, finish=FINISH, mask=MASK)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) and value >= 0 else None


def layer_label(index, name):
    """层号与层名可能重复（L2 / L2 GND）；只显示一次，避免剖面与核对表出现“L2 L2 GND”。"""
    text = str(name or f"L{index}").strip()
    if not text:
        return f"L{index}"
    upper = text.upper()
    return text if upper.startswith(f"L{index}") or upper in ("TOP", "BOTTOM", "BOT") else f"L{index} {text}"


def build_visual(rows, *, board_thickness_mm=None, surface_finish="", holes=None):
    """rows 为 copper_layers() 结果；返回可测试的纯数据剖面。"""
    rows = list(rows or [])
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("叠构行必须是映射")
    count = len(rows)
    holes = holes or {}
    bands = []
    finish = str(surface_finish or "").strip()
    if finish:
        bands.append(dict(kind=FINISH, label="表面处理 " + finish, thickness_um=None, source="已确认表面处理"))
    bands.append(dict(kind=MASK, label="阻焊（外层）", thickness_um=None, source="厚度需板厂叠层资料"))
    for index, row in enumerate(rows, 1):
        copper = _number(row.get("copper_um"))
        design = _number(row.get("design_copper_um"))
        confirmed = row.get("confirmed") is True or "成品已确认" in str(row.get("source") or "")
        label = layer_label(int(row.get("index") or index), row.get("name"))
        bands.append(dict(kind=COPPER, index=int(row.get("index") or index), name=str(row.get("name") or f"L{index}"),
            label=label,
            thickness_um=copper, design_thickness_um=design, outer=bool(row.get("outer")),
            source=str(row.get("source") or "叠构输入"),
            status="confirmed" if confirmed and copper is not None else "brd" if copper is not None else "missing"))
        if index < count:
            bands.append(dict(kind=DIELECTRIC, label="介质 / 芯板", thickness_um=None,
                source="未提供逐层介质厚度；仅等分示意，不参与判定"))
    bands.append(dict(kind=MASK, label="阻焊（外层）", thickness_um=None, source="厚度需板厂叠层资料"))
    # 剖面自下而上：外层铜之后仍是阻焊/表面处理；这里保持与板厂叠层表相同的阅读顺序。
    if finish:
        bands.append(dict(kind=FINISH, label="表面处理 " + finish, thickness_um=None, source="已确认表面处理"))
    total_copper = sum(row["thickness_um"] for row in bands if row["kind"] == COPPER and row["thickness_um"] is not None)
    missing = [row["name"] for row in bands if row["kind"] == COPPER and row["thickness_um"] is None]
    thickness = _number(board_thickness_mm)
    notes = []
    if thickness is not None and total_copper and total_copper / 1000 > thickness:
        notes.append(f"铜层合计 {total_copper / 1000:.3f} mm 已超过板厚 {thickness:.3f} mm，请核对成品/设计铜厚")
    if not count:
        notes.append("尚未取得铜层叠构")
    if missing:
        notes.append("缺铜厚层：" + "、".join(missing) + "；不套用外层铜厚")
    drills = []
    if int(holes.get("pth_span_count") or 0):
        drills.append(dict(kind="PTH", spans=int(holes["pth_span_count"]), top=1, bottom=count,
                           label="通孔跨度（快照给出跨度，不绘位置）"))
    if int(holes.get("npth_span_count") or 0):
        drills.append(dict(kind="NPTH", spans=int(holes["npth_span_count"]), top=1, bottom=count,
                           label="无镀铜孔跨度（跨度未逐孔确认，示意贯穿）"))
    return dict(layer_count=count, bands=bands, drills=drills, total_copper_um=total_copper,
                board_thickness_mm=thickness, missing_copper=missing, notes=notes,
                scale_note="铜厚按已知值比例；介质/阻焊厚度未给时仅等分示意，数值仍为未获取")

"""Cadence 孔类型与制造工艺分开处理；未知、冲突不能当作通孔。"""
import re


def is_through_span(span):
    parts = re.split(r"\s+to\s+", span or "", flags=re.I)
    return len(parts) == 2 and parts[0].strip().upper() == "TOP" and parts[1].strip().upper() == "BOTTOM"


def unverified_hole_inputs(design, sources):
    """旧版名称/孔径推算值不能作为已确认的机械或激光孔能力输入。

    只检查带来源的孔能力字段；人工确认值和实际几何测量值不受影响。
    返回被拒绝的字段及来源，供判定层保留待确认说明。
    """
    fields = ("min_mech_drill", "max_mech_drill", "min_laser_via",
              "min_annular_ring", "min_annular_ring_laser")
    hints = ("焊盘栈命名", "按孔径", "同孔径下焊盘", "启发", "推算")
    rejected = {}
    for field in fields:
        detail = (sources.get(field) or {}).get("source") or ""
        if design.get(field) is not None and any(hint in detail for hint in hints):
            rejected[field] = detail
    return rejected


def cadence_hole_kind(value):
    """Cadence 类型：Thru Pin/Via 为通孔，BBVia/Microvia 为盲埋孔。"""
    token = re.sub(r"[\s_-]+", "", str(value or "")).upper()
    return {"THRUPIN": "through", "THROUGHPIN": "through", "THRUVIA": "through",
            "THROUGH": "through", "BBVIA": "blind", "MICROVIA": "blind",
            "UVIA": "blind"}.get(token, "unknown")


def padstack_kind(name):
    """返回名称前缀的非权威提示；绝不能用于认定实际 Cadence 类型。"""
    text = str(name or "").strip()
    match = re.match(r"^(THRU[ _-]?PIN|BBVIA|MICROVIA|VIA)(?=$|[0-9_ .-])", text, re.I)
    if not match:
        return "unknown"
    return cadence_hole_kind(match.group(1))


def classify_hole(name="", cadence_type="", is_through=None, span=None, layer_count=None):
    """Return kind/reason from explicit object attributes, never a padstack name.

    An Allegro generic VIA/PIN object with isThrough is classified using that
    attribute, not the generic object's label. Process is deliberately separate.
    """
    evidence = []
    token = re.sub(r"[\s_-]+", "", str(cadence_type or "")).upper()
    if is_through is True:
        evidence.append(("through", "Cadence isThrough"))
    elif is_through is False and token in ("VIA", "THRUVIA", "BBVIA", "MICROVIA"):
        evidence.append(("blind", "Cadence isThrough=false：非贯通 Via"))
    if token not in ("VIA", "PIN"):
        kind = cadence_hole_kind(cadence_type)
        if kind != "unknown":
            evidence.append((kind, f"Cadence 类型 {cadence_type}"))
    # Padstack names and VIA_LIST entries are user-defined. A suffix like
    # _1-2 is a hint only; it does not prove the database's actual span.
    span_unresolved = False
    if span and layer_count and len(span) == 2:
        first, last = sorted(span)
        if 1 <= first < last <= layer_count:
            evidence.append(("through" if first == 1 and last == layer_count else "blind",
                             f"跨层 L{first}-L{last}/{layer_count}"))
        else:
            span_unresolved = True
    elif span:
        span_unresolved = True
    kinds = {kind for kind, _ in evidence}
    if span_unresolved and not evidence:
        return {"kind": "unknown", "reason": "名称标有跨层，但总层数未知或跨层无效，需确认"}
    if is_through is False and evidence and all(kind == "through" for kind, _ in evidence):
        return {"kind": "unknown", "reason": "孔型证据冲突：Cadence isThrough=false 与通孔类型不一致"}
    if len(kinds) == 1:
        return {"kind": evidence[0][0], "reason": "；".join(source for _, source in evidence)}
    if len(kinds) > 1:
        return {"kind": "unknown", "reason": "孔型证据冲突：" + "；".join(source for _, source in evidence)}
    return {"kind": "unknown", "reason": "缺少已验证的 Cadence 类型或跨层；焊盘栈名称和孔径不证明孔型"}

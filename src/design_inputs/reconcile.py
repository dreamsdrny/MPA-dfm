"""BRD 部分器件信息与完整 BOM 的逐位号双向核对。"""
import re
from collections import OrderedDict


def _norm_ref(refdes):
    return re.sub(r"\s+", "", str(refdes or "")).upper()


def _norm_attr(value, numeric=False):
    text = str(value or "")
    if numeric:
        return re.sub(r"\s+", "", text).upper()
    return re.sub(r"[^0-9A-Z]+", "", text).upper()


def _prefix(refdes):
    match = re.match(r"^([A-Za-z]+)\d", str(refdes or ""))
    return match.group(1).upper() if match else ""


def _natural_key(refdes):
    parts = re.split(r"(\d+)", str(refdes or ""))
    return [(1, int(part), "") if part.isdigit() else (0, 0, part.upper()) for part in parts if part]


def _record(value):
    return value if isinstance(value, dict) else {"refdes": value}


def brd_component_records(brd_path, raw=None):
    """读取 BRD 可可靠给出的器件字段：位号、前缀及防御式提取的封装。"""
    from src.brd_components import _PREFIX_CATEGORY, extract_components
    from src.brd_design import read_brd_objects

    try:
        objects, _ = read_brd_objects(brd_path, raw)
    except (OSError, ValueError) as exc:
        return {"ok": False, "records": [], "warnings": [str(exc)]}

    components = extract_components(objects)
    if not components.get("ok"):
        return {"ok": False, "records": [], "warnings": [components.get("reason", "BRD 无器件信息")]}

    packages, warnings = {}, []
    try:
        from src.brd_analysis.cat_database import _package_map
        scanned = _package_map(objects, components.get("refdes") or [])
        packages = {key.upper(): (value or {}).get("封装", "")
                    for key, value in (scanned.get("by_refdes") or {}).items()}
        warnings.extend(scanned.get("notes") or [])
    except Exception as exc:  # noqa: BLE001  封装只是补充字段，失败不影响位号核对
        warnings.append(f"BRD 封装探查失败: {exc}")

    records = []
    for refdes in components.get("refdes") or []:
        prefix = _prefix(refdes)
        records.append({
            "refdes": refdes,
            "prefix": prefix,
            "category": _PREFIX_CATEGORY.get(prefix, prefix or "未知"),
            "package": packages.get(refdes.upper(), ""),
            "source": "objects/Part.xml" + (" + objects/PartDefn.xml/Gate.xml" if packages.get(refdes.upper()) else ""),
        })
    return {"ok": True, "records": records, "warnings": warnings}


def reconcile_bom(brd_records, bom_rows):
    """双向核对 BRD 位号与完整 BOM。

    BRD 缺 BOM：BOM 漏项，状态 incomplete；
    BOM 多出位号：可能是有意保留的 DNP/机构件，状态 review，必须人工确认；
    双方都有但封装/料号/值不一致：单独列为 conflict，不自动判定哪边正确。
    """
    brd, bom = OrderedDict(), OrderedDict()
    duplicate_brd, duplicate_bom = [], []
    for item in brd_records or []:
        rec = _record(item)
        key = _norm_ref(rec.get("refdes"))
        if not key:
            continue
        if key in brd:
            duplicate_brd.append(rec)
            continue
        brd[key] = rec
    for item in bom_rows or []:
        rec = _record(item)
        key = _norm_ref(rec.get("refdes"))
        if not key:
            continue
        if key in bom:
            duplicate_bom.append(rec)
            continue
        bom[key] = rec

    conflicts = []
    for key in brd.keys() & bom.keys():
        for field, brd_key, bom_key in (
                ("footprint", "package", "footprint"),
                ("part_number", "part_number", "part_number"),
                ("manufacturer_part_number", "manufacturer_part_number", "manufacturer_part_number"),
                ("value", "value", "value")):
            left = brd[key].get(brd_key) or ""
            right = bom[key].get(bom_key) or ""
            numeric = field == "value"
            if (_norm_attr(left, numeric) and _norm_attr(right, numeric)
                    and _norm_attr(left, numeric) != _norm_attr(right, numeric)):
                conflicts.append({
                    "refdes": brd[key].get("refdes") or bom[key].get("refdes"),
                    "field": field, "brd": left, "bom": right, "severity": "review",
                    "note": "命名或版本可能不同，需人工确认，不能自动认定哪边正确",
                })

    missing_in_bom = [{
        "refdes": brd[key].get("refdes"), "prefix": _prefix(brd[key].get("refdes")),
        "category": brd[key].get("category", ""), "package": brd[key].get("package", ""),
        "note": "BRD 有该位号但完整 BOM 未列，优先按 BOM 漏项核对；若为不贴/机构标记需确认",
    } for key in brd.keys() - bom.keys()]
    missing_in_brd = [{
        "refdes": bom[key].get("refdes"), "prefix": _prefix(bom[key].get("refdes")),
        "package": bom[key].get("footprint", ""), "dnp": bom[key].get("dnp"),
        "note": "完整 BOM 有该位号但 BRD 位号清单未列，核对是否 DNP、替代料、机构件或版本串用",
    } for key in bom.keys() - brd.keys()]
    missing_in_bom.sort(key=lambda x: _natural_key(x["refdes"]))
    missing_in_brd.sort(key=lambda x: _natural_key(x["refdes"]))
    conflicts.sort(key=lambda x: (_natural_key(x["refdes"]), x["field"]))

    brd_available = bool(brd)
    if not brd_available:
        # 没有 BRD 位号清单时，BOM 行只能列出，不能称为“BRD 漏列”。
        missing_in_brd = []

    conflict_refs = {_norm_ref(c["refdes"]) for c in conflicts}
    rows = []
    for key in sorted(brd.keys() | bom.keys(), key=lambda k: _natural_key(k)):
        has_brd, has_bom = key in brd, key in bom
        if not brd_available:
            status = "unverified"
        elif not has_brd:
            status = "missing_in_brd"
        elif not has_bom:
            status = "missing_in_bom"
        elif key in conflict_refs:
            status = "conflict"
        else:
            status = "matched"
        rows.append({
            "refdes": (brd.get(key) or bom.get(key) or {}).get("refdes"),
            "in_brd": has_brd, "in_bom": has_bom, "status": status,
            "side": (bom.get(key) or {}).get("side", ""),
            "dnp": (bom.get(key) or {}).get("dnp"),
            "package": (bom.get(key) or {}).get("footprint") or (brd.get(key) or {}).get("package", ""),
            "brd": brd.get(key), "bom": bom.get(key),
        })

    if not brd_available:
        status = "unavailable"
    elif missing_in_bom or duplicate_bom or duplicate_brd:
        status = "incomplete"
    elif missing_in_brd or conflicts:
        status = "review"
    else:
        status = "verified"

    summary = {
        "brd_count": len(brd), "bom_count": len(bom),
        "matched_count": len(brd.keys() & bom.keys()),
        "missing_in_bom_count": len(missing_in_bom),
        "missing_in_brd_count": len(missing_in_brd),
        "conflict_count": len(conflicts),
        "duplicate_count": len(duplicate_brd) + len(duplicate_bom),
    }
    warnings = []
    if not brd_available:
        warnings.append("BRD 没有可用位号清单，BOM 只能列出，不能判定缺漏")
    if missing_in_bom:
        warnings.append(f"{len(missing_in_bom)} 个 BRD 位号不在 BOM 中")
    if missing_in_brd:
        warnings.append(f"{len(missing_in_brd)} 个 BOM 位号不在 BRD 中，需确认 DNP/机构件/版本")
    if conflicts:
        warnings.append(f"{len(conflicts)} 个已匹配位号存在属性差异，需人工确认")
    if duplicate_brd or duplicate_bom:
        warnings.append("任一侧存在重复位号，不能作为可信核对结果")

    return {
        "status": status, "consistent": status == "verified",
        "brd_available": brd_available, "summary": summary,
        "fields_compared": ["footprint", "part_number", "manufacturer_part_number", "value"],
        "missing_in_bom": missing_in_bom, "missing_in_brd": missing_in_brd,
        "conflicts": conflicts,
        "duplicates": {"brd": duplicate_brd, "bom": duplicate_bom},
        "rows": rows, "warnings": warnings,
    }

"""BRD object/package evidence reader, preserving incomplete-source warnings."""
from src.domain.bom_rules import _prefix


def brd_component_records(brd_path, raw=None):
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
        packages = {key.upper(): (value or {}).get("封装", "") for key, value in (scanned.get("by_refdes") or {}).items()}
        warnings.extend(scanned.get("notes") or [])
    except Exception as exc:  # noqa: BLE001 supplemental fields never invent evidence
        warnings.append(f"BRD 封装探查失败: {exc}")
    records = []
    for refdes in components.get("refdes") or []:
        prefix = _prefix(refdes)
        records.append({"refdes": refdes, "prefix": prefix,
                        "category": _PREFIX_CATEGORY.get(prefix, prefix or "未知"),
                        "package": packages.get(refdes.upper(), ""),
                        "source": "objects/Part.xml" + (" + objects/PartDefn.xml/Gate.xml" if packages.get(refdes.upper()) else "")})
    return {"ok": True, "records": records, "warnings": warnings}

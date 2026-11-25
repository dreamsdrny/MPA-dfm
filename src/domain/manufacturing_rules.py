"""Manufacturing/BOM evidence reconciliation. No file reads or coordinate guesses."""
from copy import deepcopy

from src.domain.bom_rules import _norm_ref, reconcile_bom


def merge_placements(records, placements, confirmed, warnings):
    records = deepcopy(records)
    lookup = {}
    for record in records:
        lookup.setdefault(_norm_ref(record.get("refdes")), []).append(record)
    for placement in placements:
        candidates = lookup.get(_norm_ref(placement["refdes"]), [])
        if len(candidates) > 1:
            warnings.append(f"{placement['refdes']}: 版图位号重复，坐标 CSV 未覆盖任何实例")
        elif candidates:
            target = candidates[0]
            target["placement_evidence"] = placement
            for field in ("package", "side"):
                if target.get(field) and placement.get(field) and target[field].upper() != placement[field].upper():
                    warnings.append(f"{placement['refdes']}: 坐标 CSV 与版图 {field} 不一致")
            if target.get("coordinate_kind") != "component_origin":
                target.update({key: placement[key] for key in ("x_mm", "y_mm", "rotation", "coordinate_kind", "rotation_convention")})
                target["position_source"] = placement["source"]
                target["alignment_confirmed"] = confirmed
                target["side"] = target.get("side") or placement["side"]
                target["package"] = target.get("package") or placement["package"]
            elif confirmed and any(abs(target[key] - placement[key]) > .01 for key in ("x_mm", "y_mm")):
                warnings.append(f"{placement['refdes']}: ODB++ 与坐标 CSV 原点差 >0.01mm，未覆盖 ODB++ 坐标")
        else:
            record = deepcopy(placement)
            record["alignment_confirmed"] = confirmed
            record["position_source"] = record["source"]
            records.append(record)
    return records


def reconcile_layout(records, rows):
    old = reconcile_bom(records, rows)
    keys = {"brd": "layout", "brd_count": "layout_count", "brd_available": "layout_available",
            "in_brd": "in_layout", "missing_in_brd": "missing_in_layout", "missing_in_brd_count": "missing_in_layout_count"}
    def translate(value):
        if isinstance(value, dict):
            return {keys.get(key, key): translate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [translate(item) for item in value]
        return value
    result = translate(old)
    for row in result["rows"]:
        if row["status"] == "missing_in_brd":
            row["status"] = "missing_in_layout"
    for field in ("missing_in_bom", "missing_in_layout", "conflicts"):
        for row in result[field]:
            row["note"] = row["note"].replace("BRD", "版图")
    result["warnings"] = [text.replace("BRD", "版图") for text in result["warnings"]]
    for row in result["rows"]:
        layout, bom = row.get("layout") or {}, row.get("bom") or {}
        if layout.get("side") and bom.get("side") and layout["side"] != bom["side"]:
            result["conflicts"].append(dict(refdes=row["refdes"], field="side", layout=layout["side"], bom=bom["side"],
                                            severity="review", note="贴装面别不一致"))
            row["status"] = "conflict"
        if layout.get("dnp_known") and bom.get("dnp_known") and isinstance(bom.get("dnp"), bool) and layout.get("dnp") != bom["dnp"]:
            result["conflicts"].append(dict(refdes=row["refdes"], field="dnp", layout=layout["dnp"], bom=bom["dnp"],
                                            severity="review", note="ODB++ no_pop 与 BOM 不贴信息不一致"))
            row["status"] = "conflict"
    result["summary"]["conflict_count"] = len(result["conflicts"])
    if result["conflicts"] and result["status"] == "verified":
        result.update(status="review", consistent=False)
    return result

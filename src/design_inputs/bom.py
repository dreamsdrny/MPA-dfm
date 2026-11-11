"""BOM CSV 合同 v1: 一行一个位号，UTF-8，逗号分隔。"""
import csv

FIELDS = ("refdes", "part_number", "value", "footprint", "description", "manufacturer", "manufacturer_part_number")
ALIASES = {
    "reference": "refdes", "references": "refdes", "designator": "refdes", "位号": "refdes",
    "part number": "part_number", "partnumber": "part_number", "料号": "part_number",
    "封装": "footprint", "描述": "description", "manufacturer part number": "manufacturer_part_number",
    "数量": "quantity", "qty": "quantity",
}


def import_bom(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("BOM 缺少 CSV 表头")
        mapping = {key: ALIASES.get(key.strip().lower(), key.strip().lower()) for key in reader.fieldnames}
        if "refdes" not in mapping.values():
            raise ValueError("BOM 必须包含 refdes/Reference/Designator/位号列")
        rows, seen = [], set()
        for row in reader:
            values = {mapping[k]: (v or "").strip() for k, v in row.items() if k is not None}
            # 常见导出在一个单元格中用逗号或分号列出多个位号。
            refs = [r.strip() for r in values.get("refdes", "").replace(";", ",").split(",") if r.strip()]
            if not refs:
                raise ValueError("BOM 存在空位号")
            if values.get("quantity"):
                try:
                    quantity = int(values["quantity"])
                except ValueError as exc:
                    raise ValueError("BOM 数量必须为整数") from exc
                if quantity != len(refs):
                    raise ValueError("BOM 数量与位号数量不一致")
            for ref in refs:
                if ref.upper() in seen:
                    raise ValueError("BOM 位号重复: " + ref)
                seen.add(ref.upper())
                rows.append({key: ref if key == "refdes" else values.get(key, "") for key in FIELDS})
        return rows


def export_bom(path, rows):
    rows = list(rows)
    seen = set()
    for row in rows:
        ref = str(row.get("refdes") or "").strip()
        if not ref or "," in ref or ";" in ref:
            raise ValueError("导出 BOM 的每行必须包含且仅包含一个位号")
        if ref.upper() in seen:
            raise ValueError("BOM 位号重复: " + ref)
        seen.add(ref.upper())
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

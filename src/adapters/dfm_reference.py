"""Read external DFM workbooks as evidence, not executable supplier rules.

Never executes macros, guesses units, selects a threshold tier or changes the file.
"""
import math
import re
from pathlib import Path

import openpyxl

from src.infrastructure.snapshots import capture_sources, verify_sources

FACTORS = {"mm": 1.0, "mil": .0254, "um": .001}


def measurement_basis(rule, header_unit):
    """Rule semantics override generic mil headers for NON-distance statistics."""
    text = str(rule).lower()
    if "non-uniform smd pads size" in text:
        return "vendor_area_difference_unknown"
    if "%" in text or "percent" in text:
        return "percent"
    if any(token in text for token in ("fiducial primary", "fiducial secondary", "hole in smd", "pin count")):
        return "count"
    if any(token in text for token in ("mismatch", "short detected", "refdes in bom", "missing", "cad package is not unique")):
        return "categorical"
    return header_unit


def heading_unit(value):
    text = str(value or "").lower()
    tokens = re.findall(r"(?<![a-z])(?:mm|mils?|um)(?![a-z])", text)
    units = {"mil" if token == "mils" else token for token in tokens}
    return next(iter(units)) if len(units) == 1 else None


def converted(value, unit):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or unit not in FACTORS:
        return None
    return round(value * FACTORS[unit], 9)


def read_dfm_reference(path):
    path = Path(path).resolve()
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ValueError("DFM参考报告只支持xlsx/xlsm；不会执行宏")
    sources = capture_sources([path])
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_links=False)
    details, summaries, warnings = [], [], []
    try:
        for sheet in workbook:
            if sheet.title.lower() == "issue summary":
                for row in sheet.iter_rows(min_row=14, max_row=min(sheet.max_row, 2000), max_col=15, values_only=True):
                    if isinstance(row[2], (int, float)) and row[3]:
                        summaries.append(dict(number=row[2], name=row[3], description=row[5], advice=row[6], count=row[7], severity=row[8], status=row[9]))
            elif re.fullmatch(r"Issue\d+", sheet.title, re.IGNORECASE):
                if sheet.max_row > 10000:
                    raise ValueError("DFM参考报告明细行数超限，未截断成成功")
                units = {key: heading_unit(sheet.cell(28, col).value) for key, col in
                         (("measured", 9), ("limits", 10), ("x", 11), ("y", 12))}
                if any(unit is None for unit in units.values()):
                    warnings.append(sheet.title + " 表头单位未获取/歧义，不猜测毫米值")
                rows = []
                for values in sheet.iter_rows(min_row=29, max_col=16, values_only=True):
                    if not isinstance(values[2], (int, float)) or not values[3] or not str(values[3]).strip():
                        continue
                    basis=measurement_basis(values[3],units["measured"])
                    if basis=="vendor_area_difference_unknown":
                        warnings.append(sheet.title+" 焊盘大小差原始统计口径不明（可能超过100），不当mil或0..1比例")
                    limit_basis=measurement_basis(values[3],units["limits"])
                    row = dict(number=values[2], rule=values[3], layer=values[5], from_object=values[6], to_object=values[7],
                               measured_original=values[8], limits_original=values[9], x_original=values[10], y_original=values[11],
                               severity=values[12], status=values[13], version=values[14], feedback=values[15],
                               measurement_basis=basis, measured_mm=converted(values[8], basis),
                               x_mm=converted(values[10], units["x"]), y_mm=converted(values[11], units["y"]))
                    # Keep ALL tiers and their original order; do not choose one.
                    tiers = []
                    raw_tiers=[]
                    for token in re.split(r"[,，]", str(values[9] or "")):
                        try:
                            value = float(token.strip())
                        except ValueError:
                            tiers.append(None)
                            raw_tiers.append(None)
                        else:
                            raw_tiers.append(value if math.isfinite(value) else None)
                            tiers.append(converted(value,limit_basis) if value>=0 else None)
                    row["limit_tiers_mm"] = tiers
                    row["limit_tiers_numeric"] = raw_tiers
                    row["measured_ratio"] = converted(values[8],"mm")/100 if basis=="percent" and converted(values[8],"mm") is not None else None
                    row["measured_count"] = values[8] if basis=="count" else None
                    row["selected_limit_mm"] = None
                    rows.append(row)
                descriptions = []
                for values in sheet.iter_rows(min_row=4, max_row=25, min_col=9, max_col=16, values_only=True):
                    descriptions.extend(str(value) for value in values if value is not None and str(value).strip())
                details.append(dict(sheet=sheet.title, units=units, descriptions=descriptions, records=rows))
    finally:
        workbook.close()
    verify_sources(sources)
    return dict(source=str(path), source_sha256=sources[0]["sha256"], macros_executed=False, details=details,
                expert_summary=summaries, detail_categories=len(details), detail_positions=sum(len(d["records"]) for d in details),
                expert_categories=len(summaries), warnings=warnings, thresholds_confirmed=False, brd_alignment_confirmed=False)

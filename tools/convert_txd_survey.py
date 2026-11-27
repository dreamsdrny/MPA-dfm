"""Read-only TXD survey conversion: complete cell archive plus reviewed numeric mapping."""
import argparse
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys

import openpyxl
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def convert(source):
    source = Path(source)
    before = sha256(source.read_bytes()).hexdigest()
    wb = openpyxl.load_workbook(source, data_only=False)
    cached = openpyxl.load_workbook(source, data_only=True)
    doc = dict(schema_version=1, meta=dict(factory="同兴达 / TXD", revision="2026-11-13",
        scope="SMT / SMT+COB", source_files=[source.name], source_sha256=before,
        mapping_note="保留全部原文；仅供应商明确同口径值作定量规则。歧义、冲突、通常值及严格大于条件待确认，不套用解释列示例。"),
        profiles={}, requirements=[], source_workbook=dict(filename=source.name, sha256=before, sheets=[]))
    # Explicit mapping audited against column F; never copy Lijing thresholds.
    common = [
        (1, "board_size_x", "单板 X 上限", "<=", 500), (1, "board_size_y", "单板 Y 上限", "<=", 500),
        (2, "panel_size_x", "拼板 X 上限", "<=", 500), (2, "panel_size_y", "拼板 Y 上限", "<=", 500),
        (3, "process_edge_mm", "单边工艺边宽度", ">=", 5),
        (5, "component_spacing", "通用器件间距（0.2/口径不全）", ">=", None),
        (6, "component_to_edge", "通用板边距离（严格大于、与分档冲突）", ">=", None),
        (7, "component_to_vcut", "通用分板距离（通常值、与Chip分档冲突）", ">=", None),
        (8, "board_thickness", "板厚范围（非常用厚度需设备评估）", "range", (.3, 3)),
        (11, "bga_pitch", "BGA球距（0.25与0.4冲突待确认）", ">=", None),
        (15, "tooling_hole_diameter", "机械定位孔最小直径", ">=", 2),
        (16, "tooling_hole_to_edge", "定位孔中心到板边", ">=", 3),
        (20, "mark_diameter", "圆形Mark直径", ">=", .6),
        (21, "mark_keepout", "Mark禁铜单边（严格大于1mm待确认）", ">=", None),
        (22, "silkscreen_char_height", "丝印字高（严格大于0.75mm）", ">=", None),
        (24, "silkscreen_clearance", "丝印到焊盘净空", ">=", .2),
        (30, "chip_0402_spacing", "0402间距（0.2~0.3分档不明）", ">=", None),
        (30, "chip_0603_spacing", "0603间距（0.2~0.3分档不明）", ">=", None),
        (35, "qfp_pitch", "QFP/SOP Pitch（通常值）", ">=", None),
        (41, "qfn_pitch", "QFN/DFN Pitch（通常值）", ">=", None),
        (44, "connector_pitch", "连接器Pitch（通常值）", ">=", None),
    ]
    columns = ("no", "category", "process", "item", "definition", "supplier_value", "equipment", "inspection", "design_guidance", "reference")
    for index, sheet in enumerate(wb):
        key = "smt" if index == 0 else "smt_cob"
        cells = []
        for row in sheet:
            for cell in row:
                if cell.value is not None or cell.comment or cell.hyperlink:
                    record = dict(coordinate=cell.coordinate, value=cell.value, data_type=cell.data_type)
                    if cell.data_type == "f": record["cached_value"] = cached[sheet.title][cell.coordinate].value
                    if cell.comment: record["comment"] = cell.comment.text
                    if cell.hyperlink: record["hyperlink"] = cell.hyperlink.target
                    cells.append(record)
        doc["source_workbook"]["sheets"].append(dict(name=sheet.title, rows=sheet.max_row, columns=sheet.max_column,
            merged_cells=list(map(str, sheet.merged_cells.ranges)), cells=cells))
        rules = []
        for no, field, label, op, value in common + ([] if index else [(51, "test_pad_diameter", "测试点直径", ">=", .8)]):
            row = dict(id=f"TXD-{key.upper()}-{field.upper()}", field=field, label=label, scope="design", unit="mm", op=op,
                severity="high", source_cell=f"{sheet.title}!F{no + 2}", note=str(sheet.cell(no + 2, 6).value or ""),
                advice="核对当前工艺适用性；原文的限制、冲突和未填写项仍需工程评审")
            if op == "range": row.update(min=value[0], max=value[1])
            else: row["value"] = value
            rules.append(row)
        # COB 工作表没有单独的 TP 尺寸行；TP 间距行号也不同，按各自原文行绑定。
        no = 52 if index == 0 else 50
        rules.append(dict(id=f"TXD-{key.upper()}-TEST-PITCH", field="test_pad_pitch", label="测试点间距", scope="design",
            unit="mm", op=">=", value=1.5, severity="high", source_cell=f"{sheet.title}!F{no+2}",
            note=str(sheet.cell(no + 2, 6).value or ""), advice="核对当前工艺适用性；原文的限制、冲突和未填写项仍需工程评审"))
        doc["profiles"][key] = dict(label="同兴达 · " + ("SMT" if index == 0 else "SMT + COB"), source_sheet=sheet.title, rules=rules)
        for r in range(3, sheet.max_row + 1):
            row = dict(zip(columns, [sheet.cell(r, c).value for c in range(1, 11)]))
            row.update(profile=key, source_sheet=sheet.title, source_row=r,
                source_cells={name: f"{sheet.title}!{openpyxl.utils.get_column_letter(c)}{r}" for c, name in enumerate(columns, 1)})
            doc["requirements"].append(row)
    wb.close(); cached.close()
    if sha256(source.read_bytes()).hexdigest() != before: raise ValueError("转换期间源文件已变化")
    from src.domain.smt_rules import validate_profile
    validate_profile(deepcopy(doc))
    return doc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source"); parser.add_argument("output")
    args = parser.parse_args()
    target = Path(args.output)
    if target.exists(): raise FileExistsError("不覆盖已有配置：" + str(target))
    doc = convert(args.source)
    target.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"{target}: {len(doc['requirements'])} rows, {len(doc['profiles'])} profiles, source {doc['meta']['source_sha256']}")


if __name__ == "__main__": main()

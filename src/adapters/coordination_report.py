"""制造资料/BOM 关联报告和可追溯叠图，不推测缺失坐标。"""
import html
import json
from pathlib import Path

import openpyxl
from shapely import from_wkb

STATUS_LABELS = {"verified": "位号一致（非 DFM 放行）", "review": "需复核", "incomplete": "资料缺项",
                 "unavailable": "无版图位号证据", "matched": "匹配", "conflict": "冲突",
                 "missing_in_bom": "BOM 漏项", "missing_in_layout": "BOM 多出",
                 "unverified": "未验证", "needs_step": "请选择 step", "no_bom": "未导入 BOM"}
COMPONENT_KEYS = ("refdes", "status", "part_number", "manufacturer_part_number", "value", "package",
                  "side", "dnp", "x_mm", "y_mm", "rotation", "coordinate_kind", "spatial_verified", "position_source")
COMPONENT_HEADERS = ("位号", "关联状态", "料号", "MPN", "值", "封装", "面别", "不贴", "X mm", "Y mm",
                     "旋转", "坐标类型", "空间对齐已确认", "位置来源")


def export_coordination(project, path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(project, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        return str(path)
    if path.suffix.lower() != ".xlsx":
        raise ValueError("协同报告仅支持 .xlsx/.json")
    workbook = openpyxl.Workbook()
    summary = workbook.active
    summary.title = "导入与关联摘要"
    summary.append(["制造格式", project["kind"], "状态", STATUS_LABELS.get(project["status"], project["status"])])
    summary.append(["选中 step", project["layout"].get("selected_step")])
    summary.append(["放行", "仅资料关联；不替代完整几何 DFM/SMT 分析"])
    for source in project["sources"]:
        summary.append(["来源", source["path"], source["sha256"]])
    for warning in project["warnings"]:
        summary.append(["提示/缺失", warning])
    components = workbook.create_sheet("器件BOM关联")
    components.append(COMPONENT_HEADERS)
    for row in project["components"]:
        components.append([row.get(key) for key in COMPONENT_KEYS])
    pins = workbook.create_sheet("器件引脚坐标")
    pins.append(["位号", "引脚", "X mm", "Y mm", "网络", "层", "来源"])
    for row in project["components"]:
        for pin in row.get("pins") or []:
            pins.append([row["refdes"], *[pin.get(key) for key in ("pin", "x_mm", "y_mm", "net", "layer")],
                         pin.get("source") or row.get("position_source")])
    layers = workbook.create_sheet("制造图层")
    layers.append(["文件/层", "角色", "图元数", "渲染面积 mm²", "解析状态", "来源"])
    for row in project["layout"].get("films", []):
        layers.append([row.get(key) for key in ("name", "role", "feature_count", "area_mm2", "status", "source")])
    conflicts = workbook.create_sheet("属性冲突")
    conflicts.append(["位号", "字段", "版图值", "BOM值", "说明"])
    for row in (project.get("reconciliation") or {}).get("conflicts", []):
        conflicts.append([row.get(key) for key in ("refdes", "field", "layout", "bom", "note")])
    duplicates = workbook.create_sheet("重复与非电气项")
    duplicates.append(["类型", "位号", "内容"])
    for kind, rows in (project.get("reconciliation") or {}).get("duplicates", {}).items():
        for row in rows:
            duplicates.append([kind, row.get("refdes"), json.dumps(row, ensure_ascii=False)])
    for row in project.get("non_electrical", []):
        duplicates.append(["非电气项", row.get("refdes"), row.get("part_number")])
    if project.get("object_dfm"):
        analysis = project["object_dfm"]
        findings = workbook.create_sheet("对象DFM问题")
        keys = ("item", "status", "layer", "refdes", "pin", "other_refdes", "other_pin", "net", "other_net",
                "measured", "capability", "unit", "x_mm", "y_mm", "source", "note")
        findings.append(["规则", "状态", "层", "位号", "引脚", "另一位号", "另一引脚", "网络", "另一网络",
                         "实测", "要求", "单位", "X mm", "Y mm", "来源", "说明"])
        for row in analysis["checks"]:
            findings.append([row.get(key) for key in keys])
        coverage = workbook.create_sheet("对象DFM覆盖")
        coverage.append(["规则", "状态", "已测", "缺失", "说明", "需要资料"])
        for row in analysis["geometry_coverage"]:
            coverage.append([row.get(k) for k in ("rule", "status", "checked", "missing", "note", "required_data")])
        summary.append(["对象 DFM 结论", analysis["verdict"], analysis["scope"]])
    for sheet in workbook:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = 28
        # BOM/ODB/archive strings are data, never spreadsheet formulas.
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
    workbook.save(path)
    return str(path)


def write_coordination_preview(project, path, selected_ref=""):
    films = project["layout"].get("films", [])
    shapes = [(film, from_wkb(film["geometry_wkb"])) for film in films]
    bounds = [shape.bounds for _, shape in shapes if not shape.is_empty]
    if not bounds:
        raise ValueError("没有可渲染制造图层，请先选择有效 step/图层")
    x0, y0 = min(b[0] for b in bounds), min(b[1] for b in bounds)
    x1, y1 = max(b[2] for b in bounds), max(b[3] for b in bounds)
    span = max(x1-x0, y1-y0, 1)
    margin = span * .03
    drawings, markers, rows = [], [], []
    for film, shape in shapes:
        if shape.is_empty:
            continue
        color = {"copper": "#c78c42", "mask": "#38b88b", "paste": "#cccccc",
                 "silkscreen": "#b299f0", "drill": "#ff5555"}.get(film["role"].split("_")[0], "#999999")
        drawings.append(f'<g opacity=".4"><title>{html.escape(film["name"])}</title>{shape.svg(fill_color=color)}</g>')
    for row in project["components"]:
        chosen = row["refdes"].upper() == selected_ref.upper()
        color = "#ff5555" if chosen else "#61c9ff"
        position = []
        if row.get("spatial_verified") and row.get("x_mm") is not None:
            position = [(row["x_mm"], row["y_mm"], "器件原点")]
        else:
            # Explicit Gerber X2 pads and ODB pins are native evidence; external
            # CSV origins not confirmed aligned are never silently overlaid.
            position = [(pin["x_mm"], pin["y_mm"], "引脚 " + str(pin.get("pin", "")))
                        for pin in row.get("pins") or []]
        for x, y, label in position:
            markers.append(f'<circle cx="{x}" cy="{y}" r="{span/180 if chosen else span/300}" fill="{color}">'
                           f'<title>{html.escape(row["refdes"] + " " + label)}</title></circle>')
        rows.append("<tr>" + "".join(f"<td>{html.escape(str(row.get(key) if row.get(key) is not None else ''))}</td>"
                                    for key in ("refdes", "status", "part_number", "value", "package", "x_mm", "y_mm", "spatial_verified")) + "</tr>")
    text = f'''<!doctype html><meta charset="utf-8"><title>制造资料 BOM 协同</title>
<style>body{{background:#18202d;color:#eee;font:14px sans-serif}}svg{{width:100%;height:65vh}}td,th{{padding:5px;border:1px solid #667}}table{{border-collapse:collapse}}</style>
<h2>制造资料 / BOM 关联 · {html.escape(selected_ref)}</h2>
<p>器件原点仅叠加已确认对齐的坐标；焊盘闪光不是器件原点。此预览不是完整 DFM 放行。</p>
<p>{html.escape('; '.join(project['warnings']))}</p>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0-margin} {-y1-margin} {max(x1-x0,1)+2*margin} {max(y1-y0,1)+2*margin}"><g transform="scale(1,-1)">{''.join(drawings+markers)}</g></svg>
<table><tr><th>位号</th><th>状态</th><th>料号</th><th>值</th><th>封装</th><th>X mm</th><th>Y mm</th><th>对齐</th></tr>{''.join(rows)}</table>'''
    Path(path).write_text(text, encoding="utf-8")
    return str(Path(path).resolve())

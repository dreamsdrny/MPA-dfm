"""Checks on Allegro-exported silkscreen coordinates, separate from component origins.

Straight strokes use segment geometry. Text/arcs/pads use envelope checks marked
for review rather than claiming precise copper/mask polygon clearance.
"""
import csv
import math
import os
from pathlib import Path

from src.design_geometry import _seg_seg_dist
from src.pick import _UNIT_MM, _to_mm

GEOMETRY_SKILL = r'''
procedure(evan_geometry_row(fp obj kind ref)
  let((box xy se p1 p2 width layer type parent)
    box = obj->bBox
    when(box
      layer = obj->layer
      type = obj->objType
      when(ref == ""
        parent = obj->parent
        when(parent && parent->objType == "path" parent = parent->parent)
        when(parent && parent->objType == "symbol" ref = parent->refdes))
      xy = car(box)
      width = 0.0
      se = nil
      when(type == "line" || type == "arc"
        width = obj->width
        se = obj->startEnd)
      when(type == "text" xy = obj->xy)
      unless(stringp(layer) layer = "")
      p1 = car(box)
      p2 = cadr(box)
      when(se p1 = car(se) p2 = cadr(se))
      when(type == "line" || type == "arc" xy = list((car(p1)+car(p2))/2 (cadr(p1)+cadr(p2))/2))
      fprintf(fp "%s,%s,%s,%s,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f\n"
        kind evan_string(type) evan_string(layer) evan_string(ref)
        car(xy) cadr(xy) width car(car(box)) cadr(car(box)) car(cadr(box)) cadr(cadr(box))
        car(p1) cadr(p1) car(p2) cadr(p2)))))

procedure(evan_geometry_object(fp obj kind)
  if(obj->objType == "path" || obj->objType == "polygon" || obj->objType == "shape"
    then foreach(seg obj->segments evan_geometry_row(fp seg kind ""))
    else evan_geometry_row(fp obj kind "")))

procedure(evan_geometry_file(dir fileName layers kind)
  prog((fp vis enabled buttons selected objects answer count)
    vis = axlVisibleGet()
    enabled = axlGetFindFilter()
    buttons = axlGetFindFilter(t)
    selected = axlGetSelSet()
    fp = outfile(strcat(dir "/" fileName) "w")
    fprintf(fp "UNITS,%s\n" car(axlDBGetDesignUnits()))
    fprintf(fp "KIND,TYPE,LAYER,REFDES,X_UU,Y_UU,WIDTH_UU,XMIN_UU,YMIN_UU,XMAX_UU,YMAX_UU,X1_UU,Y1_UU,X2_UU,Y2_UU\n")
    answer = errset(progn(
      axlClearSelSet()
      axlVisibleDesign(nil)
      foreach(layer layers axlVisibleLayer(layer t))
      axlSetFindFilter(?enabled list("noall" "lines" "text" "shapes")
                       ?onButtons list("noall" "lines" "text" "shapes"))
      axlAddSelectAll()
      objects = axlGetSelSet()
      count = 0
      foreach(obj objects
        evan_geometry_object(fp obj kind)
        count = count + 1)) t)
    close(fp)
    axlVisibleSet(vis)
    axlSetFindFilter(?enabled cons("noall" enabled) ?onButtons cons("noall" buttons))
    axlClearSelSet()
    when(selected axlAddSelectObject(selected))
    unless(answer printf("Evan geometry export failed: %s\n" fileName))
    return(count)))

procedure(evan_geometry_all(dir)
  evan_geometry_file(dir "evan_silkscreen.csv"
    list("REF DES/SILKSCREEN_TOP" "REF DES/SILKSCREEN_BOTTOM"
         "PACKAGE GEOMETRY/SILKSCREEN_TOP" "PACKAGE GEOMETRY/SILKSCREEN_BOTTOM"
         "BOARD GEOMETRY/SILKSCREEN_TOP" "BOARD GEOMETRY/SILKSCREEN_BOTTOM") "SILK")
  evan_geometry_file(dir "evan_outline.csv"
    list("BOARD GEOMETRY/OUTLINE" "BOARD GEOMETRY/DESIGN_OUTLINE") "OUTLINE")
  t)
'''


def write_geometry_script(folder):
    path = Path(folder) / "evan_geometry.il"
    path.write_text(GEOMETRY_SKILL, encoding="ascii")
    return str(path)


def parse_geometry(path):
    rows, unit = [], ""
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = None
        for values in reader:
            if not values:
                continue
            if values[0].upper() == "UNITS":
                unit = values[1].strip().lower()
                continue
            if header is None:
                header = [v.upper().strip() for v in values]
                continue
            raw = dict(zip(header, values))
            row = dict(kind=raw.get("KIND", ""), type=raw.get("TYPE", ""),
                       layer=raw.get("LAYER", ""), refdes=raw.get("REFDES", ""), source=os.path.abspath(path))
            try:
                for key in ("X", "Y", "WIDTH", "XMIN", "YMIN", "XMAX", "YMAX", "X1", "Y1", "X2", "Y2"):
                    val = _to_mm(float(raw[key + "_UU"]), unit, None)
                    if val is None:
                        raise ValueError("未知单位或坐标")
                    row[key.lower() + "_mm"] = val
            except (KeyError, ValueError):
                continue
            rows.append(row)
    return rows


def _side(layer):
    return "BOTTOM" if "BOTTOM" in layer.upper() else "TOP" if "TOP" in layer.upper() else ""


def _box(row):
    return row["xmin_mm"], row["ymin_mm"], row["xmax_mm"], row["ymax_mm"]


def _box_gap(a, b):
    return math.hypot(max(a[0] - b[2], b[0] - a[2], 0), max(a[1] - b[3], b[1] - a[3], 0))


def _segments(row):
    if row.get("type", "").lower() == "line":
        return [((row["x1_mm"], row["y1_mm"]), (row["x2_mm"], row["y2_mm"]))]
    x0, y0, x1, y1 = _box(row)
    return [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]


def check_positions(silks, outlines, pads, capability, components=()):
    """All measured violations retain coordinates; approximate envelope hits are WARN."""
    from src.dfm_engine import _to_mm as capability_mm
    rules = capability.get("silkscreen", {})
    minimum_width = capability_mm(rules.get("min_line_width"))
    minimum_edge = capability_mm(rules.get("min_to_outline"))
    minimum_mask = capability_mm(rules.get("min_to_solder_mask"))
    output = []

    def add(obj, item, field, measured, limit, status, note):
        row = dict(item=item, field=field, measured=round(measured, 6), capability=limit, unit="mm",
                   status=status, x_mm=obj["x_mm"], y_mm=obj["y_mm"], layer=obj.get("layer", ""),
                   refdes=obj.get("refdes", ""), source=obj["source"], note=note)
        same_side = [c for c in components if not c.get("side") or c["side"] == _side(obj.get("layer", ""))]
        if same_side:
            nearest = min(same_side, key=lambda c: math.hypot(c["x_mm"] - row["x_mm"], c["y_mm"] - row["y_mm"]))
            row["note"] += f"；最近器件 {nearest.get('refdes')}（仅位置关联）"
        output.append(row)

    for silk in silks:
        width = silk.get("width_mm", 0)
        if width > 0 and minimum_width is not None and width < minimum_width - 1e-9:
            add(silk, "丝印图形线宽", "silkscreen_width", width, minimum_width, "fail", "Allegro 实际图形线宽低于能力")
        if minimum_edge is not None and outlines:
            # Nonlinear outline envelopes cannot prove distance to the real edge.
            linear_outlines = [o for o in outlines if o["type"].lower() == "line"]
            near = [o for o in linear_outlines if _box_gap(_box(silk), _box(o)) <= minimum_edge + width / 2]
            distance = min((_seg_seg_dist(a, b, c, d) for a, b in _segments(silk)
                            for outline in near for c, d in _segments(outline)), default=float("inf"))
            distance = max(0, distance - width / 2)
            if distance < minimum_edge - 1e-9:
                exact = silk["type"].lower() == "line"
                if silk["type"].lower() != "line":
                    # If a straight edge traverses an envelope, its interior
                    # might be ink-free. Keep it a candidate, never exact FAIL.
                    distance = min(distance, min((_box_gap(_box(silk), _box(o)) for o in near), default=distance))
                add(silk, "丝印离成型线位置", "silkscreen_to_outline", distance, minimum_edge,
                    "fail" if exact else "warn_limit", "线段边缘距离" if exact else "文字/圆弧外框候选，需核对真实字形/轮廓")
        if minimum_mask is not None and _side(silk.get("layer", "")):
            for pad in pads:
                if not pad.get("side") or pad["side"] != _side(silk.get("layer", "")):
                    continue
                box = (pad["x_mm"] - pad["w_mm"] / 2, pad["y_mm"] - pad["h_mm"] / 2,
                       pad["x_mm"] + pad["w_mm"] / 2, pad["y_mm"] + pad["h_mm"] / 2)
                gap = _box_gap(_box(silk), box)
                if gap < minimum_mask - 1e-9:
                    add(silk, "丝印靠近焊盘位置", "silkscreen_to_mask", gap, minimum_mask, "warn_limit",
                        f"与 {pad.get('refdes','')} 引脚外框过近；焊盘外框不等于阻焊开窗，需 CAM 复核")
                    break
    return output


def load_position_checks(directories, inventory, capability):
    silks, outlines, errors = [], [], []
    seen = set()
    for directory in directories:
        for folder in (Path(directory), Path(directory) / "allegro_bridge"):
            for name, collection in (("evan_silkscreen.csv", silks), ("evan_outline.csv", outlines)):
                path = folder / name
                key = str(path.resolve())
                if key in seen or not path.is_file():
                    continue
                seen.add(key)
                try:
                    collection.extend(parse_geometry(path))
                except (OSError, ValueError) as exc:
                    errors.append(str(exc))
    checks = check_positions(silks, outlines, inventory.get("pads", []), capability, inventory.get("components", []))
    return checks, dict(silkscreen_objects=len(silks), outline_objects=len(outlines), errors=errors)

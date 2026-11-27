"""Documented extracta geometry fields; preserve unsupported records as gaps."""
import csv
import math
from collections import defaultdict

from shapely.geometry import LineString, Point, box
from shapely.ops import polygonize, unary_union

from src.adapters.extracta import OBJECT_VIEWS, length_mm, number, parse_view
from src.pick import _UNIT_MM


def graphic(row, factor):
    kind = row["GRAPHIC_DATA_NAME"].upper()
    n = [number(row.get(f"GRAPHIC_DATA_{i}")) for i in range(1, 11)]
    if kind in ("LINE", "ARC"):
        if any(v is None for v in n[:4]):
            raise ValueError("线/圆弧端点缺失")
        points = [(n[0]*factor, n[1]*factor), (n[2]*factor, n[3]*factor)]
        width = n[4] if kind == "LINE" else n[7]
        if width is None or width < 0:
            raise ValueError("线/圆弧宽度缺失")
        shape_points = points
        extras = {}
        if kind == "ARC":
            if any(v is None for v in n[4:8]) or n[6] <= 0:
                raise ValueError("圆弧中心/半径无效")
            direction = row.get("GRAPHIC_DATA_9", "").upper().replace("COUNTERCLOCKWISE", "COUNTER-CLOCKWISE")
            if direction not in ("CLOCKWISE", "COUNTER-CLOCKWISE"):
                raise ValueError("圆弧方向缺失")
            cx, cy, radius = n[4]*factor, n[5]*factor, n[6]*factor
            if any(abs(math.hypot(x-cx, y-cy)-radius) > .005 for x, y in points):
                raise ValueError("圆弧端点与半径不一致")
            start, end = (math.atan2(y-cy, x-cx) for x, y in points)
            sweep = (end-start) % (2*math.pi)
            if direction == "CLOCKWISE":
                sweep = -((start-end) % (2*math.pi))
            if points[0] == points[1]:
                sweep = -2*math.pi if direction == "CLOCKWISE" else 2*math.pi
            steps = max(8, math.ceil(abs(sweep) / max(.0001, 2*math.acos(max(-1, 1-.001/radius)))))
            if steps > 10000:
                raise ValueError("圆弧离散预算超限")
            shape_points = [(cx+radius*math.cos(start+sweep*i/steps), cy+radius*math.sin(start+sweep*i/steps)) for i in range(steps+1)]
            shape_points[0], shape_points[-1] = points[0], points[1]
            extras = dict(center_mm=[cx, cy], radius_mm=radius, direction=direction)
        return dict(kind=kind, width_mm=width*factor, endpoints_mm=points, points=shape_points, **extras)
    if kind == "RECTANGLE" and all(v is not None for v in n[:4]):
        poly = box(n[0]*factor, n[1]*factor, n[2]*factor, n[3]*factor)
        return dict(kind=kind, polygon=list(poly.exterior.coords))
    if str(row.get("GRAPHIC_DATA_NUMBER")) in ("5", "6") and all(v is not None for v in n[:4]) and n[2] > 0 and n[3] > 0:
        poly = box((n[0]-n[2]/2)*factor, (n[1]-n[3]/2)*factor, (n[0]+n[2]/2)*factor, (n[1]+n[3]/2)*factor)
        if "PIN_ROTATION_ABSOLUTE" in row and row.get("CLASS")=="PIN" and abs(n[2]-n[3])>.000001:
            angle=number(row["PIN_ROTATION_ABSOLUTE"])
            if angle is None:raise ValueError("非对称标准焊盘缺绝对旋转，不能猜轴向")
            from shapely.affinity import rotate
            poly=rotate(poly,angle,origin=(n[0]*factor,n[1]*factor))
        return dict(kind="FIG_RECTANGLE", polygon=list(poly.exterior.coords))
    if str(row.get("GRAPHIC_DATA_NUMBER")) in ("11","12") and all(v is not None for v in n[:4]) and min(n[2:4])>0:
        x,y,w,h=[v*factor for v in n[:4]]
        radius=min(w,h)/2
        line=LineString([(x-(w-h)/2,y),(x+(w-h)/2,y)]) if w>=h else LineString([(x,y-(h-w)/2),(x,y+(h-w)/2)])
        poly=line.buffer(radius,quad_segs=32)
        if row.get("CLASS")=="PIN":
            angle=number(row.get("PIN_ROTATION_ABSOLUTE"))
            if angle is None:raise ValueError("椭圆长盘缺绝对旋转")
            from shapely.affinity import rotate
            poly=rotate(poly,angle,origin=(x,y))
        return dict(kind="OBLONG",polygon=list(poly.exterior.coords))
    if str(row.get("GRAPHIC_DATA_NUMBER")) == "2" and all(v is not None for v in n[:4]) and n[2] > 0 and n[3] > 0:
        # Bound circular chord error to <=0.001mm, like existing ARC decoding.
        radius=max(n[2],n[3])*factor/2
        quad_segs=max(8,math.ceil(math.pi/(4*math.acos(max(-1,1-.001/radius)))))
        circle = Point(n[0]*factor, n[1]*factor).buffer(1, quad_segs=quad_segs)
        from shapely.affinity import scale
        poly = scale(circle, n[2]*factor/2, n[3]*factor/2, origin=(n[0]*factor, n[1]*factor))
        return dict(kind="ROUND", polygon=list(poly.exterior.coords), diameter_mm=min(n[2], n[3])*factor)
    raise ValueError("不支持的图形类型：" + kind)


def parse_objects(folder, unit, components, copper_layers=()):
    factor = _UNIT_MM[unit]
    result = dict(pins=[], pads=[], holes=[], vias=[], traces=[], bounds=[], board_outline=[], vcut_lines=[],
                  properties=[], pin_metadata=[], warnings=[], complete={}, raw_geometry_count=0,silkscreen_refdes={"TOP":[],"BOTTOM":[]},silkscreen_text_records=[],mask_openings=[],paste_openings=[])
    component_map = {c["refdes"].upper(): c for c in components}
    copper_indices = {name.upper(): i for i, name in enumerate(copper_layers, 1)}
    raw = {}
    for name, (_, fields) in OBJECT_VIEWS.items():
        try:
            output_unit, rows = parse_view(folder / (name + ".out"), fields)
            if output_unit != unit:
                raise ValueError("单位与器件输出不同")
            raw[name] = rows
            result["complete"][name] = True
        except (OSError, ValueError) as exc:
            raw[name] = []
            result["complete"][name] = False
            result["warnings"].append(name + "：" + str(exc))
    for row in raw["properties"]:
        ref = row["REFDES"].upper()
        height = length_mm(row["COMP_HEIGHT"], unit)
        result["properties"].append(dict(refdes=ref, footprint=row["COMP_PACKAGE"],
            height_mm=height if height is not None and height > 0 else None,
            part_number=row["COMP_PART_NUMBER"], value=row["COMP_VALUE"], component_class=row["COMP_CLASS"], source=str(folder / "properties.out")))
    for row in raw["pins"]:
        x, y = number(row["PIN_X"]), number(row["PIN_Y"])
        if x is None or y is None:
            result["complete"]["pins"] = False
            continue
        result["pins"].append(dict(refdes=row["REFDES"], pin=row["PIN_NUMBER"], x_mm=x*factor, y_mm=y*factor,
            net=row["NET_NAME"], padstack=row["PAD_STACK_NAME"], pin_type=row["PIN_TYPE"], source=str(folder / "pins.out")))
    holes, vias = {}, {}
    for row in raw["pads"]:
        cls = row["CLASS"].upper()
        is_via = cls == "VIA CLASS"
        x, y = (number(row["VIA_X"]), number(row["VIA_Y"])) if is_via else (number(row["PIN_X"]), number(row["PIN_Y"]))
        base = dict(kind="VIA" if is_via else "PIN", refdes=row["REFDES"], pin=row["PIN_NUMBER"], net=row["NET_NAME"],
            padstack=row["PAD_STACK_NAME"], start_layer=row["START_LAYER_NAME"], end_layer=row["END_LAYER_NAME"],
            test_point=row["TEST_POINT"], source=str(folder / "pads.out"))
        if x is not None and y is not None and not is_via:
            result["pin_metadata"].append(dict(base, x_mm=x*factor, y_mm=y*factor))
        if x is not None and y is not None and is_via:
            obj = dict(base, x_mm=x*factor, y_mm=y*factor)
            vias[(x, y, base["padstack"], base["start_layer"], base["end_layer"], base["net"])] = obj
        hx, hy = number(row["DRILL_HOLE_X"]), number(row["DRILL_HOLE_Y"])
        if hx is not None and hy is not None and row["DRILL_HOLE_NAME"]:
            w, h = number(row["DRILL_FIGURE_WIDTH"]), number(row["DRILL_FIGURE_HEIGHT"])
            plated = {"PLATED": True, "NON_PLATED": False, "NON-PLATED": False, "YES": True, "NO": False}.get(row["DRILL_HOLE_PLATING"].upper())
            drill = length_mm(row["DRILL_HOLE_NAME"], unit)
            minor = length_mm(row.get("DRILL_HOLE_NAME2", ""), unit)
            obj = dict(base, x_mm=hx*factor, y_mm=hy*factor, drill_mm=drill if drill is not None and drill > 0 and (minor is None or abs(drill-minor) < 1e-9) else None,
                slot_length_mm=drill if minor is not None and drill != minor else None, slot_width_mm=minor if minor != drill else None,
                plated=plated, drill_shape=row["DRILL_FIGURE_SHAPE"], process=None)
            holes[(hx, hy, base["padstack"], base["start_layer"], base["end_layer"], base["net"])] = obj
    result["holes"], result["vias"] = list(holes.values()), list(vias.values())
    line_groups = defaultdict(list)
    direct_polys = defaultdict(list)
    unsupported = defaultdict(int)
    for row in raw["geometry"]:
        result["raw_geometry_count"] += 1
        cls, layer, ref = row["CLASS"].upper(), row["SUBCLASS"], row["REFDES"]
        side = "TOP" if layer.upper().endswith("TOP") else "BOTTOM" if layer.upper().endswith("BOTTOM") else ""
        if "SILKSCREEN" in layer.upper() and side and row["GRAPHIC_DATA_NAME"].upper()=="TEXT":
            result["silkscreen_text_records"].append(dict(text=row.get("GRAPHIC_DATA_7", "").strip(),side=side,refdes=ref,source=str(folder/"geometry.out")))
        if cls=="REF DES" and "SILKSCREEN" in layer.upper() and side and row["GRAPHIC_DATA_NAME"].upper()=="TEXT":
            text=row.get("GRAPHIC_DATA_7", "").strip()
            if ref and text.upper()==ref.upper():
                result["silkscreen_refdes"][side].append(ref)
        use = ("bound" if cls == "PACKAGE GEOMETRY" and "PLACE_BOUND" in layer.upper() else
               "outline" if cls == "BOARD GEOMETRY" and layer.upper() in ("OUTLINE", "DESIGN_OUTLINE") else
               "vcut" if cls == "BOARD GEOMETRY" and layer.upper() in ("V-CUT", "VCUT", "V_SCORE") else
               "pad" if cls in ("PIN", "VIA CLASS") and layer.upper() in (set(copper_indices) | {"TOP", "BOTTOM"}) else
               "mask" if cls in ("PIN", "VIA CLASS") and "SOLDERMASK" in layer.upper() and side else
               "paste" if cls in ("PIN", "VIA CLASS") and "PASTEMASK" in layer.upper() and side else
               "trace" if cls == "ETCH" and row["GRAPHIC_DATA_10"].upper() == "CONNECT" else "")
        if not use:
            continue
        if row["GRAPHIC_DATA_NAME"].upper() == "TEXT":
            # An annotation on OUTLINE/PLACE_BOUND is not an unsupported boundary.
            continue
        try:
            obj = graphic(row, factor)
        except ValueError:
            unsupported[use] += 1
            continue
        source = str(folder / "geometry.out")
        if use == "trace":
            if "endpoints_mm" not in obj or obj["width_mm"] <= 0:
                unsupported[use] += 1
                continue
            result["traces"].append(dict(obj, layer="ETCH/" + layer, net=row["NET_NAME"], object_id=row["RECORD_TAG"], source=source))
        elif use == "vcut" and obj.get("points") and obj["kind"] == "LINE":
            result["vcut_lines"].append(dict(points=obj["points"], confirmed=True, source=source))
        elif use == "vcut":
            unsupported[use] += 1
        else:
            key = (use, ref, side, row["PIN_NUMBER"], row["PAD_STACK_NAME"], row["RECORD_TAG"].split()[0] if row["RECORD_TAG"] else "", cls, layer)
            if obj.get("polygon"):
                direct_polys[key].append(obj["polygon"])
            elif obj.get("points"):
                line_groups[key].append(LineString(obj["points"]))
    board_polys = []
    for key in set(line_groups) | set(direct_polys):
        use, ref, side, pin, padstack, _, cls, layer = key
        polygons = list(polygonize(unary_union(line_groups[key]))) if line_groups[key] else []
        from shapely.geometry import Polygon
        polygons += [Polygon(points) for points in direct_polys[key]]
        if len(polygons) != 1 or not polygons[0].is_valid or polygons[0].interiors:
            unsupported[use] += 1
            continue
        poly = polygons[0]
        net = next((p["net"] for p in result["pins"] if p["refdes"] == ref and p["pin"] == pin), "")
        obj = dict(refdes=ref, side=side, net=net, outline=list(poly.exterior.coords), pin=pin, padstack=padstack,
                   source=str(folder / "geometry.out"), boundary="place_bound" if use == "bound" else "copper_pad",
                   copper_layer=layer if use == "pad" else "", layer_index=copper_indices.get(layer.upper()))
        obj["object_kind"] = "via" if cls == "VIA CLASS" else "pin"
        if use == "bound":
            if ref.upper() in component_map and side:
                result["bounds"].append(obj)
        elif use == "pad":
            center = poly.centroid
            if cls == "VIA CLASS":
                nets = {via["net"] for via in result["vias"] if via["padstack"] == padstack
                        and abs(via["x_mm"] - center.x) <= .0003 and abs(via["y_mm"] - center.y) <= .0003}
                if len(nets) == 1:
                    obj["net"] = next(iter(nets))
                elif len(nets) > 1:
                    unsupported["pad"] += 1
                    obj["net"] = ""
            result["pads"].append(dict(obj, confirmed=True, x_mm=center.x, y_mm=center.y))
        elif use in ("mask","paste"):
            center=poly.centroid
            result[use+"_openings"].append(dict(obj,confirmed=True,x_mm=center.x,y_mm=center.y,
                boundary="native_"+use+"_opening",object_id=key[5]))
        elif use == "outline":
            board_polys.append(poly)
    # Multiple outlines/cutouts are not collapsed into a rectangle or guessed union.
    if len(board_polys) == 1:
        result["board_outline"] = list(board_polys[0].exterior.coords)
    for kind, count in unsupported.items():
        result["warnings"].append(f"{kind}: {count} 个对象图形未完整解码，不认定完整覆盖")
    metadata_complete = result["complete"].get("pads", False)
    result["complete"]["silkscreen_text_records"] = result["complete"].get("geometry", False)
    for kind in ("mask","paste"):
        result["complete"][kind+"_openings"]=result["complete"].get("geometry",False) and not unsupported[kind]
    for kind in ("traces", "pads", "bounds", "board_outline", "vcut_lines"):
        use = {"traces": "trace", "pads": "pad", "bounds": "bound", "board_outline": "outline", "vcut_lines": "vcut"}[kind]
        result["complete"][kind] = result["complete"].get("geometry", False) and not unsupported[use]
    result["complete"]["pads"] &= metadata_complete
    decoded_pin_layers = {(pad["refdes"].upper(), pad["pin"], pad["copper_layer"].upper())
                          for pad in result["pads"] if pad["object_kind"] == "pin"}
    missing_pin_endpoints = 0
    for pin in result["pin_metadata"]:
        for layer in set((pin["start_layer"], pin["end_layer"])):
            if layer and (pin["refdes"].upper(), pin["pin"], layer.upper()) not in decoded_pin_layers:
                missing_pin_endpoints += 1
    if missing_pin_endpoints:
        result["complete"]["pads"] = False
        result["warnings"].append(f"{missing_pin_endpoints} 个引脚跨度端点未取得铜盘，不认定焊盘清单完整")
    if not result["pads"]:
        result["complete"]["pads"] = False
    # Deduplicate identical symbol bounds; multiple distinct outlines need review.
    unique_bounds = {}
    for bound in result["bounds"]:
        key = (bound["refdes"].upper(), bound["side"], Polygon(bound["outline"]).normalize().wkt)
        unique_bounds.setdefault(key, bound)
    result["bounds"] = list(unique_bounds.values())
    result["complete"]["bounds"] &= len(result["bounds"]) == len(components)
    result["complete"]["traces"] &= bool(result["traces"])
    # No V-CUT on recognized layers does not prove an arbitrary mechanical
    # drawing has no score cuts; absence requires an explicit engineering record.
    result["complete"]["vcut_lines"] &= bool(result["vcut_lines"])
    if not result["board_outline"]:
        result["complete"]["board_outline"] = False
    # A successful empty file isn't proof of all assembly semantics.
    result["complete"]["marks"] = False
    result["complete"]["tooling_holes"] = False
    result["complete"]["silkscreen_refdes"]=result["complete"].get("geometry",False)
    return result


def pcb_geometry_options(payload, layer_count, aligned):
    """No implicit alignment for external Gerber; manual object data takes precedence."""
    objects = payload.get("objects") or {}
    pads, vias = [], []
    for i, obj in enumerate(objects.get("pads", [])):
        layer = obj.get("layer_index") or (1 if obj["side"] == "TOP" else layer_count if obj["side"] == "BOTTOM" else None)
        if not layer:
            continue
        row = dict(obj, id=f"extracta-{obj.get('refdes') or 'via'}-{obj.get('pin')}-{obj['side']}-{i}", layer=layer,
                   mask_required=None, mask_policy="")
        (vias if obj.get("object_kind") == "via" else pads).append(row)
    return dict(pad_geometry=dict(unit="mm", coordinate_system="gerber", alignment_confirmed=bool(aligned),
                                  complete=objects.get("complete", {}).get("pads", False), pads=pads),
                via_geometry=dict(unit="mm", coordinate_system="gerber", alignment_confirmed=bool(aligned),
                                  complete=False, vias=vias))


def write_object_csvs(folder, payload):
    objects = payload.get("objects") or {}
    unit, factor = payload.get("unit"), _UNIT_MM.get(payload.get("unit"))
    if not factor:
        return
    with (folder / "evan_traces.csv").open("w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["UNITS", unit])
        w.writerow(["KIND", "LAYER", "WIDTH_UU", "X1_UU", "Y1_UU", "X2_UU", "Y2_UU", "NET"])
        for row in objects.get("traces", []):
            a, b = row["endpoints_mm"]
            w.writerow([row["kind"], row["layer"], row["width_mm"]/factor, a[0]/factor, a[1]/factor, b[0]/factor, b[1]/factor, row["net"]])
    # No fabricated drill diameter/process is exported as a confirmed hole.
    with (folder / "evan_holes.csv").open("w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["UNITS", unit])
        w.writerow(["KIND", "PADSTACK", "X_UU", "Y_UU", "NET", "REFDES", "DRILL_UU", "IS_THROUGH", "HOLE_TYPE"])
        for row in objects.get("holes", []):
            if row["drill_mm"] is not None:
                w.writerow([row["kind"], row["padstack"], row["x_mm"]/factor, row["y_mm"]/factor, row["net"], row["refdes"], row["drill_mm"]/factor, "", ""])

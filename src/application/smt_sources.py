"""Associate PCB exports/Gerber/BOM by exact identity, not closest geometry/name guesses."""
import json
import math
from copy import deepcopy
from pathlib import Path
from tempfile import mkdtemp

from shapely.affinity import rotate, translate
from shapely.geometry import Polygon, box

from src.adapters.bom_reader import read_bom
from src.adapters.smt_evidence import sha256_file
from src.domain.smt_objects import CATEGORIES
from src.infrastructure.snapshots import capture_sources, verify_sources


def explicit_bool(value):
    if isinstance(value, bool):
        return value
    return {"YES": True, "TRUE": True, "1": True, "Y": True, "是": True,
            "NO": False, "FALSE": False, "0": False, "N": False, "否": False}.get(str(value).strip().upper())


def dimension(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("BOM 本体尺寸/高度必须为 mm 有限正数") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError("BOM 本体尺寸/高度必须为 mm 有限正数")
    return number


def build_smt_sources(design, pcb_result=None, bom_path="", gerber_dir="", output_dir="", gerber_confirmed=False):
    """Generate board-bound evidence plus explicit per-side completeness and association log."""
    from src.application.smt_workflow import evidence_template
    from src.application.brd_extraction import prepare_brd_data
    brd = design.get("_input_brd", "")
    if not brd:
        raise ValueError("SMT 自动资料关联需要当前 BRD")
    snapshots = capture_sources([brd] + ([bom_path] if bom_path else []))
    pcb = deepcopy(pcb_result or {})
    if pcb and pcb.get("input_brd_sha256") != sha256_file(brd):
        raise ValueError("PCB 分析资料与当前 BRD 不一致，请重新执行 PCB 分析")
    if pcb:
        from src.application.pcb_workflow import verify_analysis_sources
        verify_analysis_sources(pcb)
    if output_dir:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
    folder = Path(mkdtemp(prefix="smt-sources-", dir=output_dir or None))
    native = pcb.get("native_extraction") or design.get("_native_payload")
    if not native:
        native = prepare_brd_data(brd, str(folder), design.get("_editor_path", ""))
    if native.get("source_brd_sha256") != sha256_file(brd):
        raise ValueError("SMT 原生对象来自另一版 BRD")
    if native.get("directory") and Path(native["directory"]).is_dir():
        snapshots += capture_sources([native["directory"]])
    objects = native.get("objects") or {}
    data = evidence_template(brd)
    data["coordinate_alignment_confirmed"] = True
    data["_source"] = str(folder / "smt_objects.json")
    data["_brd_path"] = str(Path(brd).resolve())
    errors, associations = [], []
    bounds = {}
    for row in objects.get("bounds", []):
        key = (row["refdes"].upper(), row["side"])
        if key in bounds:
            errors.append(row["refdes"] + " 有多个 PLACE_BOUND，需确认本体轮廓")
            bounds[key] = None
        else:
            bounds[key] = row
    properties = {row["refdes"].upper(): row for row in objects.get("properties", [])}
    bom = read_bom(bom_path, strict=True) if bom_path else {"rows": [], "columns": {}, "warnings": []}
    bom_map = {row["refdes"].upper(): row for row in bom["rows"]}
    columns = set(bom.get("columns", {}).values())
    errors.extend(bom.get("warnings", []))
    observed = set()
    side_complete = {side: True for side in ("TOP", "BOTTOM")}
    dnp_rows = []
    conflict_refs = set()
    for placement in native.get("components", []):
        ref, side = placement["refdes"], placement.get("side", "")
        observed.add(ref.upper())
        b = bom_map.get(ref.upper())
        prop = properties.get(ref.upper(), {})
        row = dict(placement, source=placement.get("source"), category="", boundary="", tall=None)
        conflicts = []
        if side not in side_complete:
            errors.append(ref + " 面别未获取")
            side_complete = {s: False for s in side_complete}
            continue
        if bom_path and b is None:
            conflicts.append("BOM 未找到位号")
        if b:
            if b.get("side") and b["side"] != side:
                conflicts.append("BOM 面别与 BRD 冲突")
            if b.get("footprint") and placement.get("footprint") and b["footprint"].strip().upper() != placement["footprint"].strip().upper():
                conflicts.append("BOM 封装与 BRD 冲突")
            if b.get("part_number") and prop.get("part_number") and b["part_number"].strip().upper() != prop["part_number"].strip().upper():
                conflicts.append("BOM 料号与 BRD 冲突")
            dnp = b.get("dnp") if b.get("dnp_explicit") and isinstance(b.get("dnp"), bool) else None
            row.update(part_number=b.get("part_number"), bom_source=str(Path(bom_path).resolve()), dnp=dnp)
            row["manufacturer_part_number"]=b.get("manufacturer_part_number", "")
            if dnp is True and not conflicts:
                dnp_rows.append(dict(refdes=ref, side=side, reason="BOM 明确 DNP"))
                associations.append(dict(refdes=ref, side=side, status="dnp", issues=[]))
                continue
            if dnp is False and explicit_bool(b.get("global_mark")) is True and not conflicts:
                associations.append(dict(refdes=ref, side=side, status="global_mark", issues=[], footprint=placement.get("footprint")))
                continue
            if dnp is False and (explicit_bool(b.get("tooling_hole")) is True or explicit_bool(b.get("mounting_hole")) is True) and not conflicts:
                associations.append(dict(refdes=ref, side=side, status="mounting_hole" if explicit_bool(b.get("mounting_hole")) is True else "tooling_hole", issues=[], footprint=placement.get("footprint")))
                continue
            category = str(b.get("category", "")).strip().lower()
            if category in CATEGORIES:
                row["category"] = category
            elif category:
                conflicts.append("BOM 封装类别不在受支持字典")
            height = dimension(b.get("height_mm"))
            if height is not None:
                row["height_mm"] = height
                row["height_source"] = "BOM："+str(Path(bom_path).resolve())
            length, width = dimension(b.get("body_length_mm")), dimension(b.get("body_width_mm"))
            if (length and width and explicit_bool(b.get("body_confirmed")) is True
                    and explicit_bool(b.get("body_origin_centered")) is True and placement.get("rotation") is not None):
                shape = box(-length/2, -width/2, length/2, width/2)
                shape = rotate(shape, placement["rotation"], origin=(0, 0))
                shape = translate(shape, placement["x_mm"], placement["y_mm"])
                row.update(outline=list(shape.exterior.coords), boundary="body", body_length_mm=length,
                           body_width_mm=width, package_dimensions_confirmed=True, body_source="BOM 已确认尺寸 + BRD 放置")
        if row.get("height_mm") is None and prop.get("height_mm") is not None:
            row["height_mm"] = prop["height_mm"]
            row["height_source"] = "BRD COMP_HEIGHT（设计值）"
        if row.get("height_mm") is not None:
            row["tall"] = row["height_mm"] >= 8
        bound = bounds.get((ref.upper(), side))
        if not row.get("outline") and bound:
            row.update(outline=bound["outline"], boundary="place_bound", body_source=bound["source"])
        if conflicts:
            # Wrong variant/side/package cannot yield fabricated body geometry.
            row.pop("outline", None)
            row["category"] = ""
            errors.extend(ref + "：" + msg for msg in conflicts)
            conflict_refs.add(ref.upper())
        if not row.get("outline") or not row["category"] or conflicts:
            side_complete[side] = False
        if bom_path and row.get("dnp") is None:
            errors.append(ref + "：BOM 未明确 DNP/贴装状态，保留待复核")
            side_complete[side] = False
        associations.append(dict(refdes=ref, side=side, status="conflict" if conflicts else "matched" if b else "brd_only", issues=conflicts,
                                 boundary=row.get("boundary"), category=row.get("category"), height_mm=row.get("height_mm"), footprint=placement.get("footprint")))
        data["components"].append(row)
    extras = sorted(set(bom_map) - observed)
    if extras:
        errors.append("BOM 存在 BRD 无位号：" + ", ".join(extras[:30]))
        side_complete = {s: False for s in side_complete}
    native_complete = native.get("component_count") == len(native.get("components", [])) and native.get("unit") is not None
    if not native_complete:
        side_complete = {s: False for s in side_complete}
    if native.get("warnings") and not native.get("objects"):
        errors.extend(native["warnings"])
    data["assembly_sides"] = sorted({c["side"] for c in data["components"]})
    data["complete"].update(components=all(side_complete.values()), components_top=side_complete["TOP"], components_bottom=side_complete["BOTTOM"])
    data["expected_refdes"] = [row["refdes"] for row in native.get("components", [])]
    data["dnp_components"] = dnp_rows
    data["non_assembly_refdes"] = [row["refdes"] for row in associations if row["status"] in ("global_mark", "tooling_hole", "mounting_hole")]
    if bom_path and not data["components"]:
        errors.append("没有可检查的贴装器件（全 DNP 或资料未获取），需确认适用性")
    data["board_outline"] = objects.get("board_outline") or []
    data["vcut_lines"] = objects.get("vcut_lines") or []
    data["complete"]["vcut_lines"] = objects.get("complete", {}).get("vcut_lines", False)
    data["pads"] = [row for row in objects.get("pads", []) if row.get("object_kind") != "via" and row.get("side") in ("TOP", "BOTTOM")]
    data["via_pads"] = [row for row in objects.get("pads", []) if row.get("object_kind") == "via" and row.get("side") in ("TOP", "BOTTOM")]
    data["complete"]["via_pads"] = objects.get("complete", {}).get("pads", False)
    data["silkscreen_text_records"] = objects.get("silkscreen_text_records", [])
    for kind in ("mask_openings","paste_openings"):
        data[kind]=objects.get(kind,[])
        data["complete"][kind]=objects.get("complete",{}).get(kind,False)
    data["complete"]["silkscreen_text_records"] = objects.get("complete", {}).get("silkscreen_text_records", False)
    data["native_pins"] = objects.get("pins", [])
    data["native_holes"] = objects.get("holes", [])
    data["native_traces"] = objects.get("traces", [])
    data["layer_names"] = native.get("updates", {}).get("layers", design.get("layers", []))
    data["layer_count"] = len(data["layer_names"])
    data["complete"]["native_pins"] = objects.get("complete", {}).get("pins", False)
    data["complete"]["native_holes"] = objects.get("complete", {}).get("pads", False)
    data["complete"]["native_traces"] = objects.get("complete", {}).get("traces", False)
    data["silkscreen_refdes"]=objects.get("silkscreen_refdes",{})
    data["complete"]["silkscreen_refdes"]=objects.get("complete",{}).get("silkscreen_refdes",False)
    for side in ("top", "bottom"):
        data["complete"]["pads_" + side] = objects.get("complete", {}).get("pads", False)
    pin_map = {(r["refdes"].upper(), r["pin"]): r for r in objects.get("pins", [])}
    pin_metadata = objects.get("pin_metadata", [])
    for component in data["components"]:
        metadata=[p for p in pin_metadata if str(p.get("refdes", "")).upper()==component["refdes"].upper()]
        # Explicit SMD confirmation in BOM wins. Same-side, no drill native
        # pads are design evidence only, not a part-body/VPL certification.
        bom_row=bom_map.get(component["refdes"].upper(),{})
        explicit_smd=explicit_bool(bom_row.get("smd"))
        if explicit_smd is not None:
            component["smd"]=explicit_smd
        elif metadata and all(p.get("start_layer")==component["side"] and p.get("end_layer")==component["side"] for p in metadata):
            component["smd"]=True
            component["smd_source"]="BRD引脚同面跨度（设计对象）"
        explicit_pth=explicit_bool(bom_row.get("pth"))
        if explicit_pth is not None:
            component["pth"]=explicit_pth
            component["pth_source"]="BOM明确通孔元件属性"
        elif data["layer_names"] and any(str(h.get("refdes", "")).upper()==component["refdes"].upper() and h.get("plated") is True
                 and h.get("start_layer")==data["layer_names"][0] and h.get("end_layer")==data["layer_names"][-1]
                 for h in data["native_holes"]):
            component["pth"]=True
            component["pth_source"]="BRD位号关联明确镀通孔跨度（设计对象）"
        if component.get("pth") is True and component.get("smd") is True:
            errors.append(component["refdes"]+"：同时被声明SMD/PTH，贴装类型冲突，相关检查待确认")
            component["pth"],component["smd"]=None,None
            side_complete[component["side"]]=False
        component["thermal_pair_confirmed"]=explicit_bool(bom_row.get("thermal_pair_confirmed")) is True
        component["sourcing_role"]=bom_row.get("sourcing_role", "")
        component["alternatives"]=bom_row.get("alternatives",[])
        if str(bom_row.get("expected_pin_count", "")).strip():
            value=float(bom_row["expected_pin_count"])
            if not math.isfinite(value) or not value.is_integer() or value<1:
                raise ValueError("BOM expected_pin_count 必须为正整数")
            component["expected_pin_count"]=int(value)
    pad_by_ref = {}
    data["mounting_holes"]=[]
    for pad in data["pads"]:
        pad_by_ref.setdefault(pad["refdes"].upper(), []).append(pad)
    for placement in native.get("components", []):
        ref, side = placement["refdes"], placement.get("side", "")
        b = bom_map.get(ref.upper(), {})
        if ref.upper() in conflict_refs or ref.upper() in {r["refdes"].upper() for r in dnp_rows} or side not in ("TOP", "BOTTOM"):
            continue
        pads = [p for p in pad_by_ref.get(ref.upper(), []) if p["side"] == side]
        if explicit_bool(b.get("global_mark")) is True:
            diameter = min((2*Polygon(p["outline"]).centroid.distance(Polygon(p["outline"]).boundary)
                            for p in pads if Polygon(p["outline"]).covers(Polygon(p["outline"]).centroid)), default=None)
            center = Polygon(pads[0]["outline"]).centroid if len(pads) == 1 else None
            data["marks"].append(dict(id=ref, side=side, x_mm=center.x if center else placement["x_mm"],
                y_mm=center.y if center else placement["y_mm"], diameter_mm=diameter, global_confirmed=center is not None))
        for pad in pads:
            pin = pin_map.get((ref.upper(), pad["pin"]), {})
            native_test = any(h.get("refdes", "").upper() == ref.upper() and h.get("pin") == pad["pin"] and str(h.get("test_point", "")).upper() in ("YES", "TRUE", "TOP", "BOTTOM") for h in objects.get("pin_metadata", []))
            if explicit_bool(b.get("test_point")) is True or native_test:
                shape = Polygon(pad["outline"])
                diameter = 2*shape.centroid.distance(shape.boundary) if shape.covers(shape.centroid) else None
                data["test_pads"].append(dict(pad, confirmed=False, identity_confirmed=True, diameter_mm=diameter,
                    diameter_source="铜盘质心内接圆保守直径（不使用包围盒当接触直径）", net=pin.get("net", "")))
        if explicit_bool(b.get("tooling_hole")) is True:
            for hole in objects.get("holes", []):
                if hole.get("refdes", "").upper() == ref.upper():
                    data["tooling_holes"].append(dict(hole, id=ref, diameter_mm=hole.get("drill_mm"), tooling_confirmed=True))
        if explicit_bool(b.get("mounting_hole")) is True:
            raw_sides=str(b.get("mounting_sides", "")).strip().upper().replace(";", ",")
            sides=[s.strip() for s in raw_sides.split(",") if s.strip()]
            if not sides or any(s not in ("TOP", "BOTTOM") for s in sides):
                errors.append(ref+"：螺丝孔检查面未明确，不自动猜上下两面")
            for hole in objects.get("holes", []):
                if str(hole.get("refdes", "")).upper()==ref.upper():
                    data["mounting_holes"].append(dict(hole,id=ref,diameter_mm=hole.get("drill_mm"),mounting_confirmed=True,
                        assembly_sides=sides,identity_source=str(Path(bom_path).resolve())))
            if not any(h["id"]==ref for h in data["mounting_holes"]):
                errors.append(ref+"：BOM明确螺丝孔但没有精确关联孔位，未用位号原点代替孔中心")
    data["complete"]["mounting_holes"]=bool(bom_path) and "mounting_hole" in columns and native_complete and not conflict_refs and all(
        ref.upper() in bom_map and explicit_bool(bom_map[ref.upper()].get("mounting_hole")) is not None for ref in data["expected_refdes"])
    requested_mounting={ref.upper() for ref in data["expected_refdes"] if ref.upper() in bom_map and explicit_bool(bom_map[ref.upper()].get("mounting_hole")) is True}
    found_mounting={str(h.get("refdes") or h.get("id", "")).upper() for h in data["mounting_holes"]
                    if h.get("diameter_mm") is not None and h.get("assembly_sides")}
    data["complete"]["mounting_holes"] &= not bool(requested_mounting-found_mounting)
    for kind, field in (("marks", "global_mark"), ("tooling_holes", "tooling_hole"), ("test_pads", "test_point")):
        data["complete"][kind] = bool(bom_path) and field in columns and not extras and native_complete and all(
            ref.upper() in bom_map and explicit_bool(bom_map[ref.upper()].get(field)) is not None for ref in data["expected_refdes"])
        if kind in ("marks", "test_pads"):
            data["complete"][kind] &= objects.get("complete", {}).get("pads", False)
        if kind == "tooling_holes":
            data["complete"][kind] &= objects.get("complete", {}).get("pads", False)
        if associations and any(row["status"] == "conflict" for row in associations):
            data["complete"][kind] = False
        requested = {ref.upper() for ref in data["expected_refdes"] if ref.upper() in bom_map and explicit_bool(bom_map[ref.upper()].get(field)) is True}
        found = {str(row.get("refdes") or row.get("id", "")).upper() for row in data[kind]}
        if requested - found:
            errors.append(kind + "：已声明对象缺坐标/轮廓，未完整取得：" + ", ".join(sorted(requested-found)))
            data["complete"][kind] = False
        for side in ("top", "bottom"):
            data["complete"][kind + "_" + side] = data["complete"][kind]
    for side in ("TOP", "BOTTOM"):
        if any(row.get("global_confirmed") is not True for row in data["marks"] if row["side"] == side):
            data["complete"]["marks_" + side.lower()] = False
    data["complete"]["marks"] &= all(row.get("global_confirmed") is True for row in data["marks"])
    if not objects.get("complete", {}).get("geometry"):
        errors.append("原生几何对象未完整读取，自动对象检查不代表全板完整")
    data["source_association"] = dict(rows=associations, errors=errors, dnp=dnp_rows, per_side=side_complete,
                                      extraction_complete=objects.get("complete", {}))
    gerber = gerber_dir or pcb.get("gerber_folder", "")
    if gerber:
        from src.adapters.gerber_export import gerber_files
        files = gerber_files(gerber)
        snapshots += capture_sources([gerber])
        data["gerber_sha256"] = {p.name: sha256_file(p) for p in files}
        native_folder = pcb.get("manufacturing_export", {}).get("stages", {}).get("gerber", {}).get("directory")
        data["gerber_alignment_confirmed"] = bool(gerber_confirmed or native_folder and Path(native_folder).resolve() == Path(gerber).resolve())
        # External films without validated origin cannot provide body/board-edge coordinates.
        if not data["gerber_alignment_confirmed"]:
            errors.append("外部 Gerber 与 BRD 原点/版本未确认，相关几何保持待复核")
        elif not data["board_outline"]:
            from src.adapters.gerber_analysis import read_film
            from shapely.ops import polygonize, unary_union
            outlines = []
            for path in files:
                film = read_film(path)
                if film["role"] == "outline":
                    lines = [obj["centerline"] for obj in film["objects"] if obj.get("centerline") is not None]
                    candidates = list(polygonize(unary_union(lines)))
                    if len(candidates) == 1 and candidates[0].is_valid and not candidates[0].interiors:
                        outlines.append(candidates[0])
            if len(outlines) == 1:
                data["board_outline"] = list(outlines[0].exterior.coords)
            else:
                errors.append("板框存在多轮廓/挖空/未闭合，未猜测矩形边界")
        if data["gerber_alignment_confirmed"] and data["test_pads"]:
            from src.adapters.gerber_analysis import read_film
            mask_films = {}
            for path in files:
                film = read_film(path)
                if film["role"] in ("mask_top", "mask_bottom"):
                    mask_films.setdefault(film["role"], []).append(film)
            for test_pad in data["test_pads"]:
                masks = mask_films.get("mask_" + test_pad["side"].lower(), [])
                if len(masks) == 1 and masks[0]["geometry"].covers(Polygon(test_pad["outline"])):
                    test_pad["confirmed"] = True
                    test_pad["exposure_source"] = masks[0]["path"]
    for side in ("TOP", "BOTTOM"):
        if any(row.get("confirmed") is not True for row in data["test_pads"] if row["side"] == side):
            data["complete"]["test_pads_" + side.lower()] = False
            data["complete"]["test_pads"] = False
            errors.append(side + " 测试点开窗未核对，不确认可接触直径")
    if bom_path:
        data["bom_file"] = str(Path(bom_path).resolve())
        data["bom_file_sha256"] = sha256_file(bom_path)
    data["association_contract"] = "exact-refdes-v1"
    data["complete"].update(components=all(side_complete.values()),components_top=side_complete["TOP"],components_bottom=side_complete["BOTTOM"])
    data["board_type"]=design.get("board_type", "")
    data["board_type_confirmed"]=design.get("board_type_confirmed") is True
    data["bom_columns"] = sorted(columns)
    data["linked_source_fingerprints"] = snapshots
    verify_sources(snapshots)
    path = folder / "smt_objects.json"
    # Full native Via polygons can be large: retain every coordinate but avoid
    # indentation inflation; the reader's 16 MiB safety cap remains unchanged.
    serialized=json.dumps(data,ensure_ascii=False,separators=(",",":"),allow_nan=False)
    if len(serialized.encode("utf-8"))>16*1024*1024:
        # Large boards use hash-bound, bounded chunks rather than disabling the
        # existing per-file safety limit or discarding native geometry.
        from src.adapters.smt_evidence import CHUNK_FIELDS
        output_data=dict(data,object_files=[])
        for field in sorted(CHUNK_FIELDS):
            records=data.get(field,[])
            if not records:continue
            output_data[field]=[]
            batches=[];batch=[];size=2
            for row in records:
                text=json.dumps(row,ensure_ascii=False,separators=(",",":"),allow_nan=False)
                length=len(text.encode("utf-8"))+1
                if length>8*1024*1024:raise ValueError("单个原生对象超出分块预算")
                if batch and size+length>8*1024*1024:batches.append(batch);batch=[];size=2
                batch.append(row);size+=length
            if batch:batches.append(batch)
            for i,batch in enumerate(batches):
                child=folder/f"objects-{field}-{i}.json"
                child.write_text(json.dumps(batch,ensure_ascii=False,separators=(",",":"),allow_nan=False),encoding="utf-8")
                output_data["object_files"].append(dict(field=field,path=child.name,sha256=sha256_file(child)))
        serialized=json.dumps(output_data,ensure_ascii=False,separators=(",",":"),allow_nan=False)
    path.write_text(serialized,encoding="utf-8")
    return dict(path=str(path), gerber_dir=gerber, data=data, input_fingerprints=snapshots)

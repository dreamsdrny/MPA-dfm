"""Rasterized issue crops from actual Gerber/object snapshots, never synthetic boards."""
import io
import math
from collections import defaultdict
from pathlib import Path

from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Alignment, Font, PatternFill
from PIL import Image, ImageDraw
from shapely.geometry import Polygon, box

PICTURE_WIDTH, PICTURE_HEIGHT = 320, 220
MAX_PICTURES = 200


def finite(value):
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def pcb_entries(pcb):
    entries = []
    locations = pcb.get("problem_locations", [])
    for issue in pcb.get("issues", []):
        matches = [point for point in locations if point.get("item") == issue.get("rule")
                   and (not issue.get("layer") or point.get("layer") == issue["layer"])]
        point = next((p for p in matches if finite(p.get("x_mm")) is not None and finite(p.get("y_mm")) is not None), {})
        entries.append(dict(issue, scope="PCB", label=issue.get("rule", ""), description=issue.get("description", ""),
            x_mm=point.get("x_mm", issue.get("x")), y_mm=point.get("y_mm", issue.get("y")),
            refdes=point.get("refdes", issue.get("from", "")), other_refdes=point.get("other_refdes", ""),
            source=point.get("source") or issue.get("source", ""), layer=point.get("layer") or issue.get("layer", ""),
            coordinate_system="gerber" if point.get("brd_alignment") is False or issue.get("category") in ("Gerber", "NC Drill") else "brd",
            caption="代表位置 1 / " + str(len(matches) or issue.get("count", 1))))
    return entries


def smt_entries(smt):
    groups = defaultdict(list)
    for finding in smt.get("object_findings", []) + smt.get("bom_findings", []) + smt.get("assembly_findings",[]):
        if finding.get("status") not in ("pass", "not_applicable"):
            groups[(finding.get("field") or finding.get("rule") or finding.get("id"), finding.get("side", ""))].append(finding)
    entries = []
    represented = set()
    for (key, side), rows in groups.items():
        represented.add(key)
        candidates=sorted(rows,key=lambda row:0 if row.get("status")=="fail" else 1)
        representative = next((row for row in candidates if finite(row.get("x_mm")) is not None and finite(row.get("y_mm")) is not None), candidates[0])
        failed = any(row.get("status") == "fail" for row in rows)
        entries.append(dict(representative, scope="SMT", label=representative.get("label") or str(key),
            code=representative.get("id") or str(key), description=representative.get("reason", ""),
            suggestion=representative.get("advice", ""), status="fail" if failed else "review", layer=side,
            count=len(rows), coordinate_system="brd", caption=f"代表位置 1 / {len(rows)}"))
    for item in smt.get("items", []):
        if item.get("status") not in ("pass", "not_applicable") and item.get("field") not in represented:
            entries.append(dict(item, scope="SMT", code=item.get("id"), label=item.get("label", ""),
                description=item.get("reason", ""), suggestion=item.get("advice", ""), count=1,
                coordinate_system="brd", caption="无对象坐标"))
    for state in smt.get("assembly_coverage",[]):
        if state["check_key"] not in represented and state.get("phase") in ("unavailable","partial"):
            entries.append(dict(scope="SMT",code="ASM-"+state["check_key"],label=state["label"],description="未完成："+state["required_data"],
                suggestion="补充确认资料/规则后重新检查",count=1,severity="medium",status="review",coordinate_system="brd",caption="无可靠坐标"))
    return entries


class IssuePictureContext:
    def __init__(self, pcb=None, smt=None):
        self.pcb, self.smt = pcb or {}, smt or {}
        self.cache = {}
        self.rendered = 0
        self.geometry = self.smt.get("picture_geometry", {})
        self.path_roles = {}
        if not self.geometry and self.smt.get("object_evidence_path"):
            from src.adapters.smt_evidence import load_evidence
            data = load_evidence(self.smt["object_evidence_path"], self.smt.get("input_brd", ""))
            self.geometry = dict(data, alignment_confirmed=True)

    def _film(self, path):
        path = Path(path)
        if path not in self.cache:
            if len(self.cache) >= 24:
                raise ValueError("图片图层预算超限")
            from src.adapters.gerber_analysis import read_film
            self.cache[path] = read_film(path)
        return self.cache[path]

    def _layers(self, entry):
        from src.adapters.gerber_analysis import identify_layer
        target = str(entry.get("layer", "")).lower()
        side = "bottom" if "bottom" in target or target == "bot" else "top"
        if entry["scope"]=="SMT" and self.smt.get("picture_odb_layers"):
            from shapely import from_wkb
            return [dict(role=r["role"],path=r["source"],geometry=from_wkb(r["geometry_wkb"]),objects=[]) for r in self.smt["picture_odb_layers"]
                    if r["role"].endswith("_"+side)]
        paths = []
        source = entry.get("source", "")
        if isinstance(source, str) and source and Path(source).is_file():
            if entry.get("coordinate_system") == "brd" and entry["scope"] == "SMT" and self.smt.get("picture_gerber_alignment") is not True:
                source = ""
            # A CSV/JSON source is not a Gerber layer.
            if source and Path(source).suffix.lower() in (".art", ".gbr", ".gts", ".gbs", ".gtl", ".gbl", ".gto", ".gbo"):
                paths.append(Path(source))
        folder = self.smt.get("picture_gerber_dir") if entry["scope"] == "SMT" else self.pcb.get("gerber_folder")
        if folder and Path(folder).is_dir():
            if entry["scope"] == "SMT" and self.smt.get("picture_gerber_alignment") is not True:
                return []
            from src.adapters.gerber_export import gerber_files
            if folder not in self.path_roles:
                identified=[]
                for path in gerber_files(folder):
                    with path.open(encoding="ascii", errors="replace") as f:
                        identified.append((path,identify_layer(path,f.read(20000))))
                self.path_roles[folder]=identified
            for path,role in self.path_roles[folder]:
                if role in ("copper_"+side, "mask_"+side, "silkscreen_"+side, "paste_"+side, "outline") or role.lower() == target:
                    paths.append(path)
        # For PCB crops prefer the exact problem layer; other unverified origins
        # are not overlaid. SMT uses only its verified/aligned geometry sources.
        if entry["scope"] == "PCB":
            exact = [p for p in paths if str(p) == source]
            if exact:
                return [self._film(exact[0])]
            candidates = [self._film(p) for p in dict.fromkeys(paths)]
            return [f for f in candidates if f["role"].lower() == target or
                    entry.get("coordinate_system") == "brd" and f.get("brd_alignment") and f["role"] == "copper_"+side]
        return [self._film(p) for p in dict.fromkeys(paths)]

    def render(self, entry):
        if self.rendered >= MAX_PICTURES:
            return None, "图片数量超过 200；其余坐标/问题仍完整保留"
        x, y = finite(entry.get("x_mm")), finite(entry.get("y_mm"))
        if x is None or y is None:
            return None, "未生成图片：没有可验证的问题 X/Y"
        try:
            layers = self._layers(entry)
        except (OSError, ValueError, SyntaxError, ArithmeticError) as exc:
            layers = []
            error = str(exc)
        else:
            error = ""
        geometry = self.geometry if entry["scope"] == "SMT" else {}
        if not geometry and entry["scope"] == "PCB":
            geometry = (self.pcb.get("native_extraction") or {}).get("objects", {})
        use_objects = bool(geometry) and (geometry.get("alignment_confirmed") is True or entry["scope"] == "PCB" and entry.get("coordinate_system") == "brd")
        if not layers and not use_objects:
            return None, "未生成图片：缺少对应 Gerber/已确认对象轮廓" + ("；"+error if error else "")
        radius = 2.5
        refs = {str(entry.get("refdes", "")).upper(), str(entry.get("other_refdes", "")).upper()} - {""}
        selected = [row for row in geometry.get("components", []) if str(row.get("refdes", "")).upper() in refs and row.get("outline")]
        pin_targets={(str(entry.get("refdes", "")).upper(),str(entry.get("pin", ""))),
                     (str(entry.get("other_refdes", "")).upper(),str(entry.get("other_pin", "")))}
        pin_selected=[row for row in geometry.get("pads", []) if row.get("outline") and
                      (str(row.get("refdes", "")).upper(),str(row.get("pin", ""))) in pin_targets and
                      row.get("side")==entry.get("side",entry.get("layer"))] if entry.get("pin") else []
        if pin_selected:radius=.6
        for row in pin_selected or selected:
            for px, py in row["outline"]:
                radius = min(4 if pin_selected else 15, max(radius, abs(px-x)+(.15 if pin_selected else .5), abs(py-y)+(.15 if pin_selected else .5)))
        window = box(x-radius, y-radius, x+radius, y+radius)
        scale = min((PICTURE_WIDTH-24)/(2*radius), (PICTURE_HEIGHT-48)/(2*radius))
        cx, cy = PICTURE_WIDTH/2, (PICTURE_HEIGHT-24)/2
        def transform(px, py):
            return (cx+(px-x)*scale, cy-(py-y)*scale)
        image = Image.new("RGB", (PICTURE_WIDTH, PICTURE_HEIGHT), "#101820")
        draw = ImageDraw.Draw(image)
        def paint(shape, fill, outline=None):
            if shape.is_empty:
                return
            if shape.geom_type == "Polygon":
                draw.polygon([transform(a,b) for a,b in shape.exterior.coords], fill=fill, outline=outline)
                for hole in shape.interiors:
                    draw.polygon([transform(a,b) for a,b in hole.coords], fill="#101820")
            elif shape.geom_type in ("LineString", "LinearRing"):
                draw.line([transform(a,b) for a,b in shape.coords], fill=outline or fill, width=1)
            elif hasattr(shape, "geoms"):
                for part in shape.geoms:
                    paint(part, fill, outline)
        sources = []
        visible_geometry = False
        colors = {"copper": "#d79529", "mask": "#236f3d", "silkscreen": "#eeeecc", "paste": "#7d91af", "outline": "#ffffff"}
        for film in layers:
            part = film["geometry"].intersection(window)
            visible_geometry |= not part.is_empty
            paint(part, colors.get(film["role"].split("_")[0], "#999999"))
            if film["role"] == "outline":
                for obj in film["objects"]:
                    if obj.get("centerline") is not None:
                        local = obj["centerline"].intersection(window)
                        visible_geometry |= not local.is_empty
                        paint(local, "#ffffff")
            sources.append(film["path"])
        object_count = 0
        if use_objects:
            side = str(entry.get("layer", entry.get("side", ""))).upper()
            for row in sum((geometry.get(k, []) for k in ("components","bounds","pads","via_pads","shields","laser_regions","thermal_pads","mask_openings","paste_openings")), []):
                if not row.get("outline") or side in ("TOP", "BOTTOM") and row.get("side") != side:
                    continue
                poly = Polygon(row["outline"])
                if not poly.intersects(window):
                    continue
                selected_ref = str(row.get("refdes", "")).upper() in refs
                paint(poly.boundary.intersection(window), None, "#53b267" if row.get("boundary")=="native_mask_opening" else "#ad91d5" if row.get("boundary")=="native_paste_opening" else "#ff5b58" if selected_ref else "#70ccec")
                object_count += 1
                if row.get("refdes") and row in selected:
                    px,py = poly.representative_point().coords[0]
                    draw.text(transform(px,py), str(row["refdes"]), fill="white")
            if geometry.get("board_outline"):
                paint(Polygon(geometry["board_outline"]).boundary.intersection(window), None, "#ffffff")
            for hole in geometry.get("mounting_holes", []):
                if hole.get("diameter_mm") is not None and finite(hole.get("x_mm")) is not None and finite(hole.get("y_mm")) is not None:
                    from shapely.geometry import Point
                    h=Point(hole["x_mm"],hole["y_mm"]).buffer(hole["diameter_mm"]/2)
                    if h.intersects(window):
                        paint(h.boundary.intersection(window),None,"#d47cff")
                        object_count+=1
            for rib in geometry.get("panel_support_ribs", []):
                if rib.get("outline"):
                    h=Polygon(rib["outline"])
                    if h.intersects(window):
                        paint(h.boundary.intersection(window),None,"#d47cff")
                        object_count+=1
            from shapely.geometry import LineString
            for edge in geometry.get("conveyed_edges", []):
                if edge.get("points") and (side not in ("TOP","BOTTOM") or side in edge.get("assembly_sides", [])):
                    line=LineString(edge["points"]).intersection(window)
                    if not line.is_empty:
                        paint(line,None,"#d47cff")
                        object_count+=1
            sources.append(str(geometry.get("source") or geometry.get("_source") or "BRD extracta 对象轮廓"))
            for row in pin_selected:
                poly=Polygon(row["outline"])
                if poly.intersects(window):
                    px,py=poly.intersection(window).representative_point().coords[0]
                    draw.text(transform(px,py),str(row.get("refdes",""))+":"+str(row.get("pin","")),fill="white")
        if not visible_geometry and not object_count:
            return None, "未生成图片：此位置未取得对象轮廓"
        draw.rectangle((cx-12,cy-12,cx+12,cy+12), outline="#ff3333", width=2)
        draw.line((cx-18,cy,cx+18,cy), fill="#ff3333", width=1)
        draw.line((cx,cy-18,cx,cy+18), fill="#ff3333", width=1)
        draw.rectangle((0,PICTURE_HEIGHT-25,PICTURE_WIDTH,PICTURE_HEIGHT), fill="#203040")
        draw.text((5,PICTURE_HEIGHT-20), f"X={x:.4f} Y={y:.4f} mm {str(entry.get('layer',''))[:10]}"+ (f" pins {entry.get('pin')}/{entry.get('other_pin')}" if pin_selected else ""), fill="white")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        stream.seek(0)
        self.rendered += 1
        boundary_note = "；PLACE_BOUND 为占位边界，非本体认证" if any(row.get("boundary") == "place_bound" for row in selected) else ""
        return stream, "真实资料局部图；红框为问题位置（非尺寸标尺）；保持原点轴向，不自动镜像底面；" + str(entry.get("caption", "代表位置")) + boundary_note + "\n来源：" + "；".join(dict.fromkeys(sources))


def embed_picture(sheet, row, col, entry, context, note_col):
    try:
        stream, note = context.render(entry)
    except (OSError, ValueError, TypeError, ArithmeticError) as exc:
        stream,note=None,"未生成图片：图形/图片解析失败；"+str(exc)
    sheet.cell(row, note_col, note)
    if stream is None:
        sheet.cell(row, col, "未生成图片（见图片来源/状态）")
        return False
    picture = ExcelImage(stream)
    picture.width, picture.height = PICTURE_WIDTH, PICTURE_HEIGHT
    sheet.add_image(picture, f"{sheet.cell(row, col).column_letter}{row}")
    sheet.row_dimensions[row].height = max(sheet.row_dimensions[row].height or 24, PICTURE_HEIGHT*.75+8)
    # Layout may insert continuation rows; keep image anchors tied to their issue.
    sheet._issue_picture_rows = getattr(sheet, "_issue_picture_rows", []) + [(picture, row, col)]
    return True


def add_issue_summary(wb, entries, pcb=None, smt=None, name="Issue summary"):
    if name in wb.sheetnames:
        raise ValueError("问题汇总页名称重复：" + name)
    sheet = wb.create_sheet(name)
    headers = ["序号", "范围", "问题点(code)", "问题图片", "问题描述", "改善建议", "数量", "优先级", "状态",
               "面/层", "X mm", "Y mm", "位号", "相邻位号", "图片来源/状态"]
    sheet.append(headers)
    widths = [8,8,28,47,48,44,8,10,12,18,12,12,14,14,52]
    for i,width in enumerate(widths,1):
        sheet.column_dimensions[sheet.cell(1,i).column_letter].width=width
        sheet.cell(1,i).font=Font(bold=True,color="FFFFFF")
        sheet.cell(1,i).fill=PatternFill("solid",fgColor="24486A")
    context=IssuePictureContext(pcb,smt)
    for index, entry in enumerate(entries,1):
        sheet.append([index,entry.get("scope"),f"{entry.get('label','')} [{entry.get('code','')}]", "",
            entry.get("description"),entry.get("suggestion"),entry.get("count",1),entry.get("severity"),entry.get("status"),
            entry.get("layer"),finite(entry.get("x_mm")),finite(entry.get("y_mm")),entry.get("refdes"),entry.get("other_refdes"),""])
        embed_picture(sheet,index+1,4,entry,context,15)
    for row in sheet:
        for cell in row:
            cell.alignment=Alignment(vertical="top",wrap_text=True)
            if isinstance(cell.value,str):
                cell.data_type="s"
    sheet.freeze_panes="A2"
    sheet.auto_filter.ref=sheet.dimensions
    return sheet

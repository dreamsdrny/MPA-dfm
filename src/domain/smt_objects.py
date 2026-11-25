"""Object-level SMT checks against explicit, board-bound geometry evidence.

Coordinates are absolute BRD millimeters; absence of an object is meaningful only
when its corresponding completeness flag has been explicitly confirmed.
"""
from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree

from src.domain.smt_rules import number

SPACING = {
    "chip_01005": "chip_0402_spacing", "chip_0201": "chip_0402_spacing",
    "chip_0402": "chip_0402_spacing", "chip_0603": "chip_0603_spacing",
    "chip_0805": "chip_0805_spacing", "tantalum": "tantalum_spacing",
    "qfp": "qfp_spacing", "qfn": "qfn_spacing", "connector": "connector_spacing",
    "crystal": "crystal_transformer_spacing", "transformer": "crystal_transformer_spacing",
    "sensor": "crystal_transformer_spacing",
}
EDGE = {"tantalum": "tantalum_to_edge", "qfp": "qfp_to_edge", "qfn": "qfn_to_edge",
        "bga": "bga_to_edge", "connector": "connector_to_edge", "crystal": "crystal_to_edge",
        "sensor": "crystal_to_edge"}
for _category in ("chip_01005", "chip_0201", "chip_0402", "chip_0603", "chip_0805"):
    EDGE[_category] = "chip_to_edge"
CATEGORIES = set(SPACING) | set(EDGE) | {"bga", "other"}
OBJECT_FIELDS = set(SPACING.values()) | set(EDGE.values()) | {
    "component_to_edge", "bga_spacing_to_tall", "bga_keepout_0603", "bga_keepout_0402",
    "mark_count", "mark_to_edge_mm", "mark_diameter", "mark_keepout",
    "tooling_hole_count", "tooling_hole_diameter", "tooling_hole_to_edge",
    "silkscreen_width", "silkscreen_char_height", "silkscreen_clearance", "silkscreen_to_mask"}


def _polygon(value, name):
    try:
        points = [(number(p[0], name), number(p[1], name)) for p in value]
        shape = Polygon(points)
    except (TypeError, IndexError, ValueError):
        raise ValueError(f"{name}: 轮廓必须为至少三个 [x,y] 毫米坐标") from None
    if not shape.is_valid or shape.is_empty or shape.area <= 0:
        raise ValueError(f"{name}: 轮廓无效或不闭合")
    return shape


def _distance(shape, other):
    # Geometric engines may return 0.2999999999999998 for a 0.3 mm boundary.
    return round(shape.distance(other), 9)


def _record(status, reason, actual=None, shape=None, **attrs):
    out = dict(status=status, reason=reason, actual=actual, **attrs)
    if shape is not None and not shape.is_empty:
        point = shape if isinstance(shape, Point) else shape.representative_point()
        out.update(x_mm=round(point.x, 6), y_mm=round(point.y, 6))
    return out


def _side(obj):
    value = str(obj.get("side", "")).upper()
    return value if value in ("TOP", "BOTTOM") else ""


def _measured(value, rule, reason, shape=None, **attrs):
    op = rule["op"]
    bounds = [rule.get("min"), rule.get("max")] if op == "range" else [rule.get("value")]
    if any(v is None for v in bounds):
        return _record("unconfigured", "厂商能力未配置", value, shape, **attrs)
    margin = (value - bounds[0] if op == ">=" else bounds[0] - value if op == "<="
              else min(value - bounds[0], bounds[1] - value))
    return _record("pass" if margin >= -1e-10 else "fail", reason, value, shape, margin=margin, **attrs)


def analyze_objects(doc, profile_key, data, *, bounds=None, films=None, input_errors=(), brd_components=None):
    """Return per-field evidence and individual findings, without GUI dependencies."""
    rules = {r["field"]: r for r in doc["profiles"][profile_key]["rules"]}
    evidence = {field: [] for field in OBJECT_FIELDS if field in rules}
    complete = data.get("complete") or {}
    source = data.get("_source", "SMT 对象资料")
    errors = list(input_errors)

    def add(field, status, reason, actual=None, shape=None, **attrs):
        if field in evidence:
            evidence[field].append(_record(status, reason, actual, shape, source=source, **attrs))

    def measure(field, actual, reason, shape=None, **attrs):
        if field in evidence:
            evidence[field].append(_measured(actual, rules[field], reason, shape, source=source, **attrs))

    outline = None
    if data.get("board_outline"):
        outline = _polygon(data["board_outline"], "板框")
    components = []
    bounds = bounds or {}
    component_rows = list(data.get("components", []))
    seen = set()
    for obj in component_rows:
        ref = str(obj.get("refdes", "")).strip()
        side = _side(obj)
        key = (ref.upper(), side)
        if not ref or not side or key in seen:
            raise ValueError("器件必须有唯一的位号和 TOP/BOTTOM 面别")
        seen.add(key)
        shape = _polygon(obj["outline"], ref) if obj.get("outline") else bounds.get(key)
        category = obj.get("category", "")
        category = category if category in CATEGORIES else ""
        components.append(dict(obj, refdes=ref, side=side, shape=shape, category=category,
                               boundary=obj.get("boundary", ""), tall=obj.get("tall")))
    if data.get("complete", {}).get("components") is True and brd_components is not None:
        if not brd_components.get("ok"):
            data = dict(data, complete=dict(data.get("complete", {}), components=False))
            errors.append("BRD 中没有可核对的完整位号清单，无法确认器件完整性")
        else:
            expected = {ref.upper() for ref in brd_components.get("refdes", [])}
            observed = {c["refdes"].upper() for c in components}
            if expected != observed:
                data = dict(data, complete=dict(data.get("complete", {}), components=False))
                errors.append(f"BRD 与对象资料位号不一致：缺失 {len(expected - observed)}，额外 {len(observed - expected)}")

    complete = data.get("complete") or {}
    spacing_fields = set(SPACING.values()) | {"bga_spacing_to_tall", "bga_keepout_0603", "bga_keepout_0402"}
    edge_fields = set(EDGE.values()) | {"component_to_edge"}
    unknown_bga = {side: sum(c["category"] == "bga" and c["side"] == side and c["tall"] is None for c in components)
                   for side in ("TOP", "BOTTOM")}
    for obj in components:
        ref, side, shape, category = obj["refdes"], obj["side"], obj["shape"], obj["category"]
        attrs = dict(refdes=ref, side=side)
        if not shape or obj["boundary"] not in ("body", "place_bound"):
            for field in spacing_fields | edge_fields:
                add(field, "review", "器件轮廓或本体/占位边界类型未确认", **attrs)
            continue
        if not category:
            for field in spacing_fields | set(EDGE.values()):
                add(field, "review", "器件类别或封装尺寸命名未确认", shape=shape, **attrs)
        elif category == "other":
            for field in spacing_fields:
                add(field, "review", "其他器件缺少明确的间距规则，须人工确认", shape=shape, **attrs)
        if obj["boundary"] == "place_bound":
            for field in spacing_fields | edge_fields:
                add(field, "review", "采用 PLACE_BOUND 占位轮廓，需确认是否等同器件本体边界", shape=shape, **attrs)
        if outline:
            distance = _distance(shape, outline.boundary)
            if not outline.covers(shape):
                distance = 0.0
            point = nearest_points(shape, outline.boundary)[0]
            measure("component_to_edge", distance, "器件轮廓到板框最短距离", point, **attrs)
            if category in EDGE:
                measure(EDGE[category], distance, "按器件类别检查到板框距离", point, **attrs)
        else:
            for field in edge_fields:
                add(field, "missing", "缺少已确认的板框", shape=shape, **attrs)
        if category == "bga" and obj["tall"] is None and unknown_bga[side] > 1:
            add("bga_spacing_to_tall", "review", "相邻 BGA 高度类别未确认", shape=shape, **attrs)

    # STRtree filters to the largest configured spacing before exact polygon distances.
    valid = [c for c in components if c["shape"] is not None and c["boundary"] in ("body", "place_bound")]
    largest = max(0.5, *[float(r.get("value") or 0) for f, r in rules.items() if f in spacing_fields])
    shapes = [c["shape"] for c in valid]
    tree = STRtree(shapes) if shapes else None
    nearby = {i: [] for i in range(len(valid))}
    side_count = {side: sum(c["side"] == side for c in valid) for side in ("TOP", "BOTTOM")}
    unknown_tall = {side: sum(c["side"] == side and c["tall"] is None for c in valid) for side in ("TOP", "BOTTOM")}
    checked = set()
    for i, a in enumerate(valid):
        for j in tree.query(a["shape"].buffer(largest)):
            j = int(j)
            if j <= i:
                continue
            b = valid[j]
            if a["side"] != b["side"]:
                continue
            nearby[i].append(j)
            nearby[j].append(i)
            if not a["category"] or not b["category"]:
                continue
            candidates = [SPACING[c["category"]] for c in (a, b) if c["category"] in SPACING]
            for left, right in ((a, b), (b, a)):
                if left["category"] == "bga":
                    if right["category"] in ("chip_01005", "chip_0201", "chip_0402"):
                        candidates.append("bga_keepout_0402")
                    if right["category"] in ("chip_0603", "chip_0805"):
                        candidates.append("bga_keepout_0603")
                    if right["tall"] is True:
                        candidates.append("bga_spacing_to_tall")
                    elif right["tall"] is None and right["category"] not in ("chip_01005", "chip_0201", "chip_0402", "chip_0603", "chip_0805"):
                        add("bga_spacing_to_tall", "review", "相邻器件高度类别未确认", shape=right["shape"], refdes=left["refdes"], other_refdes=right["refdes"], side=left["side"])
                if left["category"] in ("qfp", "qfn") and right["category"] in ("chip_0603", "chip_0805"):
                    # The supplier specifies >=0.4 mm for ICs next to >=0603.
                    candidates.append("chip_0805_spacing")
            candidates = [f for f in set(candidates) if f in rules and rules[f].get("value") is not None]
            if not candidates:
                continue
            strict = max(candidates, key=lambda f: rules[f]["value"])
            distance = _distance(a["shape"], b["shape"])
            point = nearest_points(a["shape"], b["shape"])[0]
            # Each applicable rule remains visible; stricter pair requirement is retained.
            for field in candidates:
                item = _measured(distance, rules[strict], "同面器件轮廓间距；混排采用最严格阈值", point,
                                 source=source, refdes=a["refdes"], other_refdes=b["refdes"], side=a["side"],
                                 applied_rule=rules[strict]["id"], applied_limit=rules[strict]["value"])
                evidence[field].append(item)
                checked.add((field, i))
                checked.add((field, j))

    for item in evidence.get("qfn_spacing", []) + evidence.get("qfp_spacing", []):
        if item["status"] == "pass" and item.get("actual") is not None and item["actual"] < 0.5:
            # QFN 0.5 mm AOI clearance is advisory; keep the manufacturing threshold distinct.
            if item in evidence.get("qfn_spacing", []):
                item.update(status="review", reason="满足供应商间距能力，但 QFN 四边建议留 0.5mm 便于 AOI")

    # A rule must cover every relevant object, not only pairs found near some other object.
    for i, obj in enumerate(valid):
        category = obj["category"]
        applicable = {SPACING[category]} if category in SPACING else set()
        if category == "bga":
            applicable |= {"bga_spacing_to_tall", "bga_keepout_0603", "bga_keepout_0402"}
        peers = side_count[obj["side"]] - 1
        for field in applicable:
            if field not in evidence or (field, i) in checked or not peers:
                continue
            if field == "bga_spacing_to_tall" and unknown_tall[obj["side"]] - (obj["tall"] is None) > 0:
                add(field, "review", "BGA 相邻器件高度类别未完整确认", shape=obj["shape"], refdes=obj["refdes"], side=obj["side"])
            else:
                if field == "qfn_spacing":
                    nearest = min((_distance(obj["shape"], valid[j]["shape"]) for j in nearby[i]), default=None)
                    if nearest is not None and nearest < 0.5:
                        add(field, "review", "QFN 四边建议留 0.5mm 便于 AOI", actual=nearest, shape=obj["shape"], refdes=obj["refdes"], side=obj["side"])
                        continue
                add(field, "pass", "该器件周边没有适用的近邻", shape=obj["shape"], refdes=obj["refdes"], side=obj["side"])

    if complete.get("components") and not data.get("components_file") and not components:
        for field in spacing_fields | edge_fields:
            add(field, "review", "未提供可核对的器件清单，不能确认无器件")
    for field in spacing_fields | edge_fields:
        if field not in evidence:
            continue
        if complete.get("components") is not True:
            add(field, "missing", "器件清单或轮廓完整性未确认")
        elif not evidence[field]:
            relevant = [c for c in valid if SPACING.get(c["category"]) == field or EDGE.get(c["category"]) == field
                        or (c["category"] == "bga" and field.startswith("bga_"))
                        or field == "component_to_edge"]
            if field in spacing_fields and relevant and any(side_count[a["side"]] > 1 for a in relevant):
                if field == "bga_spacing_to_tall" and any(unknown_tall[a["side"]] - (a["tall"] is None) > 0 for a in relevant):
                    add(field, "review", "BGA 相邻器件高度类别未完整确认", shape=relevant[0]["shape"], refdes=relevant[0]["refdes"], side=relevant[0]["side"])
                    continue
                add(field, "pass", "同面邻近器件均在最大检查距离以外", shape=relevant[0]["shape"], refdes=relevant[0]["refdes"], side=relevant[0]["side"])
            else:
                add(field, "not_applicable", "完整器件清单中没有适用对象或同面器件对")

    for kind, fields in (("marks", ("mark_count", "mark_diameter", "mark_to_edge_mm", "mark_keepout")),
                         ("tooling_holes", ("tooling_hole_count", "tooling_hole_diameter", "tooling_hole_to_edge"))):
        objs = data.get(kind, [])
        confirmed = [o for o in objs if o.get("global_confirmed" if kind == "marks" else "tooling_confirmed") is True]
        unknown = [o for o in objs if o.get("global_confirmed" if kind == "marks" else "tooling_confirmed") is not True]
        if complete.get(kind) is True:
            if kind == "marks":
                sides = set(data.get("assembly_sides", [])) | {c["side"] for c in components} | {_side(o) for o in objs}
                sides.discard("")
                if not sides:
                    add(fields[0], "missing", "缺少已确认的贴装面别，不能按面统计 Mark")
                for side in sorted(sides):
                    measure(fields[0], sum(_side(o) == side for o in confirmed), "同一贴装面已确认全局 Mark 数量", side=side)
            else:
                measure(fields[0], len(confirmed), "已确认对象数量")
        else:
            add(fields[0], "missing", "对象清单完整性未确认")
        if unknown:
            # Candidate identities may change the count, so a shortage is not a confirmed violation.
            for record in evidence.get(fields[0], []):
                if record["status"] == "fail":
                    record.update(status="review", reason="候选对象身份未确认，数量暂不能判定")
            add(fields[0], "review", "存在身份未确认的候选对象，未计入数量")
            for field in fields[1:]:
                add(field, "review", "候选对象身份未确认，不能完成该对象检查")
        if complete.get(kind) is not True:
            for field in fields[1:]:
                add(field, "missing", "对象清单完整性未确认")
        for obj in confirmed:
            try:
                point = Point(number(obj.get("x_mm"), "X"), number(obj.get("y_mm"), "Y"))
            except ValueError:
                for field in fields[1:]:
                    add(field, "review", "对象坐标缺失或无效")
                continue
            attrs = dict(refdes=str(obj.get("refdes") or obj.get("id") or ""), side=_side(obj))
            if obj.get("diameter_mm") is None:
                add(fields[1], "missing", "缺少已确认直径", shape=point, **attrs)
            else:
                measure(fields[1], number(obj["diameter_mm"], "直径"), "已确认对象直径", point, **attrs)
            if outline:
                measure(fields[2], _distance(point, outline.boundary) if outline.covers(point) else 0.0,
                        "对象中心到板框距离", point, **attrs)
            else:
                add(fields[2], "missing", "缺少已确认的板框", shape=point, **attrs)
            if kind == "tooling_holes" and obj.get("plated") is not False:
                add(fields[1], "fail" if obj.get("plated") is True else "review",
                    "定位孔为金属化孔" if obj.get("plated") is True else "定位孔非金属化属性未确认", shape=point, **attrs)
        for field in fields[1:]:
            if field in evidence and not evidence[field]:
                add(field, "not_applicable" if complete.get(kind) is True and not unknown else "missing", "无已确认对象" if complete.get(kind) is True else "对象资料不完整")
        if kind == "marks":
            if any(not _side(o) for o in confirmed):
                add("mark_count", "review", "Mark 面别未确认，不能合并正反面数量")
            for side in ("TOP", "BOTTOM"):
                same_side = [o for o in confirmed if _side(o) == side]
                if len(same_side) < 3:
                    continue
                try:
                    pts = [(number(o["x_mm"], "X"), number(o["y_mm"], "Y")) for o in same_side]
                    if unary_union([Point(p) for p in pts]).convex_hull.area < 1e-9:
                        add("mark_count", "review", "同面已确认的 Mark 共线，需调整", side=side)
                except (KeyError, ValueError):
                    add("mark_count", "review", "Mark 坐标不完整，无法判断共线", side=side)

    _check_films(data, films or {}, rules, evidence, source)
    from src.domain.smt_extended import check_extended_objects
    check_extended_objects(data, rules, evidence, source, components, outline)
    for field in OBJECT_FIELDS:
        if field in evidence and not evidence[field]:
            add(field, "missing", "该对象检查没有足够的确认数据")
    return evidence, errors


def _check_films(data, films, rules, evidence, source):
    TOLERANCE = .002
    pads = []
    for pad in data.get("pads", []):
        if pad.get("confirmed") is not True or not _side(pad) or not pad.get("outline"):
            continue
        pads.append((pad, _polygon(pad["outline"], "焊盘")))
    for side in ("top", "bottom"):
        silk = films.get("silkscreen_" + side)
        if not silk:
            if data.get("complete", {}).get("no_silkscreen_" + side) is True:
                continue
            for field in ("silkscreen_width", "silkscreen_clearance", "silkscreen_to_mask"):
                if field in evidence:
                    evidence[field].append(_record("missing", f"缺少已核对的 {side} 丝印 Gerber", source=source))
            continue
        if "silkscreen_width" in evidence and not any(obj.get("dark") and obj.get("width") for obj in silk["objects"]):
            evidence["silkscreen_width"].append(_record("review", f"{side} 面丝印没有可判定线宽的描画对象", source=silk["path"]))
        for obj in silk["objects"]:
            width = obj.get("width")
            if obj.get("dark") and width and "silkscreen_width" in evidence:
                record = _measured(width, rules["silkscreen_width"], "Gerber 描画线宽", obj["geometry"], source=silk["path"], side=side.upper())
                if abs(record.get("margin", 1)) <= TOLERANCE:
                    record.update(status="review", reason="线宽接近几何精度边界，需复核")
                evidence["silkscreen_width"].append(record)
        mask = films.get("mask_" + side)
        if mask and "silkscreen_to_mask" in evidence:
            rule = rules["silkscreen_to_mask"]
            distance = _distance(silk["geometry"], mask["geometry"])
            if not silk["geometry"].is_empty and not mask["geometry"].is_empty:
                point = nearest_points(silk["geometry"], mask["geometry"])[0]
                record = _measured(distance, rule, "同面丝印到阻焊开窗最短距离", point, source=silk["path"] + " + " + mask["path"], side=side.upper())
                if abs(record.get("margin", 1)) <= TOLERANCE:
                    record.update(status="review", reason="间距接近几何精度边界，需复核")
                evidence["silkscreen_to_mask"].append(record)
            else:
                evidence["silkscreen_to_mask"].append(_record("review", f"{side} 面丝印或阻焊开窗为空", source=silk["path"]))
        elif "silkscreen_to_mask" in evidence:
            evidence["silkscreen_to_mask"].append(_record("missing", f"缺少已核对的 {side} 阻焊 Gerber", source=source))
        if "silkscreen_clearance" in evidence:
            matching = [(obj, shape) for obj, shape in pads if _side(obj).lower() == side]
            if not data.get("complete", {}).get("pads_" + side):
                evidence["silkscreen_clearance"].append(_record("missing", f"{side} 面焊盘对象完整性未确认", source=source))
            for obj, shape in matching:
                if silk["geometry"].is_empty:
                    continue
                distance = _distance(silk["geometry"], shape)
                point = nearest_points(silk["geometry"], shape)[0]
                record = _measured(distance, rules["silkscreen_clearance"], "同面丝印到已确认焊盘轮廓的距离", point,
                                   source=silk["path"] + " + " + source, side=side.upper(), refdes=str(obj.get("refdes", "")))
                if abs(record.get("margin", 1)) <= TOLERANCE:
                    record.update(status="review", reason="间距接近 Gerber 几何精度边界，需复核")
                evidence["silkscreen_clearance"].append(record)
            if not matching and data.get("complete", {}).get("pads_" + side):
                evidence["silkscreen_clearance"].append(_record("not_applicable", f"已确认 {side} 面没有焊盘", source=source))
    for field in ("silkscreen_width", "silkscreen_clearance", "silkscreen_to_mask"):
        if field in evidence and not evidence[field] and all(data.get("complete", {}).get("no_silkscreen_" + s) is True for s in ("top", "bottom")):
            evidence[field].append(_record("not_applicable", "已确认两面均无丝印", source=source))
    if "silkscreen_char_height" in evidence:
        if data.get("complete", {}).get("silkscreen_text") is not True:
            evidence["silkscreen_char_height"].append(_record("missing", "丝印文字对象不完整；Gerber 不保留字高语义", source=source))
        for obj in data.get("silkscreen_text", []):
            if obj.get("confirmed") is not True or obj.get("height_mm") is None:
                evidence["silkscreen_char_height"].append(_record("review", "丝印文字字高未确认", source=source, refdes=str(obj.get("refdes", ""))))
                continue
            point = Point(number(obj["x_mm"], "文字 X"), number(obj["y_mm"], "文字 Y")) if obj.get("x_mm") is not None and obj.get("y_mm") is not None else None
            evidence["silkscreen_char_height"].append(_measured(number(obj["height_mm"], "字高"), rules["silkscreen_char_height"],
                "已确认的 Allegro 文字字高；分封装字高建议需另行复核", point, source=source, refdes=str(obj.get("refdes", "")), side=_side(obj)))
            if obj.get("package_category") == "chip_0805" and number(obj["height_mm"], "字高") < 1.0:
                evidence["silkscreen_char_height"].append(_record("review", "0805 及以上器件丝印字高建议至少 1.0mm", number(obj["height_mm"], "字高"), point,
                    source=source, refdes=str(obj.get("refdes", "")), side=_side(obj)))
            elif obj.get("package_category") not in ("chip_01005", "chip_0201", "chip_0402", "chip_0603", "chip_0805"):
                evidence["silkscreen_char_height"].append(_record("review", "未确认文字对应封装类别，无法判断分档字高建议", shape=point,
                    source=source, refdes=str(obj.get("refdes", "")), side=_side(obj)))
        if not data.get("silkscreen_text") and data.get("complete", {}).get("silkscreen_text") is True:
            evidence["silkscreen_char_height"].append(_record("not_applicable", "已确认没有丝印文字", source=source))
    if "mark_keepout" in evidence:
        for mark in data.get("marks", []):
            if mark.get("global_confirmed") is not True:
                continue
            side = _side(mark).lower()
            if not side or not data.get("complete", {}).get("other_copper_" + side):
                evidence["mark_keepout"].append(_record("missing", "需确认同面非 Mark 铜图形完整性和面别", source=source))
                continue
            point = Point(number(mark.get("x_mm"), "Mark X"), number(mark.get("y_mm"), "Mark Y"))
            others = [_polygon(obj["outline"], "非 Mark 铜") for obj in data.get("other_copper", [])
                      if obj.get("confirmed") is True and _side(obj).lower() == side and obj.get("outline")]
            if others:
                nearest = min(others, key=point.distance)
                evidence["mark_keepout"].append(_measured(_distance(point, nearest), rules["mark_keepout"], "Mark 中心到非 Mark 铜最短距离", point,
                    source=source, side=side.upper(), refdes=str(mark.get("id", ""))))
            else:
                evidence["mark_keepout"].append(_record("pass", "已确认同面没有其他铜图形", shape=point, source=source, side=side.upper(), refdes=str(mark.get("id", ""))))


def apply_object_results(result, evidence, errors=()):
    """Combine object evidence without allowing a partial inspection to pass."""
    priority = {"fail": 0, "review": 1, "missing": 2, "unconfigured": 3, "pass": 4, "not_applicable": 5}
    findings = []
    for item in result["items"]:
        records = evidence.get(item["field"])
        if not records:
            continue
        for record in records:
            if record["status"] in ("fail", "review", "missing"):
                findings.append(dict(record, id=item["id"], field=item["field"], label=item["label"],
                                     severity=item["severity"], requirement=item["requirement"], unit=item["unit"], advice=item["advice"]))
        chosen = min(records, key=lambda r: (priority[r["status"]], r.get("margin", float("inf"))))
        item.update(status=chosen["status"], reason=chosen["reason"], actual=chosen.get("actual"),
                    margin=chosen.get("margin"), source=chosen.get("source", ""))
    result["object_findings"] = findings
    result["object_errors"] = list(errors)
    from src.domain.smt_rules import summarize_smt
    summarize_smt(result)
    return result

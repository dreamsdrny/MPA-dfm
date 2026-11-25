"""Additional SMT measurements from explicitly confirmed object attributes (mm)."""
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

from src.domain.smt_rules import UNITS, number

VCUT_FIELDS = {"component_to_vcut", "chip_to_vcut", "tantalum_to_vcut", "qfp_to_vcut", "qfn_to_vcut", "bga_to_vcut"}
PACKAGE_FIELDS = {"bga_pitch", "bga_ball_diameter", "bga_body_length", "bga_body_width", "bga_body_thickness",
                  "qfp_pitch", "qfn_pitch", "connector_pitch"}
TEST_FIELDS = {"test_pad_diameter", "test_pad_pitch", "test_pad_to_edge", "test_pad_to_low_component",
               "test_pad_to_mid_component", "test_pad_to_high_component", "test_pad_to_shield"}
FIELDS = VCUT_FIELDS | PACKAGE_FIELDS | TEST_FIELDS


def check_extended_objects(data, rules, evidence, source, components, outline):
    from src.domain.smt_objects import _distance, _measured, _polygon, _record, _side

    for field in FIELDS & rules.keys():
        evidence[field] = []

    def add(field, status, reason, actual=None, shape=None, **attrs):
        if field in evidence:
            evidence[field].append(_record(status, reason, actual, shape, source=source, **attrs))

    def measure(field, value, reason, shape=None, **attrs):
        if field in evidence:
            # All object dimensions use mm; profile thresholds may use um/mil.
            actual = value / UNITS[rules[field]["unit"]][1]
            evidence[field].append(_measured(actual, rules[field], reason, shape, source=source, **attrs))

    complete = data.get("complete") or {}
    cuts = []
    for obj in data.get("vcut_lines", []):
        if obj.get("confirmed") is not True:
            for field in VCUT_FIELDS:
                add(field, "review", "V-CUT 线位置未确认")
            continue
        points = obj.get("points", [])
        if len(points) != 2:
            raise ValueError("V-CUT 必须为两个 [x,y] mm 坐标")
        line = LineString([(number(p[0], "V-CUT X"), number(p[1], "V-CUT Y")) for p in points])
        if line.length <= 0:
            raise ValueError("V-CUT 线长度必须大于零")
        cuts.append(line)
    for obj in components:
        category, shape = obj["category"], obj["shape"]
        attrs = {"refdes": obj["refdes"], "side": obj["side"]}
        cut_field = ("chip_to_vcut" if category.startswith("chip_") else category + "_to_vcut")
        applicable = {"component_to_vcut", cut_field} & VCUT_FIELDS
        if cuts:
            if shape is None or obj["boundary"] not in ("body", "place_bound"):
                for field in applicable:
                    add(field, "review", "缺少已确认器件轮廓", **attrs)
            else:
                line = min(cuts, key=shape.distance)
                point = nearest_points(shape, line)[0]
                for field in applicable:
                    measure(field, _distance(shape, line), "器件轮廓到 V-CUT 线最短距离", point, **attrs)
                    if obj["boundary"] == "place_bound":
                        add(field, "review", "PLACE_BOUND 是否等同本体边界需复核", shape=point, **attrs)
        if not category:
            for field in PACKAGE_FIELDS | VCUT_FIELDS:
                add(field, "review", "器件封装类别未确认", shape=shape, **attrs)
        attrs_by_category = {
            "bga": {"pitch_mm": "bga_pitch", "ball_diameter_mm": "bga_ball_diameter",
                    "body_length_mm": "bga_body_length", "body_width_mm": "bga_body_width", "height_mm": "bga_body_thickness"},
            "qfp": {"pitch_mm": "qfp_pitch"}, "qfn": {"pitch_mm": "qfn_pitch"},
            "connector": {"pitch_mm": "connector_pitch"}}
        for key, field in attrs_by_category.get(category, {}).items():
            if obj.get("package_dimensions_confirmed") is not True or obj.get(key) is None:
                add(field, "missing", "缺少经数据手册/封装确认的器件尺寸", shape=shape, **attrs)
            else:
                value = number(obj[key], key)
                if value <= 0:
                    raise ValueError(f"{obj['refdes']}: {key} 必须大于零")
                measure(field, value, "经确认的器件封装尺寸（不以 PCB 焊盘反推球径）", shape, **attrs)
    for field in VCUT_FIELDS & evidence.keys():
        if complete.get("vcut_lines") is not True:
            add(field, "missing", "V-CUT 清单完整性未确认")
        elif not cuts and not evidence[field]:
            add(field, "not_applicable", "已确认本板没有 V-CUT")
        if cuts and complete.get("components") is not True:
            add(field, "missing", "器件清单完整性未确认")
    for field in PACKAGE_FIELDS & evidence.keys():
        if complete.get("components") is not True:
            add(field, "missing", "器件清单完整性未确认")

    pads = []
    for obj in data.get("test_pads", []):
        attrs = {"refdes": str(obj.get("refdes") or obj.get("id") or ""), "side": _side(obj)}
        if obj.get("confirmed") is not True or not attrs["side"]:
            for field in TEST_FIELDS:
                add(field, "review", "测试点身份或面别未确认", **attrs)
            continue
        point = Point(number(obj.get("x_mm"), "测试点 X"), number(obj.get("y_mm"), "测试点 Y"))
        shape = _polygon(obj["outline"], "测试点") if obj.get("outline") else None
        pads.append((obj, point, shape, attrs))
        if obj.get("diameter_mm") is None:
            add("test_pad_diameter", "missing", "缺少已确认测试点可接触直径", shape=point, **attrs)
        else:
            diameter = number(obj["diameter_mm"], "测试点直径")
            if diameter <= 0:
                raise ValueError("测试点直径必须大于零")
            measure("test_pad_diameter", diameter, "测试点可接触直径", point, **attrs)
        if outline is None or shape is None:
            add("test_pad_to_edge", "missing", "缺少板框或测试点轮廓，不能用中心代替边缘间距", shape=point, **attrs)
        else:
            measure("test_pad_to_edge", _distance(shape, outline.boundary) if outline.covers(shape) else 0,
                    "测试点边缘到板框距离", point, **attrs)
        for field in ("test_pad_to_low_component", "test_pad_to_mid_component", "test_pad_to_high_component"):
            if complete.get("components") is not True:
                add(field, "missing", "器件清单完整性未确认", shape=point, **attrs)
        for comp in components:
            if comp["side"] != attrs["side"]:
                continue
            height = comp.get("height_mm")
            if height is None or comp.get("package_dimensions_confirmed") is not True or shape is None or comp["shape"] is None or comp["boundary"] != "body":
                for field in ("test_pad_to_low_component", "test_pad_to_mid_component", "test_pad_to_high_component"):
                    add(field, "review", "测试点/器件本体轮廓或高度未确认", shape=point, other_refdes=comp["refdes"], **attrs)
                continue
            height = number(height, "器件高度")
            if height <= 0:
                raise ValueError("器件高度必须大于零")
            field = "test_pad_to_low_component" if height < 5 else "test_pad_to_mid_component" if height < 8 else "test_pad_to_high_component"
            measure(field, _distance(shape, comp["shape"]), "同面测试点边缘到器件本体距离，按高度分档", point,
                    other_refdes=comp["refdes"], **attrs)
        if complete.get("shields") is not True:
            add("test_pad_to_shield", "missing", "屏蔽罩清单完整性未确认", shape=point, **attrs)
        for shield in data.get("shields", []):
            if shield.get("confirmed") is not True or not _side(shield):
                add("test_pad_to_shield", "review", "屏蔽罩轮廓或面别未确认", shape=point, **attrs)
            elif _side(shield) == attrs["side"]:
                if shape is None or not shield.get("outline"):
                    add("test_pad_to_shield", "missing", "缺少测试点或屏蔽罩轮廓", shape=point, **attrs)
                else:
                    measure("test_pad_to_shield", _distance(shape, _polygon(shield["outline"], "屏蔽罩")),
                            "同面测试点到屏蔽罩覆盖区域的距离（罩内为零）", point, **attrs)
    for i, (_, point, _, attrs) in enumerate(pads):
        for _, other, _, other_attrs in pads[i + 1:]:
            if attrs["side"] == other_attrs["side"]:
                measure("test_pad_pitch", _distance(point, other), "同面测试点中心距", point,
                        other_refdes=other_attrs["refdes"], **attrs)
    for field in TEST_FIELDS & evidence.keys():
        if complete.get("test_pads") is not True:
            add(field, "missing", "测试点清单完整性未确认")
        elif not evidence[field]:
            add(field, "not_applicable", "完整资料中没有适用对象或同面测试点对")
    for field in (VCUT_FIELDS | PACKAGE_FIELDS) & evidence.keys():
        if complete.get("components") is True and not components and (field in PACKAGE_FIELDS or cuts):
            add(field, "review", "没有可核对的器件清单，不能确认无适用器件")
        if not evidence[field]:
            add(field, "not_applicable", "完整资料中没有适用对象")

"""Real object geometry checks. Copper, mask and paste remain separate.

Explicit supplier limits may fail; internal baseline violations and uncertain
pad policies remain review candidates. No absence is proof of full-board PASS.
"""
from math import isfinite
from shapely.geometry import Point, box
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree

from src.domain.layout_geometry import polygons, nearby_pairs, ScanBudgetExceeded

TOLERANCE = .004


def validate_object_rules(doc, overrides=None):
    if not isinstance(doc, dict) or doc.get("schema_version") != 1 or not isinstance(doc.get("rules"), dict):
        raise ValueError("对象规则 YAML 合同无效")
    rules = dict(doc["rules"])
    if overrides is not None and not isinstance(overrides, dict):
        raise ValueError("object_rules 必须是对象")
    if set(overrides or {}) - set(rules):
        raise ValueError("未知 object_rules 键: " + ", ".join(sorted(set(overrides)-set(rules))))
    rules.update(overrides or {})
    for key, value in rules.items():
        if key == "paste_bridge":
            if not isinstance(value, bool):
                raise ValueError("paste_bridge 必须为布尔值")
        elif key.endswith("_mm") and value is not None:
            if isinstance(value, bool) or not isfinite(float(value)) or float(value) < 0:
                raise ValueError(f"{key} 必须为非负有限毫米数")
            rules[key] = float(value)
    return rules, set(overrides or {})


def scan_object_checks(films, pads, vias, limits, overrides=None, complete=False, *, rule_bundle):
    """films keyed by role; pad/via geometry must already be aligned and verified."""
    rules, rule_file, explicit, rule_sha256, effective_sha256 = rule_bundle
    checks, coverage, errors, stats = [], [], [], []
    candidates, truncated = 0, False
    usable = []

    def emit(film, shape, label, field, actual, required, status, note, a=None, b=None, unit="mm"):
        nonlocal truncated
        if len(checks) >= limits.max_findings:
            truncated = True
            return
        a, b = a or {}, b or {}
        p = shape if isinstance(shape, Point) else shape.representative_point()
        checks.append(dict(item=label, field=field, measured=round(float(actual), 6), capability=required,
                           status=status, severity="高" if status == "fail" else "中", unit=unit,
                           x_mm=round(p.x, 6), y_mm=round(p.y, 6), layer=film["role"], source=film["path"],
                           brd_alignment=film.get("brd_alignment", False), refdes=a.get("refdes", ""),
                           other_refdes=b.get("refdes", ""), pin=a.get("pin", ""), other_pin=b.get("pin", ""),
                           net=a.get("net", ""), other_net=b.get("net", ""), padstack=a.get("id", ""),
                            object_id=a.get("id", ""), other_object_id=b.get("id", ""),
                            rule_source=rule_file, rule_sha256=effective_sha256,
                            note=note + "；轮廓距离判定带 ±0.004mm"))

    def covered(rule, checked, missing, need):
        coverage.append(dict(rule=rule, status="measured" if checked and not missing and complete else
                             "partial" if checked else "missing", checked=checked, missing=missing,
                             note=f"实测对象/比较 {checked}；未判定 {missing}；仅当前对象规则范围",
                             required_data=need))

    def minimum(film, a, b, label, field, key, actual, point):
        required = rules.get(key)
        if required is not None and actual < required + TOLERANCE:
            status = "fail" if key in explicit and actual < required - TOLERANCE else "warn_limit"
            emit(film, point, label, field, actual, required, status,
                 ("显式规则" if key in explicit else "内部基线，非板厂硬性能力") + f"；{a.get('id', '')} / {b.get('id', '')}", a, b)

    indices = {}
    for role, film in films.items():
        if role.startswith("copper") or role.startswith(("mask", "paste")):
            shapes = polygons(film["geometry"])
            indices[role] = (shapes, STRtree(shapes))

    verified_vias = []
    for via in vias:
        role = via["layer"]
        if role not in indices:
            continue
        pieces, tree = indices[role]
        x0, y0, x1, y1 = via["geometry"].bounds
        window = box(x0-.01, y0-.01, x1+.01, y1+.01)
        local = unary_union([pieces[int(j)].intersection(window) for j in tree.query(window)])
        if not local.buffer(.002).covers(via["geometry"]):
            errors.append(f"{via.get('id')}: 过孔铜盘与最终铜箔不一致，未进行对象检查")
        else:
            verified_vias.append(via)
    rejected_vias = len(vias) - len(verified_vias)
    vias = verified_vias

    mask_checked = mask_missing = paste_checked = paste_missing = 0
    net_nodes = {}
    for pad in pads:
        role = pad["layer"]
        film = films.get(role)
        if not film:
            mask_missing += 1
            continue
        shape = pad["geometry"]
        pieces, tree = indices[role]
        x0, y0, x1, y1 = shape.bounds
        window = box(x0-.01, y0-.01, x1+.01, y1+.01)
        local = unary_union([pieces[int(j)].intersection(window) for j in tree.query(window)])
        if not local.buffer(.002).covers(shape):
            errors.append(f"{pad.get('id')}: 焊盘与最终铜箔不一致，未进行对象检查")
            mask_missing += 1
            continue
        usable.append(pad)
        for j in tree.query(shape, predicate="intersects"):
            candidates += 1
            if candidates > limits.max_candidates:
                errors.append("对象关联候选超过扫描预算，检查未完成")
                break
            if pad.get("net") and pieces[int(j)].intersection(shape).area > .002 ** 2:
                net_nodes.setdefault((role, int(j)), {}).setdefault(pad["net"], []).append(pad)
        if candidates > limits.max_candidates:
            break
        side = "top" if role == "copper_top" else "bottom" if role == "copper_bottom" else ""
        if not side:
            continue
        mask = films.get("mask_" + side)
        if mask is None or pad.get("mask_policy") not in ("open", "smd", "required"):
            mask_missing += 1
        else:
            mask_checked += 1
            if pad["mask_policy"] == "required":
                mask_missing += 1  # Opening observed, but SMD/NSMD policy unconfirmed.
            openings, mtree = indices[mask["role"]]
            margin = max(rules.get("pad_to_mask_mm") or 0, .05) + TOLERANCE
            window = box(x0-margin, y0-margin, x1+margin, y1+margin)
            opening = unary_union([openings[int(j)].intersection(window) for j in mtree.query(window)])
            uncovered = shape.difference(opening.buffer(.002))
            if not uncovered.is_empty and uncovered.area > .002 ** 2:
                is_open = pad["mask_policy"] == "open"
                if is_open or pad["mask_policy"] == "required" or opening.intersection(shape).area <= .002 ** 2:
                    emit(mask, uncovered, "焊盘阻焊漏开/覆盖", "object_pad_mask_covered",
                         100 * uncovered.area / shape.area, 0, "fail" if is_open else "warn_limit",
                         "需完全开窗焊盘被阻焊覆盖" if is_open else "实际铜焊盘存在阻焊覆盖；SMD/有意覆盖工艺未确认，不作硬判定", pad, unit="percent")
            if pad["mask_policy"] == "open" and rules.get("pad_to_mask_mm") is not None:
                distance = shape.distance(opening.boundary) if opening.covers(shape) else 0
                minimum(mask, pad, {}, "焊盘阻焊扩窗余量", "object_pad_mask_margin", "pad_to_mask_mm",
                        distance, shape.representative_point())
        if pad.get("smd") is True and rules.get("paste_bridge"):
            paste = films.get("paste_" + side)
            if paste is None:
                paste_missing += 1
            else:
                paste_checked += 1

    covered("object_pad_mask", mask_checked, mask_missing, "实际铜焊盘 + 同面阻焊 + open/SMD/有意覆盖策略")
    pair_checked = via_checked = 0
    try:
        if candidates > limits.max_candidates:
            raise ScanBudgetExceeded("对象网络关联预算耗尽，后续规则未检查")
        by_layer = {}
        for pad in usable:
            by_layer.setdefault(pad["layer"], []).append(pad)
        for role, objects in by_layer.items():
            scan = dict(rule="object_pad_spacing", layer=role)
            stats.append(scan)
            required = rules.get("pad_to_pad_mm")
            if required is not None:
                for i, j in nearby_pairs([p["geometry"] for p in objects], required+TOLERANCE, limits, scan):
                    a, b = objects[i], objects[j]
                    if not a.get("refdes") or not b.get("refdes") or a["refdes"] == b["refdes"]:
                        continue
                    pair_checked += 1
                    p, q = nearest_points(a["geometry"], b["geometry"])
                    minimum(films[role], a, b, "不同器件焊盘边沿间距", "object_pad_spacing", "pad_to_pad_mm",
                            p.distance(q), Point((p.x+q.x)/2, (p.y+q.y)/2))
            shapes = [p["geometry"] for p in objects]
            tree = STRtree(shapes)
            required = rules.get("via_to_pad_mm")
            if required is not None:
                for via in (v for v in vias if v["layer"] == role and v.get("net")):
                    for j in tree.query(via["geometry"], predicate="dwithin", distance=required+TOLERANCE):
                        candidates += 1
                        if candidates > limits.max_candidates:
                            raise ScanBudgetExceeded("过孔到焊盘候选超过预算")
                        pad = objects[int(j)]
                        if not pad.get("net") or via["net"] == pad["net"]:
                            continue
                        via_checked += 1
                        p, q = nearest_points(via["geometry"], pad["geometry"])
                        minimum(films[role], via, pad, "过孔到异网络焊盘边沿间距", "object_via_pad_spacing",
                                "via_to_pad_mm", p.distance(q), Point((p.x+q.x)/2, (p.y+q.y)/2))
            paste = films.get("paste_" + ("top" if role == "copper_top" else "bottom"))
            if paste and rules.get("paste_bridge"):
                openings, ptree = indices[paste["role"]]
                touched = {}
                for pad in (p for p in objects if p.get("smd") is True):
                    for j in ptree.query(pad["geometry"], predicate="intersects"):
                        candidates += 1
                        if candidates > limits.max_candidates:
                            raise ScanBudgetExceeded("锡膏关联候选超过预算")
                        if openings[int(j)].intersection(pad["geometry"]).area > .002 ** 2:
                            touched.setdefault(int(j), []).append(pad)
                for j, hits in touched.items():
                    if len({p["id"] for p in hits}) > 1:
                        emit(paste, openings[j], "锡膏连通开口跨多个焊盘", "object_paste_bridge", len(hits), 1,
                             "warn_limit", "同一实际锡膏开口接触多个 SMD 焊盘，需排除有意共用/热焊盘；"
                             + ", ".join(p["id"] for p in hits[:12]), hits[0], hits[1], unit="count")
    except ScanBudgetExceeded as exc:
        errors.append(str(exc))
    for (role, j), nets in net_nodes.items():
        if len(nets) > 1:
            pads_by_net = [rows[0] for rows in nets.values()]
            emit(films[role], indices[role][0][j], "异网络焊盘铜连通候选", "object_net_contact", len(nets), 1,
                 "warn_limit", "同层最终铜块接触多个网络；需排除 net-tie，不证明全板短路；网络 " + ", ".join(nets),
                 pads_by_net[0], pads_by_net[1], unit="count")
    via_mask_checked = via_mask_missing = hole_checked = hole_missing = 0
    try:
        for via in vias:
            role = via["layer"]
            film = films.get(role)
            if film is None:
                via_mask_missing += 1
                hole_missing += 1
                continue
            mask = films.get("mask_" + ("top" if role == "copper_top" else "bottom"))
            required = rules.get("via_to_mask_mm")
            # Tenting is an explicit process policy, not inferred from aperture absence.
            if required is None or mask is None or via.get("mask_policy") != "tented":
                via_mask_missing += 1
            else:
                via_mask_checked += 1
                pieces, tree = indices[mask["role"]]
                for j in tree.query(via["geometry"], predicate="dwithin", distance=required+TOLERANCE):
                    candidates += 1
                    if candidates > limits.max_candidates:
                        raise ScanBudgetExceeded("过孔阻焊候选超过预算")
                    p, q = nearest_points(via["geometry"], pieces[int(j)])
                    minimum(mask, via, {}, "盖油过孔到阻焊开窗", "object_via_mask_spacing", "via_to_mask_mm",
                            p.distance(q), Point((p.x+q.x)/2, (p.y+q.y)/2))
            required = rules.get("hole_to_unconnected_copper_mm")
            hole = via.get("hole_geometry")
            if required is None or hole is None or film is None or not via.get("net"):
                hole_missing += 1
                continue
            pieces, tree = indices[role]
            hole_checked += 1
            for j in tree.query(hole, predicate="dwithin", distance=required+TOLERANCE):
                candidates += 1
                if candidates > limits.max_candidates:
                    raise ScanBudgetExceeded("孔到非连接铜箔候选超过预算")
                nets = net_nodes.get((role, int(j)), {})
                if not nets or len(nets) != 1:
                    hole_missing += 1
                    continue
                net = next(iter(nets))
                if net == via["net"]:
                    continue
                other = nets[net][0]
                p, q = nearest_points(hole, pieces[int(j)])
                minimum(film, via, other, "孔壁到异网络铜箔", "object_hole_copper_spacing",
                        "hole_to_unconnected_copper_mm", p.distance(q), Point((p.x+q.x)/2, (p.y+q.y)/2))
    except ScanBudgetExceeded as exc:
        errors.append(str(exc))
    covered("object_pad_spacing", len(usable) if rules.get("pad_to_pad_mm") is not None else 0,
            len(pads)-len(usable)+len([p for p in usable if not p.get("refdes")]), "逐盘真实轮廓/位号；内部基线 0.254mm 或显式规则")
    net_pad_layers = {p["layer"] for p in usable if p.get("net")}
    covered("object_via_pad_spacing", len([v for v in vias if v.get("net") and v["layer"] in net_pad_layers])
            if rules.get("via_to_pad_mm") is not None else 0,
            rejected_vias + len([v for v in vias if not v.get("net")]), "实际过孔外层铜盘 + EDA 网络；内部基线 0.1524mm 或显式规则")
    covered("object_paste_bridge", paste_checked if rules.get("paste_bridge") else 0, paste_missing, "同面实际锡膏 + 明确 SMD 焊盘")
    covered("object_net_contact", len(net_nodes), len([p for p in usable if not p.get("net")]),
            "完整网络连接/跨层及 net-tie 例外；当前仅同层候选")
    if coverage[-1]["status"] == "measured":
        coverage[-1]["status"] = "partial"  # Native net anchors do not prove no net-tie/short/open.
    covered("object_via_mask_spacing", via_mask_checked, via_mask_missing,
            "逐过孔铜盘 + 明确 tented 策略 + 同面开窗 + 显式 via_to_mask_mm")
    covered("object_hole_copper_spacing", hole_checked, hole_missing,
            "逐孔真实轮廓 + 网络 + 实际铜块网络锚点 + 显式 hole_to_unconnected_copper_mm")
    if errors or truncated:
        coverage.append(dict(rule="object_scan_completion", status="incomplete", note="对象扫描/关联未完成",
                             required_data="修复资料或提高候选/问题预算后重新扫描"))
    if truncated:
        errors.append("对象问题超过报告预算，结果截断")
    return dict(checks=checks, coverage=coverage, errors=errors, scan_stats=stats,
                object_counts=dict(pads=len(pads), verified_pads=len(usable), vias=len(vias),
                                   pad_pairs=pair_checked, via_pairs=via_checked), rule_file=rule_file,
                rule_sha256=rule_sha256, effective_rules_sha256=effective_sha256, effective_rules=rules)

"""ODB++ outer-object DFM entry: native FID identities, real copper/mask/paste."""
from collections import Counter
from time import perf_counter

from shapely import from_wkb

from src.design_inputs.odb import parse_odb
from src.design_inputs.coordination import source_fingerprint
from src.layout_geometry import ScanLimits
from src.pcb_object_checks import scan_object_checks


def analyze_odb(path, step=None, object_rules=None, scan_limits=None, preview_path=None):
    started = perf_counter()
    before = source_fingerprint(path)
    layout = parse_odb(path, step=step, object_checks_only=True)
    if layout["status"] == "needs_step":
        raise ValueError("ODB++ 有多个 step，请选择: " + ", ".join(layout["steps"]))
    films, pads, vias, errors = {}, [], [], []
    unknown_pads = 0
    count = layout["copper_layer_count"]
    native_holes = {}
    for row in layout["films"]:
        if row["role"] == "drill" and row["status"] == "parsed":
            for feature in row["records"]:
                if feature.get("connection_id") and feature.get("geometry_wkb") and feature.get("polarity") == "P":
                    native_holes.setdefault(feature["connection_id"], []).append((row, feature))
    for row in layout["films"]:
        role = row["role"]
        if role == "copper":
            role = "copper_top" if row["copper_index"] == 1 else "copper_bottom"
        elif role in ("mask", "paste"):
            if row["side"] not in ("TOP", "BOTTOM"):
                errors.append(row["name"] + ": 面别未知，未参与对象检查")
                continue
            role += "_" + row["side"].lower()
        else:
            continue
        if row["status"] != "parsed":
            errors.append(row["name"] + ": 存在未解析图元，未使用部分底片做硬判定")
            continue
        if role in films:
            raise ValueError("ODB++ 多层映射到同一角色: " + role)
        film = dict(role=role, path=row["source"], geometry=from_wkb(row["geometry_wkb"]),
                    brd_alignment=False, copper_index=row.get("copper_index"))
        films[role] = film
        if not role.startswith("copper"):
            continue
        for feature in row["records"]:
            if feature.get("primitive_kind") != "P" or feature.get("polarity") != "P" or not feature.get("geometry_wkb"):
                continue
            attrs = feature.get("attributes", {})
            obj = dict(id=feature.get("id") or f"{row['name']}:{feature['index']}",
                       geometry=from_wkb(feature["geometry_wkb"]), layer=role,
                       net=feature.get("net", ""), refdes=feature.get("refdes", ""),
                       pin=feature.get("pin", ""), source=row["source"],
                       smd=attrs.get(".smd") in (True, "1"), mask_policy="required")
            if feature.get("refdes") and str(attrs.get(".pad_usage", "0")) == "0":
                pads.append(obj)
            elif feature.get("kind") == "VIA" or str(attrs.get(".pad_usage")) == "1":
                holes = native_holes.get(feature.get("connection_id"), [])
                index = row["copper_index"]
                matching = [(drill, hole) for drill, hole in holes
                            if drill.get("start_layer") is not None and drill.get("end_layer") is not None and
                            drill["start_layer"] <= index <= drill["end_layer"]]
                if len(matching) == 1:
                    obj["hole_geometry"] = from_wkb(matching[0][1]["geometry_wkb"])
                vias.append(obj)
            elif str(attrs.get(".pad_usage", "")) == "0":
                unknown_pads += 1
    for role in ("copper_top", "copper_bottom", "mask_top", "mask_bottom"):
        if role not in films:
            errors.append(role + ": 缺可用对象制造底片，当前面未完整检查")
    # The final generic scope notice is always present; other input warnings
    # prevent claiming complete object coverage even if a subset can be measured.
    complete = not errors and not unknown_pads and layout.get("component_complete") is True and len(layout["warnings"]) == 1
    bundle = scan_object_checks(films, pads, vias, ScanLimits(**(scan_limits or {})), object_rules, complete)
    errors.extend(bundle["errors"])
    bundle["coverage"].append(dict(rule="odb_pad_identity", status="partial" if unknown_pads else
                                  "measured" if pads else "missing", checked=len(pads), missing=unknown_pads,
                                  note="身份来自 EDA FID，不按最近坐标猜位号", required_data="完整 EDA TOP/FID/CMP 关联"))
    for rule, need in (("full_copper_width_spacing", "全部铜层/逐层铜厚及完整最小宽度算法"),
                       ("full_annular_ring", "全层功能盘/非功能盘及镀孔工艺/制造位置公差"),
                       ("cross_layer_net_drc", "完整跨层网络连接与网表比对及 net-tie 例外")):
        bundle["coverage"].append(dict(rule=rule, status="missing", note="本入口为外层对象检查，不执行该完整规则", required_data=need))
    if layout["warnings"]:
        bundle["coverage"].append(dict(rule="odb_input_subset", status="partial", note="ODB++ 导入存在提示/未覆盖内容",
                                       required_data="完整符号、跨层网络 DRC、工艺及 step-repeat"))
    for error in errors:
        bundle["coverage"].append(dict(rule="odb_input_error", status="incomplete", note=error,
                                       required_data="补齐并重新导出对应制造图层"))
    after = source_fingerprint(path)
    if before != after:
        raise ValueError("ODB++ 在分析期间变化，结果已丢弃")
    failed = sum(c["status"] == "fail" for c in bundle["checks"])
    preview = ""
    if preview_path:
        from src.gerber_analysis import write_preview
        preview = write_preview(dict(films=list(films.values()), checks=bundle["checks"], errors=errors), preview_path)
    return dict(schema_version=1, input=before, selected_step=layout["selected_step"],
                checks=bundle["checks"], geometry_coverage=bundle["coverage"], errors=errors,
                warnings=layout["warnings"], object_counts=bundle["object_counts"],
                rule_file=bundle["rule_file"], scan_stats=bundle["scan_stats"],
                rule_sha256=bundle["rule_sha256"], effective_rules_sha256=bundle["effective_rules_sha256"],
                effective_rules=bundle["effective_rules"],
                parsed_layers=[dict(name=f["name"], role=f["role"], status=f["status"],
                                   objects=f["feature_count"], source=f["source"]) for f in layout["films"]],
                rule_counts=dict(Counter(c["field"] for c in bundle["checks"])),
                verdict_code="fail" if failed else "review", error_count=failed,
                warning_count=len(bundle["checks"])-failed, geometry_complete=False, releasable=False,
                verdict="不通过（存在明确对象违规）" if failed else "需复核（对象候选/制造覆盖不完整）",
                elapsed_seconds=round(perf_counter()-started, 3), copper_layer_count=count,
                preview=preview,
                scope="ODB++ 外层对象初筛；不替代完整 DFM、跨层开短路或板厂能力认证")


def attach_odb_result(pcb, bundle, design_unit=""):
    """Reuse existing PCB issue/coordinate/report pipeline; never guess BRD alignment."""
    from src.gerber_analysis import attach_gerber_result
    bridge = dict(checks=bundle["checks"], errors=bundle["errors"], source_label="ODB++",
                  parsed_layers=[dict(path=r["source"], role=r["role"], objects=r["objects"], warnings=[])
                                 for r in bundle["parsed_layers"]], folder=bundle["input"]["path"],
                  copper_um=pcb["copper_um"], mask_bridge_checks=[], copper_layer_checks=[],
                  geometry_coverage=bundle["geometry_coverage"], geometry_complete=False,
                  geometry_requirements=[dict(row) for row in bundle["geometry_coverage"]
                                         if row["status"] != "measured"],
                  geometry_data_source=bundle["input"]["path"], scan_stats=bundle["scan_stats"],
                  elapsed_seconds=bundle["elapsed_seconds"])
    result = attach_gerber_result(pcb, bridge, design_unit)
    result["odb_object_result"] = bundle
    result["geometry_input_source"] = "ODB++ 原生对象/FID"
    if bundle.get("preview"):
        result["gerber_preview"] = bundle["preview"]
    return result

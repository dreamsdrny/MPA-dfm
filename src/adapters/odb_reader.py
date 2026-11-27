"""ODB++ ASCII job 导入：matrix、EDA 包、CMP/PRP/TOP 和常用 features。

每次选择一个 step；panel step-repeat 不展开，不混合不同 step 的位号。
"""
import re
import shlex
import math

from src.infrastructure.archives import InputContainer


def _blocks(text, name):
    rows = []
    for body in re.findall(r"\b" + name + r"\s*\{([^}]*)\}", text, re.IGNORECASE):
        row = {}
        for line in body.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                row[key.strip().upper()] = value.strip().strip('"')
        rows.append(row)
    return rows


def _packages(text):
    packages, nets = [], []
    for line in text.splitlines():
        if line.strip().startswith("PKG "):
            tokens = shlex.split(line.split(";", 1)[0])
            packages.append(tokens[1])
        elif line.strip().startswith("NET "):
            tokens = shlex.split(line.split(";", 1)[0])
            nets.append(tokens[1])
    return packages, nets


def _components(text, side, packages, nets, source, step, default_scale=None):
    from src.design_inputs.odb_features import number, unit_scale
    scale = unit_scale(text, default_scale)
    records, warnings, attributes, current = [], [], {}, None
    component_index = -1
    properties = {"PART_NUMBER": "part_number", "VALUE": "value", "MPN": "manufacturer_part_number",
                  "MANUFACTURER_PART_NUMBER": "manufacturer_part_number", "MANUFACTURER": "manufacturer",
                  "PACKAGE": "package", "DESCRIPTION": "description"}
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("@"):
            key, _, value = line[1:].partition(" ")
            attributes[key] = value.strip()
            continue
        if not line or line.startswith(("#", "&", "UNITS=", "U ")) or re.fullmatch(r"ID=\d+", line):
            continue
        try:
            tokens = shlex.split(line.split(";", 1)[0])
            if tokens[0] == "CMP":
                component_index += 1
                current = None  # Invalid new record must not attach following properties to old component.
                if len(tokens) != 8:
                    raise ValueError("CMP 字段数无效")
                package_index = int(tokens[1])
                if package_index < 0 or tokens[5] not in ("N", "M"):
                    raise ValueError("CMP 封装索引/镜像无效")
                dnp = False
                dnp_explicit = False
                attrs = line.split(";")[1] if ";" in line else ""
                for attr in attrs.split(","):
                    key, _, value = attr.strip().partition("=")
                    if attributes.get(key) == ".no_pop":
                        dnp_explicit = True
                        dnp = value not in ("0", "N", "NO", "FALSE")
                current = dict(refdes=tokens[6], x_mm=number(tokens[2])*scale,
                               y_mm=number(tokens[3])*scale, rotation=number(tokens[4]),
                               rotation_convention="ODB clockwise", mirror=tokens[5] == "M", side=side,
                               package=packages[package_index] if package_index < len(packages) else "",
                                 package_index=package_index, part_name=tokens[7], dnp=dnp,
                                 dnp_explicit=dnp_explicit,
                                component_index=component_index,
                               dnp_known=any(name == ".no_pop" for name in attributes.values()),
                               pins=[], source=source, step=step, coordinate_kind="component_origin")
                if not current["package"]:
                    warnings.append(f"{tokens[6]}: 无 EDA 封装定义 {package_index}")
                records.append(current)
            elif tokens[0] == "PRP":
                if current is None or len(tokens) < 3:
                    raise ValueError("PRP 无有效 CMP/值")
                name, value = tokens[1].upper(), tokens[2]
                current.setdefault("properties", {})[name] = value
                if name in properties:
                    current[properties[name]] = value
            elif tokens[0] == "TOP":
                if current is None or len(tokens) != 9:
                    raise ValueError("TOP 无有效 CMP 或字段数无效")
                net_index = int(tokens[6])
                current["pins"].append(dict(pin_index=int(tokens[1]), x_mm=number(tokens[2])*scale,
                                            y_mm=number(tokens[3])*scale, rotation=number(tokens[4]),
                                            mirror=tokens[5], net_index=net_index,
                                            subnet_index=int(tokens[7]), pin=tokens[8],
                                            net=nets[net_index] if 0 <= net_index < len(nets) else ""))
            else:
                warnings.append(f"行{line_number}: 未支持 components 命令 {tokens[0]}")
        except (ValueError, IndexError) as exc:
            warnings.append(f"行{line_number}: {exc}")
    return records, warnings


def parse_odb(path, step=None, object_checks_only=False):
    from src.design_inputs.odb_features import parse_features
    with InputContainer(path) as container:
        roots = {name[:-len("matrix/matrix")] for name in container.names
                 if name.lower().endswith("matrix/matrix")}
        # matrix files may be individually gzip-compressed.
        roots.update(name[:-len("matrix/matrix.gz")] for name in container.names
                      if name.lower().endswith("matrix/matrix.gz"))
        roots.update(name[:-len("matrix/matrix.z")] for name in container.names
                     if name.lower().endswith("matrix/matrix.z"))
        if len(roots) != 1:
            raise ValueError("未找到唯一的 ODB++ matrix/matrix 根目录")
        prefix = next(iter(roots))
        matrix = container.text(container.find(prefix + "matrix/matrix"))
        layer_defs = _blocks(matrix, "LAYER")
        step_defs = _blocks(matrix, "STEP")
        steps = set(row["NAME"] for row in step_defs if row.get("NAME"))
        step_names = {}
        for name in container.names:
            relative = name[len(prefix):] if name.startswith(prefix) else ""
            parts = relative.split("/")
            if len(parts) >= 3 and parts[0].lower() == "steps":
                # Matrix step names are often uppercase; paths lowercase.
                if not any(value.casefold() == parts[1].casefold() for value in steps):
                    steps.add(parts[1])
                step_names[parts[1].casefold()] = parts[1]
        steps = sorted(steps)
        if not steps:
            raise ValueError("ODB++ 没有可用 step")
        selected = next((value for value in steps if step and value.casefold() == step.casefold()), step)
        selected = selected or (steps[0] if len(steps) == 1 else None)
        if selected and selected not in steps:
            raise ValueError("ODB++ step 不存在: " + str(selected))
        misc = container.find(prefix + "misc/info")
        version = None
        if misc:
            info = container.text(misc)
            match = re.search(r"(?im)^\s*(?:ODB_VERSION|VERSION)\s*[= ]\s*([^\r\n]+)", info)
            version = match[1].strip() if match else None
            major = re.search(r"(?im)^ODB_VERSION_MAJOR=(\d+)$", info)
            minor = re.search(r"(?im)^ODB_VERSION_MINOR=(\d+)$", info)
            if version is None and major:
                version = major[1] + "." + (minor[1] if minor else "0")
        layer_paths = sorted({"/".join(name[len(prefix):].split("/")[1:4:2]) for name in container.names
                              if name.startswith(prefix + "steps/") and len(name[len(prefix):].split("/")) >= 5
                              and name[len(prefix):].split("/")[2].lower() == "layers"})
        result = dict(status="partial", root=prefix.rstrip("/"), version=version, steps=steps,
                      selected_step=selected, layers=layer_paths, layer_definitions=layer_defs,
                      component_records=[], components_available=False, films=[], warnings=[],
                       geometry_complete=False, component_complete=True)
        result["render_scope"] = "outer_object_layers" if object_checks_only else "all_supported_layers"
        if selected is None:
            result["status"] = "needs_step"
            result["warnings"].append("ODB++ 有多个 step，请明确选择单板/拼板，不自动混合位号")
            return result
        selected_path = step_names.get(selected.casefold(), selected)
        step_prefix = prefix + "steps/" + selected_path + "/"
        eda = container.find(step_prefix + "eda/data")
        eda_text = container.text(eda) if eda else ""
        packages, nets = _packages(eda_text) if eda else ([], [])
        from src.design_inputs.odb_connectivity import feature_links
        links, notices = feature_links(eda_text)
        result["warnings"].extend(notices)
        # ODB v7 is an inch-only specification. Do not infer units from geometry.
        default_scale = 25.4 if version and version.split(".")[0] == "7" else None
        symbol_cache, symbol_loading = {}, set()

        def user_symbol(name):
            key = name.casefold()
            if key in symbol_loading:
                raise ValueError("ODB++ 用户符号循环引用: " + name)
            if key not in symbol_cache:
                file = container.find(prefix + "symbols/" + name + "/features")
                if not file:
                    raise ValueError("缺 ODB++ 用户符号: " + name)
                symbol_loading.add(key)
                try:
                    parsed = parse_features(container.text(file), symbols_external=user_symbol)
                    if parsed["warnings"] or parsed["geometry"].is_empty:
                        raise ValueError("用户符号未完整解析: " + name)
                    symbol_cache[key] = parsed["geometry"]
                finally:
                    symbol_loading.remove(key)
            return symbol_cache[key]

        copper_defs = [r for r in layer_defs if r.get("TYPE", "").upper() in ("SIGNAL", "POWER_GROUND")
                       and r.get("CONTEXT", "BOARD").upper() == "BOARD"]
        copper_indices = {r["NAME"].casefold(): i for i, r in enumerate(copper_defs, 1)}
        result["copper_layer_count"] = len(copper_defs)
        layer_positions = {r.get("NAME", "").casefold(): i for i, r in enumerate(layer_defs)}
        first_copper = min((layer_positions[r["NAME"].casefold()] for r in copper_defs), default=-1)
        last_copper = max((layer_positions[r["NAME"].casefold()] for r in copper_defs), default=-1)
        stephdr = container.find(step_prefix + "stephdr")
        if stephdr and re.search(r"\bSTEP-REPEAT\s*\{", container.text(stephdr), re.IGNORECASE):
            result["warnings"].append("所选 step 含 STEP-REPEAT，未展开子板实例，器件列表不是完整拼板")
            result["component_complete"] = False
        for row in layer_defs:
            name = row.get("NAME", "")
            if not name:
                continue
            layer_prefix = step_prefix + "layers/" + name + "/"
            kind = row.get("TYPE", "").upper()
            comp_file = container.find(layer_prefix + "components")
            if kind == "COMPONENT" or comp_file:
                side = row.get("SIDE", "").upper()
                if side not in ("TOP", "BOTTOM"):
                    side = "TOP" if name.lower() == "comp_+_top" else "BOTTOM" if name.lower() == "comp_+_bot" else ""
                if comp_file:
                    try:
                        records, notices = _components(container.text(comp_file), side, packages, nets,
                                                        f"{path}!/{comp_file}", selected, default_scale)
                        result["component_records"].extend(records)
                        result["warnings"].extend(f"{name}: {notice}" for notice in notices)
                        if notices:
                            result["component_complete"] = False
                    except ValueError as exc:
                        result["warnings"].append(f"{name}: {exc}")
                        result["component_complete"] = False
                else:
                    result["warnings"].append(f"{name}: 缺 components 文件，器件覆盖不完整")
                    result["component_complete"] = False
                continue
            feature_file = container.find(layer_prefix + "features")
            if not feature_file:
                continue
            role = {"SIGNAL": "copper", "POWER_GROUND": "copper", "SOLDER_MASK": "mask",
                     "SOLDER_PASTE": "paste", "SILK_SCREEN": "silkscreen", "DRILL": "drill"}.get(kind, "unknown")
            if object_checks_only and (role not in ("copper", "mask", "paste", "drill") or
                    role == "copper" and copper_indices.get(name.casefold()) not in (1, len(copper_defs))):
                continue  # Explicit outer-object scope; not full layer coverage.
            if row.get("POLARITY", "POSITIVE").upper() != "POSITIVE":
                result["warnings"].append(f"{name}: 负片图层未渲染，不能猜图幅")
                continue
            try:
                geometry = parse_features(container.text(feature_file), symbols_external=user_symbol)
                shape = geometry.pop("geometry")
                side = row.get("SIDE", "").upper()
                if not side and role in ("mask", "paste", "silkscreen"):
                    pos = layer_positions[name.casefold()]
                    side = "TOP" if first_copper >= 0 and pos < first_copper else "BOTTOM" if last_copper >= 0 and pos > last_copper else ""
                for feature in geometry["records"]:
                    feature["primitive_kind"] = feature["kind"]
                    feature.update(links.get((name.casefold(), feature["index"]), {}))
                result["films"].append(dict(name=name, role=role, side=side,
                                              copper_index=copper_indices.get(name.casefold()),
                                              start_layer=copper_indices.get(row.get("START_NAME", "").casefold()),
                                              end_layer=copper_indices.get(row.get("END_NAME", "").casefold()),
                                              source=f"{path}!/{feature_file}", geometry_wkb=shape.wkb_hex,
                                             bounds_mm=[] if shape.is_empty else list(shape.bounds), area_mm2=shape.area, **geometry))
                result["warnings"].extend(f"{name}: {notice}" for notice in geometry["warnings"])
            except ValueError as exc:
                result["warnings"].append(f"{name}: {exc}")
        # Even malformed/legacy matrix jobs retain their inventory, never fabricate layers.
        if not layer_defs:
            result["warnings"].append("matrix 未解析到 LAYER 定义，仅保留结构清单")
        profile = container.find(step_prefix + "profile")
        if profile:
            try:
                geometry = parse_features(container.text(profile), profile=True, symbols_external=user_symbol)
                shape = geometry["geometry"]
                result["profile"] = dict(geometry_wkb=shape.wkb_hex, area_mm2=shape.area,
                                         status=geometry["status"], source=f"{path}!/{profile}")
                result["warnings"].extend(f"profile: {notice}" for notice in geometry["warnings"])
            except ValueError as exc:
                result["warnings"].append(f"profile: {exc}")
        result["components_available"] = bool(result["component_records"])
        components = {(c["side"], c["component_index"]): c for c in result["component_records"]}
        for film in result["films"]:
            for feature in film.get("records", []):
                if feature.get("kind") == "TOP":
                    component = components.get((feature.get("side"), feature.get("component_index")))
                    pin = next((p for p in (component or {}).get("pins", [])
                                if p["pin_index"] == feature.get("pin_index")), None)
                    if component and pin:
                        if not feature.get("geometry_wkb"):
                            continue  # Unsupported source record retained only for FID indexing.
                        if feature.get("primitive_kind") == "P" and math.hypot(
                                feature["x_mm"] - pin["x_mm"], feature["y_mm"] - pin["y_mm"]) > .01:
                            result["warnings"].append(f"{component['refdes']}.{pin['pin']}: FID 焊盘中心与 TOP 引脚坐标差大于0.01mm")
                            feature["net"] = ""
                        elif feature.get("net") and pin.get("net") != feature["net"]:
                            result["warnings"].append(f"{component['refdes']}.{pin['pin']}: EDA FID 与 TOP 网络冲突")
                            feature["net"] = ""
                        else:
                            feature.update(refdes=component["refdes"], pin=pin["pin"],
                                           id=component["refdes"] + "." + pin["pin"])
        result["eda_links_available"] = bool(links)
        if not result["components_available"]:
            result["warnings"].append("所选 ODB++ step 无可用 CMP 器件，不能凭铜图形推测位号")
            result["component_complete"] = False
        result["warnings"].append("ODB++ 对象/FID 子集导入；未完成完整网络跨层 DRC、全部符号/工艺及 step-repeat，不作为完整几何 DFM 放行")
        return result

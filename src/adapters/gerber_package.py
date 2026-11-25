"""Gerber 单文件/目录/安全归档导入，保留最终几何和明确 X2 器件属性。"""
import tempfile
from pathlib import Path

from gerbonara import GerberFile
from gerbonara.graphic_objects import Flash
from gerbonara.utils import MM

from src.infrastructure.archives import InputContainer
from src.adapters.gerber_reader import _role
from src.gerber_analysis import read_film
from src.gerber_export import GERBER_EXTENSIONS


def _is_gerber(name):
    path = Path(name)
    return path.suffix.lower() in GERBER_EXTENSIONS | {".pho"} or (
        path.suffix.lower().startswith(".g") and path.suffix[2:].isdigit())


def parse_gerber_package(path):
    path = Path(path)
    films, components, warnings = [], {}, []

    def parse(file, name):
        source = str(path.resolve()) if path.is_file() and _is_gerber(path.name) else f"{path.resolve()}!/{name}"
        try:
            film = read_film(file)
            with file.open(encoding="ascii", errors="replace") as stream:
                role, _ = _role(name, stream.read(20000))
            role = film["role"] if film["role"] != "unknown" else role
            # Temporary flat filename has no layer-name hint: use the real name.
            if role == "unknown":
                from src.gerber_analysis import identify_layer
                with file.open(encoding="ascii", errors="replace") as stream:
                    role = identify_layer(name, stream.read(20000))
            geometry = film["geometry"]
            films.append(dict(name=name, role=role, source=source, area_mm2=geometry.area,
                              bounds_mm=[] if geometry.is_empty else list(geometry.bounds), geometry_wkb=geometry.wkb_hex,
                              feature_count=len(film["objects"]), status="parsed", warnings=film["warnings"]))
            gerber = GerberFile.open(file)
            for index, original in enumerate(gerber.objects):
                attrs = original.attrs or {}
                ref_attr = attrs.get(".C") or attrs.get(".P")
                if not ref_attr or not ref_attr[0].strip():
                    continue
                ref = ref_attr[0].strip().upper()
                record = components.setdefault(ref, dict(refdes=ref, package="", pins=[], sources=[],
                                                          coordinate_kind="not_provided", side=""))
                if source not in record["sources"]:
                    record["sources"].append(source)
                side = "TOP" if role.endswith("_top") else "BOTTOM" if role.endswith("_bottom") else ""
                if side and (role.startswith("paste") or role.startswith("silkscreen")):
                    if record["side"] and record["side"] != side:
                        warnings.append(f"{ref}: Gerber 同位号跨两面，需确认版本/通孔属性")
                    else:
                        record["side"] = side
                for attr, field in ((".CFtp", "package"), (".CVal", "value"), (".CMPN", "manufacturer_part_number")):
                    if attrs.get(attr):
                        value = attrs[attr][0]
                        if record.get(field) and record[field] != value:
                            warnings.append(f"{ref}: Gerber 器件属性 {field} 不一致")
                        else:
                            record[field] = value
                if attrs.get(".P") and isinstance(original, Flash):
                    obj = original.converted(MM)
                    record["pins"].append(dict(pin=attrs[".P"][1] if len(attrs[".P"]) > 1 else "",
                                               x_mm=obj.x, y_mm=obj.y, layer=role, source=source,
                                               feature_index=index, coordinate_kind="pad_flash"))
                record["source"] = "; ".join(record["sources"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            warnings.append(f"{name}: Gerber 导入失败: {exc}")

    if path.is_file() and _is_gerber(path.name):
        parse(path, path.name)
    else:
        with InputContainer(path) as container, tempfile.TemporaryDirectory(prefix="evan-gerber-import-") as temp:
            for index, name in enumerate(container.names):
                if not _is_gerber(name):
                    continue
                # A private flat filename avoids applying archive paths to disk.
                file = Path(temp) / f"{index}{Path(name).suffix}"
                file.write_bytes(container.read(name))
                parse(file, name)
    if not films:
        raise ValueError("未读取到有效 Gerber 图层: " + "; ".join(warnings[:5]))
    if not components:
        warnings.append("Gerber 没有明确 X2 器件属性；关联 BOM 需要另附器件坐标 CSV，不从铜形状猜位号")
    roles = {}
    for film in films:
        roles.setdefault(film["role"], []).append(film["name"])
    for role, names in roles.items():
        if role == "unknown":
            warnings.append("Gerber 图层角色未知: " + ", ".join(names))
        elif len(names) > 1:
            warnings.append(f"Gerber {role} 多文件，可能版本不一致；叠图不是已确认单套底片")
    warnings.append("Gerber 引脚位置不等于器件放置原点；不平均焊盘坐标生成器件原点")
    return dict(status="partial" if warnings else "parsed", films=films,
                component_records=list(components.values()), components_available=bool(components),
                geometry_complete=False, warnings=warnings, steps=[])

"""Read BRD object origins and cross-section via Cadence's headless extracta."""
import csv
import json
import math
import os
import re
import shutil
import tempfile
from pathlib import Path

from src.adapters.allegro_bridge import discover_editor
from src.adapters.manufacturing_export import _run
from src.brd_input import content_sha256
from src.infrastructure.resources import read_bounded
from src.pick import _UNIT_MM

VIEWS = {
    "components": ("SYMBOL", ("REFDES", "SYM_X", "SYM_Y", "SYM_ROTATE", "SYM_MIRROR", "SYM_TYPE", "SYM_NAME")),
    "layers": ("LAYER", ("LAYER_SORT", "LAYER_SUBCLASS", "LAYER_CONDUCTOR", "LAYER_MATERIAL", "LAYER_THICKNESS", "LAYER_TYPE", "LAYER_STACKUP_NAME")),
    "board": ("BOARD", ("BOARD_UNITS", "BOARD_LAYERS", "BOARD_THICKNESS", "BOARD_BOARD_THICKNESS")),
}

OBJECT_VIEWS = {
    "pins": ("COMPONENT_PIN", ("REFDES", "PIN_NUMBER", "PIN_X", "PIN_Y", "PAD_STACK_NAME", "NET_NAME", "PIN_TYPE")),
    "pads": ("COMPOSITE_PAD", ("CLASS", "PAD_STACK_NAME", "START_LAYER_NAME", "END_LAYER_NAME", "REFDES", "PIN_NUMBER",
        "PIN_X", "PIN_Y", "VIA_X", "VIA_Y", "NET_NAME", "TEST_POINT", "DRILL_HOLE_NAME", "DRILL_HOLE_X", "DRILL_HOLE_Y",
        "DRILL_HOLE_PLATING", "DRILL_HOLE_NAME2", "DRILL_FIGURE_SHAPE", "DRILL_FIGURE_WIDTH", "DRILL_FIGURE_HEIGHT")),
    "geometry": ("FULL_GEOMETRY", ("CLASS", "SUBCLASS", "REFDES", "PIN_NUMBER", "PAD_STACK_NAME", "NET_NAME", "RECORD_TAG",
        "GRAPHIC_DATA_NAME", "GRAPHIC_DATA_NUMBER", *(f"GRAPHIC_DATA_{i}" for i in range(1, 11)), "PIN_ROTATION_ABSOLUTE")),
    "properties": ("COMPONENT", ("REFDES", "COMP_PACKAGE", "COMP_HEIGHT", "COMP_PART_NUMBER", "COMP_VALUE", "COMP_CLASS")),
}


def discover_extracta(editor_path=""):
    editor = discover_editor(editor_path)
    candidates = [str(Path(editor).with_name("extracta.exe")) if editor else "", shutil.which("extracta.exe")]
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()), "")


def number(text):
    try:
        value = float(str(text).strip())
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def length_mm(text, unit):
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-zA-Zμµ]*)\s*", str(text))
    if not match:
        return None
    suffix = match[2].lower().replace("μ", "u").replace("µ", "u") or unit
    factor = _UNIT_MM.get(suffix)
    value = number(match[1])
    return value * factor if value is not None and factor is not None else None


def parse_view(path, required):
    """A/J/S field labels and units are mandatory; never infer output units."""
    raw = read_bounded(path, 64 * 1024 * 1024)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("mbcs" if os.name == "nt" else "utf-8", errors="strict")
    header, unit, rows = None, "", []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.strip().split("!")
        if fields[-1:] == [""]:
            fields.pop()
        if fields[0] == "A":
            if header is not None:
                raise ValueError("extracta 表头重复")
            header = fields[1:]
            if not set(required).issubset(header) or len(header) != len(set(header)):
                raise ValueError("extracta 输出字段缺失或重复")
        elif fields[0] == "J":
            if len(fields) < 9 or fields[8].strip().lower() not in _UNIT_MM:
                raise ValueError("extracta 设计单位未获取，不换算坐标")
            unit = fields[8].strip().lower()
        elif fields[0] == "S":
            if header is None or len(fields) != len(header) + 1:
                raise ValueError("extracta 数据行与表头不一致")
            rows.append(dict(zip(header, fields[1:])))
            if len(rows) > 200000:
                raise ValueError("extracta 对象数超限")
    if header is None or not unit:
        raise ValueError("extracta 输出没有完整表头/设计单位")
    return unit, rows


def parse_components(path):
    unit, rows = parse_view(path, VIEWS["components"][1])
    objects, warnings, seen = [], [], set()
    for row in rows:
        ref = row["REFDES"].strip()
        if not ref or row["SYM_TYPE"].upper() != "PACKAGE":
            continue
        if ref in seen:
            raise ValueError("extracta 位号重复：" + ref)
        seen.add(ref)
        x, y = number(row["SYM_X"]), number(row["SYM_Y"])
        if x is None or y is None:
            warnings.append(ref + " X/Y 未获取")
            continue
        rotation = number(row["SYM_ROTATE"])
        mirror = row["SYM_MIRROR"].strip().upper()
        side = {"YES": "BOTTOM", "NO": "TOP"}.get(mirror, "")
        if rotation is None or not side:
            warnings.append(ref + " 旋转/面别未获取")
        objects.append(dict(refdes=ref, x_mm=x * _UNIT_MM[unit], y_mm=y * _UNIT_MM[unit],
                            rotation=rotation, side=side, footprint=row["SYM_NAME"],
                            source=str(Path(path).resolve())))
    return dict(unit=unit, objects=objects, package_count=len(seen), warnings=warnings,
                complete=not warnings)


def parse_layers(path):
    unit, rows = parse_view(path, VIEWS["layers"][1])
    copper = [row for row in rows if row["LAYER_CONDUCTOR"].upper() == "YES"]
    names = {row["LAYER_STACKUP_NAME"].strip() for row in copper}
    if len(names) > 1:
        raise ValueError("BRD 有多区域叠构，需选择/确认区域后才能自动使用铜厚")
    order = [number(row["LAYER_SORT"]) for row in copper]
    if any(value is None for value in order) or len(set(order)) != len(order):
        raise ValueError("extracta 铜层顺序未获取或重复")
    copper.sort(key=lambda row: number(row["LAYER_SORT"]))
    result, seen = [], set()
    for index, row in enumerate(copper, 1):
        name = row["LAYER_SUBCLASS"].strip()
        if not name or name in seen:
            raise ValueError("extracta 铜层名未获取或重复")
        seen.add(name)
        thickness = length_mm(row["LAYER_THICKNESS"], unit)
        thickness = round(thickness * 1000, 9) if thickness is not None and thickness > 0 else None
        if row["LAYER_MATERIAL"].strip().upper() != "COPPER":
            thickness = None
        result.append(dict(index=index, name=name, copper_um=thickness,
                           material=row["LAYER_MATERIAL"], source="BRD extracta LAYER_THICKNESS（叠构设计铜厚，非实测）",
                           raw_thickness=row["LAYER_THICKNESS"], thickness_basis="design_stackup"))
    return result


def extract_brd_data(brd_path, output_dir, editor_path="", timeout=90):
    """Partial/unavailable sources are explicit. Invalid bytes never become success."""
    board = Path(brd_path).resolve()
    digest = content_sha256(board)
    tool = discover_extracta(editor_path)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="extracta-", dir=output))
    payload = dict(status="unavailable", directory=str(folder), source_brd_sha256=digest,
                   components=[], stackup=[], updates={}, warnings=[], source="Cadence extracta.exe")
    if not tool:
        payload["warnings"].append("未找到 extracta.exe，器件坐标/原生铜厚未获取；不会启动 PCB Editor")
    else:
        parent = Path(tempfile.gettempdir()) / "opencode"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="evan-extracta-", dir=parent) as temp:
            work = Path(temp)
            copy = work / "board.brd"
            shutil.copy2(board, copy)
            if content_sha256(copy) != digest:
                raise ValueError("BRD 在复制期间变化")
            command_file = work / "views.txt"
            command_file.write_text("".join(view + "\n" + "\n".join(fields) + "\nEND\n"
                                            for view, fields in VIEWS.values()), encoding="ascii")
            results = [work / (name + ".out") for name in VIEWS]
            env = dict(os.environ, PATH=str(Path(tool).parent) + os.pathsep + os.environ.get("PATH", ""))
            try:
                code, log = _run([tool, str(copy), str(command_file), *(str(p) for p in results)], work, timeout, env)
                (folder / "extracta_console.log").write_text(log, encoding="utf-8")
                if code:
                    raise ValueError(f"extracta 返回 {code}，坐标/叠构未获取")
                for name, result in zip(VIEWS, results):
                    if result.is_file():
                        shutil.copy2(result, folder / result.name)
                try:
                    comp = parse_components(folder / "components.out")
                    payload.update(status="partial" if comp["warnings"] else "extracted",
                                   components=comp["objects"], unit=comp["unit"], component_count=comp["package_count"])
                    payload["warnings"].extend(comp["warnings"])
                    if comp["complete"]:
                        payload["updates"]["comp_count"] = comp["package_count"]
                except (OSError, ValueError) as exc:
                    payload["warnings"].append("器件坐标未获取：" + str(exc))
                try:
                    layer_unit, _ = parse_view(folder / "layers.out", VIEWS["layers"][1])
                    if payload.get("unit") and layer_unit != payload["unit"]:
                        raise ValueError("器件与叠构输出单位不一致")
                    stack = parse_layers(folder / "layers.out")
                    if not stack:
                        raise ValueError("铜层未获取")
                    payload["stackup"] = stack
                    payload.setdefault("unit", layer_unit)
                    payload["status"] = "extracted"
                    payload["updates"].update(layer_count=len(stack), layers=[row["name"] for row in stack], stackup=stack)
                    outer = [stack[0]["copper_um"], stack[-1]["copper_um"]]
                    payload["updates"]["copper_um"] = outer[0] if outer[0] is not None and outer[0] == outer[1] else None
                    if any(row["copper_um"] is None for row in stack):
                        payload["warnings"].append("部分铜层厚度未获取，相关能力分档待确认")
                except (OSError, ValueError) as exc:
                    payload["warnings"].append("叠构未获取：" + str(exc))
                try:
                    unit, rows = parse_view(folder / "board.out", VIEWS["board"][1])
                    if payload.get("unit") and unit != payload["unit"]:
                        raise ValueError("板信息与坐标输出单位不一致")
                    if len(rows) == 1:
                        count = number(rows[0]["BOARD_LAYERS"])
                        if payload.get("stackup") and count is not None and count != len(payload["stackup"]):
                            payload["stackup"] = []
                            for key in ("stackup", "layers", "layer_count", "copper_um"):
                                payload["updates"].pop(key, None)
                            raise ValueError("叠构铜层数与 BOARD_LAYERS 不一致，拒绝自动填铜厚")
                        thickness = length_mm(rows[0]["BOARD_BOARD_THICKNESS"], unit)
                        if thickness is not None and thickness > 0:
                            payload["updates"]["board_thickness"] = thickness
                except (OSError, ValueError) as exc:
                    payload["warnings"].append("板厚未获取：" + str(exc))
            except (OSError, ValueError, TimeoutError) as exc:
                payload["warnings"].append(str(exc))
            if payload.get("unit"):
                from src.adapters.extracta_objects import parse_objects
                views = work / "object_views.txt"
                views.write_text("".join(view + "\n" + "\n".join(fields) + "\nEND\n"
                                        for view, fields in OBJECT_VIEWS.values()), encoding="ascii")
                object_paths = [work / (name + ".out") for name in OBJECT_VIEWS]
                try:
                    code, log = _run([tool, str(copy), str(views), *(str(p) for p in object_paths)], work, timeout, env)
                    (folder / "extracta_objects.log").write_text(log, encoding="utf-8")
                    if code:
                        raise ValueError(f"extracta 对象视图返回 {code}，对象未获取")
                    for name, path in zip(OBJECT_VIEWS, object_paths):
                        if path.is_file():
                            shutil.copy2(path, folder / path.name)
                    payload["objects"] = parse_objects(folder, payload["unit"], payload["components"], payload.get("updates", {}).get("layers", []))
                    payload["warnings"].extend(payload["objects"]["warnings"])
                except (OSError, ValueError, TimeoutError) as exc:
                    payload["warnings"].append("对象提取未完成：" + str(exc))
            if content_sha256(copy) != digest:
                raise ValueError("extracta 期间 BRD 副本变化，拒绝提取结果")
    if content_sha256(board) != digest:
        raise ValueError("extracta 期间源 BRD 变化，拒绝提取结果")
    if payload["warnings"] and payload["status"] == "extracted":
        payload["status"] = "partial"
    # Feed the existing coordinate readers with known units and true origins.
    if payload.get("unit"):
        with (folder / "evan_components.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["UNITS", payload["unit"]])
            writer.writerow(["REFDES", "X_UU", "Y_UU", "ROTATION", "SIDE"])
            for row in payload["components"]:
                writer.writerow([row["refdes"], row["x_mm"] / _UNIT_MM[payload["unit"]], row["y_mm"] / _UNIT_MM[payload["unit"]],
                                 "" if row["rotation"] is None else row["rotation"], row["side"]])
        from src.adapters.extracta_objects import write_object_csvs
        write_object_csvs(folder, payload)
    (folder / "extraction.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",",":"), allow_nan=False), encoding="utf-8")
    return payload

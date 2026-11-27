"""BRD-bound SMT JSON, placements, BOM, bounds and Gerber readers."""
import json
from pathlib import Path

from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union

from src.domain.smt_objects import _side
from src.infrastructure.resources import read_bounded
from src.infrastructure.snapshots import fingerprint

CHUNK_FIELDS={"components","pads","via_pads","native_traces","native_holes","native_pins","mask_openings","paste_openings"}


def object_chunks(data, source):
    files=data.get("object_files",[])
    if not isinstance(files,list) or len(files)>128:raise ValueError("对象分块文件超限")
    paths=[]
    for row in files:
        if not isinstance(row,dict) or row.get("field") not in CHUNK_FIELDS or not isinstance(row.get("path"),str):raise ValueError("对象分块合同无效")
        path=Path(row["path"])
        if path.is_absolute() or len(path.parts)!=1 or path.name in (".",".."):raise ValueError("对象分块仅允许同目录文件")
        paths.append((row,Path(source).parent/path))
    return paths


def sha256_file(path):
    return fingerprint(path)["sha256"]


def _evidence_paths(evidence_path, brd_path, gerber_dir):
    evidence = Path(evidence_path).resolve()
    data = json.loads(read_bounded(evidence, 16 * 1024 * 1024).decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("SMT 对象资料必须为 JSON 对象")
    paths = [evidence]
    paths.extend(path.resolve() for _,path in object_chunks(data,evidence))
    if brd_path:
        paths.append(Path(brd_path).resolve())
    for key in ("components_file", "component_bounds_file", "bom_file"):
        if data.get(key):
            candidate = Path(data[key])
            paths.append((candidate if candidate.is_absolute() else evidence.parent / candidate).resolve())
    gerbers = []
    if gerber_dir:
        from src.gerber_export import gerber_files
        gerbers = [str(path.resolve()) for path in gerber_files(gerber_dir)]
        paths.extend(Path(path) for path in gerbers)
    return sorted(set(paths), key=str), gerbers


def source_signature(evidence_path, brd_path, gerber_dir=""):
    paths, _ = _evidence_paths(evidence_path, brd_path, gerber_dir)
    return [[str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns] for path in paths]


def evidence_fingerprints(evidence_path, brd_path, gerber_dir=""):
    paths, gerbers = _evidence_paths(evidence_path, brd_path, gerber_dir)
    return {"files": [{"path": str(path), "sha256": sha256_file(path)} for path in paths],
            "evidence_path": str(Path(evidence_path).resolve()),
            "brd_path": str(Path(brd_path).resolve()) if brd_path else "",
            "gerber_dir": str(Path(gerber_dir).resolve()) if gerber_dir else "",
            "gerber_files": sorted(gerbers)}


def verify_evidence_fingerprints(snapshot):
    if not isinstance(snapshot, dict) or not snapshot.get("evidence_path"):
        raise ValueError("SMT 对象资料缺少可验证的内容快照，请重新分析")
    current = evidence_fingerprints(snapshot["evidence_path"], snapshot.get("brd_path", ""), snapshot.get("gerber_dir", ""))
    if current != snapshot:
        raise ValueError("SMT 对象或制造资料内容已变化，请重新分析")


def load_evidence(path, brd_path):
    path = Path(path)
    data = json.loads(read_bounded(path, 16 * 1024 * 1024).decode("utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("SMT 对象资料需要 schema_version: 1")
    if not brd_path or not Path(brd_path).is_file():
        raise ValueError("对象检查必须选择真实 BRD 文件")
    if data.get("brd_sha256", "").lower() != sha256_file(brd_path):
        raise ValueError("对象资料的 BRD SHA-256 与当前 BRD 不一致，已停止几何判定")
    if data.get("coordinate_system") != "brd" or data.get("unit") != "mm":
        raise ValueError("对象资料必须明确使用 BRD 原点、轴向和 mm 坐标")
    if data.get("coordinate_alignment_confirmed") is not True:
        raise ValueError("请先确认全部对象资料与当前 BRD 的坐标原点、旋转和镜像一致")
    if data.get("linked_source_fingerprints"):
        from src.infrastructure.snapshots import verify_sources
        verify_sources(data["linked_source_fingerprints"])
    data.update(_source=str(path.resolve()), _brd_path=str(Path(brd_path).resolve()))
    chunks=object_chunks(data,path)
    if any(data.get(field) for field in {chunk["field"] for chunk,_ in chunks}):raise ValueError("对象同字段不能同时内嵌和分块，避免重复")
    total=path.stat().st_size
    for chunk,child in chunks:
        if sha256_file(child)!=chunk.get("sha256"):raise ValueError("对象分块哈希已变化")
        total+=child.stat().st_size
        if total>64*1024*1024:raise ValueError("对象分块总量超过64MiB安全预算")
        rows=json.loads(read_bounded(child,16*1024*1024).decode("utf-8"))
        if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise ValueError("对象分块必须为对象列表")
        field=chunk["field"]
        data.setdefault(field,[]).extend(rows)
        if len(data[field])>200000:raise ValueError("对象分块记录数量超限")
    return data


def _verified_file(data, key, source):
    path = Path(data[key])
    if not path.is_absolute():
        path = Path(source).parent / path
    if not path.is_file() or data.get(key + "_sha256", "").lower() != sha256_file(path):
        raise ValueError(f"{key}: 文件不存在或哈希不一致")
    return path


def load_exported_bounds(path):
    from src.silk_position import parse_geometry
    groups, out = {}, {}
    for row in parse_geometry(path):
        side = "BOTTOM" if "BOTTOM" in row["layer"].upper() else "TOP" if "TOP" in row["layer"].upper() else ""
        if not row.get("refdes") or not side or row.get("type", "").lower() != "line":
            continue
        groups.setdefault((row["refdes"].upper(), side), []).append(LineString([
            (row["x1_mm"], row["y1_mm"]), (row["x2_mm"], row["y2_mm"])]))
    for key, lines in groups.items():
        polygons = list(polygonize(unary_union(lines)))
        if len(polygons) == 1 and polygons[0].is_valid:
            out[key] = polygons[0]
    return out


def _read_gerbers(folder, data, source):
    from src.gerber_analysis import identify_layer, read_film
    from src.gerber_export import gerber_files
    films, errors = {}, []
    if not folder:
        return films, errors
    for path in gerber_files(folder):
        try:
            with path.open(encoding="ascii", errors="replace") as stream:
                role = identify_layer(path, stream.read(20000))
            if role == "unknown":
                continue
            if (data.get("gerber_sha256") or {}).get(path.name, "").lower() != sha256_file(path):
                errors.append(f"{path.name}: 未确认文件哈希或内容已变化")
                continue
            film = read_film(path)
            if not film["brd_alignment"] and data.get("gerber_alignment_confirmed") is not True:
                errors.append(f"{path.name}: 未确认 Gerber 与 BRD 坐标一致")
                continue
            films.setdefault(role, []).append(film)
        except Exception as exc:  # noqa: BLE001 reader errors become explicit gaps
            errors.append(f"{path.name}: {exc}")
    for role in list(films):
        if len(films[role]) != 1:
            errors.append(f"{role}: 存在多个同类图层")
            del films[role]
        else:
            films[role] = films[role][0]
    return films, errors


def prepare_objects(data, gerber_dir=""):
    data = dict(data)
    source = data.get("_source", "SMT 对象资料")
    errors, bounds = [], {}
    if data.get("component_bounds_file"):
        bounds = load_exported_bounds(_verified_file(data, "component_bounds_file", source))
    rows = list(data.get("components", []))
    if data.get("components_file"):
        from src.pick import parse_placement_txt, parse_skill_csv
        path = _verified_file(data, "components_file", source)
        parsed = parse_skill_csv(str(path)) if path.suffix.lower() == ".csv" else parse_placement_txt(str(path))
        if not parsed.get("ok") or parsed.get("kind") != "components" or parsed.get("unit_from_file") is False:
            raise ValueError("器件坐标文件未解析或未明确单位")
        overrides = {str(o.get("refdes", "")).upper(): o for o in rows}
        rows = []
        for row in parsed["items"]:
            ref = str(row.get("refdes", ""))
            override = overrides.pop(ref.upper(), {})
            if override.get("side") and _side(override) != _side(row):
                raise ValueError(f"{ref}: 器件面别与坐标文件冲突")
            rows.append(dict(row, **{k: v for k, v in override.items() if k not in ("refdes", "side")}))
        if overrides:
            raise ValueError("对象资料包含坐标文件中不存在的位号: " + ", ".join(overrides))
    if data.get("bom_file"):
        from src.design_inputs.bom import read_bom
        bom = read_bom(str(_verified_file(data, "bom_file", source)), strict=True)
        refs = {r["refdes"].upper() for r in bom["rows"]}
        errors.extend(f"{row.get('refdes')}: BOM 中未找到位号；类别须人工确认" for row in rows
                      if row.get("refdes", "").upper() not in refs)
    errors.extend(data.get("source_association", {}).get("errors", []))
    data["components"] = rows
    brd_components = None
    if data.get("complete", {}).get("components") is True and Path(str(data.get("_brd_path", ""))).is_file():
        from src.brd_components import extract_components
        from src.brd_design import read_brd_objects
        try:
            objects, _ = read_brd_objects(data["_brd_path"])
            brd_components = extract_components(objects)
        except (OSError, ValueError) as exc:
            brd_components = {"ok": False}
            errors.append(f"BRD 位号读取失败：{exc}")
        if data.get("association_contract") == "exact-refdes-v1" and data.get("expected_refdes"):
            expected = {ref.upper() for ref in data["expected_refdes"]}
            # The extracta identity list is source-hash bound; disagreement is
            # explicit rather than assuming offline Part.xml was complete.
            if brd_components and brd_components.get("ok") and {ref.upper() for ref in brd_components.get("refdes", [])} != expected:
                errors.append("离线位号清单与原生 extracta 清单不一致，器件完整性待复核")
                data["complete"] = dict(data.get("complete", {}), components=False)
    films, film_errors = _read_gerbers(gerber_dir, data, source)
    errors.extend(film_errors)
    return data, {"bounds": bounds, "films": films, "input_errors": errors, "brd_components": brd_components}

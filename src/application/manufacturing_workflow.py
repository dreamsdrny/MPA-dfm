"""BRD -> native artifacts -> validated separate analyses -> one authoritative result."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

from src.manufacturing_export import export_brd_manufacturing, verify_export_manifest, native_drill_metadata
from src.dfm_engine import analyze_design, capability_fingerprint
from src.gerber_analysis import analyze_gerbers, attach_gerber_result, read_film, write_preview
from src.gerber_export import gerber_files
from src.odb_dfm import analyze_odb, attach_odb_result
from src.pcb_locations import attach_locations
from src.verdict import finalize_verdict


def _stage_coverage(manifest):
    return [dict(rule="native_export_"+kind, status="measured" if row["status"] == "exported" else "incomplete",
                 note=row.get("error") or row.get("note") or row["status"],
                 required_data="" if row["status"] == "exported" else
                 "安装/设置对应原生导出器并核实许可、板内输出定义；重新执行", source=manifest["manifest_path"])
            for kind, row in manifest["stages"].items()]


def merge_geometry_metadata(result, first, second):
    """attach_gerber_result handles issues; preserve BOTH sources' coverage/statistics."""
    result["geometry_coverage"] = first.get("geometry_coverage", []) + second.get("geometry_coverage", [])
    result["geometry_requirements"] = first.get("geometry_requirements", []) + [dict(r) for r in second.get("geometry_coverage", []) if r["status"] != "measured"]
    result["gerber_errors"] = first.get("errors", []) + second.get("errors", [])
    result["gerber_layers"] = first.get("parsed_layers", []) + [dict(path=r["source"], role="ODB++/"+r["role"], objects=r["objects"]) for r in second.get("parsed_layers", [])]
    result["gerber_folder"] = first.get("folder", "")
    result["gerber_copper_layer_checks"] = first.get("copper_layer_checks", [])
    result["gerber_mask_bridge_checks"] = first.get("mask_bridge_checks", [])
    result["copper_density"] = first.get("copper_density", [])
    result["copper_balance"] = first.get("copper_balance", [])
    result["geometry_scan_stats"] = first.get("scan_stats", []) + second.get("scan_stats", [])
    result["geometry_elapsed_seconds"] = first.get("elapsed_seconds", 0) + second.get("elapsed_seconds", 0)
    result["geometry_complete"] = False
    result["geometry_review_count"] = sum(it.get("status") == "warn_limit" and it.get("source") in ("Gerber", "ODB++") for it in result["items"])
    return result


def prepare_native_nc(manifest, aligned):
    """Combine both native hole/route sources without depending on drill success."""
    import shutil
    from src.pcb_geometry_data import file_sha256
    stages = manifest["stages"]
    if not any(stages[k]["status"] == "exported" for k in ("drill", "route")):
        return {}
    folder = Path(manifest["directory"])/"nc_analysis"
    folder.mkdir(exist_ok=True)
    metadata = native_drill_metadata(manifest, aligned)
    for kind in ("drill", "route"):
        stage = stages[kind]
        if stage["status"] != "exported":
            continue
        shutil.copy2(Path(stage["directory"])/"nc_param.txt", folder/"nc_param.txt")
        for file in stage["files"]:
            dest = folder/Path(file).name
            if dest.is_file():
                raise ValueError("NC Drill/Route 导出名称冲突，不能覆盖：" + dest.name)
            shutil.copy2(file, dest)
            if kind == "route":
                metadata[dest.name] = dict(number_format=[3, 5], native_cadence=True, alignment_confirmed=bool(aligned),
                                          brd_alignment=bool(aligned), process=None, plated=None,
                                          start_layer=None, end_layer=None)
            manifest["artifacts"].append(dict(path=str(dest), sha256=file_sha256(dest)))
    manifest["artifacts"].append(dict(path=str(folder/"nc_param.txt"), sha256=file_sha256(folder/"nc_param.txt")))
    Path(manifest["manifest_path"]).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return dict(drill_directory=str(folder), drills=metadata)


def analyze_brd_manufacturing(brd_path, design, factory="hongban", board_type="rigid", capability_level="standard",
                              editor_path="", odb_exporter="", out_dir="", geometry_options=None,
                              design_unit="", coordinate_dir="", timeout=180, progress=None):
    manifest = export_brd_manufacturing(brd_path, out_dir or str(Path(brd_path).resolve().parent/"Manufacturing_DFM"),
                                       editor_path, odb_exporter, timeout, progress)
    verify_export_manifest(manifest)
    rule_hash = capability_fingerprint(factory)
    options = deepcopy(geometry_options or {})
    # Native mode never falls back to a previous/external NC directory on failure.
    options.pop("drill_directory", None)
    options.pop("drills", None)
    result = analyze_design(design, board_type, capability_level, factory)
    result.update(input_brd=str(Path(brd_path).resolve()), analyzed_at=datetime.now().isoformat(timespec="seconds"),
                  manufacturing_export=manifest, geometry_input_source="当前 BRD 原生一键制造导出",
                  geometry_complete=False)
    result = attach_locations(result, brd_path, design_unit=design_unit, extra_dirs=[coordinate_dir] if coordinate_dir else [])
    coverage = _stage_coverage(manifest)
    stages = manifest["stages"]
    geometry, objects = None, None
    run_dir = Path(manifest["directory"])
    nc_inputs = None
    # Independently run available sources. Never silently substitute parameter-only success.
    if stages["gerber"]["status"] == "exported":
        folder = stages["gerber"]["directory"]
        try:
            if progress:
                progress("gerber_analysis")
            films = []
            for path in gerber_files(folder):
                try:
                    films.append(read_film(path))
                except (ValueError, OSError, SyntaxError, ArithmeticError) as exc:
                    # A failed film must be recorded, not abort valid layers
                    # and the independent NC/ODB checks during preflight.
                    coverage.append(dict(rule="native_gerber_parse", layer=path.name,
                                         status="incomplete", note=f"{path.name}: {exc}",
                                         required_data="修复此底片的图形/格式并重新导出分析"))
            recognized = [f for f in films if f["role"] != "unknown"]
            aligned = bool(recognized) and all(f["brd_alignment"] for f in recognized)
            nc_inputs = prepare_native_nc(manifest, aligned)
            options.update(nc_inputs)
            options["data_source"] = manifest["manifest_path"]
            candidate = analyze_gerbers(folder, factory, board_type, capability_level, copper_um=design.get("copper_um", 35),
                                       stackup=design.get("stackup"), layer_count=design.get("layer_count"), geometry_options=options)
            result = attach_gerber_result(result, candidate, design_unit)
            geometry = candidate
            result["gerber_preview"] = write_preview(geometry, run_dir/"DFM_Gerber_preview.html")
            observed = {f["role"] for f in films}
            for role in ("mask_top", "mask_bottom", "paste_top", "paste_bottom", "silkscreen_top", "silkscreen_bottom", "outline"):
                if role not in observed:
                    coverage.append(dict(rule="native_layer_inventory", layer=role, status="missing",
                                         note="导出产物缺此角色；不证明该面无对象", required_data="核实 Artwork film 定义或明确不适用"))
        except (ValueError, OSError, SyntaxError) as exc:
            coverage.append(dict(rule="gerber_analysis", status="incomplete", note=str(exc), required_data="修复本次底片/元数据并重新分析"))
    if stages["odb"]["status"] == "exported":
        try:
            if progress:
                progress("odb_analysis")
            objects = analyze_odb(stages["odb"]["path"], object_rules=options.get("object_rules"),
                                  preview_path=run_dir/"DFM_ODB_preview.html")
            if design.get("layer_count") and objects["copper_layer_count"] != design["layer_count"]:
                raise ValueError("ODB++ 铜层数与当前 BRD 不一致，拒绝对象结果")
            gerber_preview = result.get("gerber_preview")
            result = attach_odb_result(result, objects, design_unit)
            result["odb_preview"] = objects.get("preview", "")
            if gerber_preview:
                result["gerber_preview"] = gerber_preview
            if geometry:
                merge_geometry_metadata(result, geometry, objects)
        except (ValueError, OSError, SyntaxError) as exc:
            objects = None
            coverage.append(dict(rule="odb_analysis", status="incomplete", note=str(exc), required_data="修复原生 ODB++ 输出再分析"))
    if geometry is None and any(stages[k]["status"] == "exported" for k in ("drill", "route")):
        # Hole dimensions/overlap can still be measured if Artwork is unavailable.
        # No cross-file copper/edge comparison is allowed without its films.
        from shapely.geometry import GeometryCollection
        from src.layout_checks import scan_layout_checks
        from src.layout_geometry import ScanLimits
        from src.dfm_engine import load_capability
        from src.dfm_stackup import copper_layers
        if nc_inputs is None:
            nc_inputs = prepare_native_nc(manifest, False)
        drill_options = dict(options, **nc_inputs)
        extra = scan_layout_checks(manifest["directory"], [], set(), [], [], GeometryCollection(),
                                   load_capability(board_type, capability_level, factory),
                                   copper_layers(design, design.get("copper_um", 35)), drill_options,
                                   ScanLimits(**options.get("scan_limits", {})))
        geometry = dict(checks=extra["checks"], errors=extra["errors"], source_label="NC Drill",
                        parsed_layers=extra["drill_layers"], folder=nc_inputs["drill_directory"],
                        copper_um=design.get("copper_um", 35), mask_bridge_checks=[], copper_layer_checks=[],
                        geometry_coverage=extra["coverage"], geometry_complete=False,
                        geometry_requirements=[dict(r) for r in extra["coverage"]], scan_stats=extra["scan_stats"])
        previous_preview = result.get("gerber_preview")
        result = attach_gerber_result(result, geometry, design_unit)
        if previous_preview:
            result["gerber_preview"] = previous_preview
        if objects:
            merge_geometry_metadata(result, geometry, objects)
    if geometry is None and objects is None:
        raise ValueError("本次没有可执行的制造几何资料，未返回仅参数分析结果。导出日志：" + manifest["manifest_path"])
    result.setdefault("geometry_coverage", []).extend(coverage)
    result["geometry_requirements"] = [dict(r) for r in result["geometry_coverage"] if r["status"] != "measured"]
    for row in result["geometry_coverage"]:
        if row["status"] in ("incomplete", "missing"):
            result.setdefault("release_blockers", []).append(f"{row['rule']}: {row.get('note', '')}")
    result["geometry_coverage_summary"] = {s: sum(r["status"] == s for r in result["geometry_coverage"]) for s in ("measured", "partial", "missing", "incomplete")}
    result["manufacturing_export"] = manifest
    result["geometry_data_source"] = manifest["manifest_path"]
    result["geometry_input_source"] = "当前 BRD 原生 Gerber / NC Drill / NC Route / ODB++ 编排"
    result["manufacturing_layer_inventory"] = result.get("gerber_layers", [])
    verify_export_manifest(manifest)
    if capability_fingerprint(factory) != rule_hash:
        raise ValueError("制造分析期间板厂能力规则变化，请重新分析")
    finalize_verdict(result, geometry=dict(geometry_complete=False))
    # Persist an audit snapshot (no live GEOS geometries) separately from raw native files.
    (run_dir/"analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False, default=str), encoding="utf-8")
    return result

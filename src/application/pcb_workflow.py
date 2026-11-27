"""PCB 应用服务：输入校验 -> 原生/外部资料分析 -> 版本校验 -> 唯一结论。

GUI 只收集输入和渲染输出。所有分支都检查源 BRD 与能力文件版本，
制造资料失败不退回仅参数结果。调用方输入通过深拷贝隔离。
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp

from src.application.contracts import PCBAnalysisRequest
from src.brd_input import content_sha256, require_brd_signature
from src.dfm_engine import analyze_design, capability_fingerprint
from src.verdict import finalize_verdict


__all__ = ["PCBAnalysisRequest", "run_pcb_analysis", "verify_analysis_sources"]


def run_pcb_analysis(request: PCBAnalysisRequest, progress=None):
    from src.infrastructure.snapshots import verify_sources
    verify_sources(request.stackup_sources)
    design = deepcopy(request.design)
    expected = design.get("_brd_signature")
    require_brd_signature(request.brd_path, expected)
    brd_digest = content_sha256(request.brd_path)
    if design.get("_brd_sha256") and design["_brd_sha256"] != brd_digest:
        raise ValueError("BRD 内容已变化，请重新导入")
    native = request.native_data
    if request.extract_native and native is None:
        from src.application.brd_extraction import prepare_brd_data
        if progress:
            progress("extracta")
        # Synthetic/offline callers can explicitly disable native enrichment.
        native = prepare_brd_data(request.brd_path, request.output_dir or mkdtemp(prefix="evan-brd-data-"),
                                  request.editor_path, min(request.timeout, 90))
    if native is not None:
        from src.application.brd_extraction import enrich_design
        if native.get("source_brd_sha256") != brd_digest:
            raise ValueError("坐标提取来源与当前 BRD 不一致，请重新分析")
        design = enrich_design(design, native)
        # Only this board's freshly generated coordinate source is automatic.
        request = replace(request, design=design, design_unit=native.get("unit") or request.design_unit,
                          coordinate_dir=request.coordinate_dir or native["directory"])
    rule_hash = capability_fingerprint(request.factory)
    from src.design_inputs.coordination import source_fingerprint
    from src.pcb_geometry_data import load_geometry_data
    geometry_source_before = (source_fingerprint(request.geometry_data)
                              if request.geometry_data else None)
    geometry_options = (load_geometry_data(request.geometry_data, request.brd_path)
                         if request.geometry_data else None)
    if native and native.get("objects"):
        from src.adapters.extracta_objects import pcb_geometry_options
        generated_options = pcb_geometry_options(native, design.get("layer_count"), request.auto_manufacturing)
        generated_options.update(geometry_options or {})
        geometry_options = generated_options
    sources = _input_fingerprints(request, geometry_options)
    if geometry_source_before and next((item for item in sources
            if item["path"] == geometry_source_before["path"]), None) != geometry_source_before:
        raise ValueError("几何补充资料在读取期间变化，请重新分析")
    options = {"board_type": request.board_type, "capability_level": request.capability_level,
               "factory": request.factory}
    if request.auto_manufacturing:
        from src.manufacturing_pipeline import analyze_brd_manufacturing
        result = analyze_brd_manufacturing(
            request.brd_path, design, **options, editor_path=request.editor_path,
            odb_exporter=request.odb_exporter, geometry_options=geometry_options,
            design_unit=request.design_unit, coordinate_dir=request.coordinate_dir,
            out_dir=request.output_dir, timeout=request.timeout, progress=progress)
    else:
        result = _analyze_external(request, design, options, geometry_options)

    # 原生、外部 ODB++、外部 Gerber 都必须通过相同的末端检查。
    require_brd_signature(request.brd_path, expected)
    if content_sha256(request.brd_path) != brd_digest:
        raise ValueError("BRD 内容在分析期间变化，请重新导入")
    if _input_fingerprints(request, geometry_options) != sources:
        raise ValueError("制造或补充资料在分析期间变化，请重新分析")
    if capability_fingerprint(request.factory) != rule_hash or result.get("rule_sha256") != rule_hash:
        raise ValueError("PCB 能力 YAML 在分析期间发生变化，请重新分析")
    verify_sources(request.stackup_sources)
    result["input_brd"] = request.brd_path
    result["input_brd_sha256"] = brd_digest
    result["input_fingerprints"] = sources + [deepcopy(source) for source in request.stackup_sources
                                            if source["path"] not in {item["path"] for item in sources}]
    result["analyzed_at"] = datetime.now().isoformat(timespec="seconds")
    result["analysis_mode"] = ("native" if request.auto_manufacturing else
                                "external_odb" if request.odb_path else "external_gerber")
    if native is not None:
        from src.pcb_locations import component_coordinates
        result["native_extraction"] = native
        result["analyzed_design"] = deepcopy(design)
        if native.get("unit") and request.coordinate_dir == native["directory"]:
            result["component_coordinates"] = component_coordinates({"components": native["components"]}, native.get("unit", ""))
        for warning in native.get("warnings", []):
            result.setdefault("release_blockers", []).append("BRD 后台提取：" + warning)
            result.setdefault("items", []).append(dict(category="数据获取", item="BRD 数据未获取", status="info",
                design=None, capability=None, unit="", field="", source="extracta", message=warning))
        if any(row.get("thickness_basis") == "design_stackup" for row in design.get("stackup", [])):
            result.setdefault("release_blockers", []).append("铜厚使用 BRD 叠构设计值，需与板厂成品铜厚核对；不等于实测制造放行")
    from src.domain.inspection_state import reconcile_inspections
    reconcile_inspections(result)
    finalize_verdict(result, geometry={"geometry_complete": result.get("geometry_complete", False)})
    if result.get("manufacturing_export", {}).get("directory"):
        import json
        path = Path(result["manufacturing_export"]["directory"]) / "analysis.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False, default=str), encoding="utf-8")
    return result


def _input_fingerprints(request, geometry_options=None):
    from src.design_inputs.coordination import source_fingerprint
    paths = [request.geometry_data, request.coordinate_dir]
    if not request.auto_manufacturing:
        paths += [request.odb_path or (request.gerber_dir if not request.auto_gerber else "")]
        # Explicit JSON and folder-local dfm_geometry.json can point at an NC
        # directory outside the Gerber folder. Both contribute to the result.
        if not geometry_options and request.geometry_data:
            from src.pcb_geometry_data import load_geometry_data
            geometry_options = load_geometry_data(request.geometry_data, request.brd_path)
        if not request.odb_path:
            folder = request.gerber_dir if not request.auto_gerber else ""
            drill = _drill_source(folder, geometry_options)
            if drill:
                paths.append(drill)
    snapshots = []
    seen = set()
    for path in paths:
        if path:
            canonical = str(Path(path).resolve())
            if canonical in seen:
                continue
            seen.add(canonical)
            if not Path(canonical).exists():
                snapshots.append({"path": canonical, "missing": True})
            else:
                snapshots.append(source_fingerprint(canonical))
    return snapshots


def _drill_source(folder, geometry_options):
    """Resolve the same effective drill directory as analyze_gerbers."""
    import json

    from src.resource_limits import read_bounded
    options = {}
    manifest = Path(folder) / "dfm_geometry.json" if folder else None
    if manifest and manifest.is_file():
        try:
            local = json.loads(read_bounded(manifest, 16 * 1024 * 1024).decode("utf-8-sig"))
        except (ValueError, OSError) as exc:
            raise ValueError("无法读取 Gerber 几何配置，不能确认钻孔资料来源") from exc
        if not isinstance(local, dict):
            raise ValueError("dfm_geometry.json 必须是对象")
        options.update(local)
    options.update(geometry_options or {})
    if not options.get("drill_directory"):
        return ""
    drill = Path(options["drill_directory"])
    if not drill.is_absolute():
        if not folder:
            raise ValueError("外部钻孔目录为相对路径，但没有固定 Gerber 输入目录，请改用绝对路径")
        drill = Path(folder) / drill
    return str(drill.resolve())


def verify_analysis_sources(result):
    if result.get("analysis_mode") == "gerber_only":
        from src.infrastructure.snapshots import verify_sources
        verify_sources(result.get("input_fingerprints", []))
        if capability_fingerprint(result["factory"]) != result.get("rule_sha256"):
            raise ValueError("PCB 能力 YAML 已变化，请重新分析")
        return
    from src.design_inputs.coordination import source_fingerprint
    if result.get("input_brd_sha256") and content_sha256(result["input_brd"]) != result["input_brd_sha256"]:
        raise ValueError("BRD 内容已变化，请重新分析")
    for source in result.get("input_fingerprints", []):
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            raise ValueError("制造资料版本记录无效，请重新分析")  # noqa: TRY004 失效快照
        current = ({"path": source["path"], "missing": True} if not Path(source["path"]).exists()
                   else source_fingerprint(source["path"]))
        if current != source:
            raise ValueError("制造或补充资料已变化，请重新分析：" + source["path"])
    # A native Artwork export in the non-native branch uses its own output
    # directory, which is unavailable when the request snapshot is created.
    # Hash it around geometry analysis and check again before report export.
    for source in result.get("analysis_fingerprints", []):
        if source_fingerprint(source["path"]) != source:
            raise ValueError("本次导出的制造资料已变化，请重新分析：" + source["path"])
    if (result.get("rule_sha256") and result.get("factory")
            and capability_fingerprint(result["factory"]) != result["rule_sha256"]):
        raise ValueError("PCB 能力 YAML 已变化，请重新分析")


def _analyze_external(request, design, options, geometry_options):
    from src.pcb_geometry_data import ensure_geometry_folder
    from src.pcb_locations import attach_locations
    bundle = (None if request.odb_path else
              ensure_geometry_folder(request.brd_path, request.gerber_dir,
                                     request.editor_path, force_export=request.auto_gerber))
    result = analyze_design(design, **options)
    result = attach_locations(result, request.brd_path, design_unit=request.design_unit,
                              extra_dirs=[request.coordinate_dir] if request.coordinate_dir else [])
    if request.odb_path:
        from src.odb_dfm import analyze_odb, attach_odb_result
        preview_dir = Path(mkdtemp(prefix="evan-odb-dfm-"))
        objects = analyze_odb(request.odb_path, step=request.odb_step,
                              object_rules=(geometry_options or {}).get("object_rules"),
                              preview_path=preview_dir / "ODB_DFM_preview.html")
        if design.get("layer_count") and objects["copper_layer_count"] != design["layer_count"]:
            raise ValueError("ODB++ 铜层数与当前 BRD 不一致，请核实设计版本")
        result = attach_odb_result(result, objects, request.design_unit)
        result.setdefault("release_blockers", []).append(
            "外部 ODB++ 与 BRD 的设计版本/坐标一致性未自动证明，需核实")
        return result

    from src.gerber_analysis import analyze_gerbers, attach_gerber_result, write_preview
    folder = bundle["directory"]
    from src.design_inputs.coordination import source_fingerprint
    exported_before = source_fingerprint(folder)
    drill_path = _drill_source(folder, geometry_options)
    drill_before = source_fingerprint(drill_path) if drill_path else None
    result["gerber_export_warnings"] = bundle["warnings"]
    result["geometry_input_source"] = bundle["source"]
    geometry = analyze_gerbers(folder, request.factory, request.board_type, request.capability_level,
                               copper_um=design.get("copper_um", 35), stackup=design.get("stackup"),
                               layer_count=design.get("layer_count"), geometry_options=geometry_options)
    if (source_fingerprint(folder) != exported_before
            or (drill_before and source_fingerprint(drill_path) != drill_before)):
        raise ValueError("Gerber 在分析期间变化，请重新分析")
    result = attach_gerber_result(result, geometry, request.design_unit)
    result["analysis_fingerprints"] = [exported_before] + ([drill_before] if drill_before else [])
    preview_dir = Path(mkdtemp(prefix="evan-gerber-preview-"))
    result["gerber_preview"] = write_preview(geometry, str(preview_dir / "DFM_Gerber_preview.html"))
    return result

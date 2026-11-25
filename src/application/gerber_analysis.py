"""Gerber-only import and analysis. No BRD, Cadence or guessed board semantics required."""
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp

from src.adapters.gerber_input import inspect_gerbers, staged_gerbers
from src.application.common import attach_summary
from src.infrastructure.resources import read_bounded
from src.infrastructure.snapshots import capture_sources, verify_sources


@dataclass(frozen=True)
class GerberAnalysisRequest:
    source_path: str
    imported_sources: tuple = ()
    design: dict = field(default_factory=dict)
    factory: str = "hongban"
    board_type: str = "rigid"
    capability_level: str = "standard"
    geometry_data: str = ""
    stackup_sources: tuple = ()


def import_gerber(source_path):
    if not str(source_path or "").strip():
        raise ValueError("请选择 Gerber 文件、目录或压缩包，路径不能为空")
    sources = capture_sources([source_path])
    with staged_gerbers(source_path) as folder:
        info = inspect_gerbers(folder)
    verify_sources(sources)
    return dict(info, source_path=sources[0]["path"], input_fingerprints=sources)


def run_gerber_analysis(request):
    from src.adapters.gerber_analysis import (
        analyze_gerbers,
        attach_gerber_result,
        write_preview,
    )
    from src.adapters.pcb_configuration import capability_fingerprint
    from src.application.pcb_workflow import _drill_source
    from src.dfm_engine import analyze_design

    request = deepcopy(request)
    if not request.imported_sources or request.imported_sources[0]["path"] != str(Path(request.source_path).resolve()):
        raise ValueError("请先成功导入当前 Gerber，再执行分析")
    verify_sources(request.imported_sources)
    verify_sources(request.stackup_sources)
    sources = capture_sources([request.source_path, request.geometry_data])
    sources += list(request.stackup_sources)
    rule_hash = capability_fingerprint(request.factory)
    options = {}
    if request.geometry_data:
        config = Path(request.geometry_data).resolve()
        doc = json.loads(read_bounded(config, 16 * 1024 * 1024).decode("utf-8-sig"))
        if not isinstance(doc, dict):
            raise ValueError("Gerber 几何配置必须是 JSON 对象")
        if doc.get("brd_sha256"):
            raise ValueError("此 JSON 绑定 BRD，不能用于独立 Gerber 分析，请使用 Gerber 几何配置")
        options = doc.get("geometry_options", doc)
        if not isinstance(options, dict):
            raise ValueError("geometry_options 必须为对象")
        options = deepcopy(options)
        if options.get("drill_directory"):
            drill = Path(options["drill_directory"])
            options["drill_directory"] = str((drill if drill.is_absolute() else config.parent / drill).resolve())
        options["data_source"] = str(config)
    design = deepcopy(request.design)
    # A BRD-free input never inherits measurements or command alignment from BRD.
    if any(key.startswith("_brd") for key in design):
        raise ValueError("独立 Gerber 分析不能带入 BRD 测量快照")
    design.setdefault("board_name", Path(request.source_path).stem)
    design["_data_origin"] = "Gerber + 人工确认参数"
    with staged_gerbers(request.source_path) as folder:
        drill = _drill_source(folder, options)
        if drill and not Path(drill).is_relative_to(folder):
            sources += capture_sources([drill])
        verify_sources(sources)
        base = analyze_design(design, request.board_type, request.capability_level, request.factory)
        geometry = analyze_gerbers(folder, request.factory, request.board_type, request.capability_level,
                                   copper_um=design.get("copper_um", 35), stackup=design.get("stackup"),
                                   layer_count=design.get("layer_count"), geometry_options=options)
        # Replace ephemeral paths with durable source/member labels in reports/previews.
        def source_label(value):
            path = Path(value)
            if path.is_relative_to(folder):
                return f"{Path(request.source_path).resolve()}!/{path.relative_to(folder).as_posix()}"
            return value
        for film in geometry["films"]:
            film["path"] = source_label(film["path"])
            film["brd_alignment"] = False
        for rows in (geometry["checks"], geometry["parsed_layers"], geometry["copper_layer_checks"], geometry["mask_bridge_checks"]):
            for row in rows:
                for key in ("source", "path"):
                    if row.get(key):
                        row[key] = source_label(row[key])
                row["brd_alignment"] = False
        geometry["folder"] = str(Path(request.source_path).resolve())
        if geometry.get("geometry_data_source"):
            geometry["geometry_data_source"] = source_label(geometry["geometry_data_source"])
        result = attach_gerber_result(base, geometry, "mm")
        preview_dir = Path(mkdtemp(prefix="evan-gerber-preview-"))
        result["gerber_preview"] = write_preview(geometry, preview_dir / "Gerber_DFM_preview.html")
    verify_sources(request.imported_sources)
    verify_sources(sources)
    if capability_fingerprint(request.factory) != rule_hash:
        raise ValueError("PCB 能力 YAML 在分析期间变化，请重新分析")
    result.update(input_gerber=str(Path(request.source_path).resolve()), input_fingerprints=sources,
                  analysis_mode="gerber_only", geometry_input_source="独立 Gerber 导入（无需 BRD / Cadence）",
                  analyzed_at=datetime.now().isoformat(timespec="seconds"))
    result.setdefault("problem_locations", [])
    return attach_summary("pcb", result, sources)

"""Report use cases: snapshot first, verify sources, delegate format writing."""
from pathlib import Path
from tempfile import mkdtemp

from src.adapters import reports
from src.application.manufacturing import require_project_sources
from src.infrastructure.snapshots import copy_request, verify_sources
from src.adapters.workbench_report import REPORT_FORMATS, REPORT_LABELS, default_report_name
from src.domain.verdict import VERDICT_LABELS
from src.adapters.coordination_report import STATUS_LABELS

__all__ = ["REPORT_FORMATS", "REPORT_LABELS", "STATUS_LABELS", "VERDICT_LABELS", "default_report_name", "export_brd_report",
           "export_coordination", "export_report", "preview_coordination"]


def export_report(kind, path, pcb=None, smt=None, board_meta=None):
    pcb, smt, board_meta = copy_request((pcb, smt, board_meta))
    meta = board_meta or {}
    if pcb and pcb.get("analysis_mode") == "gerber_only":
        target = Path(path).resolve()
        for source in pcb.get("input_fingerprints", []):
            original = Path(source["path"])
            if target == original or (original.is_dir() and target.is_relative_to(original)):
                raise ValueError("Gerber 报告不能覆盖输入文件或写入输入底片目录")
    if meta.get("brd_path"):
        from src.application.board_inputs import content_sha256
        actual = content_sha256(meta["brd_path"])
        for name, result in (("pcb", pcb), ("smt", smt)):
            if kind in (name, "dfm") and result and result.get("input_brd_sha256") != actual:
                raise ValueError("当前 BRD 与分析快照不一致，请重新分析")
    return reports.export_workbench(kind, path, pcb=pcb, smt=smt, board_meta=board_meta)


def export_brd_report(bundle, out_dir, base_name="BRD分析报告", formats=None):
    bundle = copy_request(bundle)
    verify_sources(bundle.get("input_fingerprints") or [])
    from src.infrastructure.staging import stage_outputs
    from src.adapters.staged_workbooks import prepare_workbooks
    return stage_outputs(out_dir, lambda temp: reports.export_brd(bundle, temp, base_name, formats),
                         lambda: verify_sources(bundle.get("input_fingerprints") or []), prepare=prepare_workbooks)


def export_coordination(project, path):
    project = copy_request(project)
    require_project_sources(project)
    from src.infrastructure.staging import stage_outputs
    from src.adapters.staged_workbooks import prepare_workbooks
    target = Path(path).resolve()
    return stage_outputs(target.parent, lambda temp: reports.export_manufacturing(project, Path(temp) / target.name),
                         lambda: require_project_sources(project), prepare=prepare_workbooks)


def preview_coordination(project, refdes=""):
    project = copy_request(project)
    require_project_sources(project)
    folder = Path(mkdtemp(prefix="evan-manufacturing-preview-"))
    return reports.preview_manufacturing(project, folder / "preview.html", refdes)


def write_geometry_template(board_path, directory, path):
    import json
    from src.pcb_geometry_data import geometry_template
    from src.infrastructure.publication import publish_files
    from tempfile import TemporaryDirectory
    data = geometry_template(board_path, directory)
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".geometry-template-", dir=target.parent) as temp:
        staged = Path(temp) / target.name
        staged.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        publish_files([(staged, target)])
    return str(target)


def export_store_report(bundle, out_dir):
    from src.adapters.store_report import write_report
    bundle = copy_request(bundle)
    verify_sources(bundle.get("input_fingerprints", []))
    from src.infrastructure.staging import stage_outputs
    return stage_outputs(out_dir, lambda temp: write_report(bundle, temp),
                         lambda: verify_sources(bundle.get("input_fingerprints", [])))


def write_smt_template(brd_path, path):
    import json
    from src.application.smt_workflow import evidence_template
    from src.infrastructure.staging import stage_outputs
    sources = capture_template_sources(brd_path)
    data = evidence_template(brd_path)
    target = Path(path).resolve()
    def generate(temp):
        staged = Path(temp) / target.name
        staged.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(staged)
    return stage_outputs(target.parent, generate, lambda: verify_sources(sources))


def capture_template_sources(brd_path):
    from src.infrastructure.snapshots import capture_sources
    return capture_sources([brd_path])


def write_assembly_rule_template(path):
    import json
    from src.infrastructure.publication import publish_files
    from tempfile import TemporaryDirectory
    from src.domain.smt_multireport import NUMERIC_RULES, OTHER_RULES
    from src.domain.smt_pad_integrity import NUMERIC_RULES as PAD_RULES
    from src.domain.smt_report_extensions import NUMERIC_RULES as EXT_RULES, OTHER_RULES as EXT_OTHER
    data={"schema_version":1,"assembly_rules":{"source":"","revision":"","via_in_pad_policy":"review",
        "wave_solder_applicable":None,"transport_axis":None,
        **{key:None for key in ("toe_max_mm","paste_spacing_mm","test_to_silk_mm","mark_like_pad_mm",
            "mark_mask_clearance_mm","opposite_side_clearance_mm","board_width_min_mm","board_width_max_mm","edge_gap_max_mm",
            "pth_to_opposite_smd_mm","mounting_clearance_mm","heel_min_mm","thermal_halo_mm","thermal_copper_ratio_max",
            "thermal_imbalance_max","panel_rib_min_mm")}, **{key:None for key in sorted(NUMERIC_RULES | OTHER_RULES | PAD_RULES | EXT_RULES | EXT_OTHER)}}}
    target=Path(path).resolve()
    target.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix=".assembly-template-",dir=target.parent) as temp:
        staged=Path(temp)/target.name
        staged.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        publish_files([(staged,target)])
    return str(target)


def write_step_model_template(brd_path,step_path,path):
    """Hash-bind empty confirmation template, never invent a mechanical body."""
    import json
    from tempfile import TemporaryDirectory
    from src.adapters.smt_evidence import sha256_file
    from src.infrastructure.publication import publish_files
    from src.adapters.step_reader import parse_step
    inventory=parse_step(step_path)
    data=dict(schema_version=1,unit="mm",coordinate_system="brd",alignment_confirmed=False,
              brd_sha256=sha256_file(brd_path),step_sha256=sha256_file(step_path),components=[],
              note="经CAD/工程确认填refdes、side、step_product、outline、height_mm、source、confirmed；不能用STP点集合外接盒代替本体",
              available_step_products=inventory["products"])
    target=Path(path).resolve();target.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix=".step-template-",dir=target.parent) as folder:
        staged=Path(folder)/target.name;staged.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        publish_files([(staged,target)])
    return str(target)

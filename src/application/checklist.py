"""Checklist application service; the UI never orchestrates rule filling."""
from dataclasses import dataclass
from pathlib import Path

from src.adapters import readers
from src.application import engines as analysis
from src.application.common import attach_summary
from src.infrastructure.snapshots import capture_sources, copy_request, verify_sources


@dataclass(frozen=True)
class ChecklistRequest:
    checklist_path: str = ""
    output_dir: str = ""
    brd_path: str = ""
    nets_path: str = ""
    factory: str = "hongban"
    board_type: str = "rigid"
    level: str = "standard"
    copper_um: str | float = "35"
    checker: str = "BRD-AI"
    demo: bool = False


def run_checklist(request: ChecklistRequest, log=None):
    request = copy_request(request)
    paths = [] if request.demo else [request.brd_path, request.nets_path, request.checklist_path]
    sources = capture_sources(paths)
    from src.adapters.checklist_reader import default_config_path
    from src.adapters.pcb_configuration import capability_fingerprint, configuration_root
    config_path = default_config_path()
    if Path(config_path).is_file():
        sources += capture_sources([config_path])
    design_rules = configuration_root() / "internal/pcb/pcb_design_rules.yaml"
    sources += capture_sources([design_rules])
    capability_hash = capability_fingerprint(request.factory)
    from dataclasses import replace
    from src.infrastructure.staging import stage_outputs
    from src.adapters.staged_workbooks import prepare_workbooks
    if not request.output_dir:
        raise ValueError("请指定 Checklist 输出目录")
    def verify_inputs():
        verify_sources(sources)
        if capability_fingerprint(request.factory) != capability_hash:
            raise ValueError("Checklist 使用的 PCB 能力已变化，请重新分析")
    result = stage_outputs(request.output_dir,
                           lambda temp: analysis.analyze_checklist(replace(request, output_dir=temp), log),
                           verify_inputs, prepare=prepare_workbooks)
    verify_sources(sources)
    result["input_fingerprints"] = sources
    return attach_summary("checklist", result, sources)


def inspect_template(path):
    sources = capture_sources([path])
    result = readers.read_checklist_template(path)
    verify_sources(sources)
    return result


def run(**options):
    """Keyword compatibility for CLI and existing Checklist window."""
    log = options.pop("log", None)
    return run_checklist(ChecklistRequest(**options), log)

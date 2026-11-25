"""PCB use case, including import and analysis snapshot boundaries."""
from src.adapters import readers
from src.application.common import attach_summary
from src.application.contracts import PCBAnalysisRequest
from src.infrastructure.snapshots import capture_sources, copy_request, verify_sources

__all__ = ["PCBAnalysisRequest", "import_brd", "run_pcb_analysis"]


def import_brd(path, reader=None):
    from src.brd_input import brd_signature, require_brd_signature, validate_brd_info
    signature = brd_signature(path)
    sources = capture_sources([path])
    info = (reader or readers.read_brd_design)(path)
    if not info.get("ok"):
        raise ValueError(str(info.get("error") or "BRD 结构未通过验证；旧版文本猜测不允许作为正式导入"))
    validate_brd_info(info)
    require_brd_signature(path, signature)
    verify_sources(sources)
    return dict(info, _import_signature=signature, _import_sha256=sources[0]["sha256"])


def run_pcb_analysis(request: PCBAnalysisRequest, progress=None):
    request = copy_request(request)
    from src.application.pcb_workflow import run_pcb_analysis as run
    result = run(request, progress=progress)
    sources = [{"path": result["input_brd"], "sha256": result["input_brd_sha256"], "type": "file"}]
    sources += [s for s in result.get("input_fingerprints", []) if not s.get("missing")]
    return attach_summary("pcb", result, sources)

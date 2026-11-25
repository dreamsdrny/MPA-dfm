"""BRD multi-capability orchestration with explicit source version checks."""
from dataclasses import dataclass

from src.application import engines as analysis
from src.application.common import attach_summary
from src.infrastructure.snapshots import capture_sources, copy_request, verify_sources


@dataclass(frozen=True)
class BRDAnalysisRequest:
    brd_path: str
    out_dir: str = ""
    export_files: tuple[str, ...] = ()
    only: tuple[str, ...] | None = None
    categories: tuple[str, ...] | None = None
    skip: tuple[str, ...] | None = None


def capability_rows():
    from src.brd_analysis import capability_rows as rows
    return rows()


def catalog_errors():
    from src.brd_analysis.catalog import CATALOG
    return list(CATALOG.errors)


def run_brd_analysis(request: BRDAnalysisRequest, log=None):
    request = copy_request(request)
    if request.only is not None and not request.only:
        raise ValueError("请至少选择一项 BRD 分析能力")
    sources = capture_sources([request.brd_path, *request.export_files])
    if not request.export_files:
        from src.brd_analysis.imports import _auto_dirs, _scan_dir
        candidates = [row["path"] for directory in _auto_dirs(request.brd_path, [request.out_dir] if request.out_dir else [])
                      for row in _scan_dir(directory)]
        sources = capture_sources([request.brd_path, *candidates])
    result = analysis.analyze_brd(request, log)
    verify_sources(sources)
    result["input_fingerprints"] = sources
    return attach_summary("brd", result, sources)

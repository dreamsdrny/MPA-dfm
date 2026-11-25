"""Manufacturing/BOM import, version verification and object-analysis use cases."""
from dataclasses import dataclass

from src.application import engines as analysis
from src.application.common import attach_summary
from src.infrastructure.snapshots import copy_request, verify_sources


@dataclass(frozen=True)
class ManufacturingRequest:
    layout_path: str
    bom_path: str = ""
    kind: str = "auto"
    placement_path: str = ""
    step: str | None = None
    alignment_confirmed: bool = False
    object_checks_only: bool = True


def load_manufacturing_project(**options):
    return run_manufacturing(ManufacturingRequest(**options))


def run_manufacturing(request: ManufacturingRequest):
    result = analysis.load_manufacturing(copy_request(request))
    return attach_summary("manufacturing", result, result.get("sources", []))


def require_project_sources(project):
    if not project.get("sources"):
        raise ValueError("制造资料缺少版本快照，请重新导入")
    verify_sources(project["sources"])


def run_object_dfm(project):
    project = copy_request(project)
    if project.get("kind") != "odb" or project.get("status") == "needs_step":
        raise ValueError("对象 DFM 需要已选择 step 的 ODB++ 项目")
    require_project_sources(project)
    result = analysis.analyze_manufacturing_objects(project)
    require_project_sources(project)
    return result

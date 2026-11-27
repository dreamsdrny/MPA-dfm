"""SMT service: isolate mutable caller facts/profiles before running engines."""
from src.adapters import readers
from src.application import engines as analysis
from src.application.common import attach_summary
from src.infrastructure.snapshots import copy_request
from src.application.smt_workflow import design_facts, evidence_template
from src.adapters.smt_configuration import verify_profile_sources
from src.adapters.smt_evidence import source_signature
from src.domain.smt_rules import STATUSES, analyze_smt, manual_requirements, number
from src.domain.inspection_state import LABELS as INSPECTION_LABELS, VERDICTS as INSPECTION_VERDICTS

__all__ = ["STATUSES", "analyze_smt", "analyze_smt_workflow", "design_facts", "evidence_template",
            "load_smt_profile", "manual_requirements", "number", "source_signature", "verify_profile_sources"]
__all__ += ["INSPECTION_LABELS","INSPECTION_VERDICTS"]

load_smt_profile = readers.read_smt_profile


def analyze_smt_workflow(doc, key, facts, design=None, evidence_path="", gerber_dir="", baseline=None, analyzer=None,
                         pcb_result=None, bom_path="", auto_sources=False, gerber_confirmed=False, output_dir="", assembly_rules_path="",
                         odb_path="",odb_step="",odb_confirmed=False,step_path="",model_path="",progress=None):
    options = {"design": copy_request(design), "evidence_path": evidence_path,
                "gerber_dir": gerber_dir, "baseline": copy_request(baseline)}
    if bom_path:
        options["bom_path"] = bom_path
    if assembly_rules_path:
        options["assembly_rules_path"]=assembly_rules_path
    if odb_path or step_path or model_path:
        options.update(odb_path=odb_path,odb_step=odb_step,odb_confirmed=odb_confirmed,step_path=step_path,model_path=model_path)
    if progress:
        options["progress"] = progress
    if auto_sources:
        options.update(pcb_result=copy_request(pcb_result), bom_path=bom_path, auto_sources=True,
                       gerber_confirmed=gerber_confirmed, output_dir=output_dir)
    if analyzer is not None:
        options["analyzer"] = analyzer
    result = analysis.analyze_smt(copy_request(doc), key, copy_request(facts), **options)
    sources = list(result.get("profile_sources") or [])
    if result.get("input_brd_sha256"):
        sources.append({"path": result["input_brd"], "sha256": result["input_brd_sha256"]})
    return attach_summary("smt", result, sources)

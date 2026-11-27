"""Overall run contracts: one immutable SMT request plus UI-safe combined verdict."""
from copy import deepcopy

from src.application.smt_analysis import analyze_smt_workflow, design_facts
from src.domain.combined_verdict import combined_verdict as _combined_verdict


def combined_summary(pcb, smt):
    """UI 只调用应用层；综合结论仍由领域层唯一口径计算。"""
    return _combined_verdict(pcb or {}, smt or {})


def compute_smt_snapshot(snapshot, design, pcb):
    snapshot=deepcopy(snapshot)
    fields=snapshot.pop("fields"); overrides=snapshot.pop("overrides")
    facts=design_facts(design,fields,overrides)
    return analyze_smt_workflow(facts=facts,design=design,pcb_result=pcb,**snapshot)

"""Combined result policy shared by UI, application and report adapters."""


def _pcb_verdict_code(pcb):
    code = pcb.get("verdict_code")
    if code in ("fail", "review", "pass"):
        return code
    if pcb.get("error_count", 0) or any(it.get("status") == "fail" for it in pcb.get("items", [])):
        return "fail"
    return "pass" if pcb.get("releasable") else "review"


def combined_verdict(pcb, smt):
    smt_fail = smt.get("counts", {}).get("fail", 0) or any(it.get("status") == "fail" for it in smt.get("items", []) + smt.get("bom_findings",[]) + smt.get("assembly_findings",[]))
    smt_review = (smt.get("coverage", 0) < 1 or smt.get("counts", {}).get("review", 0)
                   or smt.get("manual_review_count", 0) or smt.get("object_errors") or smt.get("bom_findings") or smt.get("assembly_findings") or
                   smt.get("assembly_errors") or any(c.get("verdict")=="review" for c in smt.get("assembly_coverage",[])) or not smt.get("items"))
    if _pcb_verdict_code(pcb) == "fail" or smt_fail:
        return "存在不符合项"
    if _pcb_verdict_code(pcb) == "review" or smt_review:
        return "数据或能力不完整，需复核"
    if pcb.get("warning_count", 0) or pcb.get("issues"):
        return "能力匹配，存在需复核项目"
    return "本次所选 PCB / SMT 能力项匹配"

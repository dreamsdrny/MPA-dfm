"""Shared SMT workflow: supplier comparison, optional geometry and internal baseline."""
from datetime import datetime
from pathlib import Path

from src.brd_input import content_sha256, require_brd_signature
from src.domain.smt_rules import (
    analyze_smt,
    compare_smt_baseline,
    normalize_smt_facts,
    summarize_smt,
)
from src.adapters.smt_configuration import verify_profile_document
from src.smt_geometry import (
    analyze_objects,
    apply_object_results,
    evidence_fingerprints,
    load_evidence,
    sha256_file,
    source_signature,
)


def design_facts(design, fields, overrides=None):
    """Expand PCB size arrays; never use PCB defaults as confirmed SMT facts."""
    from src.domain.pcb_inputs import FIELD_META
    units = {key: meta.get("unit", "mm") for key, meta in FIELD_META.items()}
    units.update(bga_pitch="mm", mark_to_edge_mm="mm", tooling_hole_count="count")
    values = dict(design)
    for name in ("panel_size", "board_size"):
        if design.get(name):
            values[name + "_x"], values[name + "_y"] = design[name]
            units[name + "_x"] = units[name + "_y"] = "mm"
    facts = {}
    for scope, field in fields:
        if scope == "design" and field in values and values[field] is not None and field in units:
            unit = {"个": "count", "层": "count", "%": "percent", "": "ratio"}.get(units[field], units[field])
            source = (design.get("_sources", {}).get(field) or {}).get("source")
            facts[(scope, field)] = {"value": values[field], "unit": unit, "source": source or "PCB页签已确认输入"}
    facts.update(overrides or {})
    return facts


def analyze_smt_workflow(doc, key, facts, design=None, evidence_path="", gerber_dir="", baseline=None, analyzer=analyze_smt):
    design = design or {}
    brd = design.get("_input_brd", "")
    signature = design.get("_brd_signature")
    if signature:
        require_brd_signature(brd, signature)
    digest = content_sha256(brd) if brd else None
    if design.get("_brd_sha256") and design["_brd_sha256"] != digest:
        raise ValueError("BRD 内容已变化，请重新导入")
    verify_profile_document(doc)
    verify_profile_document(baseline)
    result = analyzer(doc, key, facts)
    result["inspection_mode"] = "已确认参数比较；未执行对象几何检测" if not evidence_path else "参数比较 + 已确认对象几何检测"
    data = None
    if evidence_path:
        before = source_signature(evidence_path, brd, gerber_dir)
        content_before = evidence_fingerprints(evidence_path, brd, gerber_dir)
        data = load_evidence(evidence_path, brd)
        evidence, errors = analyze_objects(doc, key, data, gerber_dir)
        apply_object_results(result, evidence, errors)
        # Object merging must not erase panel process conditions.
        from src.smt_geometry import _apply_panel_review
        _apply_panel_review(result, normalize_smt_facts(doc, facts))
        summarize_smt(result)
        result["object_evidence_path"] = str(Path(evidence_path).resolve())
    if baseline:
        base_key = next(iter(baseline["profiles"]))
        base_result = analyze_smt(baseline, base_key, normalize_smt_facts(doc, facts))
        if data is not None:
            evidence, errors = analyze_objects(baseline, base_key, data, gerber_dir)
            apply_object_results(base_result, evidence, errors)
        result["baseline_result"] = base_result
        result["baseline_comparison"] = compare_smt_baseline(doc, key, baseline, base_key)
        result["baseline_config"] = {"meta": baseline["meta"], "path": baseline["path"], "profile": base_key}
        result["baseline_verdict"] = base_result["verdict"]
        if baseline.get("sha256"):
            result.setdefault("profile_sources", []).append({"path": baseline["path"], "sha256": baseline["sha256"]})
    if evidence_path:
        after = source_signature(evidence_path, brd, gerber_dir)
        if before != after:
            raise ValueError("SMT 对象资料在分析期间发生变化，请重新分析")
        if content_before != evidence_fingerprints(evidence_path, brd, gerber_dir):
            raise ValueError("SMT 对象资料内容在分析期间发生变化，请重新分析")
        result["_source_signature"] = after
        result["evidence_fingerprints"] = content_before
    result.update(board_name=design.get("board_name", ""), input_brd=brd,
                  analyzed_at=datetime.now().isoformat(timespec="seconds"))
    if signature:
        require_brd_signature(brd, signature)
    if digest and content_sha256(brd) != digest:
        raise ValueError("BRD 内容在 SMT 分析期间变化，请重新导入")
    verify_profile_document(doc)
    verify_profile_document(baseline)
    result["input_brd_sha256"] = digest
    result["_brd_signature"] = signature
    return result


def evidence_template(brd_path):
    """Bind a template to a real board; no completeness/alignment is auto-confirmed."""
    return {"schema_version": 1, "brd_sha256": sha256_file(brd_path), "coordinate_system": "brd", "unit": "mm",
                "coordinate_alignment_confirmed": False, "assembly_sides": [],
                "complete": {key: False for key in ("components", "marks", "tooling_holes", "vcut_lines", "test_pads",
                          "shields", "silkscreen_text", "pads_top", "pads_bottom", "other_copper_top", "other_copper_bottom")},
                "board_outline": [], "components": [], "marks": [], "tooling_holes": [], "vcut_lines": [], "test_pads": [], "shields": [],
                "pads": [], "other_copper": [], "silkscreen_text": [], "gerber_sha256": {}, "gerber_alignment_confirmed": False}

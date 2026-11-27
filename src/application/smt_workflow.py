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


def analyze_smt_workflow(doc, key, facts, design=None, evidence_path="", gerber_dir="", baseline=None, analyzer=analyze_smt,
                         pcb_result=None, bom_path="", auto_sources=False, gerber_confirmed=False, output_dir="", assembly_rules_path="",
                         odb_path="",odb_step="",odb_confirmed=False,step_path="",model_path="",progress=None):
    design = design or {}
    # 阶段只说明“正在做哪一段”，不据此判定通过；缺资料仍由覆盖率与结论体现。
    stage = progress or (lambda _name: None)
    brd = design.get("_input_brd", "")
    signature = design.get("_brd_signature")
    if signature:
        require_brd_signature(brd, signature)
    digest = content_sha256(brd) if brd else None
    if design.get("_brd_sha256") and design["_brd_sha256"] != digest:
        raise ValueError("BRD 内容已变化，请重新导入")
    verify_profile_document(doc)
    verify_profile_document(baseline)
    bom = None
    if bom_path:
        from src.application.smt_bom import inspect_bom
        bom = inspect_bom(bom_path)
    generated = None
    if odb_path and gerber_dir:
        raise ValueError("SMT最终制造层请选ODB++或Gerber之一，不能无优先策略混用两版")
    if auto_sources and not evidence_path:
        stage("sources")
        from src.application.smt_sources import build_smt_sources
        source_pcb=pcb_result
        if odb_path and pcb_result:
            source_pcb=dict(pcb_result,gerber_folder="")
        generated = build_smt_sources(design, source_pcb, bom_path, gerber_dir, output_dir, gerber_confirmed)
        evidence_path, gerber_dir = generated["path"], generated["gerber_dir"]
    stage("rules")
    result = analyzer(doc, key, facts)
    result["inspection_mode"] = "已确认参数比较；未执行对象几何检测" if not evidence_path else "参数比较 + 已确认对象几何检测"
    data = None
    supplements=None
    if odb_path and gerber_dir:
        raise ValueError("SMT最终制造层请选ODB++或Gerber之一，不能无优先策略混用两版")
    if evidence_path:
        stage("geometry")
        before = source_signature(evidence_path, brd, gerber_dir)
        content_before = evidence_fingerprints(evidence_path, brd, gerber_dir)
        data = load_evidence(evidence_path, brd)
        if odb_path or step_path or model_path:
            from src.application.smt_supplements import inspect_supplements
            supplement_design=dict(design)
            if not supplement_design.get("_native_payload") and pcb_result:
                supplement_design["_native_payload"]=pcb_result.get("native_extraction")
            supplements=inspect_supplements(supplement_design,data,odb_path=odb_path,odb_step=odb_step,
                odb_confirmed=odb_confirmed,step_path=step_path,model_path=model_path)
            data=supplements["data"]
        from src.adapters.smt_evidence import prepare_objects
        from src.domain.smt_objects import analyze_objects as analyze_prepared
        prepared, prepared_inputs=prepare_objects(data,gerber_dir)
        if supplements:
            prepared_inputs["films"].update(supplements["films"])
        evidence, errors = analyze_prepared(doc,key,prepared,**prepared_inputs)
        apply_object_results(result, evidence, errors)
        from src.domain.smt_manufacturing import scan_assembly, validate_rules
        from src.infrastructure.snapshots import capture_sources, verify_sources
        rule_sources=[]
        if assembly_rules_path:
            import json
            from src.infrastructure.resources import read_bounded
            rule_sources=capture_sources([assembly_rules_path])
            rules=json.loads(read_bounded(assembly_rules_path, 1024*1024).decode("utf-8-sig"))
            if not isinstance(rules,dict) or rules.get("schema_version")!=1:
                raise ValueError("装配补充规则需要 schema_version:1")
            data["assembly_rules"]=validate_rules(rules.get("assembly_rules",{}))
        prepared["assembly_rules"]=data.get("assembly_rules",{})
        stage("assembly")
        assembly=scan_assembly(prepared,prepared_inputs["films"],prepared_inputs["input_errors"])
        result["assembly_findings"],result["assembly_coverage"]=assembly["findings"],assembly["coverage"]
        result["assembly_errors"]=assembly["errors"]
        verify_sources(rule_sources)
        result["assembly_rule_fingerprints"]=rule_sources
        result["assembly_rules_path"]=assembly_rules_path
        # Object merging must not erase panel process conditions.
        from src.smt_geometry import _apply_panel_review
        _apply_panel_review(result, normalize_smt_facts(doc, facts))
        summarize_smt(result)
        result["object_evidence_path"] = str(Path(evidence_path).resolve())
        result["source_association"] = data.get("source_association", {})
        result["assembly_completeness"] = {side: {kind: data.get("complete", {}).get(kind + "_" + side.lower(), data.get("complete", {}).get(kind, False))
                                          for kind in ("components", "marks", "test_pads", "pads")} for side in ("TOP", "BOTTOM")}
    if baseline:
        base_key = next(iter(baseline["profiles"]))
        base_result = analyze_smt(baseline, base_key, normalize_smt_facts(doc, facts))
        if data is not None:
            evidence, errors = analyze_prepared(baseline,base_key,prepared,**prepared_inputs)
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
        if data.get("linked_source_fingerprints"):
            from src.infrastructure.snapshots import verify_sources
            verify_sources(data["linked_source_fingerprints"])
            result["linked_source_fingerprints"] = data["linked_source_fingerprints"]
    result.update(board_name=design.get("board_name", ""), input_brd=brd,
                  analyzed_at=datetime.now().isoformat(timespec="seconds"))
    if signature:
        require_brd_signature(brd, signature)
    if digest and content_sha256(brd) != digest:
        raise ValueError("BRD 内容在 SMT 分析期间变化，请重新导入")
    verify_profile_document(doc)
    verify_profile_document(baseline)
    if generated:
        from src.infrastructure.snapshots import verify_sources
        verify_sources(generated["input_fingerprints"])
        result["linked_source_fingerprints"] = generated["input_fingerprints"]
        result["generated_evidence"] = generated["path"]
    if bom:
        from src.application.smt_bom import compare_bom
        from src.infrastructure.snapshots import verify_sources
        bom_design = dict(design)
        if not bom_design.get("_native_payload") and pcb_result and pcb_result.get("native_extraction"):
            bom_design["_native_payload"] = pcb_result["native_extraction"]
        result["bom_analysis"] = compare_bom(bom, bom_design, data)
        result["bom_findings"] = result["bom_analysis"]["findings"]
        verify_sources(bom["input_fingerprints"])
        result.setdefault("linked_source_fingerprints", []).extend(bom["input_fingerprints"])
        summarize_smt(result)
    if data:
        # A plain serializable snapshot makes report pictures reproducible even
        # after the GUI changes. PLACE_BOUND retains its actual boundary label.
        result["picture_geometry"] = {key: data.get(key, []) for key in
            ("components", "pads", "marks", "tooling_holes", "test_pads", "board_outline", "vcut_lines", "mounting_holes", "panel_support_ribs",
             "shields", "via_pads", "laser_regions", "thermal_pads", "conveyed_edges", "mask_openings", "paste_openings", "panel_outline")}
        result["picture_geometry"]["source"] = data.get("_source", evidence_path)
        result["picture_geometry"]["unit"] = "mm"
        result["picture_geometry"]["alignment_confirmed"] = data.get("coordinate_alignment_confirmed") is True
        result["picture_geometry"]["components"] = prepared.get("components",data.get("components",[]))
        if data.get("component_bounds_file"):
            from src.adapters.smt_evidence import _verified_file, load_exported_bounds
            bounds = load_exported_bounds(_verified_file(data, "component_bounds_file", data["_source"]))
            result["picture_geometry"]["components"] = [dict(c, outline=list(bounds[(c["refdes"].upper(),c["side"])].exterior.coords),
                boundary=c.get("boundary") or "place_bound") if not c.get("outline") and (c["refdes"].upper(),c["side"]) in bounds else c for c in data["components"]]
    result["picture_gerber_dir"] = gerber_dir
    result["picture_gerber_alignment"] = bool(data and data.get("gerber_alignment_confirmed") is True)
    if data is None:
        from src.domain.smt_manufacturing import CHECKS
        result["assembly_coverage"]=[dict(check_key=k,label=label,phase="unavailable",verdict="review",checked=0,missing=1,
            required_data=need,source="未提供对象资料",full_board_complete=False) for k,(label,need) in CHECKS.items()]
        if assembly_rules_path:
            raise ValueError("已选装配补充规则但没有对象几何资料，请启用自动关联或选择对象JSON")
        if odb_path or step_path or model_path:
            from src.application.smt_supplements import inspect_supplements
            supplements=inspect_supplements(design,odb_path=odb_path,odb_step=odb_step,odb_confirmed=odb_confirmed,step_path=step_path,model_path=model_path)
    if supplements:
        from src.infrastructure.snapshots import verify_sources
        verify_sources(supplements["sources"])
        result["supplement_analysis"]=supplements["summary"]
        result.setdefault("assembly_findings",[]).extend(supplements["findings"])
        result.setdefault("assembly_coverage",[]).extend(supplements["coverage"])
        result.setdefault("linked_source_fingerprints",[]).extend(supplements["sources"])
        if supplements["films"]:
            result["picture_odb_layers"]=[dict(role=k,geometry_wkb=v["geometry"].wkb_hex,source=v["path"]) for k,v in supplements["films"].items()]
    from src.domain.inspection_state import LABELS, VERDICTS
    result["inspection_states"] = []
    for item in result["items"]:
        records = result.get("object_evidence", {}).get(item["field"], [])
        measured = any(row.get("actual") is not None or row["status"] == "pass" for row in records)
        incomplete = any(row["status"] in ("missing", "review", "unconfigured") for row in records)
        phase = ("partial" if measured and incomplete else "unavailable" if item["status"] in ("missing", "unconfigured")
                 else "partial" if item["status"] == "review" else "checked")
        verdict = "fail" if item["status"] == "fail" else "review" if item["status"] in ("missing", "review", "unconfigured") else "within_scope"
        result["inspection_states"].append(dict(check_key=item["field"], phase=phase, phase_label=LABELS[phase],
            verdict=verdict, verdict_label=VERDICTS[verdict], full_board_complete=False))
    result["inspection_states"] += [dict(row,phase_label=LABELS[row["phase"]],verdict_label=VERDICTS[row["verdict"]]) for row in result.get("assembly_coverage",[])]
    result["input_brd_sha256"] = digest
    if result.get("assembly_rule_fingerprints"):
        from src.infrastructure.snapshots import verify_sources
        verify_sources(result["assembly_rule_fingerprints"])
        result.setdefault("linked_source_fingerprints",[]).extend(result["assembly_rule_fingerprints"])
    summarize_smt(result)
    result["_brd_signature"] = signature
    return result


def evidence_template(brd_path):
    """Bind a template to a real board; no completeness/alignment is auto-confirmed."""
    return {"schema_version": 1, "brd_sha256": sha256_file(brd_path), "coordinate_system": "brd", "unit": "mm",
                "coordinate_alignment_confirmed": False, "assembly_sides": [],
                "complete": {key: False for key in ("components", "marks", "tooling_holes", "vcut_lines", "test_pads",
                          "shields", "silkscreen_text", "pads_top", "pads_bottom", "other_copper_top", "other_copper_bottom",
                          "physical_leads", "conveyed_edges", "via_pads", "laser_regions", "thermal_pads", "part_models", "silkscreen_text_records",
                          "mask_openings", "paste_openings", "panel_marks", "thermal_void_records")},
                "board_outline": [], "components": [], "marks": [], "tooling_holes": [], "vcut_lines": [], "test_pads": [], "shields": [],
                "pads": [], "other_copper": [], "silkscreen_text": [], "gerber_sha256": {}, "gerber_alignment_confirmed": False,
                "native_pins":[],"native_holes":[],"native_traces":[],"foam_regions":[],
                "mounting_holes":[],"panel_support_ribs":[],"board_type":"","board_type_confirmed":False,
                "via_pads":[],"conveyed_edges":[],"laser_regions":[],"thermal_pads":[],"silkscreen_text_records":[],
                "mask_openings":[],"paste_openings":[],"panel_outline":[],"panel_alignment_confirmed":False,"thermal_void_records":[],
                "engineering_review":{**{key:{"status":"review","source":"","note":""} for key in ("laser_area","flow_direction","tooling_poka_yoke","panel_corner_support")},
                    "rigid_flex_separation":{"status":"review","source":"","note":"","equipment_confirmed":False,"method":None}}}

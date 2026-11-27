"""Background BRD enrichment without PCB Editor or Tk dependencies."""
from copy import deepcopy

from src.adapters.extracta import extract_brd_data


def prepare_brd_data(brd_path, output_dir, editor_path="", timeout=90):
    return extract_brd_data(brd_path, output_dir, editor_path, timeout)


def enrich_design(design, payload):
    design = deepcopy(design)
    manual = set(design.get("_manual_fields", []))
    sources = design.setdefault("_sources", {})
    if not design.get("_stackup_manual"):
        # A previous successful extraction is not this run's native evidence.
        design["stackup"] = []
        if "copper_um" not in manual:
            design["copper_um"] = None
            sources.pop("copper_um", None)
    for key, value in payload.get("updates", {}).items():
        if key in manual or key in ("stackup", "copper_um") and design.get("_stackup_manual"):
            continue
        design[key] = deepcopy(value)
        sources[key] = dict(source="BRD extracta " + ("LAYER_THICKNESS（叠构设计值，非实测）" if key in ("stackup", "copper_um") else key), confidence="high")
    if "copper_um" in manual and not design.get("_stackup_manual"):
        stack = design.get("stackup") or []
        for row in stack:
            if row["index"] in (1, len(stack)):
                row.setdefault("design_copper_um", row.get("copper_um"))
                row.update(copper_um=design.get("copper_um"), source="人工确认外层成品铜厚", thickness_basis="manual")
    confirmations = design.get("_copper_confirmations", {})
    from src.domain.pcb_rules import normalize_copper_um
    names = {row["name"] for row in design.get("stackup", [])}
    if set(confirmations) - names:
        raise ValueError("成品铜厚确认包含当前叠构不存在的铜层，请重新确认")
    for row in design.get("stackup", []):
        confirmed = confirmations.get(row["name"])
        if confirmed:
            if not str(confirmed.get("source", "")).strip() or not confirmed.get("confirmed_at"):
                raise ValueError("成品铜厚确认必须保留依据和日期")
            thickness = normalize_copper_um(confirmed.get("copper_um"))
            row.update(design_copper_um=row.get("design_copper_um", row.get("copper_um")), copper_um=thickness,
                copper_confirmation=confirmed, source=confirmed["source"], thickness_basis="confirmed_finished")
    if confirmations and design.get("stackup"):
        outer = [design["stackup"][0]["copper_um"], design["stackup"][-1]["copper_um"]]
        design["copper_um"] = outer[0] if outer[0] == outer[1] else None
    if "comp_count" in payload.get("updates", {}):
        refs = [row["refdes"] for row in payload["components"]]
        design["_components"] = dict(ok=True, count=len(refs), refdes=refs, source="BRD extracta SYMBOL")
    design["_native_extraction"] = dict(status=payload["status"], warnings=payload.get("warnings", []), directory=payload["directory"])
    design["_native_objects"] = deepcopy(payload.get("objects", {}))
    design["_native_payload"] = deepcopy(payload)
    return design

"""PCB form contracts, metadata and confirmed stackup import use cases."""
import json
from copy import deepcopy

from src.adapters.pcb_configuration import list_levels
from src.domain.stackup import copper_layers
from src.domain.stackup_visual import build_visual as stackup_visual, layer_label
from src.domain.stackup_visual import KINDS as STACKUP_KINDS
from src.domain.pcb_rules import normalize_copper_um, normalize_level
from src.domain.pcb_inputs import (BRD_COMPONENT_CAP, BRD_FIELDS, CATEGORY_HINTS, DFM_FIELDS,
                           FIELD_META, brd_design_values, brd_dfm_fields, brd_extra_values,
                           surface_finish_choices)
from src.infrastructure.resources import read_bounded
from src.infrastructure.snapshots import capture_sources, verify_sources

__all__ = ["BRD_COMPONENT_CAP", "BRD_FIELDS", "CATEGORY_HINTS", "DFM_FIELDS", "FIELD_META",
           "brd_design_values", "brd_dfm_fields", "brd_extra_values", "copper_layers",
           "layer_label", "list_levels", "normalize_copper_um", "normalize_level", "STACKUP_KINDS",
           "stackup_visual",
           "surface_finish_choices"]


def import_stackup(path, current_count, copper_um):
    sources = capture_sources([path])
    doc = json.loads(read_bounded(path, 4 * 1024 * 1024).decode("utf-8-sig"))
    if not isinstance(doc, dict) or not doc.get("stackup"):
        raise ValueError("JSON 需包含 layer_count 和 stackup 铜层表")
    count = doc.get("layer_count") or current_count
    if current_count and count and int(float(current_count)) != int(count):
        raise ValueError("叠构层数与当前 BRD 不一致，请确认板版本")
    rows = copper_layers(dict(doc, layer_count=count), normalize_copper_um(copper_um) if str(copper_um or "").strip() else None)
    if not rows:
        raise ValueError("未读到铜层")
    verify_sources(sources)
    raw = doc["stackup"]
    return {"rows": deepcopy(raw.get("layers", []) if isinstance(raw, dict) else raw), "count": len(rows),
            "input_fingerprints": sources}

"""Compatibility facade for prepared file evidence and pure SMT geometry."""
from src.adapters.smt_evidence import (  # noqa: F401 compatibility exports
    evidence_fingerprints, load_evidence, load_exported_bounds, prepare_objects,
    sha256_file, source_signature, verify_evidence_fingerprints,
)
from src.domain.smt_geometry import _apply_panel_review  # noqa: F401
from src.domain.smt_objects import *  # noqa: F403
from src.domain.smt_objects import _distance, _measured, _polygon, _record, _side  # noqa: F401
from src.domain.smt_objects import analyze_objects as _analyze


def analyze_objects(doc, profile_key, data, gerber_dir=""):
    prepared, inputs = prepare_objects(data, gerber_dir)
    return _analyze(doc, profile_key, prepared, **inputs)

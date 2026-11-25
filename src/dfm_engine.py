"""Legacy PCB facade: file versions/config in Adapters, decisions in Domain."""
from src.adapters.pcb_configuration import (  # noqa: F401 compatibility exports
    _load_yaml, capability_fingerprint, level_label, list_board_types, list_levels,
    load_capability, load_capability_db, rule_metadata,
)
from src.domain import pcb_rules
from src.domain.pcb_rules import *  # noqa: F403 compatibility exports
from src.domain.pcb_rules import DEFAULT_FACTORY, _mil_to_mm, _num, _to_mm, compute_coverage, normalize_factory  # noqa: F401 compatibility


def analyze_design(design, board_type="rigid", capability_level="standard", factory=DEFAULT_FACTORY):
    factory = normalize_factory(factory)
    digest = capability_fingerprint(factory)
    capability = load_capability(board_type, capability_level, factory=factory)
    metadata = rule_metadata(factory, board_type, capability_level, digest)
    result = pcb_rules.analyze_design(design, board_type, capability_level, factory,
                                      capability=capability, rule_metadata=metadata, coverage_fn=compute_coverage)
    if capability_fingerprint(factory) != digest:
        raise ValueError("PCB 能力 YAML 在分析期间发生变化，请重新分析")
    return result


def validate_constraints(constraints, capability_level="standard", board_type="rigid",
                         copper_um=35.0, factory=DEFAULT_FACTORY):
    factory = normalize_factory(factory)
    digest = capability_fingerprint(factory)
    capability = load_capability(board_type, capability_level, factory=factory)
    result = pcb_rules.validate_constraints(constraints, capability_level, board_type, copper_um, factory,
        capability=capability, rule_metadata=rule_metadata(factory, board_type, capability_level, digest))
    if capability_fingerprint(factory) != digest:
        raise ValueError("PCB 能力 YAML 在分析期间发生变化，请重新分析")
    return result

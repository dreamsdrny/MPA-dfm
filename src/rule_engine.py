"""Compatibility facade for configuration-free constraint calculations."""
from src.adapters.pcb_configuration import _load_yaml
from src.domain.constraint_rules import *  # noqa: F403
from src.domain.constraint_rules import compute_class_constraints as _compute


def compute_class_constraints(signal_classification):
    return _compute(signal_classification, _load_yaml("internal/pcb/pcb_design_rules.yaml"))

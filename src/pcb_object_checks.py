"""Compatibility facade for versioned object configuration + pure decisions."""
from src.adapters.object_configuration import object_rules
from src.domain.pcb_objects import TOLERANCE, scan_object_checks as _scan

__all__ = ["TOLERANCE", "object_rules", "scan_object_checks"]


def scan_object_checks(films, pads, vias, limits, overrides=None, complete=False):
    bundle = object_rules(overrides)
    result = _scan(films, pads, vias, limits, overrides, complete, rule_bundle=bundle)
    if object_rules(overrides) != bundle:
        raise ValueError("对象规则在分析期间变化，请重新分析")
    return result

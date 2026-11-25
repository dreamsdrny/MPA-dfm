"""Compatibility facade for supplemental geometry checks."""
from src.domain.pcb_geometry import supplemental_checks as _checks


def supplemental_checks(available, hole_records, outline, capability, layers, options, limits,
                        emit, pairs, coverage, errors):
    from src.adapters.object_configuration import object_rules
    bundle = object_rules(options.get("object_rules"))
    result = _checks(available, hole_records, outline, capability, layers, options, limits,
                     emit, pairs, coverage, errors, object_rule_bundle=bundle)
    if object_rules(options.get("object_rules")) != bundle:
        raise ValueError("对象规则在分析期间变化，请重新分析")
    return result

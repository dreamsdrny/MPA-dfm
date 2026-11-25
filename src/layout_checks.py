"""Legacy interface: preload drilling/rules, then call pure layout decisions."""
from src.domain.layout_checks import scan_layout_checks as _scan


def scan_layout_checks(folder, films, ambiguous, copper_checks, mask_checks, outline,
                       capability, layers, options, limits):
    from src.adapters.drill_inputs import read_drills
    from src.adapters.object_configuration import object_rules
    drills, errors = read_drills(folder, options)
    bundle = object_rules(options.get("object_rules"))
    result = _scan(folder, films, ambiguous, copper_checks, mask_checks, outline,
                   capability, layers, options, limits, drills=drills, drill_errors=errors, object_rule_bundle=bundle)
    if object_rules(options.get("object_rules")) != bundle:
        raise ValueError("对象规则在分析期间变化，请重新分析")
    return result

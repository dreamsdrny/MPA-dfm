"""Legacy BOM reconciliation interface."""
from src.adapters.brd_components import brd_component_records
from src.domain.bom_rules import *  # noqa: F403
from src.domain.bom_rules import _natural_key, _norm_attr, _norm_ref, _prefix, _record  # noqa: F401

__all__ = ["brd_component_records", "reconcile_bom"]

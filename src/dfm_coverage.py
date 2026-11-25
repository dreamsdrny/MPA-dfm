"""Compatibility facade for domain capability coverage."""
from src.domain.pcb_coverage import *  # noqa: F403
from src.domain.pcb_coverage import _cap_in_mm, _flatten, _raw_text

__all__ = ["_cap_in_mm", "_flatten", "_raw_text", "coverage_rows"]

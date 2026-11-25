"""Compatibility exports; authoritative PCB release logic lives in Domain."""
from src.domain.verdict import VERDICT_LABELS, finalize_verdict

__all__ = ["VERDICT_LABELS", "finalize_verdict"]

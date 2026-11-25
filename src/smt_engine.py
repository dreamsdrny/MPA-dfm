"""Compatibility facade: SMT rules in Domain, YAML and Excel in Adapters."""
from src.adapters.smt_configuration import load_smt_profile, verify_profile_document, verify_profile_sources
from src.adapters.smt_report import append_smt_sheets, export_smt_report
from src.domain.smt_rules import *  # noqa: F403

__all__ = ["append_smt_sheets", "export_smt_report", "load_smt_profile", "verify_profile_document", "verify_profile_sources"]

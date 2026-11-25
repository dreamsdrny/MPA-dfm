"""Compatibility facade for pure Checklist rules."""
from src.domain.checklist_rules import *  # noqa: F403
from src.domain.checklist_rules import load_rules as _load_rules


def load_rules(config=None):
    from src.checklist_template import load_config
    return _load_rules(config if config is not None else load_config())

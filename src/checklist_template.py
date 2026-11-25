"""Legacy import path for Checklist Excel/config readers."""
import sys

from src.adapters import checklist_reader as _implementation

sys.modules[__name__] = _implementation

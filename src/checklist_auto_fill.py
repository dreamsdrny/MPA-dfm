"""Legacy import path for Checklist Excel output."""
import sys

from src.adapters import checklist_writer as _implementation

sys.modules[__name__] = _implementation

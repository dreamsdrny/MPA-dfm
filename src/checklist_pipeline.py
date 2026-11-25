"""Legacy import path for the Checklist application workflow."""
import sys

from src.application import checklist_workflow as _implementation

sys.modules[__name__] = _implementation

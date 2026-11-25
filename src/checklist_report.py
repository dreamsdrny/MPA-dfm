"""Legacy Checklist report adapter import path."""
import sys
from src.adapters import checklist_report as _implementation
sys.modules[__name__] = _implementation

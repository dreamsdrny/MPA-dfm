"""Legacy identity for BRD capability report generation."""
import sys
from src.adapters import brd_capability_reports as _implementation
sys.modules[__name__] = _implementation

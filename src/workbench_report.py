"""Legacy identity for the version-safe report adapter."""
import sys
from src.adapters import workbench_report as _implementation
sys.modules[__name__] = _implementation

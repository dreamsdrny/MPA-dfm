"""Legacy identity for BRD format writers."""
import sys
from src.adapters import brd_report as _implementation
sys.modules[__name__] = _implementation

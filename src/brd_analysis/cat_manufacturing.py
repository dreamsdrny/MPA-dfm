"""Legacy identity for BRD manufacturing artifact adapters."""
import sys
from src.adapters import brd_manufacturing as _implementation
sys.modules[__name__] = _implementation

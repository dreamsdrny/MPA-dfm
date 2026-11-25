"""Legacy identity for assembly evidence and artifact adapters."""
import sys
from src.adapters import brd_assembly as _implementation
sys.modules[__name__] = _implementation

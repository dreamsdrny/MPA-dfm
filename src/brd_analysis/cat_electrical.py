"""Legacy identity for BRD electrical rules."""
import sys
from src.domain import brd_electrical as _implementation
sys.modules[__name__] = _implementation

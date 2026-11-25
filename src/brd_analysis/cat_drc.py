"""Legacy identity for BRD DRC rules."""
import sys
from src.domain import brd_drc as _implementation
sys.modules[__name__] = _implementation

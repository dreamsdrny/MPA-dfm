"""Legacy identity for BRD physical rules."""
import sys
from src.domain import brd_physical as _implementation
sys.modules[__name__] = _implementation

"""Legacy identity for BRD database input/output adapters."""
import sys
from src.adapters import brd_database as _implementation
sys.modules[__name__] = _implementation

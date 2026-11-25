"""Legacy identity for Excellon reading."""
import sys
from src.adapters import drill_reader as _implementation
sys.modules[__name__] = _implementation

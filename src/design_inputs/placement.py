"""Legacy placement adapter import path."""
import sys
from src.adapters import placement_reader as _implementation
sys.modules[__name__] = _implementation

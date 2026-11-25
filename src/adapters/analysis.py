"""Legacy identity for the former phase-one engine bridge."""
import sys
from src.application import engines as _implementation
sys.modules[__name__] = _implementation

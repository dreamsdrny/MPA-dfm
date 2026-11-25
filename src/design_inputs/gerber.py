"""Legacy Gerber adapter import path."""
import sys
from src.adapters import gerber_reader as _implementation
sys.modules[__name__] = _implementation

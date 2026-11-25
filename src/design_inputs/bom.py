"""Legacy BOM adapter import path."""
import sys
from src.adapters import bom_reader as _implementation
sys.modules[__name__] = _implementation

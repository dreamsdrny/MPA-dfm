"""Legacy identity for exported CAD evidence readers."""
import sys
from src.adapters import brd_exports as _implementation
sys.modules[__name__] = _implementation

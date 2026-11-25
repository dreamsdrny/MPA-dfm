"""Legacy identity for native manufacturing tool adapter."""
import sys
from src.adapters import manufacturing_export as _implementation
sys.modules[__name__] = _implementation

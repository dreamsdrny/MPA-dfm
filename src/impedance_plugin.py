"""Legacy identity for the native impedance tool adapter."""
import sys
from src.adapters import impedance_plugin as _implementation
sys.modules[__name__] = _implementation

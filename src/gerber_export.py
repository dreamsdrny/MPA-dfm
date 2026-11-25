"""Legacy identity for the native Gerber tool adapter."""
import sys
from src.adapters import gerber_export as _implementation
sys.modules[__name__] = _implementation

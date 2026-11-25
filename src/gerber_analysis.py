"""Legacy identity for Gerber parsing/analysis integration."""
import sys
from src.adapters import gerber_analysis as _implementation
sys.modules[__name__] = _implementation

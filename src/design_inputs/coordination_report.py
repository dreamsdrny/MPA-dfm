"""Legacy coordination report adapter import path."""
import sys
from src.adapters import coordination_report as _implementation
sys.modules[__name__] = _implementation

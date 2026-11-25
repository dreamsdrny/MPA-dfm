"""Legacy identity for crash diagnostics."""
import sys
from src.infrastructure import diagnostics as _implementation
sys.modules[__name__] = _implementation

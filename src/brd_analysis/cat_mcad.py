"""Legacy identity for MCAD file exchange adapters."""
import sys
from src.adapters import brd_mcad as _implementation
sys.modules[__name__] = _implementation

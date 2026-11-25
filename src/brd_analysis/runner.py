"""Legacy identity for BRD application dispatch."""
import sys
from src.application import brd_runner as _implementation
sys.modules[__name__] = _implementation

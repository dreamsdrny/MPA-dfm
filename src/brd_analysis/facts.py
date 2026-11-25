"""Legacy identity for BRD evidence-derived domain facts."""
import sys
from src.domain import brd_facts as _implementation
sys.modules[__name__] = _implementation

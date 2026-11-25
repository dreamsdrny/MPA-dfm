"""Legacy identity for SKILL script generation."""
import sys
from src.adapters import allegro_skill as _implementation
sys.modules[__name__] = _implementation

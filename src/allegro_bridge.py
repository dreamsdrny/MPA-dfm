"""Legacy identity for the Allegro external-tool adapter."""
import sys
from src.adapters import allegro_bridge as _implementation
sys.modules[__name__] = _implementation

"""Legacy identity for pure pad geometry."""
import sys
from src.domain import pad_geometry as _implementation
sys.modules[__name__] = _implementation

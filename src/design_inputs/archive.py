"""Legacy archive import path."""
import sys
from src.infrastructure import archives as _implementation
sys.modules[__name__] = _implementation

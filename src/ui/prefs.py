"""Legacy preference identity; disk persistence belongs to Infrastructure."""
import sys
from src.infrastructure import preferences as _implementation
sys.modules[__name__] = _implementation

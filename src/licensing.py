"""Legacy identity for cryptographic authorization infrastructure."""
import sys
from src.infrastructure import licensing as _implementation
sys.modules[__name__] = _implementation

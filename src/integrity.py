"""Legacy identity for signatures, hashes and integrity infrastructure."""
import sys
from src.infrastructure import integrity as _implementation
sys.modules[__name__] = _implementation

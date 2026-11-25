"""Legacy identity for the BRD file adapter (preserves existing patch targets)."""
import sys
from src.adapters import brd_reader as _implementation
sys.modules[__name__] = _implementation

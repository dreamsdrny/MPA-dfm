"""Compatibility identity for pure BRD geometry."""
import sys
from src.domain import brd_geometry as _implementation
sys.modules[__name__] = _implementation

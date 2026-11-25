"""Compatibility identity for BRD capability/result domain contracts."""
import sys
from src.domain import brd_model as _implementation
sys.modules[__name__] = _implementation

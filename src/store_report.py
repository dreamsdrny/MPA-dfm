"""Legacy library report adapter import path."""
import sys
from src.adapters import store_report as _implementation
sys.modules[__name__] = _implementation

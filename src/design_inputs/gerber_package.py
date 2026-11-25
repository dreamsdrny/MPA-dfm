"""Legacy Gerber package adapter import path."""
import sys
from src.adapters import gerber_package as _implementation
sys.modules[__name__] = _implementation

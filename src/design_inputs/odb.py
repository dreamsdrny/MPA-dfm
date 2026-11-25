"""Legacy ODB++ reader import path."""
import sys
from src.adapters import odb_reader as _implementation
sys.modules[__name__] = _implementation

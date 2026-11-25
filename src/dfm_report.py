"""Legacy PCB report adapter import path."""
import sys
from src.adapters import pcb_report as _implementation
sys.modules[__name__] = _implementation

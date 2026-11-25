"""Legacy identity for pure evidence-to-PCB input mapping."""
import sys
from src.domain import pcb_inputs as _implementation
sys.modules[__name__] = _implementation

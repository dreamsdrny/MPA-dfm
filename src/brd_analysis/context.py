"""Legacy identity for application-owned BRD evidence context."""
import sys
from src.application import brd_context as _implementation
sys.modules[__name__] = _implementation

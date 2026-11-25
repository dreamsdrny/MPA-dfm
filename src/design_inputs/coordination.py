"""Legacy identity for manufacturing collaboration application workflow."""
import sys
from src.application import coordination_workflow as _implementation
sys.modules[__name__] = _implementation

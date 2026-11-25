"""Legacy identity for native manufacturing orchestration."""
import sys
from src.application import manufacturing_workflow as _implementation
sys.modules[__name__] = _implementation

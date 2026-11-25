"""Legacy identity for infrastructure resource budgets."""
import sys
from src.infrastructure import resource_limits as _implementation
sys.modules[__name__] = _implementation

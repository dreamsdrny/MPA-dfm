"""Legacy identity for pure pad metrics."""
import sys
from src.domain import pad_metrics as _implementation
sys.modules[__name__] = _implementation

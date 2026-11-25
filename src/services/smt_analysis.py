"""Compatibility identity for the SMT application workflow."""
import sys

from src.application import smt_workflow as _implementation

sys.modules[__name__] = _implementation

"""Compatibility identity for the PCB application workflow (including old mocks)."""
import sys

from src.application import pcb_workflow as _implementation

sys.modules[__name__] = _implementation

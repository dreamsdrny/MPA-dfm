"""UI-facing board version and import contracts; no widget dependencies."""
from src.application.pcb_analysis import import_brd
from src.adapters.readers import read_brd_design as extract_brd_design
from src.brd_input import (brd_signature, canonical_brd_path, content_sha256,
                           require_brd_signature, validate_brd_info)

__all__ = ["brd_signature", "canonical_brd_path", "content_sha256", "extract_brd_design",
           "import_brd", "require_brd_signature", "validate_brd_info"]

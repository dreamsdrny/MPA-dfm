"""Application request DTOs; contain plain values and no engine/UI dependencies."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PCBAnalysisRequest:
    brd_path: str
    design: dict
    board_type: str = "rigid"
    capability_level: str = "standard"
    factory: str = "hongban"
    design_unit: str = ""
    coordinate_dir: str = ""
    gerber_dir: str = ""
    geometry_data: str = ""
    odb_path: str = ""
    odb_step: str | None = None
    auto_gerber: bool = True
    auto_manufacturing: bool = True
    editor_path: str = ""
    odb_exporter: str = ""
    output_dir: str = ""
    timeout: int = 180

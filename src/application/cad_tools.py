"""Application-facing CAD commands, separated from UI callbacks."""
from src.adapters.allegro_trace import AllegroTraceSession, trace_number
from src.adapters.cad import AllegroSession, arrange_editor, discover_editor, point_mm

__all__ = ["AllegroSession", "AllegroTraceSession", "arrange_editor", "discover_editor", "point_mm", "trace_number"]


def discover_odb_exporter(configured="", editor_path=""):
    from src.manufacturing_export import discover_odb_exporter as discover
    return discover(configured, editor_path)


def write_bundle(folder):
    from src.allegro_skill import write_bundle as write
    return write(folder)


def export_brd_gerbers(path, output, editor):
    from src.gerber_export import export_brd_gerbers as export
    from src.infrastructure.snapshots import capture_sources, verify_sources
    sources = capture_sources([path])
    result = export(path, output, editor)
    verify_sources(sources)
    return result

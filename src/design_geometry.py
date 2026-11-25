"""Legacy geometry interface; file decoding and pure geometry are separated."""
from src.adapters.design_snapshot import _decode_snapshot as _decode
from src.infrastructure.resources import MAX_BRD_ENTRY_BYTES, read_bounded
from src.domain.design_geometry import *  # noqa: F403
from src.domain.design_geometry import _bbox, _entries, _poly_dist, _polyline, _seg_seg_dist  # noqa: F401
from src.domain.design_geometry import board_geometry as _geometry


def _decode_snapshot(raw):
    return _decode(raw, entry_limit=MAX_BRD_ENTRY_BYTES)


def load_design_snapshot(path):
    try:
        return _decode_snapshot(read_bounded(path))
    except OSError:
        return None


def board_geometry(brd_path, draw_extents_mil=None, *, snapshot=None):
    return _geometry(snapshot if snapshot is not None else load_design_snapshot(brd_path), draw_extents_mil)

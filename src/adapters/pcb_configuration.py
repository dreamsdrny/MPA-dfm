"""PCB rule configuration and provenance, separate from capability decisions."""
from pathlib import Path

from src.adapters.configuration import read_yaml
from src.domain.pcb_rules import DEFAULT_FACTORY, FACTORY_FILES, normalize_factory, normalize_level, select_capability
from src.infrastructure.snapshots import fingerprint


def configuration_root():
    import sys
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "config"


def _load_yaml(filename):
    from src.integrity import verify_config_if_initialized
    path = configuration_root() / filename
    verify_config_if_initialized(path)
    return read_yaml(path)


def capability_fingerprint(factory=DEFAULT_FACTORY):
    from src.integrity import verify_config_if_initialized
    path = configuration_root() / FACTORY_FILES[normalize_factory(factory)]
    verify_config_if_initialized(path)
    return fingerprint(path)["sha256"]


def load_capability_db(factory=DEFAULT_FACTORY):
    return _load_yaml(FACTORY_FILES[normalize_factory(factory)])


def load_capability(board_type="rigid", level="standard", factory=DEFAULT_FACTORY):
    return select_capability(load_capability_db(factory), board_type, level, factory)


def list_board_types(factory=DEFAULT_FACTORY):
    return list(load_capability_db(factory).get("board_types", {}))


def list_levels(board_type="rigid", factory=DEFAULT_FACTORY):
    return list(load_capability_db(factory).get("board_types", {}).get(board_type, {}).get("levels", []))


def level_label(board_type, level, factory=DEFAULT_FACTORY):
    return load_capability_db(factory).get("board_types", {}).get(board_type, {}).get("level_labels", {}).get(level, level)


def rule_metadata(factory, board_type, level, digest):
    db = load_capability_db(factory)
    board = db["board_types"][board_type]
    return {"sha256": digest, "revision": db.get("meta", {}).get("revision", "未提供"),
            "source": "config/" + FACTORY_FILES[normalize_factory(factory)],
            "board_type_label": board.get("label", board_type),
            "capability_label": board.get("level_labels", {}).get(normalize_level(level), normalize_level(level))}

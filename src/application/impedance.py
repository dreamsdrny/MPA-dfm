"""Native impedance plugin commands and validated result imports."""
import json
from pathlib import Path


def read_catalogue(folder):
    from src.impedance import read_catalogue as read
    return read(folder)


def read_results(folder):
    from src.impedance import read_results as read
    return read(folder)


def write_plugin(folder):
    from src.impedance_plugin import write_plugin as write
    return write(folder)


def write_action(folder, **options):
    from src.impedance_plugin import write_action as write
    return write(folder, **options)


def prepare_load_script(folder):
    from src.impedance_plugin import prepare_load_script as prepare
    return prepare(folder)


def load_script_text(path):
    import os
    from src.infrastructure.resources import read_bounded
    return read_bounded(path, 1024 * 1024).decode("mbcs" if os.name == "nt" else "utf-8")


def quote_skill(value):
    from src.impedance_plugin import quote_skill as quote
    return quote(value)


def session_token(folder):
    from src.infrastructure.resources import read_bounded
    return json.loads(read_bounded(Path(folder) / "session.json", 1024 * 1024).decode("utf-8"))["token"]

"""Legacy UI log persistence; errors never prevent rendering or startup."""
from pathlib import Path


def initialize(path):
    try:
        Path(path).write_text("", encoding="utf-8")
    except OSError:
        return None
    return str(path)


def append(path, text, tag=None):
    if not path:
        return
    try:
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(f"[{tag or 'info'}] {text}\n")
    except OSError:
        return

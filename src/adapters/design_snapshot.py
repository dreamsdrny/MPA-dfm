"""Bounded BRD archive snapshot reader; geometry decisions are in Domain."""
import io
import json
import zipfile

from src.infrastructure.resources import InputBudgetExceeded, MAX_BRD_ENTRY_BYTES, read_bounded


def load_design_snapshot(path):
    try:
        raw = read_bounded(path)
    except OSError:
        return None
    return _decode_snapshot(raw)


def _decode_snapshot(raw, entry_limit=None):
    entry_limit = MAX_BRD_ENTRY_BYTES if entry_limit is None else entry_limit
    index = raw.find(b"PK\x03\x04")
    if index < 0:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(raw[index:])) as archive:
            names = archive.namelist()
            name = next((n for n in names if n.lower().endswith(".json") and "design" in n.lower()), names[0] if names else "")
            if not name:
                return None
            if archive.getinfo(name).file_size > entry_limit:
                raise InputBudgetExceeded("BRD 几何快照超过解压预算")
            with archive.open(name) as stream:
                data = stream.read(entry_limit + 1)
        if len(data) > entry_limit:
            raise InputBudgetExceeded("BRD 几何快照超过解压预算")
        doc = json.loads(data.decode("utf-8", "replace"))
    except InputBudgetExceeded:
        raise
    except (zipfile.BadZipFile, IndexError, ValueError, OSError):
        return None
    return doc if isinstance(doc, dict) else None

"""Persistent structured acceptance evidence; no UI dependencies."""
import json
from pathlib import Path


def save_analysis_evidence(path, result):
    Path(path).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

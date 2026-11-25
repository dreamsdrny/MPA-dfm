"""Strict SMT configuration reader with input version and business validation."""
from copy import deepcopy
from pathlib import Path

from src.adapters.configuration import read_yaml
from src.domain.smt_rules import validate_profile
from src.infrastructure.snapshots import capture_sources, verify_sources


def load_smt_profile(path):
    sources = capture_sources([path])
    doc = validate_profile(deepcopy(read_yaml(path, limit=4 * 1024 * 1024)))
    verify_sources(sources)
    doc.update(path=str(Path(path).resolve()), sha256=sources[0]["sha256"])
    return doc


def verify_profile_document(doc):
    if doc and doc.get("sha256"):
        try:
            verify_sources([{"path": doc["path"], "sha256": doc["sha256"], "type": "file"}])
        except ValueError as exc:
            raise ValueError("SMT 能力配置已变化，请重新导入并分析：" + doc["path"]) from exc


def verify_profile_sources(result):
    for source in result.get("profile_sources", []):
        verify_profile_document(source)

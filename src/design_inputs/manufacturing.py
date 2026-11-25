"""制造资料适配器：ODB++ ASCII 子集与 STEP 文件头识别。"""
import re
from pathlib import Path

from .odb import parse_odb as parse_odb


def parse_step(path):
    text = Path(path).read_text(encoding="ascii", errors="strict")
    if not text.lstrip().upper().startswith("ISO-10303-21;"):
        raise ValueError("不是 STEP ISO-10303-21 文本文件")
    schema = re.search(r"FILE_SCHEMA\s*\(\s*\((.*?)\)\s*\)", text, re.I | re.S)
    entities = {}
    for name in re.findall(r"(?im)^\s*#\d+\s*=\s*([A-Z_0-9]+)\s*\(", text):
        entities[name.upper()] = entities.get(name.upper(), 0) + 1
    return {"status": "partial", "schema": re.findall(r"'([^']+)'", schema.group(1)) if schema else [],
            "entity_counts": entities, "mechanical_bodies": None, "height_mm": None,
            "assembly_space": None, "warnings": ["STEP 三维实体、变换和单位需要 CAD 内核解析；高度和装配空间尚不可用"]}

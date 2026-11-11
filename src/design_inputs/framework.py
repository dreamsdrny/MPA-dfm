"""可扩展的资料输入框架；补充文件与 BRD 各自保留，不自动融合坐标。"""
import hashlib
import os
from pathlib import Path

from .bom import import_bom
from .gerber import parse_gerber
from .manufacturing import parse_odb, parse_step

_ADAPTERS = {}


def register_adapter(kind, adapter):
    """注册 adapter(path) -> dict；显式指定 kind 时可覆盖内建实现。"""
    _ADAPTERS[kind.lower()] = adapter


def _source(path):
    p = Path(path)
    digest = None
    if p.is_file():
        h = hashlib.sha256()
        with p.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        digest = h.hexdigest()
    return {"path": str(p.resolve()), "sha256": digest, "type": "directory" if p.is_dir() else "file"}


def _brd(path):
    from src.brd_design import extract_brd_design
    from src.design_geometry import load_design_snapshot

    design = extract_brd_design(str(path))
    if not design.get("ok"):
        raise ValueError(design.get("error", "BRD 解析失败"))
    snapshot = load_design_snapshot(str(path))
    geometry = design.get("board_geometry", {"ok": False, "reason": "没有可解析的板框"})
    return {"status": "parsed", "design": design, "geometry": geometry,
            "snapshot": snapshot, "warnings": list(design.get("warnings", []))}


def _dsn(path):
    return {"status": "unsupported", "warnings": ["OrCAD DSN 是专有设计文件，请从 Capture 导出符合 BOM CSV 合同的文件。"],
            "bom": None}


def _bom(path):
    return {"status": "parsed", "rows": import_bom(path), "contract": "bom-csv-v1", "warnings": []}


for _kind, _adapter in (("gerber", parse_gerber), ("bom", _bom), ("dsn", _dsn),
                        ("odb", parse_odb), ("step", parse_step)):
    register_adapter(_kind, _adapter)


def _infer(path):
    p = Path(path)
    name = p.name.lower()
    if p.is_dir() or name.endswith((".tgz", ".tar.gz", ".tar", ".zip")):
        return "odb"
    ext = p.suffix.lower()
    if ext in (".step", ".stp"):
        return "step"
    if ext == ".dsn":
        return "dsn"
    if ext == ".csv":
        return "bom"
    if ext in (".gbr", ".ger", ".pho", ".art", ".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gtp", ".gbp", ".gko", ".gm1"):
        return "gerber"
    raise ValueError("无法识别资料格式，请显式指定类型: " + str(path))


def load_project(brd_path, supplements=()):
    """加载主 BRD；补充项可为路径或 (类型, 路径)，返回可 JSON 序列化的字典。"""
    source = _source(brd_path)
    primary = {"kind": "brd", "source": source, **_brd(brd_path)}
    result = {"schema_version": "1.0", "primary": primary, "supplements": []}
    for item in supplements:
        kind = "unknown"
        path = item[1] if isinstance(item, (tuple, list)) and len(item) == 2 else item
        try:
            kind, path = item if isinstance(item, (tuple, list)) else (_infer(item), item)
            kind = kind.lower()
            if kind not in _ADAPTERS:
                raise ValueError("未注册的资料类型: " + kind)
            src = _source(path)
            if not os.path.exists(path):
                raise FileNotFoundError(path)
            entry = {"kind": kind, "source": src, **_ADAPTERS[kind](path)}
        except (OSError, ValueError) as exc:
            entry = {"kind": kind, "source": {"path": str(path)},
                     "status": "error", "warnings": [str(exc)]}
        result["supplements"].append(entry)
    return result

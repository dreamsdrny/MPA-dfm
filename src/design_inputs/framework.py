"""可扩展的资料输入框架；补充文件与 BRD 各自保留，不自动融合坐标。"""
import hashlib
import os
from pathlib import Path

from .bom import read_bom
from .gerber import parse_gerber
from .manufacturing import parse_odb, parse_step
from .reconcile import brd_component_records, reconcile_bom
from .store import split_bom_rows

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

    raw = Path(path).read_bytes()
    design = extract_brd_design(str(path), raw=raw)
    if not design.get("ok"):
        raise ValueError(design.get("error", "BRD 解析失败"))
    snapshot = load_design_snapshot(str(path))
    geometry = design.get("board_geometry", {"ok": False, "reason": "没有可解析的板框"})
    component_view = brd_component_records(str(path), raw=raw)
    warnings = list(design.get("warnings", [])) + list(component_view.get("warnings") or [])
    return {"status": "parsed", "design": design, "geometry": geometry,
            "snapshot": snapshot, "component_records": component_view.get("records") or [],
            "components_available": bool(component_view.get("ok")),
            "warnings": warnings}


def _dsn(path):
    return {"status": "unsupported", "warnings": ["OrCAD DSN 是专有设计文件，请从 Capture 导出符合 BOM CSV 合同的文件。"],
            "bom": None}


def _bom(path):
    info = read_bom(path, strict=False)
    return {"status": "parsed", "rows": info["rows"], "contract": info["contract"],
            "format": info["format"], "sheet": info.get("sheet") or "",
            "header_row": info.get("header_row"),
            "columns": info.get("columns") or {}, "unmapped": info.get("unmapped") or [],
            "bom_stats": info.get("stats") or {},
            "part_numbers": info.get("part_numbers") or [],
            "warnings": list(info.get("warnings") or [])}


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
    if ext in (".xlsx", ".xlsm"):
        return "bom"
    if ext in (".gbr", ".ger", ".pho", ".art", ".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gtp", ".gbp", ".gko", ".gm1"):
        return "gerber"
    raise ValueError("无法识别资料格式，请显式指定类型: " + str(path))


def load_project(brd_path, supplements=()):
    """加载主 BRD；补充项可为路径或 (类型, 路径)，返回可 JSON 序列化的字典。"""
    source = _source(brd_path)
    primary = {"kind": "brd", "source": source, **_brd(brd_path)}
    result = {"schema_version": "1.1", "primary": primary, "supplements": []}
    for item in supplements:
        kind = "unknown"
        path = item
        try:
            if isinstance(item, (tuple, list)):
                if len(item) != 2:
                    raise ValueError("补充项必须为 (类型, 路径)")
                kind, path = item
            else:
                kind, path = _infer(item), item
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

    # 完整 BOM 与 BRD 位号清单逐项核对；补充资料仍是独立证据，不覆盖 BRD。
    # 非电气项 (PCB1/SCH1/装配图/钢网...) 不参与位号核对, 单独列出避免假"BOM 多出"。
    checks = []
    known_refs = [item.get("refdes") for item in primary.get("component_records") or []]
    for entry in result["supplements"]:
        if entry.get("kind") == "bom" and entry.get("status") == "parsed":
            electrical, others = split_bom_rows(entry.get("rows") or [], known_refs)
            entry["non_electrical"] = [
                {"refdes": row.get("refdes"), "part_number": row.get("part_number"),
                 "value": row.get("value")} for row in others]
            reconciliation = reconcile_bom(primary.get("component_records") or [],
                                           electrical)
            entry["reconciliation"] = reconciliation
            checks.append({"source": entry.get("source"), "reconciliation": reconciliation})
    result["bom_checks"] = checks
    return result

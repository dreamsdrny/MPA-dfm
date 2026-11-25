"""制造版图 + BOM 协同项目，不要求 BRD，不覆盖原始资料和冲突值。"""
import re
import tarfile
import zipfile
from copy import deepcopy
from pathlib import Path

from src.infrastructure.archives import InputContainer
from src.adapters.bom_reader import read_bom
from src.adapters.gerber_package import parse_gerber_package
from src.adapters.odb_reader import parse_odb
from src.adapters.placement_reader import read_placements
from src.domain.bom_rules import _norm_ref
from src.domain.manufacturing_rules import merge_placements as _merge_placements, reconcile_layout as _reconcile
from src.design_inputs.store import split_bom_rows
from src.infrastructure.snapshots import fingerprint as source_fingerprint


def infer_manufacturing_kind(path):
    path = Path(path)
    if path.is_file() and not (zipfile.is_zipfile(path) or tarfile.is_tarfile(path)):
        return "gerber"
    with InputContainer(path) as container:
        return "odb" if any(name.lower().endswith(("/matrix/matrix", "/matrix/matrix.gz"))
                            or name.lower() in ("matrix/matrix", "matrix/matrix.gz")
                            for name in container.names) else "gerber"


def load_manufacturing_project(layout_path, bom_path="", kind="auto", placement_path="",
                               step=None, alignment_confirmed=False, object_checks_only=False):
    if kind not in ("auto", "gerber", "odb"):
        raise ValueError("kind 必须为 auto/gerber/odb")
    if not isinstance(alignment_confirmed, bool):
        raise ValueError("alignment_confirmed 必须为布尔值，不接受文本真假值")
    paths = [layout_path, *([bom_path] if bom_path else []), *([placement_path] if placement_path else [])]
    before = [source_fingerprint(path) for path in paths]
    kind = infer_manufacturing_kind(layout_path) if kind == "auto" else kind
    layout = parse_odb(layout_path, step=step, object_checks_only=object_checks_only) if kind == "odb" else parse_gerber_package(layout_path)
    warnings = list(layout.get("warnings") or [])
    records = deepcopy(layout.get("component_records") or [])
    placements = read_placements(placement_path) if placement_path else None
    if placements and layout.get("status") != "needs_step":
        if kind == "odb":
            refs = {_norm_ref(record["refdes"]) for record in records}
            extra_refs = [record["refdes"] for record in placements["records"] if _norm_ref(record["refdes"]) not in refs]
            if extra_refs:
                warnings.append("坐标 CSV 与 ODB++ 位号不一致，CSV 多出: " + ", ".join(extra_refs[:20]))
        records = _merge_placements(records, placements["records"], alignment_confirmed, warnings)
        if not alignment_confirmed:
            warnings.append("坐标 CSV 到制造图层的原点/镜像/旋转未确认，允许位号关联但禁止视作已对齐空间证据")
    bom = read_bom(bom_path, strict=False) if bom_path else None
    non_electrical, reconciliation = [], None
    if bom and layout.get("status") != "needs_step":
        for row in bom["rows"]:
            row["dnp_known"] = "dnp" in bom["columns"].values()
        electrical, others = split_bom_rows(bom["rows"], [r["refdes"] for r in records])
        non_electrical = others
        reconciliation = _reconcile(records, electrical)
        warnings.extend(bom["warnings"])
        if bom["warnings"] and reconciliation["status"] == "verified":
            reconciliation.update(status="review", consistent=False)
        warnings.extend(reconciliation["warnings"])
    components = []
    by_ref = {}
    for record in records:
        by_ref.setdefault(_norm_ref(record["refdes"]), []).append(record)
    if reconciliation:
        for row in reconciliation["rows"]:
            evidence = by_ref.get(_norm_ref(row["refdes"]), [])
            component = dict(refdes=row["refdes"], status=row["status"], layout=row.get("layout"),
                             bom=row.get("bom"), layout_evidence=evidence,
                             side=(row.get("layout") or {}).get("side") or row.get("side"),
                             dnp=(row.get("bom") or {}).get("dnp"),
                             part_number=(row.get("bom") or {}).get("part_number", ""),
                             manufacturer_part_number=(row.get("bom") or {}).get("manufacturer_part_number", ""),
                             value=(row.get("bom") or {}).get("value", ""), package=row.get("package", ""))
            if len(evidence) == 1:
                component.update({key: evidence[0].get(key) for key in
                                  ("x_mm", "y_mm", "rotation", "coordinate_kind", "position_source", "pins")})
                component["position_source"] = component.get("position_source") or evidence[0].get("source")
                component["spatial_verified"] = evidence[0].get("coordinate_kind") == "component_origin" and (
                    evidence[0].get("alignment_confirmed", kind == "odb"))
            components.append(component)
    else:
        components = [dict(record, layout=record, bom=None, status="no_bom",
                           position_source=record.get("position_source") or record.get("source"),
                           spatial_verified=record.get("coordinate_kind") == "component_origin" and
                           record.get("alignment_confirmed", kind == "odb")) for record in records]
    after = [source_fingerprint(path) for path in paths]
    if before != after:
        raise ValueError("导入期间资料内容变化，已丢弃结果，请重新导入")
    coordination_warning = any("STEP-REPEAT" in w or re.search(r"行\d+:", w) or "不一致" in w or "未覆盖" in w
                               or "Gerber 导入失败" in w or "多文件" in w for w in warnings)
    if (coordination_warning or layout.get("component_complete") is False) and reconciliation and reconciliation["status"] == "verified":
        reconciliation.update(status="review", consistent=False)
    return dict(schema_version="manufacturing-project-v1", kind=kind, sources=before, layout=layout,
                bom=bom, placements=placements, components=components, non_electrical=non_electrical,
                reconciliation=reconciliation, warnings=list(dict.fromkeys(warnings)),
                status="needs_step" if layout.get("status") == "needs_step" else
                reconciliation["status"] if reconciliation else "no_bom", releasable=False)

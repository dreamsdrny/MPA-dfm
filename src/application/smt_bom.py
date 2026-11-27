"""BOM identity checks run in every SMT mode, including supplied object JSON."""
from collections import defaultdict

from src.adapters.bom_reader import read_bom
from src.infrastructure.snapshots import capture_sources, verify_sources


def inspect_bom(path):
    sources = capture_sources([path])
    bom = read_bom(path, strict=True)
    verify_sources(sources)
    return dict(bom, input_fingerprints=sources)


def compare_bom(bom, design, data=None):
    data = data or {}
    native = design.get("_native_payload") or {}
    cad = native.get("components") or data.get("components") or []
    if not cad:
        components = design.get("_components") or {}
        if isinstance(components, dict):
            cad = [dict(refdes=ref) for ref in components.get("refdes", [])]
        elif isinstance(components, list):
            cad = [dict(row, refdes=row.get("refdes") or row.get("ref")) for row in components if isinstance(row, dict)]
    cad_by_ref = {row["refdes"].upper(): row for row in cad if row.get("refdes")}
    for prop in (native.get("objects") or {}).get("properties",[]):
        ref=str(prop.get("refdes", "")).upper()
        if ref in cad_by_ref:
            cad_by_ref[ref]=dict(cad_by_ref[ref],part_number=prop.get("part_number") or cad_by_ref[ref].get("part_number", ""))
    bom_by_ref = {row["refdes"].upper(): row for row in bom["rows"]}
    findings, association = [], []
    def finding(rule, reason, ref="", status="review"):
        obj = cad_by_ref.get(ref.upper(), {})
        findings.append(dict(rule=rule, label=rule, reason=reason, refdes=ref, status=status,
            severity="high" if status == "fail" else "medium", side=obj.get("side", ""),
            x_mm=obj.get("x_mm"), y_mm=obj.get("y_mm"), source=bom["input_fingerprints"][0]["path"]))
    if not cad_by_ref:
        finding("BOM/CAD 位号核对", "未取得可靠 CAD 位号清单，BOM 已读入但无法确认对应关系")
    for ref, row in bom_by_ref.items():
        obj = cad_by_ref.get(ref)
        errors = []
        if obj is None:
            errors.append("BOM 位号不在 CAD 清单；需核对装配版本/机械或文档对象")
        else:
            for field, label in (("footprint", "封装"), ("side", "面别"), ("part_number", "料号")):
                if row.get(field) and obj.get(field) and str(row[field]).strip().upper() != str(obj[field]).strip().upper():
                    errors.append(label + "与 CAD 冲突")
            if row.get("dnp_explicit") is not True:
                errors.append("DNP 未明确，不能默认贴装状态")
        for error in errors:
            finding("BOM/CAD 一致性", error, ref, "review" if obj is None or "DNP" in error else "fail")
        association.append(dict(refdes=row["refdes"], side=(obj or {}).get("side", row.get("side", "")),
            footprint=row.get("footprint"), part_number=row.get("part_number"),
            dnp=row.get("dnp") if row.get("dnp_explicit") else None,
            status="conflict" if errors else "dnp" if row.get("dnp") is True else "matched", issues=errors,
            sourcing_role=row.get("sourcing_role", ""), manufacturer_part_number=row.get("manufacturer_part_number", ""),
            source_row=row.get("source_row"),sourcing_marker=row.get("sourcing_marker"),sourcing_marker_column=row.get("sourcing_marker_column"),alternatives=row.get("alternatives", [])))
    for ref in sorted(set(cad_by_ref) - set(bom_by_ref)):
        finding("CAD 位号未列入 BOM", "需核对 DNP/非贴装对象，不自动删除器件", ref)
        obj=cad_by_ref[ref]
        association.append(dict(refdes=obj["refdes"], side=obj.get("side", ""), footprint=obj.get("footprint", ""),
            part_number=obj.get("part_number", ""), dnp=None, status="cad_only", issues=["CAD 位号未列入 BOM"]))
    packages = defaultdict(set)
    refs = defaultdict(list)
    for row in bom["rows"]:
        if row.get("part_number") and row.get("footprint"):
            key = row["part_number"].strip().upper()
            packages[key].add(row["footprint"].strip().upper())
            refs[key].append(row["refdes"])
    for key, footprints in packages.items():
        if len(footprints) > 1:
            finding("同料号多封装", key + " 对应多个封装：" + ", ".join(sorted(footprints)) + "；需核对一码一物", refs[key][0], "fail")
    cad_packages=defaultdict(set)
    cad_refs=defaultdict(list)
    for ref,obj in cad_by_ref.items():
        pn=obj.get("part_number") or bom_by_ref.get(ref,{}).get("part_number")
        if pn and obj.get("footprint"):
            key=pn.strip().upper()
            cad_packages[key].add(obj["footprint"].strip().upper())
            cad_refs[key].append(ref)
    for key,footprints in cad_packages.items():
        if len(footprints)>1:
            finding("CAD 同料号多封装",key+" 在CAD对应多个封装："+", ".join(sorted(footprints))+"；BOM封装为空时仍需检查",cad_refs[key][0],"fail")
    by_footprint=defaultdict(set)
    for row in bom["rows"]:
        if row.get("part_number") and row.get("footprint"):
            by_footprint[row["footprint"].strip().upper()].add(row["part_number"].strip().upper())
    for footprint, numbers in by_footprint.items():
        if len(numbers)>1:
            finding("同封装多料号复核",footprint+" 对应多个料号；可为正常共用封装，不自动判一码一物失败",status="review")
    for warning in bom.get("warnings", []):
        finding("BOM 数据质量", warning)
    return dict(path=bom["input_fingerprints"][0]["path"], rows=association, findings=findings,
                alternate_records=sum(len(r.get("alternatives",[])) for r in association),
                total=len(bom["rows"]), matched=sum(r["status"] == "matched" for r in association),
                dnp=sum(r["status"] == "dnp" for r in association), cad_total=len(cad_by_ref),
                source_sha256=bom["input_fingerprints"][0]["sha256"])

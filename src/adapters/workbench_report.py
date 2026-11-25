"""与图形界面（GUI）无关的报告 ：支持 PCB、SMT 或两者合并的 DFM 工作簿。
“快照字典”（Snapshot dictionaries）作为未来 WPF/PySide 前端与后端之间的边界。
报告生成过程分阶段进行；若发生错误，绝不会留下未写完整的 XLSX 文件。
"""
import json
from copy import deepcopy
import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from shutil import copyfileobj as _copy_report_bytes

from openpyxl.styles import Alignment, Font, PatternFill

from src.dfm_report import build_dfm_workbook, generate_dfm_text
from src.smt_engine import STATUSES, append_smt_sheets, export_smt_report
from src.pcb_locations import LOCATION_HEADERS, location_values
from src.excel_layout import format_workbook
from src.csv_safety import SafeCSVWriter


from src.domain.combined_verdict import _pcb_verdict_code, combined_verdict  # noqa: F401 legacy exports


REPORT_LABELS = {"dfm": "DFM综合报告", "pcb": "PCB报告", "smt": "SMT报告"}


def default_report_name(brd_path, kind, ext=".xlsx"):
    """报告文件名自动跟随 BRD 文件名: <板名>_<报告类型>.<扩展名>。

    返回 (默认文件名, 默认目录)；未导入 BRD 时退回通用名。
    保存对话框仍允许用户改名/换目录，覆盖确认由对话框负责。
    """
    label = REPORT_LABELS[kind]
    if brd_path:
        source = Path(brd_path)
        return f"{source.stem}_{label}{ext}", str(source.parent)
    return f"{label}{ext}", ""


def _table(ws, columns, rows):
    ws.append(columns)
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="24486A")
    for column in ws.columns:
        ws.column_dimensions[column[0].column_letter].width = 26
    for row in ws:
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if isinstance(cell.value, str):
                cell.data_type = "s"


def build_combined_workbook(pcb, smt, board_meta=None):
    if not pcb or not smt:
        raise ValueError("DFM 综合报告需要本次 PCB 和 SMT 两份分析结果")
    for field in ("input_brd",):
        if pcb.get(field) and smt.get(field) and os.path.normcase(os.path.abspath(pcb[field])) != os.path.normcase(os.path.abspath(smt[field])):
            raise ValueError("PCB 与 SMT 结果来自不同 BRD，不能合并")
    board_meta = board_meta or {}
    wb = build_dfm_workbook(pcb, board_meta)
    old_names = {ws.title: "PCB_" + ws.title for ws in wb}
    for ws in wb:
        ws.title = old_names[ws.title]
        for row in ws:
            for cell in row:
                if cell.hyperlink and cell.hyperlink.target:
                    for old, new in old_names.items():
                        if cell.hyperlink.target == f"#'{old}'!A1":
                            cell.hyperlink.target = f"#'{new}'!A1"
                            break
    append_smt_sheets(wb, smt, prefix="SMT_")
    overview = wb.create_sheet("DFM总览", 0)
    _table(overview, ["项目", "内容"], [
        ["报告", "PCB + SMT 综合 DFM 报告"],
        ["板名", board_meta.get("board_name", "")],
        ["BRD来源", board_meta.get("brd_path", "")],
        ["生成时间", datetime.now().isoformat(timespec="seconds")],
        ["PCB分析时间", pcb.get("analyzed_at", "")],
        ["SMT分析时间", smt.get("analyzed_at", "")],
        ["综合结论", combined_verdict(pcb, smt)],
        ["PCB厂", pcb.get("factory_label", "")],
        ["PCB等级", pcb.get("capability_label", "")],
        ["PCB规则文件", pcb.get("rule_source", "")],
        ["PCB规则版本", pcb.get("rule_revision", "")],
        ["PCB规则SHA-256", pcb.get("rule_sha256", "")],
        ["PCB结论", pcb.get("verdict", "")],
        ["PCB判定依据", "；".join(pcb.get("verdict_reasons") or []) or "无不符合项/缺数据"],
        ["PCB输入覆盖率", pcb.get("coverage", {}).get("ratio", 0)],
        ["Gerber目录", pcb.get("gerber_folder", "")],
        ["几何补充资料", pcb.get("geometry_data_source", "")],
        ["SMT厂", smt["meta"]["factory"]],
        ["SMT产线/工艺", smt["profile_label"]],
        ["SMT配置版本", smt["meta"]["revision"]],
        ["SMT配置文件", smt["config_path"]],
        ["SMT结论", smt["verdict"]],
        ["SMT判定覆盖率", smt["coverage"]],
        ["结论口径", "综合结果基于当前输入快照；不以通过项平均抵消失败或缺失数据。"],
        ["工艺管控口径", smt["conclusion_note"]],
    ])
    overview.column_dimensions["B"].width = 100
    if pcb.get("report_filter_note") or smt.get("report_filter_note"):
        overview.append(["报告筛选说明", pcb.get("report_filter_note", "") + "；" + smt.get("report_filter_note", "")])
    rows = []
    for it in pcb.get("issues", []):
        rows.append(["PCB", it.get("code"), it.get("rule"), it.get("severity"),
                     it.get("status"), it.get("measured"), it.get("required"), it.get("unit"),
                     it.get("description"), it.get("suggestion"), it.get("note")])
    for it in smt["items"]:
        if it["status"] not in ("pass", "not_applicable"):
            rows.append(["SMT", it["id"], it["label"], it["severity"], STATUSES[it["status"]],
                         it["actual"], it["requirement"], it["unit"], it["reason"],
                         it["advice"], it["source"] or "未提供"])
    _table(wb.create_sheet("DFM待办汇总", 1),
           ["范围", "编号", "项目", "优先级", "状态", "输入值", "要求", "单位",
            "说明", "改善建议", "来源/备注"], rows)
    capability_rows = [[it.get(k) for k in ("category", "item", "design", "capability", "unit",
                                             "status", "message", "source", "source_detail", "note")]
                       for it in pcb.get("items", [])]
    _table(wb.create_sheet("PCB_逐项能力"),
           ["类别", "检查项", "输入值", "能力", "单位", "状态", "依据", "来源", "证据出处", "备注"],
           capability_rows)
    # All imported strings are literal data, also in the reused PCB worksheets.
    for ws in wb:
        for row in ws:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
    wb.active = 0
    return wb


def _verify_report_sources(pcb, smt):
    if pcb and pcb.get("manufacturing_export"):
        from src.manufacturing_export import verify_export_manifest
        verify_export_manifest(pcb["manufacturing_export"])
    if pcb:
        from src.services.pcb_analysis import verify_analysis_sources
        verify_analysis_sources(pcb)
    if smt:
        from src.smt_engine import verify_profile_sources
        verify_profile_sources(smt)
        if smt.get("object_evidence_path"):
            from src.smt_geometry import verify_evidence_fingerprints
            verify_evidence_fingerprints(smt.get("evidence_fingerprints"))
    if pcb and pcb.get("odb_object_result"):
        from src.design_inputs.coordination import source_fingerprint
        from src.pcb_geometry_data import file_sha256
        objects = pcb["odb_object_result"]
        if source_fingerprint(objects["input"]["path"]) != objects["input"]:
            raise ValueError("ODB++ 原始资料已变化，请重新分析，不导出过期对象结果")
        if file_sha256(objects["rule_file"]) != objects["rule_sha256"]:
            raise ValueError("对象规则 YAML 已变化，请重新分析")
    if smt and smt.get("input_brd_sha256"):
        from src.brd_input import content_sha256
        if content_sha256(smt["input_brd"]) != smt["input_brd_sha256"]:
            raise ValueError("SMT 对应的 BRD 已变化，请重新分析")


def export_report(kind, path, pcb=None, smt=None, board_meta=None):
    if kind not in ("dfm", "pcb", "smt"):
        raise ValueError(f"未知报告类型: {kind}")
    if kind in ("dfm", "pcb") and not pcb:
        raise ValueError("请先完成 PCB 分析")
    if kind in ("dfm", "smt") and not smt:
        raise ValueError("请先完成 SMT 分析")
    _verify_report_sources(pcb, smt)
    target = Path(path).resolve()
    if target.suffix.lower() not in (".xlsx", ".csv"):
        raise ValueError("报告文件请使用 .xlsx 或 .csv 扩展名")
    if kind == "dfm":
        _validate_brd(pcb, smt)
        pcb_hash = pcb.get("input_brd_sha256")
        smt_hash = smt.get("input_brd_sha256")
        if pcb_hash and smt_hash and pcb_hash != smt_hash:
            raise ValueError("PCB 与 SMT 结果来自不同版本的 BRD，不能合并")
    board_meta = board_meta or {}
    if board_meta.get("hide_low_priority"):
        pcb, smt = filter_report_priority(pcb, smt)
    if target.suffix.lower() == ".csv":
        suffixes = (".csv",)
    else:
        suffixes = {"smt": (".xlsx", ".json"), "pcb": (".xlsx", ".txt"),
                    "dfm": (".xlsx", ".txt", ".json")}[kind]
    destinations = [target.with_suffix(suffix) for suffix in suffixes]
    protected = {Path(value).resolve() for value in (board_meta.get("brd_path"),
                 (pcb or {}).get("input_brd"), (smt or {}).get("input_brd")) if value}
    protected.update(Path(source["path"]).resolve() for source in (smt or {}).get("profile_sources", [])
                     if source.get("path"))
    protected.update(Path(source["path"]).resolve() for source in
                     ((smt or {}).get("evidence_fingerprints") or {}).get("files", [])
                     if source.get("path"))
    protected.update(Path(source["path"]).resolve() for source in (pcb or {}).get("input_fingerprints", [])
                     if source.get("path") and not source.get("missing"))
    protected.update(Path(source["path"]).resolve() for source in (pcb or {}).get("analysis_fingerprints", [])
                     if source.get("path"))
    if any(dest in protected or any(source.is_dir() and dest.is_relative_to(source) for source in protected)
           for dest in destinations):
        raise ValueError("报告输出不能覆盖源 BRD 或制造/补充资料，请选择独立目录")
    conflicts = [str(dest) for dest in destinations if dest.exists()]
    if conflicts:
        raise FileExistsError("报告或附属文件已存在，未覆盖；请选新文件名或空目录：" + "、".join(conflicts))
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".dfm-report-", dir=target.parent) as tmp:
        staged = Path(tmp) / target.name
        if target.suffix.lower() == ".csv":
            _write_csv(staged, kind, pcb, smt, board_meta)
            suffixes = (".csv",)
        elif kind == "smt":
            export_smt_report(smt, staged)
            from openpyxl import load_workbook
            workbook=load_workbook(staged)
            format_workbook(workbook).save(staged)
            workbook.close()
            suffixes = (".xlsx", ".json")
        elif kind == "pcb":
            format_workbook(build_dfm_workbook(pcb, board_meta)).save(staged)
            generate_dfm_text(pcb, str(staged.with_suffix(".txt")), board_meta)
            suffixes = (".xlsx", ".txt")
        else:
            format_workbook(build_combined_workbook(pcb, smt, board_meta)).save(staged)
            pcb_text = Path(tmp) / "pcb.txt"
            generate_dfm_text(pcb, str(pcb_text), board_meta)
            text = ["DFM 综合报告（PCB + SMT）", f"综合结论：{combined_verdict(pcb, smt)}",
                    "\n=== PCB 制造能力分析 ===", pcb_text.read_text(encoding="utf-8"),
                    "\n=== SMT 工艺能力分析 ===",
                    f"厂商：{smt['meta']['factory']} / {smt['profile_label']}",
                    f"版本：{smt['meta']['revision']} / {smt['config_path']}",
                    f"结论：{smt['verdict']}；覆盖率：{smt['coverage']:.0%}", smt["conclusion_note"]]
            for it in smt["items"]:
                text.append(f"[{STATUSES[it['status']]}] {it['id']} {it['label']}："
                            f"{it['actual']} {it['unit']} / 能力 {it['requirement']}；"
                             f"来源：{it['source'] or '未提供'}；{it['reason']}；建议：{it['advice']}")
            for it in smt.get("object_findings", []):
                text.append(f"[对象 {STATUSES.get(it.get('status'), it.get('status'))}] {it.get('id', '')} "
                            f"{it.get('refdes', '')}/{it.get('other_refdes', '')} {it.get('side', '')} "
                            f"X={it.get('x_mm', '')} Y={it.get('y_mm', '')} "
                            f"实测={it.get('actual', '')} 阈值={it.get('applied_limit', it.get('requirement', ''))}；"
                            f"{it.get('reason', '')}；来源：{it.get('source', '')}")
            staged.with_suffix(".txt").write_text("\n".join(text), encoding="utf-8")
            payload = dict(report_type="DFM", board=board_meta, verdict=combined_verdict(pcb, smt),
                           pcb=pcb, smt=smt)
            staged.with_suffix(".json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            suffixes = (".xlsx", ".txt", ".json")
        outputs = []
        conflicts = [str(dest) for dest in destinations if dest.exists()]
        if conflicts:
            raise FileExistsError("报告或附属文件已存在，未覆盖；请选新文件名或空目录：" + "、".join(conflicts))
        _verify_report_sources(pcb, smt)
        # Use exclusive creation rather than check-then-os.replace: another
        # process can create a target after the conflict check. Keep inode
        # identity for rollback so an unrelated replacement is never removed.
        from src.infrastructure.publication import publish_files
        outputs = publish_files([(staged.with_suffix(suffix), target.with_suffix(suffix))
                                 for suffix in (*suffixes[1:], suffixes[0])], copier=_copy_report_bytes)
    return outputs


def filter_report_priority(pcb, smt):
    """Presentation filter only: verdict, coverage and underlying counts remain unchanged."""
    pcb, smt = deepcopy(pcb), deepcopy(smt)
    low = {"低", "low", "LOW", "info"}
    if pcb:
        issues = pcb.get("issues", [])
        hidden = [i for i in issues if i.get("severity") in low]
        hidden_rules = {i.get("rule") for i in hidden}
        retained_rules = {i.get("rule") for i in issues if i.get("severity") not in low}
        pcb["issues"] = [i for i in issues if i.get("severity") not in low]
        pcb["items"] = [i for i in pcb.get("items", []) if i.get("status") != "info" and
                        i.get("severity") not in low and (i.get("item") not in hidden_rules or i.get("item") in retained_rules)]
        pcb["problem_locations"] = [i for i in pcb.get("problem_locations", []) if
            i.get("severity") not in low and (i.get("item") not in hidden_rules or i.get("item") in retained_rules)]
        pcb["report_filter_note"] = f"报告隐藏低优先级 {len(hidden)} 条问题；原始分析结论/错误计数不变"
        summary = dict(pcb.get("issue_summary", {}))
        summary["低"] = 0
        summary["总计"] = len(pcb["issues"])
        pcb["issue_summary"] = summary
    if smt:
        smt["items"] = [i for i in smt.get("items", []) if i.get("severity") not in low]
        smt["report_filter_note"] = "已隐藏低优先级项目；结论/覆盖率仍引用完整分析"
    return pcb, smt


def _validate_brd(pcb, smt):
    if pcb.get("input_brd") and smt.get("input_brd") and os.path.normcase(
            os.path.abspath(pcb["input_brd"])) != os.path.normcase(os.path.abspath(smt["input_brd"])):
        raise ValueError("PCB 与 SMT 结果来自不同 BRD，不能合并")


def _write_csv(path, kind, pcb, smt, board_meta):
    """One rectangular UTF-8-BOM table, including every check and every problem point."""
    headers = ["记录类型", "范围", "板名", "BRD来源", "厂商", "等级/产线", "分析时间"] + LOCATION_HEADERS
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = SafeCSVWriter(handle)
        writer.writerow(headers)

        def write(record_type, scope, result, row):
            meta = [record_type, scope, board_meta.get("board_name", ""), result.get("input_brd", ""),
                    result.get("factory_label", "") if scope == "PCB" else result.get("meta", {}).get("factory", ""),
                    result.get("capability_label", "") if scope == "PCB" else result.get("profile_label", ""),
                    result.get("analyzed_at", "")]
            writer.writerow(meta + location_values(row))

        if kind in ("pcb", "dfm"):
            write("分析结论", "PCB", pcb, dict(status=pcb.get("verdict", ""),
                  note=f"覆盖率 {pcb.get('coverage', {}).get('ratio', 0):.0%}；可放行 {bool(pcb.get('releasable'))}；"
                       f"判定依据：{'；'.join(pcb.get('verdict_reasons') or []) or '无不符合项/缺数据'}"))
            write("PCB规则", "PCB", pcb, dict(source=pcb.get("rule_source", ""),
                  note=f"版本：{pcb.get('rule_revision', '未提供')}；SHA-256：{pcb.get('rule_sha256', '')}"))
            if pcb.get("manufacturing_export"):
                for stage_kind, stage in pcb["manufacturing_export"]["stages"].items():
                    write("原生导出与校验", "PCB", pcb, dict(item=stage_kind, status=stage["status"],
                          source=pcb["manufacturing_export"]["manifest_path"],
                          note=f"产物 {len(stage['files'])}；源SHA256 {stage['source_brd_sha256']}；"
                               + (stage.get("error") or stage.get("note", ""))))
            for item in pcb.get("items", []):
                write("能力比对", "PCB", pcb, dict(item=item.get("item"), field=item.get("field"),
                      status=item.get("status"), measured=item.get("design"), capability=item.get("capability"),
                      unit=item.get("unit"), source=item.get("source"),
                      note=item.get("message", "") + (f"；证据出处：{item['source_detail']}"
                                                      if item.get("source_detail") else "")))
            for row in pcb.get("problem_locations", []):
                write("问题坐标", "PCB", pcb, row)
            for row in pcb.get("component_coordinates", []):
                write("器件坐标", "PCB", pcb, row)
            write("坐标说明", "PCB", pcb, dict(note=pcb.get("coordinate_note", "未提供对象级坐标来源")))
            if pcb.get("report_filter_note"):
                write("报告筛选", "PCB", pcb, dict(note=pcb["report_filter_note"]))
            for error in pcb.get("gerber_errors", []):
                write("Gerber缺失/解析", "PCB", pcb, dict(note=error))
            for row in pcb.get("geometry_coverage", []):
                write("几何覆盖与缺数据", "PCB", pcb, dict(item=row.get("rule"), layer=row.get("layer"),
                      status=row.get("status"), source=pcb.get("geometry_data_source", ""),
                      note=row.get("note", "") + "；需补充：" + row.get("required_data", "")))
        if kind in ("smt", "dfm"):
            write("分析结论", "SMT", smt, dict(status=smt.get("verdict", ""),
                  note=smt.get("conclusion_note", "")))
            if smt.get("report_filter_note"):
                write("报告筛选", "SMT", smt, dict(note=smt["report_filter_note"]))
            for item in smt.get("items", []):
                write("能力比对", "SMT", smt, dict(item=item.get("label"), field=item.get("field", item.get("id")),
                      status=STATUSES.get(item.get("status"), item.get("status")), measured=item.get("actual"),
                      capability=item.get("requirement"), unit=item.get("unit"), source=item.get("source"),
                       note=(item.get("reason", "") + "；" + item.get("advice", ""))))
            for item in smt.get("object_findings", []):
                write("对象问题", "SMT", smt, dict(item=item.get("label"), field=item.get("field", item.get("id")),
                      status=STATUSES.get(item.get("status"), item.get("status")), measured=item.get("actual"),
                      capability=item.get("applied_limit", item.get("requirement")), unit=item.get("unit"),
                      x_mm=item.get("x_mm"), y_mm=item.get("y_mm"), layer=item.get("side"),
                      refdes=item.get("refdes"), source=item.get("source"),
                      note=item.get("reason", "") + "；相邻位号：" + item.get("other_refdes", "")))

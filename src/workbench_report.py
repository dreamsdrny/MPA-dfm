"""GUI-independent report API: PCB, SMT, or a single combined DFM workbook.

Snapshot dictionaries are the boundary for a future WPF/PySide frontend.
Generation is staged first; an error cannot leave a half-written XLSX behind.
"""
import json
import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl.styles import Alignment, Font, PatternFill

from src.dfm_report import build_dfm_workbook, generate_dfm_text
from src.smt_engine import STATUSES, append_smt_sheets, export_smt_report


def combined_verdict(pcb, smt):
    if (pcb.get("error_count", 0) or any(it.get("status") == "fail" for it in pcb.get("items", []))
            or smt.get("counts", {}).get("fail", 0)
            or any(it.get("status") == "fail" for it in smt.get("items", []))):
        return "存在不符合项"
    if (not pcb.get("releasable") or smt.get("coverage", 0) < 1
            or not smt.get("items")):
        return "数据或能力不完整，需复核"
    if pcb.get("warning_count", 0) or pcb.get("issues"):
        return "能力匹配，存在需复核项目"
    return "本次所选 PCB / SMT 能力项匹配"


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
        ["PCB结论", pcb.get("verdict", "")],
        ["PCB输入覆盖率", pcb.get("coverage", {}).get("ratio", 0)],
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
    rows = []
    for it in pcb.get("issues", []):
        rows.append(["PCB", it.get("code"), it.get("rule"), it.get("severity"),
                     it.get("status"), it.get("measured"), it.get("required"), it.get("unit"),
                     it.get("description"), it.get("suggestion"), it.get("note")])
    for it in smt["items"]:
        if it["status"] != "pass":
            rows.append(["SMT", it["id"], it["label"], it["severity"], STATUSES[it["status"]],
                         it["actual"], it["requirement"], it["unit"], it["reason"],
                         it["advice"], it["source"] or "未提供"])
    _table(wb.create_sheet("DFM待办汇总", 1),
           ["范围", "编号", "项目", "优先级", "状态", "输入值", "要求", "单位",
            "说明", "改善建议", "来源/备注"], rows)
    capability_rows = [[it.get(k) for k in ("category", "item", "design", "capability", "unit",
                                             "status", "message", "source", "note")]
                       for it in pcb.get("items", [])]
    _table(wb.create_sheet("PCB_逐项能力"),
           ["类别", "检查项", "输入值", "能力", "单位", "状态", "依据", "来源", "备注"],
           capability_rows)
    # All imported strings are literal data, also in the reused PCB worksheets.
    for ws in wb:
        for row in ws:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
    wb.active = 0
    return wb


def export_report(kind, path, pcb=None, smt=None, board_meta=None):
    if kind not in ("dfm", "pcb", "smt"):
        raise ValueError(f"未知报告类型: {kind}")
    if kind in ("dfm", "pcb") and not pcb:
        raise ValueError("请先完成 PCB 分析")
    if kind in ("dfm", "smt") and not smt:
        raise ValueError("请先完成 SMT 分析")
    target = Path(path).resolve()
    if target.suffix.lower() != ".xlsx":
        raise ValueError("报告文件请使用 .xlsx 扩展名")
    board_meta = board_meta or {}
    with TemporaryDirectory(prefix=".dfm-report-", dir=target.parent) as tmp:
        staged = Path(tmp) / target.name
        if kind == "smt":
            export_smt_report(smt, staged)
            suffixes = (".xlsx", ".json")
        elif kind == "pcb":
            build_dfm_workbook(pcb, board_meta).save(staged)
            generate_dfm_text(pcb, str(staged.with_suffix(".txt")), board_meta)
            suffixes = (".xlsx", ".txt")
        else:
            build_combined_workbook(pcb, smt, board_meta).save(staged)
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
            staged.with_suffix(".txt").write_text("\n".join(text), encoding="utf-8")
            payload = dict(report_type="DFM", board=board_meta, verdict=combined_verdict(pcb, smt),
                           pcb=pcb, smt=smt)
            staged.with_suffix(".json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            suffixes = (".xlsx", ".txt", ".json")
        outputs = []
        # Commit sidecars first, main workbook last. Staging always cleans up.
        for suffix in (*suffixes[1:], suffixes[0]):
            dest = target.with_suffix(suffix)
            os.replace(staged.with_suffix(suffix), dest)
            outputs.append(str(dest))
    return outputs

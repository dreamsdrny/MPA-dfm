"""Explicit outputs, complete TXD survey and frozen overall PCB→SMT execution."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import hashlib
import json

import openpyxl
import pytest

from src.adapters.smt_configuration import load_smt_profile
from src.domain.smt_rules import analyze_smt
from src.adapters.workbench_report import export_report

ROOT=Path(__file__).resolve().parents[1]


def test_txd_complete_content_and_profile_scoped_review():
    doc=load_smt_profile(ROOT/"config/suppliers/smt/smt_capability_txd.yaml")
    source=next((ROOT/"config/suppliers/smt").glob("*TXD20261113.xlsx"))
    assert hashlib.sha256(source.read_bytes()).hexdigest()==doc["source_workbook"]["sha256"]
    wb=openpyxl.load_workbook(source,data_only=False)
    for sheet, archive in zip(wb,doc["source_workbook"]["sheets"]):
        assert archive["name"]==sheet.title
        assert {c["coordinate"]:c["value"] for c in archive["cells"]}=={c.coordinate:c.value for row in sheet for c in row if c.value is not None}
        assert archive["merged_cells"]==list(map(str,sheet.merged_cells.ranges))
    wb.close()
    assert len(doc["requirements"])==108
    for key in ("smt","smt_cob"):
        r=analyze_smt(doc,key,{})
        assert len(r["manual_requirements"])==54
        assert {row["profile"] for row in r["manual_requirements"]}=={key}
        assert next(x for x in r["items"] if x["field"]=="bga_pitch")["status"]=="unconfigured"
        assert r["source_workbook"]==doc["source_workbook"]
    assert not any(r["field"]=="test_pad_diameter" for r in doc["profiles"]["smt_cob"]["rules"])
    pitch = {key: next(r for r in doc["profiles"][key]["rules"] if r["field"] == "test_pad_pitch") for key in doc["profiles"]}
    assert pitch["smt"]["source_cell"].endswith("!F54") and pitch["smt_cob"]["source_cell"].endswith("!F52")
    assert pitch["smt"]["note"] == "≥1.5mm" and pitch["smt_cob"]["note"] == "≥1.5mm"
    assert all(r.get("note") for key in doc["profiles"] for r in doc["profiles"][key]["rules"])


@pytest.mark.parametrize("ext",[".xlsx",".csv",".txt",".json",".html"])
def test_selected_format_only_one_file_and_no_sidecars(tmp_path,ext):
    doc=load_smt_profile(ROOT/"config/suppliers/smt/smt_capability_txd.yaml")
    smt=analyze_smt(doc,"smt",{})
    target=tmp_path/("report"+ext)
    assert export_report("smt",target,smt=smt)==[str(target)]
    assert list(tmp_path.iterdir())==[target]
    if ext==".json": assert json.loads(target.read_text(encoding="utf-8"))["smt"]["source_workbook"]
    if ext==".html": assert "采集表原文" in target.read_text(encoding="utf-8")
    if ext==".xlsx":
        wb=openpyxl.load_workbook(target)
        assert "采集表原文" in wb.sheetnames
        wb.close()


def test_overall_capture_consumes_pcb_evidence_without_tk_worker_access():
    from tools.test_workbench_ui import WorkbenchUI
    from src.dfm_engine import analyze_design
    test=WorkbenchUI();test.setUp()
    try:
        ui=test.dialog
        test.import_board()
        ui.smt.auto_sources.set(False)
        calls=[]
        def pcb(request,progress=None):
            result=analyze_design(request.design)
            result.update(input_brd=request.brd_path,input_brd_sha256=request.design["_brd_sha256"],
                          native_extraction={},analyzed_design=deepcopy(request.design))
            calls.append("pcb");return result
        from src.application.overall_analysis import compute_smt_snapshot
        def smt(snapshot,design,result):
            calls.append("smt")
            assert result["input_brd_sha256"]==design["_brd_sha256"]
            return compute_smt_snapshot(snapshot,design,result)
        with patch("src.application.brd_extraction.prepare_brd_data",return_value={}),\
             patch("src.ui.dfm_dialog.run_pcb_analysis",side_effect=pcb),\
             patch("src.application.overall_analysis.compute_smt_snapshot",side_effect=smt):
            ui._run(overall=True)
            test.wait(lambda:not ui.tasks.pending,timeout=15)
        assert calls==["pcb","smt"]
        assert ui.last_result["overall_run_id"]==ui.smt.last_result["overall_run_id"]
        assert ui.workbench.select()==str(ui.overview)
        assert "不可" in ui.overview_vars["overall"].get() or "不完整" in ui.overview_vars["overall"].get()
        ui.smt.supplier.set("同兴达 / TXD");ui.smt._select_supplier()
        assert "TXD" in ui.smt.doc["meta"]["factory"]
        assert len(ui.smt.review_tree.get_children())==54
        ui.smt.key.set("smt_cob");ui.smt._render_fields()
        assert len(ui.smt.review_tree.get_children())==54
    finally: test.tearDown()


def test_combined_report_rejects_mixed_overall_runs(tmp_path):
    from src.dfm_engine import analyze_design
    doc=load_smt_profile(ROOT/"config/suppliers/smt/smt_capability_txd.yaml")
    smt=analyze_smt(doc,"smt",{});pcb=analyze_design({})
    pcb["overall_run_id"]="a";smt["overall_run_id"]="b"
    with pytest.raises(ValueError,match="同一轮"):export_report("dfm",tmp_path/"mixed.xlsx",pcb,smt)


def test_overview_exposes_viewers_and_progress_animation():
    from tools.test_workbench_ui import WorkbenchUI
    test = WorkbenchUI()
    test.setUp()
    try:
        ui = test.dialog
        names = [ui.workbench.tab(tab, "text") for tab in ui.workbench.tabs()]
        assert names[0] == "项目总览"
        assert ui.progress_matrix.state == "idle" and ui.progress_matrix.snapshot()["filled"] == 0
        assert set(ui.viewer_models) == {"gerber", "odb"}
        assert ui.viewer_models["gerber"]["status"].get() == "未导入"
        # 导入资料菜单同时暴露 Gerber 与 ODB++ 查看器入口
        labels = [ui.import_menu.entrycget(index, "label") for index in range(ui.import_menu.index("end") + 1)]
        assert any("ODB++" in text for text in labels), labels
        # 动画只跟随真实阶段：未知阶段不推进，真实阶段才累计
        ui._progress_start("pcb")
        assert ui.progress_matrix.state == "running"
        ui._progress_stage("不存在的阶段")
        assert ui.progress_matrix.snapshot()["target"] == 0.0
        ui._progress_stage("gerber")
        assert ui.progress_matrix.snapshot()["target"] == pytest.approx(1 / 8)
        ui._progress_stage("smt_sources")  # PCB 阶段表里没有，不能误推进
        assert ui.progress_matrix.snapshot()["target"] == pytest.approx(1 / 8)
        ui._progress_finish("partial", label="仍有资料缺口")
        assert ui.progress_matrix.state == "partial"
        ui._invalidate_results()
        assert ui.progress_matrix.state == "idle"
        assert "未执行" in ui.progress_note.get()
        # 说明文字必须跟着真实阶段走，不能停在“未执行”或提前宣布完成
        ui._progress_start("overall")
        assert "进行中" in ui.progress_note.get()
        ui._progress_stage("gerber")
        assert "原生导出 Gerber" in ui.progress_note.get() and "1/12" in ui.progress_note.get()
        ui._progress_stage("未知阶段")
        assert "1/12" in ui.progress_note.get()
        ui._progress_finish("partial", label="仍有资料缺口")
        assert "缺口" in ui.progress_note.get() and "不等于通过" in ui.progress_note.get()
        ui._progress_finish("idle")
        assert "过期" in ui.progress_note.get()
    finally:
        test.tearDown()


def test_overview_gerber_viewer_loads_real_films_and_renders(tmp_path):
    from tools.test_gerber_standalone import films
    from tools.test_workbench_ui import WorkbenchUI
    folder = tmp_path / "films"
    films(folder)
    test = WorkbenchUI()
    test.setUp()
    try:
        ui = test.dialog
        ui._load_viewer("gerber", str(folder))
        test.wait(lambda: ui.viewer_models["gerber"]["loaded"] is not None, timeout=25)
        state = ui.viewer_models["gerber"]
        assert "6 层" in state["status"].get() and state["pending"] is False
        window = ui._open_viewer("gerber")
        assert window is not None
        view = window.viewer
        test.wait(lambda: "mm/px" in view.scale_var.get(), timeout=25)
        assert view.model["layers"] and view._photo is not None
        # 查看器不假装是判定：状态文字必须说明“仅用于查看/简化”
        assert "查看" in view.status_var.get() or "简化" in view.status_var.get()
        window.destroy()
        bad = tmp_path / "broken.gbr"
        bad.write_text("not gerber", encoding="ascii")
        ui._load_viewer("gerber", str(bad))
        test.wait(lambda: "读取失败" in ui.viewer_models["gerber"]["status"].get(), timeout=25)
        assert ui.viewer_models["gerber"]["loaded"] is None
    finally:
        test.dialog.tasks.pending.clear()
        test.tearDown()

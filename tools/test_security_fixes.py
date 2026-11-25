"""Regression tests for reported security and analysis-integrity defects."""
import csv
import io
import json
import os
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.brd_design import _carve_entries, extract_brd_design
from src.brd_input import brd_signature, content_sha256
from src.design_geometry import load_design_snapshot
from src.design_inputs.bom import export_bom
from src.resource_limits import InputBudgetExceeded
from src.services.pcb_analysis import PCBAnalysisRequest, run_pcb_analysis
from src.skill_generator import generate_skill_script
from src.smt_engine import load_smt_profile
from src.workbench_report import _write_csv, export_report


def test_skill_inputs_rejected_and_no_auto_execution():
    with pytest.raises(ValueError):
        generate_skill_script({}, {"net_classes": {"DATA": {"nets": ['N") (printf "BAD")']}}})
    with pytest.raises(ValueError):
        generate_skill_script({"via_constraints": [{"name": "A", "net_class": "B", "drill": "1) (bad", "pad": 1}]}, {})
    script = generate_skill_script({}, {"net_classes": {"DATA": {"nets": ["NET_1"]}}})
    assert not script.rstrip().endswith("(BRD_AI_CreateAllConstraints)")


def test_brd_embedded_zip_limit_and_no_silent_partial(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("objects/PhysicalCSet.xml", b"A"*10000)
    with patch("src.brd_design.MAX_BRD_ENTRY_BYTES", 1024), pytest.raises(InputBudgetExceeded):
        _carve_entries(buffer.getvalue())
    board = tmp_path/"large.brd"
    board.write_bytes(buffer.getvalue())
    with patch("src.brd_design.MAX_BRD_ENTRY_BYTES", 1024):
        result = extract_brd_design(str(board))
    assert not result["ok"] and "预算" in result["error"]


def test_snapshot_zip_limit(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("design.json", json.dumps({"design": {"value": "X"*10000}}))
    board = tmp_path/"snapshot.brd"
    board.write_bytes(b"BRD" + buffer.getvalue())
    with patch("src.design_geometry.MAX_BRD_ENTRY_BYTES", 1024), pytest.raises(InputBudgetExceeded):
        load_design_snapshot(str(board))


def test_external_input_changes_rejected(tmp_path):
    board = tmp_path/"board.brd"
    board.write_bytes(b"board")
    gerbers = tmp_path/"gerber"
    gerbers.mkdir()
    film = gerbers/"top.gtl"
    film.write_text("old")
    req = PCBAnalysisRequest(str(board), {"_brd_signature": brd_signature(str(board)),
        "_brd_sha256": content_sha256(board)}, auto_manufacturing=False, auto_gerber=False, gerber_dir=str(gerbers))
    def fake(*_args):
        film.write_text("new")
        return {"items": [], "rule_sha256": "hash"}
    with patch("src.services.pcb_analysis.capability_fingerprint", return_value="hash"), \
            patch("src.services.pcb_analysis._analyze_external", side_effect=fake), \
            pytest.raises(ValueError, match="资料在分析期间变化"):
        run_pcb_analysis(req)


def test_brd_same_size_timestamp_change_rejected(tmp_path):
    board = tmp_path/"board.brd"
    board.write_bytes(b"original")
    sig = brd_signature(str(board))
    req = PCBAnalysisRequest(str(board), {"_brd_signature": sig, "_brd_sha256": content_sha256(board)})
    board.write_bytes(b"modified")
    os.utime(board, ns=(sig[2], sig[2]))
    with pytest.raises(ValueError, match="内容已变化"):
        run_pcb_analysis(req)


def test_csv_escapes_formula_and_dfm_contains_smt(tmp_path):
    pcb = {"items": [], "input_brd": "board", "verdict": "review", "coverage": {}, "issues": [],
           "manufacturing_export": {"stages": {"gerber": {"status": "exported", "files": [],
             "source_brd_sha256": "x"}}, "manifest_path": "manifest"}}
    smt = {"items": [], "meta": {"factory": "F"}, "profile_label": "P", "verdict": "PASS"}
    path = tmp_path/"dfm.csv"
    _write_csv(path, "dfm", pcb, smt, {"board_name": "=1+1"})
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert any(row[1] == "SMT" for row in rows[1:])
    assert rows[1][2] == "'=1+1"
    export_bom(tmp_path/"bom.csv", [{"refdes": "R1", "part_number": "=1+1"}])
    assert "'=1+1" in (tmp_path/"bom.csv").read_text(encoding="utf-8-sig")


def test_report_sidecar_not_overwritten(tmp_path):
    sidecar = tmp_path/"report.txt"
    sidecar.write_text("original", encoding="utf-8")
    pcb = {"items": [], "input_brd": "board", "coverage": {}, "issues": [], "error_count": 0,
           "warning_count": 0, "verdict": "review", "factory_label": "F", "capability_label": "C", "board_type_label": "R"}
    with pytest.raises(FileExistsError):
        export_report("pcb", tmp_path/"report.xlsx", pcb=pcb)
    assert sidecar.read_text(encoding="utf-8") == "original"
    assert not (tmp_path/"report.xlsx").exists()


def test_smt_duplicate_yaml_and_changed_profile(tmp_path):
    path = tmp_path/"smt.yaml"
    data = ('schema_version: 1\nmeta: {factory: A, revision: 1}\nprofiles:\n  standard:\n'
            '    rules:\n    - {id: A, field: spacing, label: test, unit: mm, op: ">=", value: 0.1}\n')
    path.write_text(data.replace("value: 0.1", "value: 0.1, value: 0.01"), encoding="utf-8")
    with pytest.raises(ValueError, match="重复键"):
        load_smt_profile(path)
    path.write_text(data, encoding="utf-8")
    profile = load_smt_profile(path)
    path.write_text(data.replace("0.1", "0.2"), encoding="utf-8")
    from src.smt_engine import verify_profile_document
    with pytest.raises(ValueError, match="配置已变化"):
        verify_profile_document(profile)


def test_foreign_plain_text_not_accepted_as_brd(tmp_path):
    import queue
    from src.ui.dfm_dialog import DFMDialog
    board = tmp_path/"not-a-board.brd"
    board.write_text("THIS IS NOT A PCB\nLAYER_NAME='TOP'", encoding="ascii")
    receiver = type("Receiver", (), {"_brd_queue": queue.Queue()})()
    DFMDialog._read_brd_worker(receiver, str(board), 1)
    _token, _path, kind, message = receiver._brd_queue.get_nowait()
    assert kind == "err" and message


def test_untrusted_z_decompression_rejected_before_unlzw(tmp_path):
    from src.design_inputs.archive import InputContainer
    archive = tmp_path/"unsafe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("x.Z", b"\x1f\x9d\x10" + b"A"*30000)
    with InputContainer(archive) as container, patch("unlzw3.unlzw") as decoder:
        with pytest.raises(ValueError, match="安全限量"):
            container.read("x.Z")
        decoder.assert_not_called()


def test_profile_duplicate_field_not_silently_overwritten():
    from src.smt_engine import compare_smt_baseline
    base = {"profiles": {"b": {"rules": [{"scope": "design", "field": "x", "label": "X",
        "id": "B", "unit": "mm", "op": ">=", "value": 1}]}}}
    rules = [{"scope": "design", "field": "x", "id": "A", "unit": "mm", "op": ">=", "value": 2},
             {"scope": "design", "field": "x", "id": "B", "unit": "mm", "op": ">=", "value": .5}]
    result = compare_smt_baseline({"profiles": {"s": {"rules": rules}}}, "s", base, "b")
    assert result[0]["status"] == "review"


def test_preview_html_escapes_untrusted_values(tmp_path):
    from shapely.geometry import Point
    from src.gerber_analysis import write_preview
    path = tmp_path/"preview.html"
    bundle = {"films": [{"geometry": Point(0, 0).buffer(1), "role": "copper_top"}], "errors": [],
              "checks": [{"x_mm": 1, "y_mm": 2, "item": "rule", "layer": "TOP",
                          "measured": "<img onerror=alert(1)>", "capability": 1, "unit": "mm"}]}
    write_preview(bundle, path)
    assert "<img onerror=alert(1)>" not in path.read_text(encoding="utf-8")


def test_plugin_csv_corruption_rejected(tmp_path):
    from src.impedance import _rolling_checksum, read_results
    from src.impedance_plugin import write_plugin
    write_plugin(tmp_path)
    token = json.loads((tmp_path/"session.json").read_text())["token"]
    path = tmp_path/"results.csv"
    header = "NET,LAYER,WIDTH_MIL,SPACING_MIL,PAIR,IMPEDANCE_RULE,PHYSICAL_CSET,BRD,UNITS,MODE,KIND,X1_UU,Y1_UU,X2_UU,Y2_UU\n"
    path.write_text(header + "A,ETCH/TOP,5,,,,,board,mils,geometry,line,0,0,1,1\n")
    checksum, size = _rolling_checksum(path.read_bytes())
    (tmp_path/"results.done").write_text(f"IMP_RESULTS {token} 1 {checksum}:{size}")
    assert read_results(tmp_path)[0]["NET"] == "A"
    path.write_text(header + "B,ETCH/TOP,5,,,,,board,mils,geometry,line,0,0,1,1\n")
    with pytest.raises(ValueError, match="不一致"):
        read_results(tmp_path)

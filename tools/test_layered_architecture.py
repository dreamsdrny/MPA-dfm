"""Architecture contracts, startup ordering and source/publication boundaries."""
import ast
import json
import queue
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from src.application.brd_analysis import BRDAnalysisRequest, run_brd_analysis
from src.application.checklist import ChecklistRequest, run_checklist
from src.application.manufacturing import run_object_dfm
from src.application.smt_analysis import analyze_smt_workflow
from src.domain.models import (
    AnalysisSummary,
    Confidence,
    Coverage,
    Evidence,
    InputSource,
    Verdict,
    release_verdict,
)
from src.domain.results import summarize
from src.infrastructure.publication import publish_files
from src.infrastructure.snapshots import capture_sources, verify_sources
from src.infrastructure.tasks import TaskExecutor

ROOT = Path(__file__).resolve().parents[1]


def imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (name.name for name in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_domain_has_no_io_or_outward_dependencies():
    for path in (ROOT / "src" / "domain").glob("*.py"):
        for name in imports(path):
            assert not name.startswith(("src.ui", "src.application", "src.adapters", "src.infrastructure")), path
            assert name.split(".")[0] not in {"tkinter", "pathlib", "os", "subprocess", "yaml", "openpyxl"}, path
            assert not name.startswith("src.") or name.startswith("src.domain"), (path, name)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            assert not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in {"open", "eval", "exec"}), path


def test_ui_depends_only_on_application_ui_and_lifecycle_infrastructure():
    for path in (ROOT / "src" / "ui").glob("*.py"):
        for name in imports(path):
            if name.startswith("src."):
                assert name.startswith(("src.ui", "src.application", "src.infrastructure")), (path, name)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Call):
                assert not (isinstance(node.func, ast.Name) and node.func.id == "open"), path
                assert not (isinstance(node.func, ast.Attribute)
                            and node.func.attr in {"write_text", "read_text", "write_bytes", "read_bytes"}), path


def test_non_ui_layers_do_not_import_ui():
    for layer in ("application", "domain", "adapters", "infrastructure"):
        for path in (ROOT / "src" / layer).glob("*.py"):
            assert not any(name.startswith("src.ui") or name == "tkinter" for name in imports(path)), path


def test_evidence_serialization_and_missing_data_release_priority():
    source = InputSource("board.brd", "brd", "rev-A", "digest")
    evidence = Evidence("min_spacing", .1, source, "mm", Confidence.CONFIRMED)
    assert evidence.source.version == "rev-A"
    incomplete = Coverage(2, 1, ("board_outline",))
    assert release_verdict(coverage=incomplete) == Verdict.REVIEW
    assert release_verdict(failed=True, coverage=incomplete) == Verdict.FAIL
    complete = Coverage(1, 1, complete=True)
    assert release_verdict(coverage=complete) == Verdict.PASS
    payload = AnalysisSummary("pcb", Verdict.REVIEW, incomplete, sources=(source,)).to_dict()
    assert json.loads(json.dumps(payload))["coverage"]["ratio"] == .5
    assert not payload["releasable"]
    with pytest.raises(ValueError):
        Coverage(1, 0, complete=True)
    with pytest.raises(ValueError):
        Coverage(0, 0, complete=True)


@pytest.mark.parametrize("service,result", [
    ("pcb", {"items": [{"status": "pass"}], "releasable": True}),
    ("smt", {"items": [{"status": "pass"}], "coverage": 1}),
    ("brd", {"ok": True, "results": [{"status": "pass"}]}),
    ("checklist", {"stats": {"total": 1, "auto_pass": 1}}),
    ("manufacturing", {"status": "verified", "reconciliation": {"rows": [{"status": "matched"}]}}),
])
def test_parameter_or_partial_analysis_never_grants_full_release(service, result):
    assert not summarize(service, result).releasable
    assert summarize(service, result).verdict == Verdict.REVIEW


def test_source_snapshot_detects_content_changes_even_with_same_size(tmp_path):
    source = tmp_path / "bom.csv"
    source.write_text("old")
    records = capture_sources([source, source])
    assert len(records) == 1
    source.write_text("new")
    with pytest.raises(ValueError, match="内容已变化"):
        verify_sources(records)


def test_checklist_service_records_versions_and_rejects_changed_input(tmp_path):
    source = tmp_path / "checklist.xlsx"
    source.write_bytes(b"template")
    request = ChecklistRequest(checklist_path=str(source), output_dir=str(tmp_path / "out"))
    result = {"stats": {"total": 1, "manual": 1}}
    with patch("src.adapters.analysis.analyze_checklist", return_value=result):
        actual = run_checklist(request)
    assert actual["input_fingerprints"] and actual["_analysis"]["verdict"] == "review"
    def changed(*_args):
        source.write_bytes(b"modified")
        return result
    with patch("src.adapters.analysis.analyze_checklist", side_effect=changed), pytest.raises(ValueError):
        run_checklist(request)


def test_brd_service_validates_explicit_sources_and_empty_selection(tmp_path):
    board, report = tmp_path / "board.brd", tmp_path / "drc.rpt"
    board.write_text("board")
    report.write_text("before")
    request = BRDAnalysisRequest(str(board), export_files=(str(report),), only=("drc",))
    def changed(*_args):
        report.write_text("after")
        return {"ok": True, "results": []}
    with patch("src.adapters.analysis.analyze_brd", side_effect=changed), pytest.raises(ValueError):
        run_brd_analysis(request)
    with pytest.raises(ValueError, match="至少选择"):
        run_brd_analysis(BRDAnalysisRequest(str(board), only=()))


def test_smt_service_does_not_mutate_caller_data():
    doc, facts = {"nested": [1]}, {("design", "spacing"): {"value": 1}}
    def compute(profile, key, inputs, **_options):
        profile["nested"].append(2)
        inputs[("design", "spacing")]["value"] = 2
        return {"items": [], "coverage": 0}
    with patch("src.adapters.analysis.analyze_smt", side_effect=compute):
        assert analyze_smt_workflow(doc, "test", facts)["_analysis"]["verdict"] == "review"
    assert doc == {"nested": [1]} and facts[("design", "spacing")]["value"] == 1


def test_manufacturing_object_service_owns_pre_and_post_checks(tmp_path):
    source = tmp_path / "job.zip"
    source.write_bytes(b"before")
    project = {"kind": "odb", "status": "parsed", "sources": capture_sources([source]),
               "layout": {"selected_step": "pcb"}}
    def compute(_project):
        source.write_bytes(b"after")
        return {}
    with patch("src.adapters.analysis.analyze_manufacturing_objects", side_effect=compute), pytest.raises(ValueError):
        run_object_dfm(project)


def test_startup_authorization_precedes_config_dependencies_and_gui():
    from src.bootstrap import prepare_startup
    with patch("src.startup_guard.deployment_status", side_effect=ValueError("invalid license")), \
            patch("src.bootstrap.load_runtime_config") as config, \
            patch("src.bootstrap.check_dependencies") as dependencies, \
            pytest.raises(ValueError, match="invalid license"):
        prepare_startup()
    config.assert_not_called()
    dependencies.assert_not_called()


def test_startup_applies_runtime_budget_after_dependency_check(tmp_path):
    from src.adapters.configuration import RuntimeConfig
    from src.bootstrap import prepare_startup
    with patch("src.startup_guard.deployment_status", return_value={}), \
            patch("src.bootstrap.load_runtime_config", return_value=RuntimeConfig(2, 3)), \
            patch("src.bootstrap.check_dependencies", return_value=("numpy",)), \
            patch("src.bootstrap.configure_executor") as configure:
        context = prepare_startup("license.json")
    assert context.config.max_workers == 2
    configure.assert_called_once_with(2, 3)


def test_unsigned_runtime_config_is_rejected(tmp_path):
    from src.bootstrap import prepare_startup
    path = tmp_path / "config" / "internal" / "application.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("schema_version: 1")
    with patch("sys._MEIPASS", str(tmp_path), create=True), \
            patch("src.startup_guard.deployment_status", return_value={"config_manifest": {"files": {}}}), \
            pytest.raises(ValueError, match="签名完整性清单"):
        prepare_startup()


@pytest.mark.parametrize("text", ["schema_version: 1\nschema_version: 1", "schema_version: 2",
                                  "schema_version: true",
                                  "schema_version: 1\ntasks:\n  max_workers: true",
                                  "schema_version: 1\ntasks:\n  max_pending: 0"])
def test_invalid_runtime_config_is_not_silently_accepted(tmp_path, text):
    from src.adapters.configuration import load_runtime_config
    path = tmp_path / "config" / "internal" / "application.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(text)
    with pytest.raises(ValueError):
        load_runtime_config(tmp_path)


def test_dependency_check_reports_missing_imports():
    from src.infrastructure.dependencies import check_dependencies
    with patch("src.infrastructure.dependencies.import_module", side_effect=ImportError("missing")), \
            pytest.raises(ValueError, match="fake_dependency"):
        check_dependencies(("fake_dependency",))


def test_executor_limits_running_and_queued_tasks():
    executor = TaskExecutor(max_workers=1, max_pending=1)
    started, release = threading.Event(), threading.Event()
    delivery = queue.Queue()
    def blocking():
        started.set()
        assert release.wait(5)
        return "first"
    try:
        assert executor.submit("first", blocking, delivery)
        assert started.wait(5)
        assert executor.submit("second", lambda: "second", delivery)
        assert not executor.submit("third", lambda: "third", delivery)
    finally:
        release.set()
    assert delivery.get(timeout=5) == ("first", True, "first")
    assert delivery.get(timeout=5) == ("second", True, "second")


def test_publication_preserves_existing_files_and_rolls_back_new_files(tmp_path):
    source, first, existing = tmp_path / "staged", tmp_path / "first", tmp_path / "existing"
    source.write_text("report")
    existing.write_text("user data")
    with pytest.raises(FileExistsError):
        publish_files([(source, first), (source, existing)])
    assert not first.exists() and existing.read_text() == "user data"


def test_executor_failure_is_delivered_and_worker_remains_available():
    executor, delivery = TaskExecutor(max_workers=1), queue.Queue()
    def fail():
        raise ValueError("analysis failed")
    assert executor.submit("failed", fail, delivery)
    assert delivery.get(timeout=5) == ("failed", False, "analysis failed")
    assert executor.submit("next", lambda: 42, delivery)
    assert delivery.get(timeout=5) == ("next", True, 42)


def test_brd_ui_discards_changed_input_and_worker_never_reads_tk(tmp_path):
    import tkinter as tk

    from src.ui.analyze_dialog import AnalyzeDialog
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(str(exc))
    root.withdraw()
    board = tmp_path / "board.brd"
    board.write_text("synthetic board")
    dialog = AnalyzeDialog(root, str(board), str(tmp_path / "out"))
    errors, threads = [], []
    root.report_callback_exception = lambda *args: errors.append(args)
    started, release = threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    def analyze(request, log=None):
        threads.append(threading.get_ident())
        assert request.brd_path == str(board)
        started.set()
        assert release.wait(5)
        return {"ok": True, "results": [], "stats": {}}
    def completed():
        if dialog.tasks.pending:
            root.after(20, completed)
        else:
            root.quit()
    try:
        with patch("src.ui.analyze_dialog.run_brd_analysis", side_effect=analyze):
            dialog._run()
            assert started.wait(5)
            dialog.brd_path.set(str(tmp_path / "another.brd"))
            release.set()
            root.after(20, completed)
            watchdog = root.after(5000, root.quit)
            root.mainloop()
            root.after_cancel(watchdog)
        assert not dialog.tasks.pending and not errors
        assert dialog.bundle is None and "过期结果" in dialog.status_var.get()
        assert threads and threads[0] != main_thread
    finally:
        release.set()
        dialog._busy = False
        dialog.close()
        root.destroy()


def test_domain_rules_run_without_file_access():
    from src.domain.pcb_rules import analyze_design
    from src.domain.smt_rules import analyze_smt
    from src.domain.checklist_rules import load_rules, evaluate
    with patch("builtins.open", side_effect=AssertionError("Domain must not read files")):
        pcb = analyze_design({"min_line_width": .01}, capability={"trace": {"min_line_spacing_by_copper": [
            {"copper_min_um": 0, "copper_max_um": 100, "line_um": 100, "space_um": 100}]}}, rule_metadata={})
        assert pcb["verdict_code"] == "fail"
        doc = {"meta": {"factory": "test", "revision": "1"}, "path": "memory",
               "profiles": {"test": {"rules": [{"id": "R1", "field": "spacing", "scope": "design", "label": "spacing",
                    "unit": "mm", "op": ">=", "value": .2}]}}}
        smt = analyze_smt(doc, "test", {("design", "spacing"): {"value": .1, "unit": "mm", "source": "measured"}})
        assert smt["counts"]["fail"] == 1
        rule = load_rules({"rules": [{"id": "count", "any": ["test"], "result": "judge", "judge": {"kind": "count", "field": "count"}}]})[0]
        assert evaluate(rule, {"count": 0})["status"] == "FAIL"


@pytest.mark.parametrize("value", [True, "nan", "inf", None])
def test_checklist_unknown_or_nonfinite_data_is_not_pass(value):
    from src.domain.checklist_rules import load_rules, evaluate
    rule = load_rules({"rules": [{"id": "R1", "any": ["test"], "result": "judge",
                                 "judge": {"field": "count", "op": ">=", "value": 1}}]})[0]
    assert evaluate(rule, {"count": value})["status"] == "MANUAL"


def test_staging_does_not_publish_changed_sources_or_overwrite_user_files(tmp_path):
    from src.infrastructure.staging import stage_outputs
    target = tmp_path / "out"
    def generate(folder):
        file = Path(folder) / "report.json"
        file.write_text(json.dumps({"path": str(file)}), encoding="utf-8")
        return str(file)
    def reject():
        raise ValueError("source changed")
    with pytest.raises(ValueError):
        stage_outputs(target, generate, reject)
    assert not (target / "report.json").exists()
    published = stage_outputs(target, generate)
    assert published == str(target / "report.json")
    assert json.loads(Path(published).read_text())["path"] == published
    old = Path(published).read_text()
    with pytest.raises(FileExistsError):
        stage_outputs(target, generate)
    assert Path(published).read_text() == old


def test_cli_requires_startup_before_running_business(monkeypatch):
    from src.application.legacy_cli import main
    monkeypatch.setattr("sys.argv", ["main.py", "--demo"])
    with patch("src.bootstrap.prepare_startup", side_effect=ValueError("license invalid")), \
            patch("src.application.legacy_cli.run_pipeline") as pipeline, pytest.raises(ValueError):
        main()
    pipeline.assert_not_called()


def test_authorization_failure_prevents_legacy_gui_imports():
    import gui
    with patch("src.bootstrap.prepare_startup", side_effect=ValueError("invalid license")), \
            patch("src.ui.main_window.MainWindow") as window, pytest.raises(ValueError):
        gui.main()
    window.assert_not_called()


def test_unified_result_preserves_evidence_units_and_confidence():
    result = {"items": [{"field": "min_spacing", "status": "fail", "design": .05, "unit": "mm",
                         "source_detail": "confirmed copper geometry"}],
              "sources": {"min_spacing": {"confidence": "high"}}, "verdict_code": "fail",
              "coverage": {"missing_items": ["板框"]}}
    actual = summarize("pcb", result).to_dict()
    assert actual["verdict"] == "fail" and not actual["releasable"]
    assert actual["evidence"][0]["unit"] == "mm"
    assert actual["evidence"][0]["confidence"] == "confirmed"
    assert "板框" in actual["coverage"]["missing"]


def test_partial_checklist_dfm_fact_is_not_a_pass():
    from src.domain.checklist_rules import load_rules, evaluate
    rule = load_rules({"rules": [{"id": "R1", "any": ["test"], "result": "judge",
                                  "judge": {"kind": "dfm_all"}}]})[0]
    assert evaluate(rule, {})["status"] == "MANUAL"
    rule["judge"] = {"kind": "dfm", "item": "test"}
    assert evaluate(rule, {"_dfm_items": [{"item": "test", "status": "missing"}]})["status"] == "MANUAL"


def test_invalid_checklist_yaml_does_not_silently_use_defaults(tmp_path):
    from src.adapters.checklist_reader import load_config
    config = tmp_path / "rules.yaml"
    config.write_text("rules: []\nrules: []", encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        load_config(str(config))


def test_auxiliary_selftest_fixtures_match_current_brd_contract():
    from tools.selftest_net_locate import main as net_selftest
    from tools.selftest_component_stats import main as component_selftest
    assert net_selftest() == 0
    assert component_selftest() == 0

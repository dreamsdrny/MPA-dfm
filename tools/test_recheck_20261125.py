"""Regressions found while rechecking the security-fix release."""
import io
import json
import os
import shutil
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.brd_design import _carve_entries, extract_brd_design
from src.design_geometry import load_design_snapshot
from src.resource_limits import InputBudgetExceeded
from src.smt_geometry import (
    evidence_fingerprints,
    source_signature,
    verify_evidence_fingerprints,
)
from src.workbench_report import export_report


@pytest.mark.parametrize("entries", [
    {"other.txt": "not a PCB"},
    {"Header.xml": "<root/>"},
    {"Header.xml": "<root/>", "objects/PhysicalCSet.xml": "invalid XML"},
    {"Header.xml": "<root/>", "objects/PhysicalCSet.xml": "<root/>"},
])
def test_arbitrary_zip_is_not_a_verified_brd(tmp_path, entries):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as writer:
        for name, content in entries.items():
            writer.writestr(name, content)
    board = tmp_path / "fake.brd"
    board.write_bytes(b"THIS IS NOT A PCB\x00" + archive.getvalue())
    result = extract_brd_design(str(board))
    assert not result["ok"] and result.get("error")


def test_renamed_zip_does_not_enable_workbench_import(tmp_path):
    import queue

    from src.ui.dfm_dialog import DFMDialog
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as writer:
        writer.writestr("readme.txt", "not a PCB")
    board = tmp_path / "fake.brd"
    board.write_bytes(archive.getvalue())
    receiver = type("Receiver", (), {"_brd_queue": queue.Queue()})()
    DFMDialog._read_brd_worker(receiver, str(board), 1)
    assert receiver._brd_queue.get_nowait()[2] == "err"


def test_real_usable_fixture_and_demo_still_parse():
    root = Path(__file__).resolve().parents[1]
    for path in (root / "demo" / "DEMO_BOARD.brd", root / "store-brd" / "S-860-000739_FAB_BRD_Rev_E.brd"):
        if path.is_file():
            assert extract_brd_design(str(path))["ok"]


def test_extract_decodes_snapshot_from_supplied_board_bytes():
    from src.design_geometry import _decode_snapshot
    board = Path(__file__).resolve().parents[1] / "demo" / "DEMO_BOARD.brd"
    raw = board.read_bytes() + b"test-snapshot-marker"
    with patch("src.design_geometry._decode_snapshot", wraps=_decode_snapshot) as decode:
        assert extract_brd_design(str(board), raw=raw)["ok"]
    decode.assert_called_once_with(raw)


def test_snapshot_same_size_same_mtime_is_reparsed(tmp_path):
    board = tmp_path / "snapshot.brd"
    def make(name):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("design.json", json.dumps({"design": {"name": name, "comps": []}}))
        return data.getvalue()
    first, second = make("A"), make("B")
    assert len(first) == len(second)
    board.write_bytes(first)
    stat = board.stat()
    assert load_design_snapshot(str(board))["design"]["name"] == "A"
    board.write_bytes(second)
    os.utime(board, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert load_design_snapshot(str(board))["design"]["name"] == "B"


def test_stored_archive_respects_remaining_budget(tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("first", b"a" * 20)
        archive.writestr("second", b"b" * 20)
    with patch("src.brd_design.MAX_BRD_UNPACKED_BYTES", 30), pytest.raises(InputBudgetExceeded):
        _carve_entries(stream.getvalue())


def test_declared_zero_size_uses_remaining_decompression_budget():
    import struct
    import zlib
    name = b"objects/PhysicalCSet.xml"
    compressed = zlib.compress(b"A" * 10000, wbits=-15)
    local_header = b"PK\x03\x04" + struct.pack("<HHHHHIIIHH", 20, 0, 8, 0, 0, 0,
                                               len(compressed), 0, len(name), 0)
    with patch("src.brd_design.MAX_BRD_UNPACKED_BYTES", 100), \
            patch("src.brd_design.MAX_BRD_ENTRY_BYTES", 1024), pytest.raises(InputBudgetExceeded):
        _carve_entries(local_header + name + compressed)


def test_smt_same_size_same_mtime_evidence_is_detected(tmp_path):
    board, evidence = tmp_path / "board.brd", tmp_path / "evidence.json"
    board.write_bytes(b"board")
    evidence.write_text('{"complete":{"components":true}}', encoding="utf-8")
    fast = source_signature(evidence, board)
    content = evidence_fingerprints(evidence, board)
    stat = evidence.stat()
    evidence.write_text('{"complete":{"components":null}}', encoding="utf-8")
    os.utime(evidence, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert source_signature(evidence, board) == fast
    with pytest.raises(ValueError, match="内容已变化"):
        verify_evidence_fingerprints(content)


def test_smt_evidence_changes_during_analysis_are_rejected(tmp_path):
    from src.services.smt_analysis import analyze_smt_workflow
    from src.smt_geometry import sha256_file
    board, evidence = tmp_path / "board.brd", tmp_path / "evidence.json"
    board.write_bytes(b"board")
    data = {"schema_version": 1, "brd_sha256": sha256_file(board), "coordinate_system": "brd",
            "unit": "mm", "coordinate_alignment_confirmed": True, "complete": {}}
    evidence.write_text(json.dumps(data), encoding="utf-8")
    profile = {"meta": {"factory": "Fixture"}, "profiles": {"default": {"rules": []}}}
    def mutate(*_args):
        stat = evidence.stat()
        old = evidence.read_text(encoding="utf-8")
        evidence.write_text(old.replace('"mm"', '"cm"'), encoding="utf-8")
        os.utime(evidence, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return {}, []
    stub_result = {"items": [], "meta": {"factory": "Fixture"}, "manual_requirements": []}
    with patch("src.services.smt_analysis.analyze_objects", side_effect=mutate), \
            patch("src.services.smt_analysis.apply_object_results"), \
            patch("src.services.smt_analysis.summarize_smt"), \
            pytest.raises(ValueError, match="内容在分析期间发生变化"):
        analyze_smt_workflow(profile, "default", {}, {"_input_brd": str(board)}, str(evidence),
                             analyzer=lambda *_args: dict(stub_result))


def test_smt_result_export_rechecks_content(tmp_path):
    board, evidence = tmp_path / "board.brd", tmp_path / "evidence.json"
    board.write_bytes(b"board")
    evidence.write_text('{"complete":{"components":true}}', encoding="utf-8")
    fingerprint = evidence_fingerprints(evidence, board)
    stat = evidence.stat()
    evidence.write_text('{"complete":{"components":null}}', encoding="utf-8")
    os.utime(evidence, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    smt = {"items": [], "object_evidence_path": str(evidence), "evidence_fingerprints": fingerprint}
    with pytest.raises(ValueError, match="内容已变化"):
        export_report("smt", tmp_path / "report.csv", smt=smt)
    assert not (tmp_path / "report.csv").exists()


def test_report_race_does_not_overwrite_competing_file(tmp_path):
    pcb = {"items": [], "input_brd": "board", "coverage": {}, "issues": [], "error_count": 0,
           "warning_count": 0, "verdict": "review", "factory_label": "F", "capability_label": "C", "board_type_label": "R"}
    main, sidecar = tmp_path / "report.xlsx", tmp_path / "report.txt"
    real_copy = shutil.copyfileobj
    def competing_creation(src, dst, **kwargs):
        real_copy(src, dst, **kwargs)
        main.write_bytes(b"created by another process")
    with patch("src.workbench_report._copy_report_bytes", side_effect=competing_creation), pytest.raises(FileExistsError):
        export_report("pcb", main, pcb=pcb)
    assert main.read_bytes() == b"created by another process"
    assert not sidecar.exists()


def test_report_failure_rolls_back_own_partial_sidecar(tmp_path):
    pcb = {"items": [], "input_brd": "board", "coverage": {}, "issues": [], "error_count": 0,
           "warning_count": 0, "verdict": "review", "factory_label": "F", "capability_label": "C", "board_type_label": "R"}
    def failed_copy(src, dst, **kwargs):
        dst.write(b"partial")
        raise OSError("simulated disk full")
    with patch("src.workbench_report._copy_report_bytes", side_effect=failed_copy), pytest.raises(OSError, match="disk full"):
        export_report("pcb", tmp_path / "report.xlsx", pcb=pcb)
    assert not (tmp_path / "report.txt").exists()
    assert not (tmp_path / "report.xlsx").exists()


def test_report_rejects_source_changed_during_staging(tmp_path):
    from src.design_inputs.coordination import source_fingerprint
    from src.workbench_report import _write_csv
    film = tmp_path / "source.gtl"
    film.write_text("before")
    pcb = {"items": [], "input_brd": "board", "coverage": {}, "issues": [], "verdict": "review",
           "input_fingerprints": [source_fingerprint(film)]}
    def write_then_change(*args, **kwargs):
        _write_csv(*args, **kwargs)
        film.write_text("after")
    target = tmp_path / "report.csv"
    with patch("src.workbench_report._write_csv", side_effect=write_then_change), \
            pytest.raises(ValueError, match="资料已变化"):
        export_report("pcb", target, pcb=pcb)
    assert not target.exists()


def test_profile_expansion_budget_is_checked_before_read(tmp_path):
    from src.smt_engine import load_smt_profile
    path = tmp_path / "oversized.yaml"
    with path.open("wb") as stream:
        stream.truncate(4 * 1024 * 1024 + 1)
    with pytest.raises(InputBudgetExceeded):
        load_smt_profile(path)


def test_smt_report_never_overwrites_evidence(tmp_path):
    from src.smt_geometry import sha256_file
    board, evidence = tmp_path / "board.brd", tmp_path / "evidence.json"
    board.write_bytes(b"board")
    evidence.write_text(json.dumps({"brd_sha256": sha256_file(board)}))
    content = evidence_fingerprints(evidence, board)
    smt = {"items": [], "object_evidence_path": str(evidence), "evidence_fingerprints": content,
           "profile_sources": []}
    with pytest.raises(ValueError, match="不能覆盖源"):
        export_report("smt", tmp_path / "evidence.xlsx", smt=smt)
    assert json.loads(evidence.read_text())["brd_sha256"] == sha256_file(board)

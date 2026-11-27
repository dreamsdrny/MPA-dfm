import json
from unittest.mock import patch

from evan_dfm import main
from src.integrity import IntegrityError


def test_deployment_diagnostics_still_requires_valid_license(tmp_path):
    output = tmp_path / "deployment.json"
    status = {"app_id": "Evan.MPA_DFM", "license": {"starts": "2026-11-23", "expires": "2027-01-21"}}
    with patch("src.startup_guard.deployment_status", return_value=status) as guard:
        assert main(["--check-deployment", str(output), "--license", "signed.json"]) == 0
        guard.assert_called_once_with(license_path="signed.json")
    assert json.loads(output.read_text(encoding="utf-8"))["valid"]
    with patch("src.startup_guard.deployment_status", side_effect=IntegrityError("授权已到期")):
        assert main(["--check-deployment", str(output)]) == 1
    assert not json.loads(output.read_text(encoding="utf-8"))["valid"]


def test_trace_ui_diagnostics_rejects_invalid_license_before_gui(tmp_path):
    output = tmp_path / "trace.json"
    with patch("src.startup_guard.deployment_status", side_effect=IntegrityError("授权已到期")), \
            patch("evan_dfm.tk.Tk") as gui:
        assert main(["--check-trace-ui", str(output)]) == 1
    gui.assert_not_called()
    assert not json.loads(output.read_text(encoding="utf-8"))["valid"]


def test_release_package_strips_markdown_docs(tmp_path):
    from tools.build_release import strip_delivery_docs
    (tmp_path / "kept.exe").write_bytes(b"x")
    (tmp_path / "TUTORIAL.md").write_text("old", encoding="utf-8")
    nested = tmp_path / "docs"
    nested.mkdir()
    (nested / "NESTED.MD").write_text("old", encoding="utf-8")
    removed = strip_delivery_docs(tmp_path)
    assert sorted(removed) == ["TUTORIAL.md", "docs/NESTED.MD"]
    assert (tmp_path / "kept.exe").is_file()
    assert not list(tmp_path.rglob("*.md")) and not list(tmp_path.rglob("*.MD"))


def test_smt_diagnostics_requires_license_and_runs_real_tab(tmp_path):
    output = tmp_path / "smt.json"
    with patch("src.startup_guard.deployment_status", side_effect=IntegrityError("授权已到期")), \
            patch("evan_dfm.tk.Tk") as gui:
        assert main(["--check-smt-ui", str(output)]) == 1
    gui.assert_not_called()
    with patch("src.startup_guard.deployment_status", return_value={}), \
            patch("src.ui.prefs.load_prefs", return_value={}):
        code = main(["--check-smt-ui", str(output)])
    result = json.loads(output.read_text(encoding="utf-8"))
    if any(text in result.get("error", "") for text in ("Can't find a usable tk.tcl", "Can't find a usable init.tcl")):
        import pytest
        pytest.skip("Source Python Tcl/Tk initialization files unavailable")
    assert code == 0, result
    assert result["manual_conditions"] == 54
    assert result["mixed_spacing_status"] == "fail"
    assert (tmp_path / "smt_packaged.xlsx").is_file()


def test_viewer_diagnostics_requires_license_then_runs_real_widgets(tmp_path):
    import json
    output = tmp_path / "viewer.json"
    with patch("src.startup_guard.deployment_status", side_effect=IntegrityError("授权已到期")), \
            patch("evan_dfm.tk.Tk") as gui:
        assert main(["--check-viewer-ui", str(output)]) == 1
    gui.assert_not_called()
    assert not json.loads(output.read_text(encoding="utf-8"))["valid"]
    with patch("src.startup_guard.deployment_status", return_value={}), \
            patch("src.ui.prefs.load_prefs", return_value={}):
        code = main(["--check-viewer-ui", str(output)])
    result = json.loads(output.read_text(encoding="utf-8"))
    if "tk.tcl" in str(result.get("error", "")) or "Tcl/Tk" in str(result.get("error", "")):
        import pytest
        pytest.skip("Source Python Tcl/Tk initialization files unavailable")
    assert code == 0, result
    assert result["layers"] == 6 and result["issues"] == 1
    assert result["overview_first_tab"] == "项目总览"
    assert sorted(result["viewer_entries"]) == ["gerber", "odb"]
    assert "mm/px" in result["rendered"]
    assert result["synthetic_data"] and not result["native_board_tested"]

"""Headless database extraction and unknown copper safety contracts."""
from pathlib import Path
from unittest.mock import patch

import pytest

from src.adapters.extracta import (
    VIEWS,
    extract_brd_data,
    parse_components,
    parse_layers,
)


def view_file(path, name, records, unit="mils"):
    header = VIEWS[name][1]
    text = "A!" + "!".join(header) + "!\nJ!board.brd!date!0!0!100!100!0.01!" + unit + "!\n"
    text += "".join("S!" + "!".join(str(row.get(key, "")) for key in header) + "!\n" for row in records)
    path.write_text(text, encoding="utf-8")
    return path


def test_components_preserve_true_origin_negative_zero_rotation_missing(tmp_path):
    path = view_file(tmp_path / "comp.out", "components", [
        dict(REFDES="R1", SYM_TYPE="PACKAGE", SYM_X="-100", SYM_Y="0", SYM_ROTATE="0", SYM_MIRROR="YES"),
        dict(REFDES="U1", SYM_TYPE="PACKAGE", SYM_X="10", SYM_Y="20", SYM_ROTATE="", SYM_MIRROR=""),
        dict(REFDES="", SYM_TYPE="MECHANICAL", SYM_X="0", SYM_Y="0")])
    payload = parse_components(path)
    assert payload["package_count"] == 2
    assert payload["objects"][0]["x_mm"] == -2.54
    assert payload["objects"][0]["y_mm"] == 0
    assert payload["objects"][0]["side"] == "BOTTOM"
    assert payload["objects"][1]["rotation"] is None
    assert payload["objects"][1]["side"] == ""
    assert not payload["complete"]


def test_layers_convert_real_units_missing_and_multi_stackup(tmp_path):
    records = [dict(LAYER_SORT="1", LAYER_SUBCLASS="TOP", LAYER_CONDUCTOR="YES", LAYER_MATERIAL="COPPER", LAYER_THICKNESS="1.81 mil", LAYER_STACKUP_NAME="PRIMARY"),
               dict(LAYER_SORT="2", LAYER_SUBCLASS="BOTTOM", LAYER_CONDUCTOR="YES", LAYER_MATERIAL="COPPER", LAYER_THICKNESS="", LAYER_STACKUP_NAME="PRIMARY")]
    path = view_file(tmp_path / "layer.out", "layers", records)
    layers = parse_layers(path)
    assert layers[0]["copper_um"] == pytest.approx(45.974)
    assert layers[1]["copper_um"] is None
    records[1]["LAYER_STACKUP_NAME"] = "FLEX"
    view_file(path, "layers", records)
    with pytest.raises(ValueError, match="多区域"):
        parse_layers(path)


def test_unknown_unit_and_missing_coordinates_are_not_guessed(tmp_path):
    path = view_file(tmp_path / "comp.out", "components", [], unit="unknown")
    with pytest.raises(ValueError, match="单位"):
        parse_components(path)


def test_unavailable_extractor_never_launches_editor(tmp_path):
    board = tmp_path / "source.brd"
    board.write_bytes(b"board")
    with patch("src.adapters.extracta.discover_extracta", return_value=""), \
            patch("src.adapters.allegro_bridge.subprocess.Popen") as editor:
        payload = extract_brd_data(board, tmp_path / "output")
    editor.assert_not_called()
    assert payload["status"] == "unavailable"
    assert not payload["components"] and not payload["updates"]
    assert "未获取" in payload["warnings"][0]


def test_headless_command_and_success_data(tmp_path):
    board = tmp_path / "board.brd"
    board.write_bytes(b"board")
    def run(command, work, timeout, env):
        assert Path(command[0]).name == "extracta.exe"
        assert "allegro.exe" not in " ".join(command)
        view_file(Path(command[3]), "components", [dict(REFDES="R1", SYM_TYPE="PACKAGE", SYM_X="0", SYM_Y="1", SYM_ROTATE="0", SYM_MIRROR="NO")])
        view_file(Path(command[4]), "layers", [dict(LAYER_SORT="1", LAYER_SUBCLASS="TOP", LAYER_CONDUCTOR="YES", LAYER_MATERIAL="COPPER", LAYER_THICKNESS="35 um", LAYER_STACKUP_NAME="PRIMARY")])
        view_file(Path(command[5]), "board", [dict(BOARD_UNITS="mils", BOARD_LAYERS="1")])
        return 0, "success"
    with patch("src.adapters.extracta.discover_extracta", return_value="C:/Cadence/tools/bin/extracta.exe"), \
            patch("src.adapters.extracta._run", side_effect=run):
        payload = extract_brd_data(board, tmp_path / "out")
    assert len(payload["components"]) == 1
    assert payload["updates"]["copper_um"] == 35
    assert (Path(payload["directory"]) / "evan_components.csv").is_file()


def test_missing_copper_cannot_select_thin_copper_bridge():
    from src.dfm_engine import analyze_design, get_sm_bridge_capability, load_capability
    cap = load_capability("rigid", "standard", "hongban")
    assert get_sm_bridge_capability(cap, None) is None
    result = analyze_design(dict(copper_um=None, layer_count=2, solder_mask_bridge=.2))
    row = next(item for item in result["items"] if item["field"] == "solder_mask_bridge")
    assert row["capability"] is None and row["status"] == "info"
    assert result["copper_um"] is None


def test_enrich_preserves_manual_copper_and_stackup():
    from src.application.brd_extraction import enrich_design
    payload = dict(status="extracted", directory="out", components=[], updates=dict(copper_um=35,
        stackup=[dict(index=1, name="TOP", copper_um=35), dict(index=2, name="BOTTOM", copper_um=35)]))
    design = enrich_design(dict(copper_um=70, _manual_fields=["copper_um"]), payload)
    assert design["copper_um"] == 70
    assert all(row["copper_um"] == 70 for row in design["stackup"])


def test_failed_rerun_never_keeps_old_automatic_copper():
    from src.application.brd_extraction import enrich_design
    payload = dict(status="unavailable", directory="new", components=[], updates={}, warnings=["未获取"])
    result = enrich_design(dict(copper_um=35, stackup=[dict(index=1, copper_um=35)], _manual_fields=[]), payload)
    assert result["copper_um"] is None and result["stackup"] == []


def test_manual_stackup_wins_over_automatic_copper():
    from src.application.brd_extraction import enrich_design
    stack = [dict(index=1, name="TOP", copper_um=70), dict(index=2, name="BOTTOM", copper_um=105)]
    payload = dict(status="extracted", directory="out", components=[], updates=dict(copper_um=35, stackup=[]))
    result = enrich_design(dict(copper_um=None, stackup=stack, _stackup_manual=True), payload)
    assert result["stackup"] == stack and result["copper_um"] is None


def test_bridge_selects_top_bottom_independently_with_unknown_global(tmp_path):
    from src.gerber_analysis import analyze_gerbers
    from tools.test_gerber_standalone import films
    films(tmp_path)
    geometry = analyze_gerbers(tmp_path, copper_um=None, layer_count=2,
        stackup=[dict(index=1, name="TOP", copper_um=35), dict(index=2, name="BOTTOM", copper_um=70)])
    rows = {row["layer"]: row for row in geometry["mask_bridge_checks"]}
    assert rows["mask_top"]["copper_um"] == 35
    assert rows["mask_bottom"]["copper_um"] == 70
    assert rows["mask_top"]["capability"] < rows["mask_bottom"]["capability"]


def test_workflow_enriches_before_geometry_and_reports_coordinates(tmp_path):
    from src.application.contracts import PCBAnalysisRequest
    from src.application.pcb_workflow import run_pcb_analysis
    from src.brd_input import brd_signature, content_sha256
    from src.dfm_engine import capability_fingerprint
    board = tmp_path / "board.brd"
    board.write_bytes(b"fixture")
    native = dict(status="extracted", directory=str(tmp_path / "extracted"), source_brd_sha256=content_sha256(board),
        components=[dict(refdes="R1", x_mm=0, y_mm=-2.54, rotation=0, side="TOP", source="extracta")], unit="mils",
        updates=dict(layer_count=2, copper_um=45.974), warnings=[])
    Path(native["directory"]).mkdir()
    request = PCBAnalysisRequest(str(board), dict(copper_um=None, _brd_signature=brd_signature(str(board))), native_data=native)
    def engine(_board, design, **kwargs):
        assert design["copper_um"] == 45.974
        assert kwargs["coordinate_dir"] == native["directory"]
        return dict(items=[], rule_sha256=capability_fingerprint("hongban"), geometry_complete=False)
    with patch("src.manufacturing_pipeline.analyze_brd_manufacturing", side_effect=engine):
        result = run_pcb_analysis(request)
    assert result["component_coordinates"][0]["y_brd"] == -100
    assert request.design["copper_um"] is None

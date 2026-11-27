"""Unit/contract tests. Explicitly NOT licensed PCB Editor integration tests."""
import csv
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src.impedance import _rolling_checksum, read_catalogue, read_results
from src.impedance_plugin import (
    prepare_load_script,
    quote_skill,
    write_action,
    write_plugin,
)


def test_plugin_bundle_and_safe_inputs(tmp_path):
    folder = write_plugin(tmp_path)
    assert (folder/"imp_highlight.il").is_file()
    assert "IMP_HIGHLIGHT" in (folder/"load_imp.scr").read_text()
    action = write_action(folder, layer="L5_SIG", width="5", spacing="6", mode="pair", color=2,
                          net='A");unsafe("')
    assert 'A\\\");unsafe(\\\"' in action.read_text()
    with pytest.raises(ValueError):
        write_action(folder, layer="L5_SIG", width="nan")
    with pytest.raises(ValueError):
        quote_skill("\n")


def test_action_numeric_input_is_canonicalized_for_skill(tmp_path):
    folder = write_plugin(tmp_path)
    action = write_action(folder, layer="TOP", width="1_0", spacing="1e1", tolerance="0.010", color=1)
    text = action.read_text()
    assert '"1_0"' not in text and '"1e1"' not in text
    assert text.count('"10"') == 2


def test_impedance_panel_has_only_layer_width_spacing(tmp_path):
    import tkinter as tk
    from src.ui.impedance_panel import ImpedancePanel
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(str(exc))
    panel = ImpedancePanel(root)
    try:
        assert set(panel.inputs) == {"layer", "width", "spacing"}
        for removed in ("folder", "mode", "net", "pair", "impedance", "tolerance", "color", "rules"):
            assert not hasattr(panel, removed)
        panel.set_layers(["TOP", "BOTTOM"])
        assert panel.layer.get() == "TOP"
        panel.set_layers([])
        assert not panel.layer.get()
        assert str(panel.apply_button.cget("state")) == "disabled"
    finally:
        panel.tasks.close()
        root.destroy()


def test_moved_bundle_reloads_without_python_and_preserves_data(tmp_path):
    original = write_plugin(tmp_path / "original")
    moved = tmp_path / "new location"
    shutil.copytree(original, moved)
    token = json.loads((moved / "session.json").read_text())["token"]
    (moved / "catalog.csv").write_text("preserve engineering data")
    path = prepare_load_script(moved)
    assert moved.as_posix() in path.read_text()
    assert token in path.read_text()
    assert (moved / "catalog.csv").read_text() == "preserve engineering data"
    if os.name == "nt":
        path.write_text("old-path")
        process = subprocess.run(["cmd.exe", "/c", str(moved / "prepare_load.cmd")], input=b"\n",
                                 capture_output=True, timeout=20, check=False)
        assert process.returncode == 0, process.stdout + process.stderr
        assert moved.as_posix() in path.read_text()
        assert token in path.read_text()
    with pytest.raises(ValueError):
        write_plugin(moved)


def test_result_empty_header_still_validated(tmp_path):
    write_plugin(tmp_path)
    token = json.loads((tmp_path / "session.json").read_text())["token"]
    (tmp_path / "results.csv").write_text("not,the,right,header\n")
    checksum, length = _rolling_checksum((tmp_path/"results.csv").read_bytes())
    (tmp_path / "results.done").write_text(f"IMP_RESULTS {token} 0 {checksum}:{length}")
    with pytest.raises(ValueError, match="表头"):
        read_results(tmp_path)


def test_catalogue_rejects_invalid_palette(tmp_path):
    write_plugin(tmp_path)
    token = json.loads((tmp_path / "session.json").read_text())["token"]
    with (tmp_path / "catalog.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("TYPE", "NAME", "VALUE", "DETAIL"))
        writer.writerow(("BOARD", "current.brd", "1", "mils"))
        writer.writerow(("LAYER", "TOP", "2", "copper"))
        writer.writerow(("COLOR", "2", "999,0,0", "palette"))
    checksum, length = _rolling_checksum((tmp_path/"catalog.csv").read_bytes())
    (tmp_path / "catalog.done").write_text(f"IMP_CATALOG {token} 3 {checksum}:{length}")
    with pytest.raises(ValueError, match="调色板"):
        read_catalogue(tmp_path)


def test_catalogue_requires_current_token_and_complete_rows(tmp_path):
    write_plugin(tmp_path)
    token = json.loads((tmp_path/"session.json").read_text())["token"]
    with (tmp_path/"catalog.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("TYPE", "NAME", "VALUE", "DETAIL"))
        writer.writerow(("BOARD", "current.brd", "14", "mils"))
        writer.writerow(("LAYER", "L5_SIG", "2", "copper"))
        writer.writerow(("COLOR", "2", "255,0,0", "palette"))
    checksum, length = _rolling_checksum((tmp_path/"catalog.csv").read_bytes())
    (tmp_path/"catalog.done").write_text(f"IMP_CATALOG {token} 3 {checksum}:{length}")
    assert read_catalogue(tmp_path)["layers"][0]["NAME"] == "L5_SIG"
    (tmp_path/"catalog.done").write_text(f"IMP_CATALOG {token} 4 {checksum}:{length}")
    with pytest.raises(ValueError):
        read_catalogue(tmp_path)


def test_failed_native_result_not_reused(tmp_path):
    write_plugin(tmp_path)
    token = json.loads((tmp_path/"session.json").read_text())["token"]
    (tmp_path/"results.csv").write_text("NET,LAYER,WIDTH_MIL,SPACING_MIL,PAIR,IMPEDANCE_RULE,PHYSICAL_CSET,BRD,UNITS,MODE,KIND,X1_UU,Y1_UU,X2_UU,Y2_UU\n")
    checksum, length = _rolling_checksum((tmp_path/"results.csv").read_bytes())
    (tmp_path/"results.done").write_text(f"IMP_RESULTS {token} 0 {checksum}:{length}\n")
    assert read_results(tmp_path) == []
    (tmp_path/"results.done").write_text("INCOMPLETE\n")
    with pytest.raises(ValueError):
        read_results(tmp_path)


def test_native_results_reject_mixed_layers_and_invalid_kind(tmp_path):
    write_plugin(tmp_path)
    token = json.loads((tmp_path / "session.json").read_text())["token"]
    header = "NET,LAYER,WIDTH_MIL,SPACING_MIL,PAIR,IMPEDANCE_RULE,PHYSICAL_CSET,BRD,UNITS,MODE,KIND,X1_UU,Y1_UU,X2_UU,Y2_UU\n"
    rows = ("A,ETCH/TOP,5,,,,,board,mils,geometry,line,0,0,1,1\n"
            "B,ETCH/BOTTOM,5,,,,,board,mils,geometry,line,0,0,1,1\n")
    path = tmp_path / "results.csv"
    path.write_text(header + rows, encoding="utf-8")
    checksum, length = _rolling_checksum(path.read_bytes())
    (tmp_path / "results.done").write_text(f"IMP_RESULTS {token} 2 {checksum}:{length}")
    with pytest.raises(ValueError, match="混入"):
        read_results(tmp_path)


@pytest.mark.parametrize("extra", [
    "",
    r'''imp_clear()
P->props = cons(list('IMPEDANCE "ALL:ALL:90:5") P->props)
unless(imp_rule(P "L5_SIG" nil list('Impedance))->state == "CONFLICT" error("RULE_CONFLICT_MISSED\n"))
unless(imp_run("L5_SIG" "5" "" "0.01" "impedance" "P" "" "100" 2) == 0 error("CONFLICT_NET_HIGHLIGHTED\n"))
''',
    r'''imp_clear()
procedure(axlCnsNetFlattened(net key) "P1:P2:90:5")
unless(imp_rule(P "L5_SIG" nil list('Impedance))->state == "UNRESOLVED" error("UNRESOLVED_OVERRIDE_ACCEPTED\n"))
unless(imp_run("L5_SIG" "5" "" "0.01" "impedance" "P" "" "100" 2) == 0 error("UNRESOLVED_NET_HIGHLIGHTED\n"))
''',
    r'''imp_clear()
mock_highlight = nil
unless(imp_run("L5_SIG" "5" "" "0.01" "geometry" "UNRELATED" "" "" 2) == 1 error("GEOMETRY_NET_FILTER_FAILED\n"))
unless(null(errset(imp_run("NOT_A_LAYER" "5" "" "0.01" "pair" "" "" "" 2) nil))
  error("INVALID_LAYER_ACCEPTED\n"))
''',
    r'''imp_clear()
DP->groupMembers = list(list(nil 'objType "group" 'groupMembers list(P)) list(nil 'objType "group" 'groupMembers list(N)))
unless(length(imp_pair_map(D)[P->name]->mate) == 1 error("XNET_PAIR_NOT_RESOLVED\n"))
unless(imp_run("L5_SIG" "5" "6" "0.01" "pair" "P" "PAIR1" "100" 2) == 1 error("XNET_PAIR_FILTER_FAILED\n"))
''',
    r'''imp_clear()
old = imp_budget imp_budget = 0
unless(null(errset(imp_run("L5_SIG" "5" "6" "0.01" "pair" "" "PAIR1" "100" 2) nil)) && null(imp_marked)
  error("PARTIAL_HIGHLIGHT_ON_BUDGET_FAILURE\n"))
imp_budget = old
''',
    r'''imp_clear()
procedure(axlCNSEcsetValueGet(@rest args) error("Constraint API unavailable\n"))
imp_refresh()
mock_highlight = nil
unless(imp_run("L5_SIG" "5" "" "0.01" "geometry" "UNRELATED" "" "" 2) == 1
  error("GEOMETRY_DEPENDED_ON_ELECTRICAL_API\n"))
''',
    r'''imp_clear()
old_highlight = getd('axlHighlightObject)
highlight_count = 0
procedure(axlHighlightObject(obj permanent)
  highlight_count = highlight_count + 1
  if(highlight_count == 2 then nil else t))
unless(null(errset(imp_run("L5_SIG" "5" "" "0.01" "pair" "" "PAIR1" "" 2) nil)) && null(imp_marked)
  error("FAILED_DISPLAY_NOT_ROLLED_BACK\n"))
''',
    r'''imp_clear()
imp_guard_board = "different.brd" imp_guard_token = imp_token
unless(null(errset(imp_run("L5_SIG" "5" "" "0.01" "geometry" "" "" "" 2) nil)) && null(imp_marked)
  error("WRONG_BOARD_GUARD_NOT_ENFORCED\n"))
''',
])
def test_native_skill_current_layer_pairs_constraints_and_temporary_highlight(tmp_path, extra):
    engine = Path(os.environ.get("CDSROOT", "C:/Cadence/SPB_17.4"))/"tools/bin/cnskill.exe"
    if not engine.is_file():
        pytest.skip("Cadence language engine absent (not PCB Editor)")
    folder = write_plugin(tmp_path)
    code = r'''
procedure(axlCmdRegister(@rest args) t)
procedure(axlDBGetDesignUnits() list("mils" 4))
procedure(axlCurrentDesign() "fixture.brd")
procedure(axlGetDrawingName() "fixture.brd")
procedure(axlSubclassRoute() list("TOP" "L5_SIG" "BOTTOM"))
procedure(axlGetParam(name) list(nil 'groupMembers list("TOP" "L5_SIG" "BOTTOM")))
procedure(axlColorGet(key) if(key == 'count then 4 else list(255 0 0)))
procedure(axlLayerGet(layer) list(nil 'color 2))
procedure(axlDBGetProperties(obj) obj->props)
procedure(axlCNSEcsetValueGet(@rest args) if(null(car(args)) then list('Impedance) else nil))
procedure(axlNetEcsetValueGet(net key) net->impedance)
procedure(axlCnsNetFlattened(net key) nil)
procedure(axlCNSGetPhysical(cset layer rule) if(rule == 'width_min then 5.0 else 6.0))
procedure(axlDBControl(@rest args) 3)
procedure(axlDBRefreshId(obj) t)
procedure(axlVisibleGet() list("previous"))
procedure(axlVisibleSet(value) t)
procedure(axlVisibleLayer(layer value) t)
procedure(axlVisibleUpdate(@rest args) t)
procedure(axlUIWUpdate(@rest args) t)
procedure(axlUIWPrint(@rest args) t)
mock_highlight = nil
procedure(axlHighlightObject(obj permanent)
  when(permanent error("PERMANENT_SEGMENT_HIGHLIGHT_USED\n"))
  unless(obj->layer == "ETCH/L5_SIG" error("OTHER_LAYER_HIGHLIGHTED\n"))
  mock_highlight = cons(obj mock_highlight) t)
procedure(axlDehighlightObject(@rest args) t)
procedure(axlMKSConvert(value source target) value)
procedure(axlAirGap(a b layer)
  unless(a->layer == b->layer && layer == "ETCH/L5_SIG" error("CROSS_LAYER_MEASUREMENT\n"))
  unless((a->net == P && b->net == N) || (a->net == N && b->net == P) error("WRONG_PAIR_NEIGHBOUR\n"))
  list(list(0 0) list(0 6) 6.0))
P = list(nil 'name "P" 'objType "net" 'impedance "ALL:ALL:100.0:5" 'props list(list('PHYSICAL_CONSTRAINT_SET "Z100")))
N = list(nil 'name "N" 'objType "net" 'impedance "ALL:ALL:100.0:5")
U = list(nil 'name "UNRELATED" 'objType "net")
DP = list(nil 'name "PAIR1" 'groupMembers list(P N))
D = list(nil 'nets list(P N U) 'diffpair list(DP))
procedure(axlDBGetDesign() D)
a = list(nil 'net P 'layer "ETCH/L5_SIG" 'width 5.0 'startEnd list(list(0 0) list(10 0)) 'bBox list(list(0 0) list(10 0)))
b = list(nil 'net N 'layer "ETCH/L5_SIG" 'width 5.0 'startEnd list(list(0 11) list(10 11)) 'bBox list(list(0 11) list(10 11)))
c = list(nil 'net U 'layer "ETCH/L5_SIG" 'width 5.0 'startEnd list(list(0 5) list(10 5)) 'bBox list(list(0 5) list(10 5)))
'''
    code += f'load({quote_skill((folder/"imp_highlight.il").as_posix())})\n'
    code += f'imp_setup({quote_skill(folder.as_posix())} "fixture")\n'
    code += r'''
procedure(imp_segments(layer)
  unless(layer == "L5_SIG" error("LAYER_FILTER_MISSING\n")) list(a b c))
unless(imp_run("L5_SIG" "5" "6" "0.01" "pair" "" "PAIR1" "100" 2) == 2 error("PAIR_FILTER_FAILED\n"))
unless(length(mock_highlight) == 2 error("WRONG_SEGMENT_COUNT\n"))
imp_clear()
mock_highlight = nil
unless(imp_run("L5_SIG" "5" "" "0.01" "impedance" "" "" "100" 2) == 2 error("IMPEDANCE_NET_FILTER_FAILED\n"))
unless(imp_target("A:B:100:5") == nil error("PINPAIR_RULE_GUESSED\n"))
imp_refresh()
unless(length(imp_rule(P "L5_SIG" nil list('Impedance))->targets) == 1 error("ELECTRICAL_RULE_NOT_READ\n"))
unless(imp_rule(P "L5_SIG" nil list('Impedance))->width == 5.0 error("PHYSICAL_RULE_NOT_READ\n"))
'''
    code += extra + '\nprintf("IMP_CONTRACT_OK\\n")\nexit()\n'
    harness = tmp_path/"contract.il"
    harness.write_text(code, encoding="ascii")
    process = subprocess.run([str(engine), "-nongraph", str(harness)], capture_output=True, timeout=30, check=False)
    output = (process.stdout + process.stderr).decode("utf-8", "replace")
    assert "*Error*" not in output and "*WARNING*" not in output, output
    assert "IMP_CONTRACT_OK" in output
    assert (folder/"results.done").is_file()
    marker = (folder/"results.done").read_text().split()
    if marker != ["INCOMPLETE"]:
        digest, length = _rolling_checksum((folder/"results.csv").read_bytes())
        assert marker[-1] == f"{digest}:{length}"


def test_integrated_main_window_selects_native_tab():
    import tkinter as tk

    from src.ui.dfm_dialog import DFMDialog
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(str(exc))
    try:
        with patch("src.ui.prefs.load_prefs", return_value={}), patch("src.ui.prefs.save_prefs"):
            dialog = DFMDialog(root, standalone=True)
        dialog.trace_filter_button.invoke()
        assert dialog.workbench.select() == str(dialog.impedance_panel)
        assert set(dialog.impedance_panel.inputs) == {"layer", "width", "spacing"}
        dialog._close()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass

"""Direct CAD launch contracts and SKILL language tests, not licensed GUI evidence."""
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src.adapters.allegro_trace import (
    TRACE_FORM,
    TRACE_SKILL,
    AllegroTraceSession,
    trace_number,
)
from src.adapters.impedance_plugin import quote_skill


def test_direct_trace_script_and_launch(tmp_path):
    board, editor = tmp_path / "board.brd", tmp_path / "allegro.exe"
    board.touch()
    editor.touch()
    session = AllegroTraceSession(board, editor, tmp_path / "session")
    text = session.prepare_trace("L5_SIG", "5", "6").read_text()
    assert "MPA_TRACE_SEARCH" in text
    assert "mpa_trace_width = 5" in text and "mpa_trace_spacing = 6" in text
    assert "imp_setup" not in text and "imp_prepare" not in text
    assert "INFO_ONLY" in TRACE_FORM and "NONEDITABLE" not in TRACE_FORM
    with patch("src.adapters.allegro_bridge.subprocess.Popen") as launch:
        session.launch()
        assert launch.call_args.args[0][1:3] == ["-readonly", "-s"]
        assert launch.call_args.args[0][-1] == str(board.resolve())
    session.ack.write_text("0 HIGHLIGHTED 8\n")
    assert session.status() == (0, "HIGHLIGHTED")
    assert session.match_count() == 8


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "0", "bad"])
def test_trace_rejects_bad_numbers(value):
    with pytest.raises(ValueError):
        trace_number(value, "线宽")


def test_native_skill_language_and_layer_width_spacing(tmp_path):
    engine = Path(os.environ.get("CDSROOT", "C:/Cadence/SPB_17.4")) / "tools/bin/cnskill.exe"
    if not engine.is_file():
        pytest.skip("Cadence language engine absent")
    il = tmp_path / "trace.il"
    il.write_text(TRACE_SKILL, encoding="ascii")
    harness = tmp_path / "contract.il"
    harness.write_text(r'''
procedure(axlCmdRegister(@rest args) t)
procedure(axlDBGetDesignUnits() list("mils" 4))
procedure(axlGetParam(name) list(nil 'groupMembers list("TOP" "BOTTOM")))
procedure(axlMKSConvert(v source target) v)
procedure(axlAirGap(a b layer) list(list(0 0) list(0 6) 6.0))
P = list(nil 'name "P") N = list(nil 'name "N")
a = list(nil 'objType "line" 'net P 'isEtch t 'layer "ETCH/TOP" 'width 5.0 'bBox list(list(0 0) list(10 0)))
b = list(nil 'objType "arc" 'net N 'isEtch t 'layer "ETCH/TOP" 'width 5.0 'bBox list(list(0 6) list(10 6)))
c = list(nil 'objType "line" 'net P 'isEtch t 'layer "ETCH/BOTTOM" 'width 5.0)
P->branches = list(list(nil 'children list(list(nil 'objType "path" 'segments list(a c)))))
N->branches = list(list(nil 'children list(list(nil 'objType "path" 'segments list(b)))))
D = list(nil 'nets list(P N))
procedure(axlDBGetDesign() D)
''' + f'load({quote_skill(il.as_posix())})\n' + r'''
unless(length(mpa_trace_scan("TOP" 5 6)) == 2 error("WRONG_COUNT\n"))
unless(length(mpa_trace_scan("TOP" 8 nil)) == 0 error("WRONG_WIDTH\n"))
unless(length(mpa_trace_scan("TOP" 5 7)) == 0 error("WRONG_SPACING\n"))
unless(null(errset(mpa_trace_scan("BAD" 5 nil) nil)) error("WRONG_LAYER\n"))
printf("TRACE_CONTRACT_OK\n")
exit()
''', encoding="ascii")
    process = subprocess.run([str(engine), "-nongraph", str(harness)], capture_output=True, timeout=30, check=False)
    output = (process.stdout + process.stderr).decode("utf-8", "replace")
    assert "*Error*" not in output and "TRACE_CONTRACT_OK" in output, output

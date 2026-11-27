"""Read-only, opt-in extracta probe; never opens the PCB Editor UI."""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.adapters.manufacturing_export import _run
from src.brd_input import content_sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    board, out = Path(args.board).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    before = content_sha256(board)
    copy = out / "board.brd"
    shutil.copy2(board, copy)
    views = {
        "components": "SYMBOL\nREFDES\nSYM_X\nSYM_Y\nSYM_ROTATE\nSYM_MIRROR\nSYM_TYPE\nSYM_NAME\nSYM_EXTENTS_X1\nSYM_EXTENTS_Y1\nSYM_EXTENTS_X2\nSYM_EXTENTS_Y2\nEND\n",
        "layers": "LAYER\nLAYER_SORT\nLAYER_SUBCLASS\nLAYER_CONDUCTOR\nLAYER_MATERIAL\nLAYER_THICKNESS\nLAYER_TYPE\nLAYER_STACKUP_NAME\nEND\n",
        "board": "BOARD\nBOARD_NAME\nBOARD_UNITS\nBOARD_LAYERS\nBOARD_BOARD_THICKNESS\nEND\n",
    }
    exe = Path(os.environ.get("CDSROOT", "C:/Cadence/SPB_17.4")) / "tools/bin/extracta.exe"
    env = dict(os.environ, PATH=str(exe.parent) + os.pathsep + os.environ.get("PATH", ""))
    outcomes = {}
    for name, text in views.items():
        view, result = out / f"{name}.txt", out / f"{name}.out"
        view.write_text(text, encoding="ascii")
        try:
            code, log = _run([str(exe), str(copy), str(view), str(result)], out, 60, env)
            (out / f"{name}.log").write_text(log, encoding="utf-8")
            outcomes[name] = dict(returncode=code, produced=result.is_file())
        except TimeoutError as exc:
            outcomes[name] = dict(error=str(exc))
    if content_sha256(board) != before:
        raise ValueError("Source changed")
    payload = dict(source_unchanged=True, outcomes=outcomes)
    (out / "probe.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()

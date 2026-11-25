"""Opt-in licensed PCB Editor current-BRD catalogue probe; no synthetic axl calls."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.impedance import read_catalogue
from src.impedance_plugin import quote_skill, write_plugin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", required=True)
    parser.add_argument("--board", required=True)
    parser.add_argument("--product", default="Allegro_performance")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    editor, board = Path(args.editor).resolve(), Path(args.board).resolve()
    if not editor.is_file() or "viewer" in editor.name.lower() or not board.is_file():
        raise ValueError("需要真实 PCB Editor（不是 Free Viewer）和有效 BRD")
    output = Path(args.out).resolve()
    folder = write_plugin(output)
    token = json.loads((folder/"session.json").read_text())["token"]
    script = folder/"native_probe.scr"
    script.write_text(f'skill load({quote_skill((folder/"imp_highlight.il").as_posix())})\n'
                      f'skill imp_setup({quote_skill(folder.as_posix())} "{token}")\n'
                      'skill imp_refresh()\nexit\n', encoding="mbcs")
    command = [str(editor), "-product", args.product, "-readonly", "-nograph", "-s", str(script), str(board)]
    try:
        process = subprocess.run(command, cwd=folder, capture_output=True, timeout=60, check=False)
        log = (process.stdout + process.stderr).decode("mbcs", "replace")
        evidence = {"synthetic_axl": False, "editor": str(editor), "board": str(board),
                    "returncode": process.returncode, "catalogue_verified": False, "highlight_verified": False}
        (folder/"native_console.log").write_text(log, encoding="utf-8")
        if process.returncode == 0:
            catalogue = read_catalogue(folder)
            evidence.update(catalogue_verified=True, layers=len(catalogue["layers"]),
                            nets=len(catalogue["nets"]), pairs=len(catalogue["pairs"]))
        else:
            evidence["error"] = "PCB Editor 未成功执行原生插件，请检查安装/产品许可/命令日志"
    except subprocess.TimeoutExpired:
        evidence = {"synthetic_axl": False, "catalogue_verified": False, "highlight_verified": False,
                    "error": "原生 CAD 读取超时（许可/产品选择/文件提示可能未确认）"}
    (folder/"native_verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["catalogue_verified"] else 1


if __name__ == "__main__":
    sys.exit(main())

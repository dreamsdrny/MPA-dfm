"""Opt-in real PCB Editor scan probe; no mocked axl calls, source stays read-only."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.adapters.allegro_trace import AllegroTraceSession
from src.infrastructure.snapshots import capture_sources, verify_sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True)
    parser.add_argument("--editor", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--layer", required=True)
    parser.add_argument("--width", default="5")
    parser.add_argument("--spacing", default="")
    args = parser.parse_args()
    sources = capture_sources([args.board])
    session = AllegroTraceSession(args.board, args.editor, args.out)
    session.prepare_trace(args.layer, args.width, args.spacing)
    text = session.script.read_text(encoding="mbcs")
    # Noninteractive database scan only; not evidence of visible highlighting.
    probe = ('skill mpa_probe = errset(length(mpa_trace_scan(mpa_trace_layer mpa_trace_width mpa_trace_spacing)) t)\n'
             'skill if(mpa_probe then mpa_trace_reply("SCANNED" car(mpa_probe)) else mpa_trace_reply("FAILED" 0))\nexit\n')
    text = text.replace("MPA_TRACE_SEARCH\n", probe)
    session.script.write_text(text, encoding="mbcs")
    command = [session.editor_path, "-product", "Allegro_performance", "-readonly", "-nograph", "-s", str(session.script), session.brd_path]
    evidence = {"synthetic_axl": False, "visible_highlight_verified": False, "command": command}
    try:
        process = subprocess.run(command, cwd=session.folder, capture_output=True, timeout=60, check=False)
        (session.folder / "console.log").write_bytes(process.stdout + process.stderr)
        evidence.update(returncode=process.returncode, state=session.status()[1], matches=session.match_count())
    except subprocess.TimeoutExpired:
        evidence.update(state="TIMEOUT", error="许可/产品选择/打开板可能未完成")
    verify_sources(sources)
    evidence["source_unchanged"] = True
    (session.folder / "verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["state"] == "SCANNED" else 1


if __name__ == "__main__":
    sys.exit(main())

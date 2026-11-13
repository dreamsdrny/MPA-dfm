"""Native Cadence artwork batch export. Source BRD is never modified."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from src.allegro_bridge import discover_editor

GERBER_EXTENSIONS = {".art", ".gbr", ".ger", ".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gko", ".gm1", ".gtp", ".gbp"}


def gerber_files(folder):
    return sorted(p for p in Path(folder).rglob("*") if p.is_file() and p.suffix.lower() in GERBER_EXTENSIONS)


def export_brd_gerbers(brd_path, out_dir, editor_path="", timeout=180):
    board = Path(brd_path).resolve()
    if not board.is_file():
        raise ValueError("BRD 文件不存在 / BRD not found")
    editor = discover_editor(editor_path)
    artwork = str(Path(editor).with_name("artwork.exe")) if editor else shutil.which("artwork.exe")
    if not artwork or not Path(artwork).is_file():
        raise ValueError("未找到 Cadence artwork.exe，请设置 PCB Editor 或选择已有 Gerber 目录")
    target = Path(out_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="evan-artwork-") as temporary:
        stage = Path(temporary)
        # Isolate film output paths and journal/log artifacts from the user's board.
        copy = stage / "board.brd"
        shutil.copy2(board, copy)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.run([artwork, str(copy)], cwd=stage, capture_output=True,
                                 timeout=timeout, creationflags=flags)
        log = (process.stdout + process.stderr).decode("utf-8", "replace")
        files = gerber_files(stage)
        if not files or (process.returncode and "ARTWORK had warnings" not in log):
            (target / "artwork_export.log").write_text(log, encoding="utf-8")
            raise ValueError("BRD Gerber 导出失败：请检查板内 Artwork films/RS-274X 设置与 Cadence 许可。\n" + log[-1600:])
        # A private directory per run avoids mixing stale and newly exported layers.
        run_dir = Path(tempfile.mkdtemp(prefix="films-", dir=target))
        outputs = []
        for file in files:
            dest = run_dir / file.relative_to(stage)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, dest)
            outputs.append(str(dest))
        (run_dir / "artwork_export.log").write_text(log, encoding="utf-8")
        (run_dir / "source.json").write_text(json.dumps(dict(brd=str(board), size=board.stat().st_size,
            modified_ns=board.stat().st_mtime_ns, exporter=artwork, files=outputs), ensure_ascii=False, indent=2), encoding="utf-8")
    return dict(directory=str(run_dir), files=outputs, source_brd=str(board), exporter=artwork,
                warnings=bool(process.returncode), log=log)

"""Native export orchestration on isolated, hash-bound copies of the source BRD.

No placeholder manufacturing files. Missing tools/licences and empty output are
explicit stages. ODB++ Inside is a separately installed Siemens translator.
"""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from src.allegro_bridge import discover_editor
from src.pcb_geometry_data import file_sha256

NC_PARAMETERS = """INTEGER-PLACES 3
DECIMAL-PLACES 5
X-OFFSET 0.000000
Y-OFFSET 0.000000
COORDINATES ABSOLUTE
OUTPUT-UNITS METRIC
REPEAT-CODES NO
SUPPRESS-LEAD-ZEROES NO
SUPPRESS-TRAIL-ZEROES NO
SUPPRESS-EQUAL NO
TOOL-SELECT YES
ENHANCED_EXCELLON YES
CODE ASCII
SEPARATE YES
SEPARATE-ROUTING YES
DRILLING LAYER-PAIR
"""


def discover_odb_exporter(configured="", editor_path=""):
    if configured:
        path = Path(configured.strip().strip('"')).expanduser()
        if path.is_dir():
            path /= "brd2odb.exe"
        return str(path.resolve()) if path.is_file() and path.suffix.lower() == ".exe" else ""
    candidates = [shutil.which("brd2odb.exe")]
    home = os.environ.get("ALLEGRO_BRD2ODB")
    if home:
        p = Path(home.strip('"'))
        candidates.append(str(p if p.is_file() else p/"brd2odb.exe"))
    editor = discover_editor(editor_path)
    if editor:
        candidates.append(str(Path(editor).with_name("brd2odb.exe")))
    for base in ("C:/MentorGraphics", "C:/Program Files/MentorGraphics", "C:/Program Files (x86)/MentorGraphics",
                 "C:/Siemens", "C:/Program Files/Siemens", "C:/Valor"):
        root = Path(base)
        if root.is_dir():
            candidates.extend(str(p) for p in root.glob("*/brd2odb.exe"))
            candidates.extend(str(p) for p in root.glob("*/*/brd2odb.exe"))
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()), "")


def _run(command, directory, timeout, environment=None):
    """No shell strings; kill only this export's process tree on timeout."""
    with subprocess.Popen(command, cwd=directory, env=environment, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
            else:
                process.kill()
            stdout, stderr = process.communicate()
            Path(directory, "export_process.log").write_bytes(stdout + stderr)
            raise TimeoutError(f"原生导出超过 {timeout}s，已停止本次进程；未完成")
    return process.returncode, (stdout + stderr).decode("utf-8", "replace")


def _odb_candidates(directory):
    """Inventory only; full container validation occurs before use."""
    paths = list(Path(directory).rglob("*.tgz")) + list(Path(directory).rglob("*.zip"))
    paths += list(Path(directory).rglob("*.tar.gz"))
    for matrix in Path(directory).rglob("matrix"):
        if matrix.is_dir() and any((matrix/n).is_file() for n in ("matrix", "matrix.gz", "matrix.Z")):
            paths.append(matrix.parent)
    archives = [p for p in paths if p.is_file()]
    return sorted(archives or paths)


def export_brd_manufacturing(brd_path, out_dir, editor_path="", odb_exporter="", timeout=180, progress=None):
    board = Path(brd_path).resolve()
    if not board.is_file() or board.suffix.lower() != ".brd":
        raise ValueError("请选择真实 .brd 文件")
    digest = file_sha256(board)
    stat = board.stat()
    target = Path(out_dir).resolve()
    if target == board:
        raise ValueError("输出不可覆盖源 BRD")
    target.mkdir(parents=True, exist_ok=True)
    # ASCII working paths isolate CAD limitations with Chinese/space filenames.
    stage_parent = Path(tempfile.gettempdir()) / "opencode"
    stage_parent.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="manufacturing-", dir=target))
    manifest = dict(schema_version=1, brd=str(board), brd_sha256=digest, size=stat.st_size,
                    modified_ns=stat.st_mtime_ns, created_at=datetime.now().isoformat(timespec="seconds"),
                    directory=str(run_dir), stages={}, artifacts=[], source_unchanged=False)
    editor = discover_editor(editor_path)
    tool_bin = Path(editor).parent if editor else None
    odb_tool = discover_odb_exporter(odb_exporter, editor_path)
    env = dict(os.environ)
    if tool_bin:
        env["PATH"] = str(tool_bin) + os.pathsep + env.get("PATH", "")
    with tempfile.TemporaryDirectory(prefix="evan-manufacturing-", dir=stage_parent) as temp:
        stage = Path(temp)
        for kind in ("gerber", "drill", "route", "odb"):
            if progress:
                progress(kind)
            work = stage/kind
            work.mkdir()
            copy = work/"board.brd"
            shutil.copy2(board, copy)
            if file_sha256(copy) != digest:
                raise ValueError("BRD 复制期间源内容变化，导出已停止")
            entry = dict(status="unavailable", source_brd_sha256=digest, files=[], log="")
            output = run_dir/kind
            output.mkdir()
            try:
                if kind == "gerber":
                    from src.gerber_export import export_brd_gerbers
                    result = export_brd_gerbers(copy, output, editor_path, timeout)
                    directory = Path(result["directory"])
                    files = [Path(p) for p in result["files"]]
                    # Bind the nested marker to the ORIGINAL exact BRD, not its temporary copy.
                    (directory/"source.json").write_text(json.dumps(dict(brd=str(board), brd_sha256=digest,
                        size=stat.st_size, modified_ns=stat.st_mtime_ns, files=[str(p) for p in files]),
                        ensure_ascii=False, indent=2), encoding="utf-8")
                    entry.update(status="exported", directory=str(directory), files=[str(p) for p in files],
                                 log=result.get("log", ""), warnings=result.get("warnings", False))
                else:
                    if kind in ("drill", "route"):
                        exe = tool_bin/("nctape.exe" if kind == "drill" else "ncroute.exe") if tool_bin else None
                        if exe is None or not exe.is_file():
                            raise FileNotFoundError(f"未找到 {'nctape.exe' if kind == 'drill' else 'ncroute.exe'}，请设置 PCB Editor")
                        (work/"nc_param.txt").write_text(NC_PARAMETERS, encoding="ascii")
                        command = [str(exe), "-$", "-v", "-n" if kind == "drill" else "-o",
                                   str(work/("drill.drl" if kind == "drill" else "route.rou")), str(copy)]
                        process_env = dict(env, NCDPATH=str(work))
                    else:
                        if not odb_tool:
                            raise FileNotFoundError("未安装/未找到 ODB++ Inside brd2odb.exe；设置导出器路径或 ALLEGRO_BRD2ODB。ODB++ 检查未执行")
                        # Vendor documented batch mode: do not suppress pads or anonymize nets.
                        command = [odb_tool, "-d", "-jp", str(work), "-jn", "board_odb", "-gz", "-no_view", str(copy)]
                        process_env = dict(env, ALLEGRO_BRD2ODB=str(Path(odb_tool).parent))
                    code, log = _run(command, work, timeout, process_env)
                    entry.update(command=command, returncode=code, log=log)
                    for log_file in work.glob("*.log"):
                        shutil.copy2(log_file, output/log_file.name)
                    if code:
                        raise ValueError(f"{kind} 原生导出返回 {code}；{log[-1200:]}")
                    if kind == "odb":
                        candidates = _odb_candidates(work)
                        if len(candidates) != 1:
                            raise ValueError(f"ODB++ 未生成唯一有效产物（{len(candidates)}），不能使用 .out 占位或猜测旧文件")
                        candidate = candidates[0]
                        from src.design_inputs.archive import InputContainer
                        with InputContainer(candidate) as container:
                            if not any(n.lower().endswith(("matrix/matrix", "matrix/matrix.gz", "matrix/matrix.z")) for n in container.names):
                                raise ValueError("ODB++ 产物缺 matrix/matrix")
                        dest = output/candidate.name
                        if candidate.is_dir():
                            shutil.copytree(candidate, dest)
                        else:
                            shutil.copy2(candidate, dest)
                        entry.update(status="exported", path=str(dest), files=[str(p) for p in dest.rglob("*") if p.is_file()]
                                     if dest.is_dir() else [str(dest)])
                    else:
                        suffix = ".drl" if kind == "drill" else ".rou"
                        files = sorted(work.glob("*" + suffix))
                        native_log = work/("ncdrill.log" if kind == "drill" else "ncroute.log")
                        log_text = native_log.read_text(encoding="ascii", errors="replace") if native_log.is_file() else ""
                        if files and (not re.search(r"FORMAT\s+3\.5\b", log_text) or
                                      not re.search(r"OUTPUT-UNITS\s+METRIC\b", log_text) or
                                      not re.search(r"ENHANCED_EXCELLON\s+YES\b", log_text) or
                                      not re.search(r"MACHINE-OFFSET\s+x:0\.0+\s+y:0\.0+", log_text)):
                            raise ValueError("NC 日志未确认本次 3.5/METRIC/零偏移/Enhanced 参数，拒绝猜测坐标格式")
                        for file in files:
                            shutil.copy2(file, output/file.name)
                        shutil.copy2(work/"nc_param.txt", output/"nc_param.txt")
                        # The recorded format is native exporter input, not guessed from coordinates.
                        entry.update(status="exported" if files else "no_output", directory=str(output),
                                     files=[str(output/p.name) for p in files], number_format=[3, 5],
                                     note="槽孔未生成；不证明板上无槽孔" if kind == "route" and not files else "")
                if file_sha256(copy) != digest:
                    entry.update(status="invalid", error="导出器修改了 BRD 副本，跨格式来源不再一致，拒绝此阶段产物")
            except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired) as exc:
                entry.update(status="timeout" if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else "unavailable", error=str(exc))
                for log_file in work.glob("*.log"):
                    shutil.copy2(log_file, output/log_file.name)
            (output/"export.log").write_text(entry.get("log", "") + "\n" + entry.get("error", ""), encoding="utf-8")
            manifest["stages"][kind] = entry
            if file_sha256(board) != digest:
                raise ValueError("源 BRD 在导出期间变化，不能合并本次制造结果")
    for entry in manifest["stages"].values():
        if entry["status"] == "exported":
            for file in entry["files"]:
                p = Path(file)
                if p.resolve().is_relative_to(run_dir):
                    manifest["artifacts"].append(dict(path=str(p), sha256=file_sha256(p)))
                if entry.get("number_format") and p.is_file():
                    parameter_file = p.parent/"nc_param.txt"
                    if parameter_file.is_file() and not any(a["path"] == str(parameter_file) for a in manifest["artifacts"]):
                        manifest["artifacts"].append(dict(path=str(parameter_file), sha256=file_sha256(parameter_file)))
    manifest["source_unchanged"] = file_sha256(board) == digest
    path = run_dir/"manufacturing_manifest.json"
    manifest["manifest_path"] = str(path)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def verify_export_manifest(manifest):
    if not manifest.get("source_unchanged") or file_sha256(manifest["brd"]) != manifest["brd_sha256"]:
        raise ValueError("源 BRD 与制造导出快照不一致，请重新分析")
    root = Path(manifest["directory"]).resolve()
    for item in manifest["artifacts"]:
        path = Path(item["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file() or file_sha256(path) != item["sha256"]:
            raise ValueError("制造产物内容变化或超出当前导出目录：" + str(path))


def native_drill_metadata(manifest, aligned=False):
    """Only files produced by THIS known nctape invocation get native semantics."""
    result = {}
    entry = manifest["stages"]["drill"]
    for file in entry.get("files", []) if entry["status"] == "exported" else []:
        path = Path(file)
        text = path.read_text(encoding="ascii", errors="replace")
        span = re.fullmatch(r"drill-(\d+)-(\d+)(?:-(laser|plasma|punch|other))?(-np)?(?:-len\d+)?\.drl", path.name, re.I)
        plating = set(re.findall(r"\b(NON_PLATED|NON-PLATED|UNPLATED|PLATED)\b", text))
        plated = True if plating == {"PLATED"} else False if plating and "PLATED" not in plating else None
        # Native filename and header must agree when both identify plating.
        if span and span[4] and plated is True:
            raise ValueError("原生钻孔文件名与镀孔声明冲突")
        process = "laser" if span and span[3] == "laser" else "mechanical" if span and not span[3] else None
        result[path.name] = dict(plated=plated, process=process,
                                start_layer=int(span[1]) if span else None, end_layer=int(span[2]) if span else None,
                                alignment_confirmed=bool(aligned), number_format=[3, 5],
                                brd_alignment=bool(aligned), evidence="本次 nctape 原生层对输出 + 文件镀孔声明", nets={})
        result[path.name]["native_cadence"] = True
    return result

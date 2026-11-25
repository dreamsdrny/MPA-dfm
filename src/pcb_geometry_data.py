"""PCB 制造补充资料：显式路径、BRD 版本和坐标合同，不推测孔型/网络。"""
import hashlib
import json
import subprocess
from pathlib import Path


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_geometry_data(path, brd_path):
    """独立 JSON 可位于任意资料目录；相对路径相对于 JSON，而非程序目录。"""
    from src.resource_limits import read_bounded
    path = Path(path).resolve()
    data = json.loads(read_bounded(path, 16 * 1024 * 1024).decode("utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("PCB 几何补充资料需要 schema_version: 1")
    if data.get("brd_sha256", "").lower() != file_sha256(brd_path):
        raise ValueError("PCB 几何资料的 BRD SHA-256 与当前 BRD 不一致，请重新导出/确认资料")
    options = data.get("geometry_options", {})
    if not isinstance(options, dict):
        raise ValueError("geometry_options 必须为对象")
    options = dict(options)
    for key in ("drill_directory",):
        if options.get(key):
            target = Path(options[key])
            options[key] = str((target if target.is_absolute() else path.parent / target).resolve())
            if not Path(options[key]).is_dir():
                raise ValueError(f"{key}: 目录不存在")
    options["data_source"] = str(path)
    return options


def geometry_template(brd_path, drill_directory=""):
    """填写真实版本哈希，但绝不自动确认孔型、跨度、坐标或制程能力。"""
    from src.layout_drill import drill_files
    directory = Path(drill_directory).resolve() if drill_directory else None
    drills = {}
    if directory and directory.is_dir():
        drills = {p.relative_to(directory).as_posix(): dict(plated=None, process=None,
                  start_layer=None, end_layer=None, alignment_confirmed=False, nets={})
                  for p in drill_files(directory)}
    return dict(schema_version=1, brd_sha256=file_sha256(brd_path),
                geometry_options=dict(drill_directory=str(directory) if directory else "",
                    drills=drills, limits=dict(npth_to_copper_mm=None, pad_to_mask_clearance_mm=None),
                    copper_balance=dict(cell_mm=10, max_delta_percent=None),
                    object_rules=dict(pad_to_pad_mm=None, via_to_pad_mm=None, via_to_mask_mm=None,
                                      hole_to_unconnected_copper_mm=None, pad_to_mask_mm=None, paste_bridge=True),
                    pad_geometry=dict(unit="mm", coordinate_system="gerber", alignment_confirmed=False,
                                      complete=False, pads=[]),
                    via_geometry=dict(unit="mm", coordinate_system="gerber", alignment_confirmed=False,
                                      complete=False, vias=[])))


def ensure_geometry_folder(brd_path, gerber_dir="", editor_path="", force_export=False):
    """几何检查不可关闭。没有指定底片时必从当前 BRD 重新导出，不复用旧目录。"""
    if gerber_dir and not force_export:
        folder = Path(gerber_dir).resolve()
        if not folder.is_dir():
            raise ValueError("Gerber 目录不存在；请选择同版本资料或清空目录后自动导出")
        # Native exports carry a version marker; reject stale exports of this BRD.
        marker = folder / "source.json"
        if marker.is_file():
            source = json.loads(marker.read_text(encoding="utf-8-sig"))
            board = Path(brd_path).resolve()
            if source.get("brd") and Path(source["brd"]).resolve() != board:
                raise ValueError("Gerber 导出记录来自另一份 BRD，请重新导出")
            stat = board.stat()
            if source.get("size") != stat.st_size or source.get("modified_ns") != stat.st_mtime_ns:
                raise ValueError("Gerber 导出记录已过期，当前 BRD 已修改，请重新导出")
            if not source.get("brd_sha256") or source["brd_sha256"] != file_sha256(board):
                raise ValueError("Gerber 导出记录缺 BRD 内容哈希或与当前 BRD 不符，请重新导出")
        return dict(directory=str(folder), warnings=False, source="已有 Gerber")
    from src.gerber_export import export_brd_gerbers
    try:
        bundle = export_brd_gerbers(brd_path, str(Path(brd_path).resolve().parent / "Gerber_DFM"), editor_path)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("实际几何检查必须执行，无法获取 Gerber。请设置 Cadence PCB Editor/Artwork，"
                         "或选择同版本 Gerber；不会退回仅参数比对。\n" + str(exc)) from exc
    return dict(bundle, source="当前 BRD 自动导出")

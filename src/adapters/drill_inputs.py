"""Read Excellon sets and explicit per-file metadata before domain evaluation."""
from pathlib import Path


def read_drills(folder, options):
    from src.layout_drill import drill_files, read_drill
    metadata = options.get("drills", {})
    if not isinstance(metadata, dict):
        raise ValueError("drills 配置必须为文件相对路径映射")
    directory = Path(options.get("drill_directory") or folder).resolve()
    if not directory.is_dir():
        raise ValueError("drill_directory 不存在")
    drills, errors, seen = [], [], set()
    for path in drill_files(directory):
        key = path.relative_to(directory).as_posix()
        seen.add(key)
        try:
            drills.append(read_drill(path, metadata.get(key)))
        except Exception as exc:  # noqa: BLE001 each unparsed file is an explicit gap
            errors.append(f"{key}: {exc}")
    errors.extend(f"{key}: 钻孔元数据对应文件不存在或格式未识别，未完成检查" for key in metadata.keys() - seen)
    return drills, errors

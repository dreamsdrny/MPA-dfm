"""Read-only Gerber file/package staging; archive paths never escape the private directory."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from src.adapters.gerber_export import gerber_files
from src.infrastructure.archives import MAX_FILE_BYTES, InputContainer
from src.infrastructure.resources import read_bounded


@contextmanager
def staged_gerbers(source):
    path = Path(source).resolve()
    if not path.exists():
        raise ValueError("Gerber 文件或目录不存在：" + str(path))
    with TemporaryDirectory(prefix="evan-gerber-input-") as temp:
        root = Path(temp)
        if path.is_dir() or path.suffix.lower() in (".zip", ".tar", ".tgz", ".gz"):
            with InputContainer(path) as container:
                for name in container.names:
                    # InputContainer validates paths, links and decompression budgets.
                    target = root / (name[:-3] if name.lower().endswith(".gz") else name)
                    if target.exists():
                        raise ValueError("解压后制造文件重名：" + name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(container.read(name))
        else:
            (root / path.name).write_bytes(read_bounded(path, MAX_FILE_BYTES))
        if not gerber_files(root):
            raise ValueError("未找到 Gerber 文件。请选择 RS-274X/X2 文件、整套底片目录或 ZIP/TGZ 压缩包。")
        yield root


def inspect_gerbers(folder):
    from src.adapters.gerber_analysis import read_film
    layers, errors = [], []
    for path in gerber_files(folder):
        try:
            film = read_film(path)
            layers.append({"name": path.relative_to(folder).as_posix(), "role": film["role"],
                           "objects": len(film["objects"]), "warnings": film["warnings"],
                           "bounds_mm": list(film["geometry"].bounds) if not film["geometry"].is_empty else []})
        except Exception as exc:  # noqa: BLE001 third-party CAM errors stay visible per layer
            errors.append(f"{path.relative_to(folder)}: {exc}")
    if not layers:
        raise ValueError("未读到有效 Gerber 图层：" + "；".join(errors[:5]))
    return {"layers": layers, "errors": errors}

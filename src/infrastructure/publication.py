"""Exclusive publication; roll back only files created by this operation."""
import os
from pathlib import Path
from shutil import copyfileobj


def publish_files(pairs, copier=copyfileobj):
    committed, outputs = [], []
    try:
        for source, destination in pairs:
            source, destination = Path(source), Path(destination)
            try:
                with destination.open("xb") as out:
                    stat = os.fstat(out.fileno())
                    committed.append((destination, stat.st_dev, stat.st_ino))
                    with source.open("rb") as stream:
                        copier(stream, out, length=1024 * 1024)
                outputs.append(str(destination))
            except FileExistsError as exc:
                raise FileExistsError(f"报告目标在导出期间出现，未覆盖：{destination}") from exc
    except Exception:
        for destination, device, inode in reversed(committed):
            try:
                stat = destination.stat()
                if stat.st_dev == device and stat.st_ino == inode:
                    destination.unlink()
            except OSError:
                # Never delete a competing writer's replacement on rollback.
                continue
        raise
    return outputs

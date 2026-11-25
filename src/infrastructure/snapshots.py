"""Content snapshots: version checks are independent of UI revisions."""
from copy import deepcopy
import hashlib
from pathlib import Path


def fingerprint(path):
    from src.infrastructure.archives import InputContainer
    path = Path(path)
    digest = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    elif path.is_dir():
        with InputContainer(path) as container:
            for name in container.names:
                digest.update(name.encode("utf-8") + b"\0")
                digest.update(hashlib.sha256(container.read(name)).digest())
    else:
        raise ValueError("输入文件/目录不存在: " + str(path))
    return {"path": str(path.resolve()), "sha256": digest.hexdigest(), "type": "directory" if path.is_dir() else "file"}


def capture_sources(paths):
    seen, records = set(), []
    for path in paths:
        if not path:
            continue
        canonical = str(Path(path).resolve())
        if canonical not in seen:
            seen.add(canonical)
            records.append(fingerprint(canonical))
    return records


def verify_sources(records):
    for source in records:
        if fingerprint(source["path"]) != source:
            raise ValueError("输入资料内容已变化，请重新分析：" + source["path"])


def copy_request(request):
    """Frozen dataclasses can contain mutable mappings: copy the entire request."""
    return deepcopy(request)

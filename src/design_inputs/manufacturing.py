"""ODB++ 结构识别与 STEP 文件头识别；不依赖商业 CAD 引擎。"""
import re
import tarfile
import zipfile
from pathlib import Path


def _classify(names, read):
    def normalize(name):
        parts = name.replace("\\", "/").split("/")
        if ".." in parts:
            raise ValueError("ODB++ 归档包含无效路径")
        return "/".join(part for part in parts if part and part != ".")

    originals = {normalize(n): n for n in names}
    normalized = list(originals)
    roots = set()
    for name in normalized:
        parts = name.split("/")
        for i, part in enumerate(parts):
            if part.lower() == "matrix" and i + 1 < len(parts) and parts[i + 1].lower() == "matrix":
                roots.add("/".join(parts[:i]))
    if len(roots) != 1:
        raise ValueError("未找到唯一的 ODB++ matrix/matrix 根目录")
    root = next(iter(roots))
    prefix = root + "/" if root else ""
    relative = [n[len(prefix):] for n in normalized if n.startswith(prefix)]
    steps, layers = set(), set()
    for name in relative:
        parts = name.split("/")
        if len(parts) >= 2 and parts[0].lower() == "steps":
            steps.add(parts[1])
        if len(parts) >= 5 and parts[0].lower() == "steps" and parts[2].lower() == "layers":
            layers.add(parts[1] + "/" + parts[3])
    misc = next((n for n in normalized if n.lower() == (prefix + "misc/info").lower()), None)
    version = None
    if misc:
        text = read(originals[misc]).decode("utf-8", "replace")
        match = re.search(r"(?im)^\s*(?:ODB_VERSION|VERSION)\s*[= ]\s*([^\r\n]+)", text)
        if match:
            version = match.group(1).strip()
    warnings = ["仅识别归档结构，尚未解析 ODB++ features 几何"]
    if not version:
        warnings.append("未找到可识别的 ODB++ 版本")
    return {"status": "partial", "root": root, "version": version, "steps": sorted(steps),
            "layers": sorted(layers), "warnings": warnings}


def parse_odb(path):
    p = Path(path)
    if p.is_dir():
        files = [x for x in p.rglob("*") if x.is_file() and not x.is_symlink()]
        names = [x.relative_to(p).as_posix() for x in files]
        return _classify(names, lambda n: (p / n).read_bytes()[:65536])
    if zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as archive:
            names = [n for n in archive.namelist() if not n.endswith("/")]
            def read(name):
                with archive.open(name) as f:
                    return f.read(65536)
            return _classify(names, read)
    if tarfile.is_tarfile(p):
        with tarfile.open(p, "r:*") as archive:
            members = {m.name: m for m in archive.getmembers() if m.isfile()}
            def read(name):
                with archive.extractfile(members[name]) as f:
                    return f.read(65536)
            return _classify(list(members), read)
    raise ValueError("ODB++ 必须是目录、ZIP 或 TAR 归档")


def parse_step(path):
    text = Path(path).read_text(encoding="ascii", errors="strict")
    if not text.lstrip().upper().startswith("ISO-10303-21;"):
        raise ValueError("不是 STEP ISO-10303-21 文本文件")
    schema = re.search(r"FILE_SCHEMA\s*\(\s*\((.*?)\)\s*\)", text, re.I | re.S)
    entities = {}
    for name in re.findall(r"(?im)^\s*#\d+\s*=\s*([A-Z_0-9]+)\s*\(", text):
        entities[name.upper()] = entities.get(name.upper(), 0) + 1
    return {"status": "partial", "schema": re.findall(r"'([^']+)'", schema.group(1)) if schema else [],
            "entity_counts": entities, "mechanical_bodies": None, "height_mm": None,
            "assembly_space": None, "warnings": ["STEP 三维实体、变换和单位需要 CAD 内核解析；高度和装配空间尚不可用"]}

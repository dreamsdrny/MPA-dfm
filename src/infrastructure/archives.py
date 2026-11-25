"""只读制造资料容器：目录/ZIP/TAR，限量解压到内存，不 extractall。"""
import gzip
import re
import tarfile
import zipfile
from contextlib import ExitStack
from pathlib import Path

MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_FILES = 100_000


def safe_name(name):
    value = str(name).replace("\\", "/")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value) or ".." in value.split("/"):
        raise ValueError("制造归档含非法路径: " + value)
    return "/".join(p for p in value.split("/") if p and p != ".")


class InputContainer:
    def __init__(self, path):
        self.path = Path(path)
        self.stack = ExitStack()
        self.entries = {}
        self.bytes_read = 0

    def __enter__(self):
        try:
            return self._open()
        except BaseException:
            self.stack.close()
            raise

    def _open(self):
        def add(name, entry, size):
            key = safe_name(name)
            if not key:
                return
            if key in self.entries:
                raise ValueError("制造归档有重复路径: " + key)
            if size > MAX_FILE_BYTES or len(self.entries) >= MAX_FILES:
                raise ValueError("制造归档超出文件大小/数量预算")
            self.entries[key] = entry

        if self.path.is_dir():
            root = self.path.resolve()
            for file in self.path.rglob("*"):
                if file.is_symlink():
                    raise ValueError("制造资料目录不接受符号链接")
                if file.is_file():
                    if not file.resolve().is_relative_to(root):
                        raise ValueError("制造文件超出目录边界")
                    add(file.relative_to(self.path).as_posix(), file, file.stat().st_size)
            self.mode = "directory"
        elif zipfile.is_zipfile(self.path):
            self.archive = self.stack.enter_context(zipfile.ZipFile(self.path))
            self.mode = "zip"
            for entry in self.archive.infolist():
                safe_name(entry.filename)
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("制造 ZIP 不接受符号链接")
                if not entry.is_dir():
                    add(entry.filename, entry, entry.file_size)
        elif tarfile.is_tarfile(self.path):
            self.archive = self.stack.enter_context(tarfile.open(self.path, "r:*"))
            self.mode = "tar"
            for entry in self.archive:
                safe_name(entry.name)
                if entry.issym() or entry.islnk() or not (entry.isfile() or entry.isdir()):
                    raise ValueError("制造 TAR 不接受链接/特殊文件")
                if entry.isfile():
                    add(entry.name, entry, entry.size)
        else:
            raise ValueError("制造资料必须是目录、ZIP、TAR 或 TGZ")
        return self

    @property
    def names(self):
        return sorted(self.entries)

    def read(self, name):
        entry = self.entries[name]
        with ExitStack() as stack:
            if self.mode == "directory":
                stream = stack.enter_context(entry.open("rb"))
            elif self.mode == "zip":
                stream = stack.enter_context(self.archive.open(entry))
            else:
                stream = stack.enter_context(self.archive.extractfile(entry))
            if name.lower().endswith(".gz"):
                stream = stack.enter_context(gzip.GzipFile(fileobj=stream))
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("制造资料读取超出预算")
        if name.lower().endswith(".z"):
            if data[:2] != b"\x1f\x9d":
                raise ValueError("ODB++ .Z 文件不是 Unix compress 数据: " + name)
            # Conservative upper bound: at most one phrase per 9 input bits,
            # each no longer than the maximum dictionary. Reject before unlzw.
            if len(data) < 3 or data[2] & 0x60 or not 9 <= (data[2] & 0x1f) <= 16:
                raise ValueError("ODB++ .Z 文件头无效: " + name)
            max_bits = max(10, data[2] & 0x1f)
            upper_bound = ((max(0, len(data) - 3) * 8 + 8) // 9) * (1 << max_bits)
            if upper_bound > min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - self.bytes_read):
                raise ValueError("ODB++ .Z 解压无法安全限量；请先在隔离环境解压为普通文件")
            from unlzw3 import unlzw
            data = unlzw(data)
        self.bytes_read += len(data)
        if len(data) > MAX_FILE_BYTES or self.bytes_read > MAX_TOTAL_BYTES:
            raise ValueError("制造资料读取/解压超出预算")
        return data

    def text(self, name):
        return self.read(name).decode("utf-8-sig", errors="strict")

    def find(self, relative):
        matches = [name for name in self.names if name.casefold() in
                   (relative.casefold(), (relative + ".gz").casefold(), (relative + ".Z").casefold())]
        if len(matches) > 1:
            raise ValueError("制造文件映射不唯一: " + relative)
        return matches[0] if matches else None

    def __exit__(self, *args):
        self.stack.close()

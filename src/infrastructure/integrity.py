"""独立校验和、签名清单和密码派生的 AES-GCM 信封。"""
import base64
import hashlib
import json
import os
import zlib
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.exceptions import InvalidTag


class IntegrityError(ValueError):
    pass


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def checksums(path):
    sha, crc = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        while block := handle.read(1024*1024):
            sha.update(block)
            crc = zlib.crc32(block, crc)
    return dict(sha256=sha.hexdigest(), crc32=f"{crc & 0xffffffff:08x}")


def verify_checksum(path, expected):
    if not isinstance(expected,dict):raise IntegrityError("校验记录格式无效 / Invalid checksum record")
    actual = checksums(path)
    for algorithm in ("sha256", "crc32"):
        if algorithm in expected and actual[algorithm] != expected[algorithm]:
            raise IntegrityError(f"完整性校验失败 / Integrity check failed: {Path(path).name} ({algorithm})")
    if not any(key in expected for key in actual):
        raise IntegrityError("校验记录缺少 SHA-256/CRC32")
    return True


def _key(password, salt):
    if not isinstance(password, str) or not password:
        raise IntegrityError("请输入加密口令 / Password required")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode("utf-8"))


def encrypt_bytes(data, password):
    salt, nonce = os.urandom(16), os.urandom(12)
    encrypted = AESGCM(_key(password, salt)).encrypt(nonce, data, b"Evan-AES-GCM-v1")
    return canonical(dict(version=1, algorithm="AES-256-GCM+scrypt", salt=base64.b64encode(salt).decode(),
                          nonce=base64.b64encode(nonce).decode(), data=base64.b64encode(encrypted).decode()))


def decrypt_bytes(envelope, password):
    try:
        if len(envelope) > 64*1024*1024:
            raise ValueError("Encrypted file too large")
        doc = json.loads(envelope)
        if doc["version"] != 1 or doc["algorithm"] != "AES-256-GCM+scrypt":
            raise ValueError("Unsupported envelope")
        salt, nonce, data = [base64.b64decode(doc[k], validate=True) for k in ("salt", "nonce", "data")]
        if len(salt) != 16 or len(nonce) != 12:
            raise ValueError("Invalid salt/nonce")
        return AESGCM(_key(password, salt)).decrypt(nonce, data, b"Evan-AES-GCM-v1")
    except (ValueError, KeyError, TypeError, InvalidTag, UnicodeError, RecursionError) as exc:
        raise IntegrityError("解密失败：口令错误、文件被篡改或格式无效 / Decryption failed") from exc


def create_manifest(root, files):
    root = Path(root).resolve()
    return dict(version=1, files={Path(path).resolve().relative_to(root).as_posix():checksums(path) for path in files})


def verify_manifest(root, manifest):
    if not isinstance(manifest,dict):raise IntegrityError("Invalid manifest")
    root = Path(root).resolve()
    records = manifest.get("files")
    if manifest.get("version") != 1 or not isinstance(records, dict) or not records:
        raise IntegrityError("完整性清单无效 / Invalid manifest")
    for name, digest in records.items():
        if not isinstance(name,str):raise IntegrityError("Invalid manifest path")
        path = (root/name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise IntegrityError("配置缺失或路径非法 / Missing configuration: " + name)
        verify_checksum(path, digest)
    return True


def verify_config_if_initialized(path):
    """Signed deployment configs are checked on read; startup enforces initialization."""
    from src.licensing import read_document, verify_signed
    path=Path(path).resolve()
    root=Path(__file__).resolve().parents[2]
    manifest_file=root/"config"/"integrity_manifest.json"
    if not manifest_file.exists():
        return
    policy=read_document(root/"config"/"license_policy.json")
    try:public=base64.b64decode(policy["public_key"],validate=True)
    except (KeyError,ValueError,TypeError) as exc:raise IntegrityError("Invalid configuration public key") from exc
    manifest=verify_signed(read_document(manifest_file),public)
    relative=path.relative_to(root).as_posix()
    if relative not in manifest.get("files",{}):
        raise IntegrityError("配置未包含在签名清单中 / Unlisted configuration: "+relative)
    verify_checksum(path,manifest["files"][relative])

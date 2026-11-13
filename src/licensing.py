"""Offline Ed25519 licenses: public verification separated from issuer private key."""
import base64
import hashlib
import json
import os
import platform
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from src.integrity import canonical, IntegrityError, encrypt_bytes, decrypt_bytes

APP_ID = "Evan.MPA_DFM"


def machine_code():
    identity = platform.node()
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                identity = winreg.QueryValueEx(key, "MachineGuid")[0]
        except OSError:
            pass
    return hashlib.sha256(("Evan-Machine-v1:"+identity).encode()).hexdigest().upper()


def public_bytes(private_key):
    return private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def create_authority(path, password):
    path = Path(path)
    if path.exists():
        raise IntegrityError("签名私钥已存在，不能覆盖 / Signing key already exists")
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypt_bytes(raw, password))
    return key


def load_authority(path, password):
    try:
        return Ed25519PrivateKey.from_private_bytes(decrypt_bytes(Path(path).read_bytes(), password))
    except (OSError, ValueError) as exc:
        raise IntegrityError("签名私钥加载失败 / Cannot load signing key: " + str(exc)) from exc


def sign_payload(payload, private_key):
    return dict(payload=payload, signature=base64.b64encode(private_key.sign(canonical(payload))).decode(), algorithm="Ed25519")


def verify_signed(envelope, public_key):
    try:
        if not isinstance(envelope, dict) or envelope.get("algorithm") != "Ed25519":
            raise ValueError("Invalid signature format")
        payload = envelope["payload"]
        key = Ed25519PublicKey.from_public_bytes(public_key)
        key.verify(base64.b64decode(envelope["signature"], validate=True), canonical(payload))
        if not isinstance(payload, dict):
            raise ValueError("Invalid payload")
        return payload
    except (KeyError, ValueError, TypeError, InvalidSignature) as exc:
        raise IntegrityError("签名无效，文件可能被修改 / Invalid signature") from exc


def issue_license(private_key, app_id, start, expiry, user="", machine="", license_id=None, target_sha256=""):
    start, expiry = date.fromisoformat(str(start)), date.fromisoformat(str(expiry))
    if not app_id.strip() or expiry < start:
        raise IntegrityError("程序标识或日期范围无效 / Invalid application/date range")
    payload = dict(version=1, kind="license", app_id=app_id.strip(), license_id=license_id or str(uuid.uuid4()),
                   starts=start.isoformat(), expires=expiry.isoformat(), user_id=user.strip(), machine_code=machine.strip().upper(),
                   target_sha256=target_sha256, issued_at=datetime.now(timezone.utc).isoformat())
    return sign_payload(payload, private_key)


def validate_license(envelope, public_key, app_id=APP_ID, today=None, machine=None, user=None,
                     revoked_ids=(), target_sha256=None):
    payload = verify_signed(envelope, public_key)
    try:
        if payload.get("version") != 1 or payload.get("kind") != "license" or payload["app_id"] != app_id:
            raise ValueError("程序标识不匹配 / Wrong application")
        current = today or date.today()
        start, end = date.fromisoformat(payload["starts"]), date.fromisoformat(payload["expires"])
        if start > end or current < start:
            raise ValueError("授权尚未生效 / License not active")
        if current > end:
            raise ValueError("授权已到期 / License expired")
        if payload["license_id"] in revoked_ids:
            raise ValueError("授权已吊销 / License revoked")
        if payload.get("machine_code") and payload["machine_code"] != (machine or machine_code()).upper():
            raise ValueError("机器码不匹配 / Machine mismatch")
        if payload.get("user_id") and payload["user_id"] != (user or os.environ.get("USERNAME") or os.environ.get("USER", "")):
            raise ValueError("用户标识不匹配 / User mismatch")
        if payload.get("target_sha256") and payload["target_sha256"] != target_sha256:
            raise ValueError("目标程序校验失败 / Target executable mismatch")
        return payload
    except (KeyError, ValueError, TypeError) as exc:
        raise IntegrityError(str(exc)) from exc


def read_document(path):
    try:
        file = Path(path)
        if file.stat().st_size > 1024*1024:
            raise ValueError("File too large")
        return json.loads(file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise IntegrityError("授权/校验文件无效 / Invalid license or manifest: " + str(exc)) from exc


def write_document(path, document):
    import tempfile
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(canonical(document))
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

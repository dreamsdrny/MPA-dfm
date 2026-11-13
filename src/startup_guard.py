"""License and signed configuration checks before any business UI is created."""
import base64
import os
import sys
from pathlib import Path

from src.integrity import IntegrityError, checksums, verify_manifest
from src.licensing import APP_ID, read_document, verify_signed, validate_license, machine_code


def deployment_status(base=None, resources=None, license_path=None):
    base = Path(base or (Path(sys.executable).parent if getattr(sys,"frozen",False) else Path(__file__).resolve().parents[1]))
    resources = Path(resources or getattr(sys,"_MEIPASS",base))
    policy = read_document(resources/"config"/"license_policy.json")
    try:
        public = base64.b64decode(policy["public_key"], validate=True)
        app_id = policy["app_id"]
    except (KeyError, ValueError, TypeError) as exc:
        raise IntegrityError("授权公钥未配置 / Issuer public key not configured") from exc
    # Signature protects the manifest; SHA-256/CRC32 protect every listed config.
    manifest = verify_signed(read_document(resources/"config"/"integrity_manifest.json"), public)
    verify_manifest(resources, manifest)
    revoked = []
    revocations = base/"revocations.json"
    if revocations.exists():
        payload = verify_signed(read_document(revocations), public)
        if payload.get("kind") != "revocations" or payload.get("app_id") != app_id or not isinstance(payload.get("ids"),list):
            raise IntegrityError("吊销清单无效 / Invalid revocation list")
        revoked = payload["ids"]
    license_file = Path(license_path) if license_path else base/"license.json"
    digest = checksums(sys.executable)["sha256"] if getattr(sys,"frozen",False) else None
    license_data = validate_license(read_document(license_file), public, app_id, revoked_ids=revoked, target_sha256=digest)
    return dict(license=license_data, config_manifest=manifest, app_id=app_id)


def check_startup(root, license_path=None):
    from tkinter import messagebox
    try:
        return deployment_status(license_path=license_path)
    except (IntegrityError, OSError, ValueError) as exc:
        messagebox.showerror("授权无效/完整性检查失败 · License invalid",
            str(exc)+"\n\n机器码 / Machine code:\n"+machine_code()+
            "\n\n请联系授权管理员。程序将退出，不修改源程序或业务数据。",parent=root)
        return None

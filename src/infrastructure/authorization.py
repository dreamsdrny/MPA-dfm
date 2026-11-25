"""License and signed configuration checks before any business UI is created."""
import base64
import sys
from pathlib import Path

from src.infrastructure.integrity import IntegrityError, checksums, verify_manifest
from src.infrastructure.licensing import read_document, verify_signed, validate_license


def deployment_status(base=None, resources=None, license_path=None):
    base = Path(base or (Path(sys.executable).parent if getattr(sys,"frozen",False) else Path(__file__).resolve().parents[2]))
    resources = Path(resources or getattr(sys,"_MEIPASS",base))
    policy = read_document(resources/"config"/"license_policy.json")
    if policy.get("initialization_required") or not policy.get("public_key"):
        raise IntegrityError("授权发行者尚未初始化。请先用授权管理工具初始化并构建正式程序。\nIssuer not initialized; initialize and build deployment first.")
    try:
        public = base64.b64decode(policy["public_key"], validate=True)
        app_id = policy["app_id"]
    except (KeyError, ValueError, TypeError) as exc:
        raise IntegrityError("授权公钥未配置 / Issuer public key not configured") from exc
    pinned=getattr(sys,"_evan_issuer_public",None)
    if getattr(sys,"frozen",False) and (not pinned or pinned!=policy["public_key"]):
        raise IntegrityError("发行者公钥与构建时固定公钥不一致 / Issuer key differs from compiled key")
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

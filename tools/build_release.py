"""构建中性产品名称的限期授权包，私钥仅保存在独立管理员目录。"""
import argparse
import base64
from datetime import date
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from src.integrity import checksums, create_manifest
from src.licensing import APP_ID, issue_license, public_bytes, sign_payload, write_document
from tools.release_support import license_dates, protect_key


def prepare_config(output, key):
    config = output / "config"
    config.mkdir(parents=True, exist_ok=True)
    for source in (ROOT / "config").rglob("*"):
        if source.is_file() and source.suffix.lower() in (".json", ".yaml", ".yml"):
            if source.name in ("license_policy.json", "integrity_manifest.json"):
                continue
            target = config / source.relative_to(ROOT / "config")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    write_document(config / "license_policy.json", {
        "version": 1, "app_id": APP_ID, "public_key": base64.b64encode(public_bytes(key)).decode(),
        "initialization_required": False})
    files = [p for p in config.rglob("*") if p.is_file() and p.name != "integrity_manifest.json"]
    write_document(config / "integrity_manifest.json", sign_payload(create_manifest(output, files), key))
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--admin", required=True, help="不得位于交付包内；保存DPAPI保护的发行私钥")
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()
    output, admin = Path(args.out).resolve(), Path(args.admin).resolve()
    archive = output.with_suffix(".zip")
    if archive.exists():
        raise ValueError("交付 ZIP 已存在，拒绝覆盖")
    if output.exists() and any(output.iterdir()):
        raise ValueError("交付目录非空，请选择新的目录，避免覆盖旧交付")
    if admin == output or admin.is_relative_to(output):
        raise ValueError("管理员私钥目录不能放入交付目录")
    admin.mkdir(parents=True, exist_ok=True)
    key_file = admin / "issuer.dpapi"
    if key_file.exists():
        key = Ed25519PrivateKey.from_private_bytes(protect_key(key_file.read_bytes(), decrypt=True))
    else:
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
        key_file.write_bytes(protect_key(raw))
    staging = admin / ("config-build-" + date.today().isoformat())
    config = prepare_config(staging, key)
    env = dict(os.environ, EVAN_CONFIG_DIR=str(config), EVAN_BUILD_NAME="Evan_MPA_DFM")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
                    "--distpath", str(output), "--workpath", str(admin / "pyinstaller"),
                    str(ROOT / "MPA_DFM.spec")], cwd=ROOT, env=env, check=True)
    exe = output / "Evan_MPA_DFM.exe"
    first, last = license_dates(days=args.days)
    write_document(output / "license.json", issue_license(
        key, APP_ID, first, last, target_sha256=checksums(exe)["sha256"]))
    from src.impedance_plugin import write_plugin
    write_plugin(output / "IMP_HIGHLIGHT")
    for source in (ROOT / "TUTORIAL.md", ROOT / "docs" / "ARCHITECTURE.md", ROOT / "docs" / "ACCEPTANCE_TESTS.md",
                    ROOT / "docs" / "LAYERED_ARCHITECTURE.md",
                    ROOT / "docs" / "PCB_TRACE_FILTER.md", ROOT / "docs" / "SMT_TESTING.md",
                    ROOT / "docs" / "IMP_QUICK_START.md", ROOT / "docs" / "SECURITY_FIXES_20261124.md",
                    ROOT / "docs" / "GERBER_STANDALONE.md"):
        text = source.read_text(encoding="utf-8")
        text = text.replace("(docs/ARCHITECTURE.md)", "(ARCHITECTURE.md)")
        text = text.replace("(docs/ACCEPTANCE_TESTS.md)", "(ACCEPTANCE_TESTS.md)")
        text = text.replace("(docs/SMT_TESTING.md)", "(SMT_TESTING.md)")
        text = text.replace("(docs/GERBER_STANDALONE.md)", "(GERBER_STANDALONE.md)")
        (output / source.name).write_text(text, encoding="utf-8")
    write_document(output / "release.json", {
        "product": "Evan MPA-DFM", "app_id": APP_ID, "license_days": args.days,
        "starts": first.isoformat(), "expires": last.isoformat(),
        "built_at": date.today().isoformat(), "files": {
            p.relative_to(output).as_posix(): checksums(p) for p in output.rglob("*") if p.is_file()}})
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        for file in output.rglob("*"):
            if file.is_file():
                package.write(file, output.name + "/" + file.relative_to(output).as_posix())
    print(json.dumps({"directory": str(output), "archive": str(archive),
                      "starts": str(first), "expires": str(last), "days": args.days}, ensure_ascii=False))


if __name__ == "__main__":
    main()

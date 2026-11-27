"""构建中性产品名称的限期授权包，私钥仅保存在独立管理员目录。"""
import argparse
import base64
from datetime import date, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

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


def unique_output(path):
    """返回一个未占用的交付目录；已有目录原样保留，绝不覆盖历史构建。"""
    if not path.exists() or not any(path.iterdir()):
        return path
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = path.with_name(f"{path.name}_{stamp}")
    counter = 2
    while candidate.exists() and any(candidate.iterdir()):
        candidate = path.with_name(f"{path.name}_{stamp}_{counter}")
        counter += 1
    return candidate


def strip_delivery_docs(output):
    """交付包不携带 Markdown 文档；只删已知输出目录内的文档，不碰历史交付。"""
    removed = []
    for stale in Path(output).rglob("*"):
        if stale.is_file() and stale.suffix.lower() == ".md":
            removed.append(stale.relative_to(output).as_posix())
            stale.unlink()
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--admin", required=True, help="不得位于交付包内；保存DPAPI保护的发行私钥")
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()
    requested = Path(args.out).resolve()
    output, admin = unique_output(requested), Path(args.admin).resolve()
    # 每次构建落到新的交付目录：已有内容一律保留，不覆盖、不删除历史 EXE。
    if output != requested:
        print(f"交付目录已存在，本次使用新目录：{output}")
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
    # 发行包保持稳定的 Evan_MPA_DFM.exe 名称，由独立的新目录实现“不覆盖历史”。
    env.pop("EVAN_BUILD_STAMP", None)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
                    "--distpath", str(output), "--workpath", str(admin / "pyinstaller"),
                    str(ROOT / "MPA_DFM.spec")], cwd=ROOT, env=env, check=True)
    exe = output / "Evan_MPA_DFM.exe"
    first, last = license_dates(days=args.days)
    write_document(output / "license.json", issue_license(
        key, APP_ID, first, last, target_sha256=checksums(exe)["sha256"]))
    # 交付目录只放可执行程序和运行所需资料。说明书不随包分发：此前每版
    # 都带十几份 .md，既重复又容易让测试者误把历史版说明当本版依据。
    removed = strip_delivery_docs(output)
    if removed:
        print("已移除交付包内文档：" + ", ".join(removed))
    shutil.copy2(ROOT / "templates" / "smt_bom_attributes.csv", output / "smt_bom_attributes.csv")
    shutil.copy2(ROOT / "templates" / "assembly_rules.json",output/"assembly_rules.json")
    from src.application.version import BUILD_ID
    write_document(output / "release.json", {
        "product": "Evan MPA-DFM", "app_id": APP_ID, "license_days": args.days,
        "build_id": BUILD_ID,
        "starts": first.isoformat(), "expires": last.isoformat(),
        "built_at": date.today().isoformat(), "files": {
            p.relative_to(output).as_posix(): checksums(p) for p in output.rglob("*") if p.is_file()}})
    print(json.dumps({"directory": str(output), "starts": str(first),
                      "expires": str(last), "days": args.days}, ensure_ascii=False))


if __name__ == "__main__":
    main()

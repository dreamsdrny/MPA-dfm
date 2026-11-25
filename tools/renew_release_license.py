"""同一Windows管理员使用原发行私钥签发续期许可，不改EXE或正式配置。"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from src.integrity import checksums
from src.licensing import APP_ID, issue_license, write_document
from tools.release_support import license_dates, protect_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin", required=True)
    parser.add_argument("--exe", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start")
    parser.add_argument("--days", type=int, required=True)
    args = parser.parse_args()
    key = Ed25519PrivateKey.from_private_bytes(
        protect_key((Path(args.admin) / "issuer.dpapi").read_bytes(), decrypt=True))
    first, last = license_dates(args.start, args.days)
    write_document(args.output, issue_license(key, APP_ID, first, last,
                                             target_sha256=checksums(args.exe)["sha256"]))
    print(f"授权文件：{args.output}；{first} 至 {last}（{args.days}天，含首尾日期）")


if __name__ == "__main__":
    main()

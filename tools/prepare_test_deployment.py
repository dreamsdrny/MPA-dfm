"""Create a temporary TEST issuer. Its public key is never a production key."""
import argparse
import base64
import shutil
import sys
from datetime import date,timedelta
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from src.licensing import public_bytes,sign_payload,issue_license,write_document
from src.integrity import create_manifest


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    output=Path(args.out);output.mkdir(parents=True,exist_ok=True)
    config=output/"config";shutil.copytree(root/"config",config,dirs_exist_ok=True)
    key=Ed25519PrivateKey.generate()
    write_document(config/"license_policy.json",dict(version=1,app_id="Evan.MPA_DFM.TEST",
        public_key=base64.b64encode(public_bytes(key)).decode(),initialization_required=False,test_only=True))
    files=[p for p in config.rglob("*") if p.is_file() and p.suffix.lower() in (".json",".yaml",".yml") and p.name!="integrity_manifest.json"]
    write_document(config/"integrity_manifest.json",sign_payload(create_manifest(output,files),key))
    today=date.today()
    write_document(output/"license.json",issue_license(key,"Evan.MPA_DFM.TEST",today,today+timedelta(days=30)))
    write_document(output/"expired_license.json",issue_license(key,"Evan.MPA_DFM.TEST",today-timedelta(days=60),today-timedelta(days=1)))
    print("TEST deployment",output,"expires",today+timedelta(days=30))


if __name__=="__main__":main()

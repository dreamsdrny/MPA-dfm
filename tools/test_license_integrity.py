import base64
import copy
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from src.integrity import IntegrityError,checksums,verify_checksum,encrypt_bytes,decrypt_bytes,create_manifest,verify_manifest
from src.licensing import issue_license,validate_license,public_bytes,sign_payload,verify_signed,create_authority,load_authority,write_document
from src.startup_guard import deployment_status
sys.path.insert(0,os.environ.get("EVAN_LICENSE_MANAGER_ROOT",r"D:\Evan\Softer-scret\Evan_License_Manager"))
from license_manager import prepare_deployment


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.key=Ed25519PrivateKey.generate()
        self.public=public_bytes(self.key)

    def test_valid_expired_not_started_machine_user_and_tamper(self):
        license=issue_license(self.key,"test.app","2026-01-01","2026-12-31",user="alice",machine="AABB")
        self.assertEqual(validate_license(license,self.public,"test.app",date(2026,11,13),"aabb","alice")["user_id"],"alice")
        for kwargs in (dict(today=date(2027,1,1)),dict(today=date(2025,1,1)),dict(machine="CC"),dict(user="bob")):
            options=dict(today=date(2026,11,13),machine="AABB",user="alice");options.update(kwargs)
            with self.assertRaises(IntegrityError):validate_license(license,self.public,"test.app",**options)
        tampered=copy.deepcopy(license);tampered["payload"]["expires"]="2099-12-31"
        with self.assertRaises(IntegrityError):validate_license(tampered,self.public,"test.app")

    def test_renewal_revocation_exe_hash_and_wrong_key(self):
        expired=issue_license(self.key,"test","2025-01-01","2025-12-31")
        renewed=issue_license(self.key,"test","2026-01-01","2027-12-31",target_sha256="abc")
        validate_license(renewed,self.public,"test",date(2026,11,13),target_sha256="abc")
        with self.assertRaises(IntegrityError):validate_license(renewed,self.public,"test",date(2026,11,13),revoked_ids=[renewed["payload"]["license_id"]],target_sha256="abc")
        with self.assertRaises(IntegrityError):validate_license(renewed,self.public,"test",date(2026,11,13),target_sha256="wrong")
        with self.assertRaises(IntegrityError):verify_signed(renewed,public_bytes(Ed25519PrivateKey.generate()))

    def test_aes_roundtrip_wrong_password_tamper_and_malformed(self):
        encrypted=encrypt_bytes(b"secret configuration","test-password-123")
        self.assertEqual(decrypt_bytes(encrypted,"test-password-123"),b"secret configuration")
        for doc,password in ((encrypted,"wrong"),(b"not-json","test"),(b"{}","test"),(encrypted[:-8]+b"bad","test")):
            with self.assertRaises(IntegrityError):decrypt_bytes(doc,password)

    def test_checksum_and_encrypted_private_key(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"data";path.write_bytes(b"original")
            expected=checksums(path);verify_checksum(path,expected)
            path.write_bytes(b"changed")
            with self.assertRaises(IntegrityError):verify_checksum(path,expected)
            keypath=Path(folder)/"authority.evankey"
            key=create_authority(keypath,"test-password-123")
            self.assertEqual(public_bytes(key),public_bytes(load_authority(keypath,"test-password-123")))
            with self.assertRaises(IntegrityError):load_authority(keypath,"wrong")

    def test_startup_license_and_config_tamper(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/"config").mkdir();(root/"MPA_DFM.spec").touch()
            settings=root/"config"/"settings.yaml";settings.write_text("limit: 1")
            prepare_deployment(root,self.key)
            write_document(root/"license.json",issue_license(self.key,"Evan.MPA_DFM","2020-01-01","2099-12-31"))
            self.assertEqual(deployment_status(root,root)["app_id"],"Evan.MPA_DFM")
            settings.write_text("limit: 9")
            with self.assertRaises(IntegrityError):deployment_status(root,root)

    def test_valid_json_wrong_type_fails_cleanly(self):
        from src.licensing import read_document
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"bad.json"
            for text in ("[]","null","123"):
                path.write_text(text)
                with self.assertRaises(IntegrityError):read_document(path)


if __name__=="__main__":unittest.main()

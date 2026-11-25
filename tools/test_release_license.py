from datetime import date, timedelta
import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from src.integrity import IntegrityError
from src.licensing import APP_ID, issue_license, public_bytes, validate_license
from tools.release_support import license_dates, protect_key


def test_exactly_60_days_including_delivery_day_and_expiry():
    first, last = license_dates("2026-11-23", 60)
    assert first == date(2026, 11, 23)
    assert last == date(2027, 1, 21)
    key = Ed25519PrivateKey.generate()
    license_doc = issue_license(key, APP_ID, first, last)
    for current in (first, last):
        validate_license(license_doc, public_bytes(key), today=current)
    with pytest.raises(IntegrityError, match="到期"):
        validate_license(license_doc, public_bytes(key), today=last + timedelta(days=1))
    with pytest.raises(IntegrityError, match="尚未生效"):
        validate_license(license_doc, public_bytes(key), today=first - timedelta(days=1))


@pytest.mark.parametrize("days", [0, -1, True, 60.5, 3661])
def test_invalid_license_period(days):
    with pytest.raises(ValueError):
        license_dates(days=days)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_private_key_dpapi_roundtrip_and_tamper():
    raw = os.urandom(32)
    protected = protect_key(raw)
    assert raw not in protected
    assert protect_key(protected, decrypt=True) == raw
    with pytest.raises(OSError):
        protect_key(protected[:-8] + b"modified", decrypt=True)

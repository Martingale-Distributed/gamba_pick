"""Tests for the .gpcat format + encryption layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from gamba_pick.gpcat import (
    GpcatHeader,
    canonicalize_license,
    derive_key,
    encrypt_to_bytes,
    decrypt_bytes,
    parse_header,
    GpcatFormatError,
    GpcatLicenseError,
    MAGIC,
    FORMAT_VER,
)


# ---- license-string canonicalization ----

def test_canonicalize_strips_dashes_and_whitespace():
    raw = "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567"
    assert canonicalize_license(raw) == "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def test_canonicalize_uppercases():
    assert canonicalize_license("abcd-efgh-ijkl-mnop-qrst-uvwx-yz23-4567") == \
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def test_canonicalize_strips_internal_whitespace():
    assert canonicalize_license("  ABCD efgh ijklmnop qrstuvwxyz234567  ") == \
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


# ---- key derivation ----

def test_derive_key_is_deterministic():
    license_str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    bundle_id = b"\x00" * 16
    k1 = derive_key(license_str, bundle_id)
    k2 = derive_key(license_str, bundle_id)
    assert k1 == k2
    assert len(k1) == 32  # AES-256


def test_derive_key_changes_with_bundle_id():
    license_str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    k1 = derive_key(license_str, b"\x00" * 16)
    k2 = derive_key(license_str, b"\x01" * 16)
    assert k1 != k2


def test_derive_key_changes_with_license():
    bundle_id = b"\x00" * 16
    k1 = derive_key("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", bundle_id)
    k2 = derive_key("BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB", bundle_id)
    assert k1 != k2


# ---- round-trip ----

def test_round_trip_encrypt_decrypt():
    plaintext = b"hello, world\n" * 1000
    license_str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    bundle_id = b"\x42" * 16

    blob = encrypt_to_bytes(plaintext, license_str=license_str, bundle_id=bundle_id)
    out = decrypt_bytes(blob, license_str=license_str)
    assert out == plaintext


def test_decrypt_with_wrong_license_raises():
    plaintext = b"secret catalog contents"
    bundle_id = b"\x01" * 16

    blob = encrypt_to_bytes(
        plaintext,
        license_str="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567",
        bundle_id=bundle_id,
    )
    with pytest.raises(GpcatLicenseError):
        decrypt_bytes(blob, license_str="WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW")


def test_decrypt_garbage_raises_format_error():
    with pytest.raises(GpcatFormatError):
        decrypt_bytes(b"not a gpcat", license_str="X" * 32)


def test_decrypt_wrong_magic_raises_format_error():
    blob = b"WRONG" + b"\x00" * 100
    with pytest.raises(GpcatFormatError):
        decrypt_bytes(blob, license_str="X" * 32)


def test_parse_header_round_trip():
    header = GpcatHeader(
        magic=MAGIC,
        format_ver=FORMAT_VER,
        bundle_id=b"\x99" * 16,
        nonce=b"\x77" * 12,
    )
    raw = header.pack()
    parsed = parse_header(raw)
    assert parsed.magic == MAGIC
    assert parsed.format_ver == FORMAT_VER
    assert parsed.bundle_id == b"\x99" * 16
    assert parsed.nonce == b"\x77" * 12

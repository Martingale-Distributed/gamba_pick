"""``.gpcat`` format: encrypted, compressed catalog bundles.

File layout (binary, network byte order for fixed fields):

    [ 4 bytes  ] magic       = b"GPCT"
    [ 1 byte   ] format_ver  = 0x01
    [16 bytes  ] bundle_id   = random per build; HKDF salt
    [12 bytes  ] nonce       = AES-GCM nonce, random per build
    [ N bytes  ] ciphertext  = AES-256-GCM(plaintext = tar.zst of catalog)
    [16 bytes  ] tag         = AES-GCM auth tag (trailing)

Key derivation:

    key = HKDF-SHA256(
        ikm    = utf8(canonicalize_license(license_string)),
        salt   = bundle_id,
        info   = b"gamba-pick.gpcat.v1",
        length = 32,
    )

License-string canonicalization: strip dashes and whitespace, uppercase.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"GPCT"
FORMAT_VER = 0x01
BUNDLE_ID_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16
HEADER_LEN = 4 + 1 + BUNDLE_ID_LEN + NONCE_LEN  # 33 bytes
HKDF_INFO = b"gamba-pick.gpcat.v1"


class GpcatError(Exception):
    """Base class for .gpcat handling errors."""


class GpcatFormatError(GpcatError):
    """Raised when a .gpcat blob is malformed (wrong magic, truncated, etc.)."""


class GpcatLicenseError(GpcatError):
    """Raised when AES-GCM tag verification fails (wrong license key)."""


@dataclass(frozen=True)
class GpcatHeader:
    magic: bytes
    format_ver: int
    bundle_id: bytes
    nonce: bytes

    def pack(self) -> bytes:
        if len(self.magic) != 4:
            raise ValueError("magic must be 4 bytes")
        if len(self.bundle_id) != BUNDLE_ID_LEN:
            raise ValueError(f"bundle_id must be {BUNDLE_ID_LEN} bytes")
        if len(self.nonce) != NONCE_LEN:
            raise ValueError(f"nonce must be {NONCE_LEN} bytes")
        return (
            self.magic
            + struct.pack("!B", self.format_ver)
            + self.bundle_id
            + self.nonce
        )


def parse_header(raw: bytes) -> GpcatHeader:
    if len(raw) < HEADER_LEN:
        raise GpcatFormatError(
            f".gpcat truncated: header needs {HEADER_LEN} bytes, got {len(raw)}"
        )
    magic = raw[:4]
    if magic != MAGIC:
        raise GpcatFormatError(
            f".gpcat magic mismatch: expected {MAGIC!r}, got {magic!r}"
        )
    format_ver = raw[4]
    if format_ver != FORMAT_VER:
        raise GpcatFormatError(
            f".gpcat format_ver {format_ver} not supported by this build "
            f"(supported: {FORMAT_VER})"
        )
    bundle_id = raw[5 : 5 + BUNDLE_ID_LEN]
    nonce = raw[5 + BUNDLE_ID_LEN : HEADER_LEN]
    return GpcatHeader(magic=magic, format_ver=format_ver, bundle_id=bundle_id, nonce=nonce)


def canonicalize_license(raw: str) -> str:
    """Strip dashes, whitespace, and uppercase. Fail-soft on weird input.

    Customers paste with dashes (XXXX-XXXX-...); we don't care about
    spacing or case. The KDF is over the canonical form.
    """
    return "".join(ch for ch in raw if not ch.isspace() and ch != "-").upper()


def derive_key(license_str: str, bundle_id: bytes) -> bytes:
    """HKDF-SHA256 key derivation. ``license_str`` is canonicalized first."""
    canonical = canonicalize_license(license_str)
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=bundle_id,
        info=HKDF_INFO,
    )
    return hkdf.derive(canonical.encode("utf-8"))


def encrypt_to_bytes(
    plaintext: bytes,
    *,
    license_str: str,
    bundle_id: bytes,
    nonce: bytes | None = None,
) -> bytes:
    """Build a .gpcat blob from raw plaintext bytes.

    ``plaintext`` should already be the compressed (zstd) catalog tarball;
    this function just AES-GCM-encrypts it. The build script
    (``tools/build_gpcat.py``) is responsible for the tar+zstd step.

    ``nonce`` is randomized when omitted (the normal path).
    """
    if len(bundle_id) != BUNDLE_ID_LEN:
        raise ValueError(f"bundle_id must be {BUNDLE_ID_LEN} bytes")
    if nonce is None:
        import os
        nonce = os.urandom(NONCE_LEN)
    if len(nonce) != NONCE_LEN:
        raise ValueError(f"nonce must be {NONCE_LEN} bytes")

    header = GpcatHeader(magic=MAGIC, format_ver=FORMAT_VER, bundle_id=bundle_id, nonce=nonce)
    key = derive_key(license_str, bundle_id)
    aesgcm = AESGCM(key)
    # AESGCM.encrypt returns ciphertext || tag.
    ct_with_tag = aesgcm.encrypt(nonce, plaintext, associated_data=header.pack())
    # Per the format, header || ciphertext || tag — but AESGCM.encrypt
    # already concatenates ciphertext + tag, so we just append.
    return header.pack() + ct_with_tag


def decrypt_bytes(blob: bytes, *, license_str: str) -> bytes:
    """Decrypt a .gpcat blob and return the raw plaintext bytes.

    Raises GpcatFormatError if the header is malformed; GpcatLicenseError
    if the AES-GCM tag fails (wrong license, truncation, tampering).
    """
    header = parse_header(blob)
    ct_with_tag = blob[HEADER_LEN:]
    if len(ct_with_tag) < TAG_LEN:
        raise GpcatFormatError("ciphertext too short to contain auth tag")
    key = derive_key(license_str, header.bundle_id)
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(header.nonce, ct_with_tag, associated_data=header.pack())
    except Exception as e:  # cryptography raises InvalidTag
        raise GpcatLicenseError(f"AES-GCM decrypt failed: {e}") from e

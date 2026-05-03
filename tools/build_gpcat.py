"""Sale-time builder for ``.gpcat`` catalog bundles.

This script lives in the gamba_pick repo but is NOT shipped in the
customer zip. It's the operator's tool: feed it a catalog directory and
optionally a license string + bundle_id, and it emits an encrypted
.gpcat plus prints the credentials you email the customer.

Usage:

    uv run python -m tools.build_gpcat \\
        --catalog-dir ./build/casino-buddy-2026-05/ \\
        --output       ./build/casino-buddy-2026-05.gpcat

    # With explicit credentials (idempotent rebuilds):
    uv run python -m tools.build_gpcat \\
        --catalog-dir   ./build/casino-buddy-2026-05/ \\
        --output        ./build/casino-buddy-2026-05.gpcat \\
        --license       ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567 \\
        --bundle-id-hex 0123456789abcdef0123456789abcdef
"""

from __future__ import annotations

import argparse
import io
import secrets
import sys
import tarfile
from pathlib import Path

import zstandard

from gamba_pick.gpcat import encrypt_to_bytes

# Crockford base32 alphabet (RFC 4648 base32 minus I, L, O, U for visual
# disambiguation — kept simple here using the standard alphabet; can
# tighten later if customer complaints come in).
_BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def generate_license_string() -> str:
    """Return a 32-char Crockford-base32 license string with dashes every 4 chars."""
    raw = secrets.token_bytes(20)  # 20 bytes = 160 bits = 32 base32 chars
    # Encode to base32 manually using the alphabet (avoids "=" padding).
    out_chars: list[str] = []
    bits = 0
    bit_count = 0
    for byte in raw:
        bits = (bits << 8) | byte
        bit_count += 8
        while bit_count >= 5:
            bit_count -= 5
            idx = (bits >> bit_count) & 0b11111
            out_chars.append(_BASE32_ALPHABET[idx])
    if bit_count > 0:
        idx = (bits << (5 - bit_count)) & 0b11111
        out_chars.append(_BASE32_ALPHABET[idx])
    s = "".join(out_chars)[:32]
    # Re-group into 8 quads separated by dashes.
    return "-".join(s[i : i + 4] for i in range(0, 32, 4))


def pack_catalog_to_zstd_tar(catalog_dir: Path) -> bytes:
    """Tar the directory contents, then zstd-compress. Returns the compressed bytes."""
    if not catalog_dir.exists():
        raise FileNotFoundError(catalog_dir)
    if not catalog_dir.is_dir():
        raise NotADirectoryError(catalog_dir)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        # Walk the directory and add each entry with a clean relative arcname
        # (no leading "./" prefix) so tar paths look like "manifest.toml",
        # "configs/site_a.py", etc.
        for entry in sorted(catalog_dir.rglob("*")):
            arcname = entry.relative_to(catalog_dir).as_posix()
            tf.add(entry, arcname=arcname)
    raw = buf.getvalue()
    cctx = zstandard.ZstdCompressor(level=10)
    return cctx.compress(raw)


def build_gpcat(
    *,
    catalog_dir: Path,
    output_path: Path,
    license_str: str,
    bundle_id: bytes,
) -> None:
    plaintext = pack_catalog_to_zstd_tar(catalog_dir)
    blob = encrypt_to_bytes(
        plaintext,
        license_str=license_str,
        bundle_id=bundle_id,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(blob)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an encrypted .gpcat catalog bundle.",
    )
    parser.add_argument(
        "--catalog-dir",
        type=Path,
        required=True,
        help="Directory holding the catalog tree (manifest.toml, configs/, ...)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output .gpcat file path.",
    )
    parser.add_argument(
        "--license",
        default=None,
        help="License string (XXXX-XXXX-...). Auto-generated if omitted.",
    )
    parser.add_argument(
        "--bundle-id-hex",
        default=None,
        help="32-char hex bundle_id. Auto-generated if omitted.",
    )
    opts = parser.parse_args(argv)

    license_str = opts.license or generate_license_string()
    if opts.bundle_id_hex:
        bundle_id = bytes.fromhex(opts.bundle_id_hex)
        if len(bundle_id) != 16:
            print("error: --bundle-id-hex must decode to 16 bytes", file=sys.stderr)
            return 2
    else:
        bundle_id = secrets.token_bytes(16)

    build_gpcat(
        catalog_dir=opts.catalog_dir,
        output_path=opts.output,
        license_str=license_str,
        bundle_id=bundle_id,
    )

    import hashlib
    sha = hashlib.sha256(opts.output.read_bytes()).hexdigest()

    print(f"Wrote {opts.output} ({opts.output.stat().st_size:,} bytes)")
    print()
    print(f"  license_string : {license_str}")
    print(f"  bundle_id_hex  : {bundle_id.hex()}")
    print(f"  sha256         : {sha}")
    print()
    print("Email the customer the license_string and the .gpcat file.")
    print("Log the {license_string, bundle_id, sha256, customer} tuple in your sales record.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

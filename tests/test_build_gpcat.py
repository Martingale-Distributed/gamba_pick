"""Tests for the build_gpcat sale-time bundler."""

from __future__ import annotations

import io
import secrets
import tarfile
from pathlib import Path

import zstandard

from gamba_pick.gpcat import decrypt_bytes
from tools.build_gpcat import (
    build_gpcat,
    generate_license_string,
    pack_catalog_to_zstd_tar,
)


def test_generate_license_string_format():
    s = generate_license_string()
    # 32 chars (no dashes) of Crockford base32 alphabet
    assert len(s.replace("-", "")) == 32
    # When dashed: 8 groups of 4 separated by dashes
    assert s.count("-") == 7
    for grp in s.split("-"):
        assert len(grp) == 4


def test_pack_catalog_round_trip(tmp_path: Path):
    src = tmp_path / "catalog"
    src.mkdir()
    (src / "manifest.toml").write_text("[meta]\nversion = '1'\n")
    (src / "configs").mkdir()
    (src / "configs" / "site_a.py").write_text("# site_a config\n")

    blob = pack_catalog_to_zstd_tar(src)
    # Decompress + untar in memory and check contents.
    dctx = zstandard.ZstdDecompressor()
    raw = dctx.decompress(blob)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tf:
        names = sorted(tf.getnames())
    assert "manifest.toml" in names
    assert "configs/site_a.py" in names


def test_build_gpcat_round_trip(tmp_path: Path):
    src = tmp_path / "catalog"
    src.mkdir()
    (src / "manifest.toml").write_text("[meta]\nversion = '1'\n")

    out = tmp_path / "test.gpcat"
    license_str = "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567"
    bundle_id = secrets.token_bytes(16)

    build_gpcat(
        catalog_dir=src,
        output_path=out,
        license_str=license_str,
        bundle_id=bundle_id,
    )

    blob = out.read_bytes()
    plaintext = decrypt_bytes(blob, license_str=license_str)
    # Plaintext is the zstd-compressed tarball.
    dctx = zstandard.ZstdDecompressor()
    raw = dctx.decompress(plaintext)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tf:
        names = tf.getnames()
    assert "manifest.toml" in names

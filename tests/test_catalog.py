"""Tests for catalog discovery + decrypt-to-tempdir."""

from __future__ import annotations

import io
import secrets
import tarfile
from pathlib import Path

import pytest
import zstandard

from gamba_pick.catalog import (
    CatalogState,
    discover_catalog,
    decrypt_catalog_to_tempdir,
)
from gamba_pick.gpcat import encrypt_to_bytes


def _make_test_gpcat(path: Path, *, license_str: str, payload_files: dict[str, str]):
    """Build a small valid .gpcat at ``path`` containing the given files."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        for name, content in payload_files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    cctx = zstandard.ZstdCompressor(level=3)
    plaintext = cctx.compress(buf.getvalue())
    blob = encrypt_to_bytes(
        plaintext,
        license_str=license_str,
        bundle_id=secrets.token_bytes(16),
    )
    path.write_bytes(blob)


# ---- discover_catalog ----

def test_discover_catalog_empty_dir_returns_none(tmp_path: Path):
    state, path = discover_catalog(tmp_path)
    assert state == CatalogState.NONE
    assert path is None


def test_discover_catalog_one_gpcat_returns_path(tmp_path: Path):
    p = tmp_path / "test.gpcat"
    p.write_bytes(b"GPCT" + b"\x01" + b"\x00" * 28 + b"x" * 32)
    state, path = discover_catalog(tmp_path)
    assert state == CatalogState.ONE
    assert path == p


def test_discover_catalog_multiple_gpcat_returns_multi(tmp_path: Path):
    (tmp_path / "a.gpcat").write_bytes(b"x")
    (tmp_path / "b.gpcat").write_bytes(b"x")
    state, path = discover_catalog(tmp_path)
    assert state == CatalogState.MULTI
    assert path is None


def test_discover_catalog_missing_dir_returns_none(tmp_path: Path):
    state, path = discover_catalog(tmp_path / "does-not-exist")
    assert state == CatalogState.NONE
    assert path is None


# ---- decrypt_catalog_to_tempdir ----

def test_decrypt_catalog_to_tempdir_round_trip(tmp_path: Path):
    license_str = "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567"
    gpcat = tmp_path / "x.gpcat"
    _make_test_gpcat(
        gpcat,
        license_str=license_str,
        payload_files={
            "manifest.toml": "[meta]\nversion = '1'\n",
            "configs/site_a.py": "# config\n",
        },
    )

    out_dir = decrypt_catalog_to_tempdir(gpcat, license_str=license_str)
    try:
        assert (out_dir / "manifest.toml").exists()
        assert (out_dir / "configs" / "site_a.py").exists()
        assert (out_dir / "manifest.toml").read_text().startswith("[meta]")
    finally:
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)


def test_decrypt_catalog_wrong_license_raises(tmp_path: Path):
    gpcat = tmp_path / "x.gpcat"
    _make_test_gpcat(
        gpcat,
        license_str="AAAA-AAAA-AAAA-AAAA-AAAA-AAAA-AAAA-AAAA",
        payload_files={"manifest.toml": "x"},
    )

    from gamba_pick.gpcat import GpcatLicenseError
    with pytest.raises(GpcatLicenseError):
        decrypt_catalog_to_tempdir(gpcat, license_str="WRONGWRONGWRONGWRONGWRONGWRONGWR")

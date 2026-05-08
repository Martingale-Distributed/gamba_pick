"""Catalog discovery + decrypt-to-tempdir.

Encapsulates the runtime side of the .gpcat lifecycle:

- ``discover_catalog(catalog_dir)`` — find the single .gpcat in
  ``catalog/``, or report none / multiple.
- ``decrypt_catalog_to_tempdir(gpcat_path, license_str)`` — derive the
  key, AES-GCM decrypt, zstd-decompress, untar to a fresh temp dir,
  return the temp-dir Path. The caller is responsible for cleanup
  (or registering an ``atexit`` handler — see ``cli.py``).
"""

from __future__ import annotations

import enum
import io
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Optional

import zstandard

from gamba_pick.gpcat import decrypt_bytes


class CatalogState(enum.Enum):
    NONE = "none"      # zero .gpcat files in catalog/
    ONE = "one"        # exactly one
    MULTI = "multi"    # more than one — error condition


def discover_catalog(catalog_dir: Path) -> tuple[CatalogState, Optional[Path]]:
    """Scan ``catalog_dir`` for .gpcat files.

    Returns ``(state, path)``. ``path`` is the .gpcat file when state is
    ONE; None otherwise.
    """
    if not catalog_dir.exists() or not catalog_dir.is_dir():
        return CatalogState.NONE, None
    matches = sorted(catalog_dir.glob("*.gpcat"))
    if not matches:
        return CatalogState.NONE, None
    if len(matches) == 1:
        return CatalogState.ONE, matches[0]
    return CatalogState.MULTI, None


def decrypt_catalog_to_tempdir(
    gpcat_path: Path,
    *,
    license_str: str,
) -> Path:
    """Decrypt ``gpcat_path`` and untar to a fresh temp directory.

    Returns the temp dir's Path. Caller is responsible for cleanup
    (the CLI registers an ``atexit`` handler to ``shutil.rmtree``).

    Raises GpcatFormatError / GpcatLicenseError on bad blob / wrong key.
    """
    blob = gpcat_path.read_bytes()
    plaintext = decrypt_bytes(blob, license_str=license_str)

    dctx = zstandard.ZstdDecompressor()
    raw = dctx.decompress(plaintext)

    out_dir = Path(tempfile.mkdtemp(prefix="gamba-pick-cat-"))
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tf:
            # ``filter='data'`` (Python 3.12+) refuses absolute paths,
            # parent-relative paths, and other tar shenanigans. The
            # project pins ``requires-python = ">=3.12"`` and
            # ``.python-version = 3.12``, so this is always available.
            tf.extractall(path=out_dir, filter="data")
    except Exception:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    return out_dir

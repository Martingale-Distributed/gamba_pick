"""Tests for .env read/write of the GAMBA_PICK_LICENSE entry."""

from __future__ import annotations

from pathlib import Path

from gamba_pick.license_store import (
    LICENSE_ENV_VAR,
    read_license,
    write_license,
)


def test_read_returns_none_when_file_missing(tmp_path: Path):
    assert read_license(tmp_path / ".env") is None


def test_read_returns_none_when_var_missing(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("FOO=bar\nBAZ=qux\n")
    assert read_license(env) is None


def test_read_returns_value_when_present(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(f"FOO=bar\n{LICENSE_ENV_VAR}=ABCD-EFGH-IJKL\nBAZ=qux\n")
    assert read_license(env) == "ABCD-EFGH-IJKL"


def test_read_handles_quoted_value(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(f'{LICENSE_ENV_VAR}="ABCD-EFGH-IJKL"\n')
    assert read_license(env) == "ABCD-EFGH-IJKL"


def test_write_creates_file_when_missing(tmp_path: Path):
    env = tmp_path / ".env"
    write_license(env, "ABCD-EFGH-IJKL")
    text = env.read_text()
    assert f"{LICENSE_ENV_VAR}=ABCD-EFGH-IJKL" in text


def test_write_updates_existing_var(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(f"FOO=bar\n{LICENSE_ENV_VAR}=OLD-VALUE\nBAZ=qux\n")
    write_license(env, "ABCD-EFGH-IJKL")

    text = env.read_text()
    assert "FOO=bar" in text
    assert "BAZ=qux" in text
    assert f"{LICENSE_ENV_VAR}=ABCD-EFGH-IJKL" in text
    assert "OLD-VALUE" not in text


def test_write_appends_when_var_absent(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("FOO=bar\nBAZ=qux\n")
    write_license(env, "ABCD-EFGH-IJKL")

    text = env.read_text()
    assert "FOO=bar" in text
    assert "BAZ=qux" in text
    assert f"{LICENSE_ENV_VAR}=ABCD-EFGH-IJKL" in text

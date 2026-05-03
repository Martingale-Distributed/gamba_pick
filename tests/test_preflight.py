"""Tests for the detect-and-instruct preflight."""

from __future__ import annotations

from pathlib import Path

import pytest

from gamba_pick.preflight import (
    PreflightAction,
    RemediationItem,
    check_catalog,
    check_site_credentials,
)


# ---- catalog state ----

def test_check_catalog_no_dir(tmp_path: Path):
    action = check_catalog(tmp_path / "no-such-dir")
    assert action.kind == "info"
    assert "public reference configs only" in action.message.lower()


def test_check_catalog_empty_dir(tmp_path: Path):
    action = check_catalog(tmp_path)
    assert action.kind == "info"


def test_check_catalog_one_gpcat(tmp_path: Path):
    (tmp_path / "x.gpcat").write_bytes(b"placeholder")
    action = check_catalog(tmp_path)
    assert action.kind == "decrypt"
    assert action.gpcat_path == tmp_path / "x.gpcat"


def test_check_catalog_multiple(tmp_path: Path):
    (tmp_path / "a.gpcat").write_bytes(b"x")
    (tmp_path / "b.gpcat").write_bytes(b"x")
    action = check_catalog(tmp_path)
    assert action.kind == "error"
    assert "multiple" in action.message.lower()


# ---- per-site credentials ----

def test_check_site_form_auth_missing_creds(tmp_path: Path):
    item = check_site_credentials(
        site_id="sportzino",
        auth="form",
        env={},
        profiles_dir=tmp_path / "profiles",
    )
    assert item is not None
    assert "SPORTZINO_USERNAME" in item.action
    assert "SPORTZINO_PASSWORD" in item.action


def test_check_site_form_auth_present(tmp_path: Path):
    item = check_site_credentials(
        site_id="sportzino",
        auth="form",
        env={
            "SPORTZINO_USERNAME": "u",
            "SPORTZINO_PASSWORD": "p",
        },
        profiles_dir=tmp_path / "profiles",
    )
    assert item is None


def test_check_site_oauth_no_profile(tmp_path: Path):
    item = check_site_credentials(
        site_id="pulsz",
        auth="oauth",
        env={},
        profiles_dir=tmp_path / "profiles",
    )
    assert item is not None
    assert "--setup pulsz" in item.action


def test_check_site_oauth_empty_profile_dir(tmp_path: Path):
    profiles = tmp_path / "profiles"
    (profiles / "pulsz").mkdir(parents=True)
    item = check_site_credentials(
        site_id="pulsz",
        auth="oauth",
        env={},
        profiles_dir=profiles,
    )
    assert item is not None  # empty dir counts as not-bootstrapped


def test_check_site_oauth_populated_profile_dir(tmp_path: Path):
    profiles = tmp_path / "profiles"
    (profiles / "pulsz").mkdir(parents=True)
    (profiles / "pulsz" / "cookies.txt").write_text("placeholder")
    item = check_site_credentials(
        site_id="pulsz",
        auth="oauth",
        env={},
        profiles_dir=profiles,
    )
    assert item is None

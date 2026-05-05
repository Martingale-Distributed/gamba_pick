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


def test_run_preflight_all_ready_returns_empty(tmp_path: Path):
    from gamba_pick.preflight import run_preflight

    profiles = tmp_path / "profiles"
    (profiles / "pulsz").mkdir(parents=True)
    (profiles / "pulsz" / "cookies").write_text("x")

    items = run_preflight(
        sites=[
            {"id": "sportzino", "auth": "form"},
            {"id": "pulsz", "auth": "oauth"},
        ],
        env={
            "SPORTZINO_USERNAME": "u",
            "SPORTZINO_PASSWORD": "p",
        },
        profiles_dir=profiles,
    )
    assert items == []


def test_run_preflight_aggregates_misses(tmp_path: Path):
    from gamba_pick.preflight import run_preflight

    items = run_preflight(
        sites=[
            {"id": "sportzino", "auth": "form"},
            {"id": "pulsz", "auth": "oauth"},
        ],
        env={},
        profiles_dir=tmp_path / "profiles",
    )
    site_ids = {item.site_id for item in items}
    assert site_ids == {"sportzino", "pulsz"}


def test_format_remediation_empty_returns_empty_string(tmp_path: Path):
    from gamba_pick.preflight import format_remediation
    assert format_remediation([]) == ""


def test_format_remediation_lists_each_action(tmp_path: Path):
    from gamba_pick.preflight import format_remediation, RemediationItem

    items = [
        RemediationItem(
            site_id="sportzino",
            reason="missing credentials",
            action="Edit .env and add: SPORTZINO_USERNAME=...; SPORTZINO_PASSWORD=...",
        ),
        RemediationItem(
            site_id="pulsz",
            reason="OAuth profile not bootstrapped",
            action="Bootstrap login for pulsz: ./run.sh --setup pulsz",
        ),
    ]
    out = format_remediation(items)
    assert "sportzino" in out
    assert "pulsz" in out
    assert "SPORTZINO_USERNAME" in out
    assert "--setup pulsz" in out
    assert "Setup incomplete: 2 step(s) remaining" in out


# ---- URL-derived env prefix ----

def test_check_site_credentials_uses_url_derived_prefix(tmp_path: Path):
    """When site has a URL, env prefix should be derived from the netloc
    via url_to_env_prefix logic (matching what the actual site scripts use)."""
    from gamba_pick.preflight import check_site_credentials

    item = check_site_credentials(
        site_id="stake_us",
        auth="form",
        env={},
        profiles_dir=tmp_path / "profiles",
        url="https://stake.us",
    )
    assert item is not None
    # Should ask for STAKE_USERNAME (URL-derived), NOT STAKE_US_USERNAME (id-derived)
    assert "STAKE_USERNAME" in item.action
    assert "STAKE_US_USERNAME" not in item.action


def test_check_site_credentials_url_takes_precedence_over_id(tmp_path: Path):
    """For shuffle_us (id) at https://shuffle.us, prefix should be SHUFFLE."""
    from gamba_pick.preflight import check_site_credentials

    # Provide credentials matching the URL-derived prefix.
    item = check_site_credentials(
        site_id="shuffle_us",
        auth="form",
        env={"SHUFFLE_USERNAME": "u", "SHUFFLE_PASSWORD": "p"},
        profiles_dir=tmp_path / "profiles",
        url="https://shuffle.us",
    )
    assert item is None  # creds satisfied via URL-derived prefix

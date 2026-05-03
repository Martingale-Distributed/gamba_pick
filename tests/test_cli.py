"""Tests for gamba_pick.cli — argument parsing + orchestration glue.

The expensive parts (subprocess spawning, browser launches) are mocked.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


# ---- argument parsing ----

def test_parser_default_no_args():
    from gamba_pick.cli import build_parser
    parser = build_parser()
    args = parser.parse_args([])
    # default mode is "sweep" with no overrides
    assert args.summary is None
    assert args.setup is None
    assert args.list is False
    assert args.show is False
    assert args.no_csv is False
    assert args.show_browser is False


def test_parser_summary_no_arg_defaults_to_30():
    from gamba_pick.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["--summary"])
    assert args.summary == 30


def test_parser_summary_with_n():
    from gamba_pick.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["--summary", "7"])
    assert args.summary == 7


def test_parser_setup_no_arg():
    from gamba_pick.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["--setup"])
    assert args.setup == ""  # sentinel for "all unbootstrapped sites"


def test_parser_setup_with_site_id():
    from gamba_pick.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["--setup", "pulsz"])
    assert args.setup == "pulsz"


# ---- summary path doesn't run the sweep ----

def test_summary_path_calls_render_summary(tmp_path: Path, capsys):
    from gamba_pick.cli import main as cli_main

    csv_path = tmp_path / "claims.csv"
    # absent file → render_summary returns the no-history message
    rc = cli_main(["--summary", "30", "--claims-csv", str(csv_path)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "no claim history" in captured.out.lower()


# ---- preflight prints remediation and exits 0 ----

def test_preflight_blocks_when_creds_missing(tmp_path: Path, capsys, monkeypatch):
    """When a site needs credentials, the CLI prints remediation and returns 0."""
    from gamba_pick import cli as cli_mod

    # Create a minimal seed file with one form-auth site, no creds.
    seed = tmp_path / "sites_seed.toml"
    seed.write_text(
        '[[site]]\n'
        'id = "spinquest"\n'
        'name = "SpinQuest"\n'
        'url = "https://spinquest.com"\n'
        'status = "working"\n'
        'auth = "form"\n'
        'module = "spinquest"\n'
        'primary_currency = "SC"\n'
    )
    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir()
    profiles = tmp_path / "profiles"
    profiles.mkdir()

    # Empty env (no SPINQUEST_USERNAME, no SPINQUEST_PASSWORD).
    monkeypatch.setattr(cli_mod, "load_env_for_preflight", lambda *_a, **_k: {})

    rc = cli_mod.main([
        "--seed-file", str(seed),
        "--catalog-dir", str(catalog_dir),
        "--profiles-dir", str(profiles),
        "--claims-csv", str(tmp_path / "claims.csv"),
        "--log-file", str(tmp_path / "claim_history.jsonl"),
        "--picks-env", str(tmp_path / "picks.env"),
    ])

    assert rc == 0
    captured = capsys.readouterr()
    assert "SPINQUEST_USERNAME" in captured.out
    assert "Setup incomplete" in captured.out

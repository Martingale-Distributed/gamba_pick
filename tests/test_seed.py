"""Tests for sites_seed.toml loading and the Site dataclass."""

from __future__ import annotations

from pathlib import Path

from gamba_pick.runner import Site, load_sites


def test_site_has_primary_currency_field():
    """Site dataclass should expose primary_currency with a default of 'SC'."""
    s = Site(id="x", name="X", url="https://x", status="working")
    assert s.primary_currency == "SC"


def test_seed_working_sites_declare_primary_currency():
    """Every status='working' site in sites_seed.toml should have primary_currency set."""
    repo = Path(__file__).resolve().parent.parent
    sites = load_sites(repo / "sites_seed.toml")
    working = [s for s in sites if s.status == "working"]
    assert working, "expected at least one working site"
    for s in working:
        assert s.primary_currency, (
            f"working site '{s.id}' missing primary_currency"
        )

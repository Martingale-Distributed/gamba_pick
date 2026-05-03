"""Smoke tests: the package imports and core surface is reachable."""

from __future__ import annotations


def test_package_imports():
    import gamba_pick
    assert hasattr(gamba_pick, "__version__")


def test_framework_modules_import():
    from gamba_pick import casino, scrapling_ext, scrapling_pick, selectors_generic
    # No assertions — the import succeeding is the test.
    _ = (casino, scrapling_ext, scrapling_pick, selectors_generic)

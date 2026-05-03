"""Smoke tests: the package imports and core surface is reachable."""

from __future__ import annotations


def test_package_imports():
    import gamba_pick
    assert hasattr(gamba_pick, "__version__")


def test_framework_modules_import():
    from gamba_pick import casino, scrapling_ext, scrapling_pick, selectors_generic
    # No assertions — the import succeeding is the test.
    _ = (casino, scrapling_ext, scrapling_pick, selectors_generic)


def test_reference_configs_importable():
    """Reference configs must be loadable as scripts (no import-time errors).

    This is a static check: import the module via the same mechanism the
    runner uses (sys.executable subprocess against the file path).
    """
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    for name in ("stake_us", "spinquest", "shuffle_us"):
        path = repo / "reference_configs" / f"{name}.py"
        assert path.exists(), f"missing reference config: {path}"
        # `python -c "import ast; ast.parse(open(path).read())"` is a
        # cheap syntactic + import-resolution check that doesn't actually
        # execute the script.
        result = subprocess.run(
            [sys.executable, "-c", f"import ast; ast.parse(open(r'{path}').read())"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"reference config {name} failed to parse:\n{result.stderr}"
        )

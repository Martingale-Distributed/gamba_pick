"""``gamba-pick`` console-script entrypoint.

Phase-2 placeholder: forwards to the existing ``runner.main()`` so the
console script declared in ``pyproject.toml`` works end-to-end while
the real customer-facing CLI is built up in Phase 4. Do NOT add new
behavior here — every flag and code path that lives in ``runner.main``
today must keep working unchanged through Phase 3.
"""

from __future__ import annotations


def main() -> int:
    # Imported lazily so a bad import in runner.py doesn't break
    # `gamba-pick --help` for unrelated reasons.
    import importlib.util
    import sys
    from pathlib import Path

    # Load runner.py from repo root by absolute path
    repo_root = Path(__file__).resolve().parent.parent
    runner_path = repo_root / "runner.py"

    spec = importlib.util.spec_from_file_location("runner", runner_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load runner module from {runner_path}")

    runner_module = importlib.util.module_from_spec(spec)
    sys.modules["runner"] = runner_module
    spec.loader.exec_module(runner_module)

    return runner_module.main()

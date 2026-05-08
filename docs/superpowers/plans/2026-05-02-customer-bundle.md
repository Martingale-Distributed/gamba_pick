# Customer-Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `gamba_pick`'s dev-grade `runner.py` workflow into a packaged, customer-facing CLI: a properly-organized Python package, a no-arg default invocation with detect-and-instruct preflight, append-only `claims.csv` with a `--summary` rollup view, an encrypted `.gpcat` catalog format with a per-customer license-key flow, and a `uv`-bootstrap distribution wrapper that runs without a Python preinstall.

**Architecture:** Reorganize the framework into a real `gamba_pick/` Python package with `cli.py` as the console-script entrypoint. The CLI orchestrates: catalog detection → license-key prompt → AES-GCM-via-HKDF decrypt to a temp dir → preflight checks → existing per-site subprocess loop → `claims.csv` append + JSONL append. A pair of bootstrap scripts (`run.sh` / `run.cmd`) install `uv` if missing, sync deps, fetch browsers once, then exec `gamba-pick`. A separate `tools/build_gpcat.py` (not shipped to customers) builds encrypted catalogs at sale time.

**Tech Stack:** Python 3.11+, `uv` for dependency management, `cryptography` (HKDF + AES-GCM), `zstandard` for catalog compression, `pytest` for tests. Existing deps unchanged: `playwright`, `patchright`, `camoufox`, `scrapling`.

**Spec:** [`docs/superpowers/specs/2026-05-02-customer-bundle-design.md`](../specs/2026-05-02-customer-bundle-design.md)

---

## Phase milestones (you can stop here and ship)

- **End of Phase 2:** Source-tree reorg complete; existing `runner.py` behavior preserved through a thin `gamba_pick.cli` shim. Dev workflow unchanged. **Ships as 0.7.0-alpha1.**
- **End of Phase 4:** New CLI default behavior + CSV writer + `--summary` view live. Customers using the dev install path get the new UX. **Ships as 0.7.0-alpha2.**
- **End of Phase 6:** Encryption layer + license flow + `tools/build_gpcat.py` working end-to-end. Catalogs can ship as `.gpcat`. **Ships as 0.7.0-alpha3.**
- **End of Phase 8:** Distribution wrapper + customer zip ready. Phase-1 commercial deliverable. **Ships as 0.7.0.**

---

## Conventions used in this plan

- All paths are relative to repo root: `/home/lothrop/src/gamba_pick/`.
- Test files live in `tests/`. Run: `uv run pytest tests/` (after Phase 1 Task 2 adds pytest).
- After every task that introduces or modifies code: run the relevant tests, then commit.
- Never use `--no-verify` on commits. If a hook fails, fix the underlying issue.
- Per the project's versioning policy: bump the pyproject `version` only at the end of the plan (Phase 8 Task 21), not per-phase.

---

# Phase 1 — Source-tree reorganization (prerequisite for everything)

## Task 1: Create `gamba_pick/` package and move framework modules

**Files:**
- Create: `gamba_pick/__init__.py`
- Move: `casino.py` → `gamba_pick/casino.py`
- Move: `scrapling_ext.py` → `gamba_pick/scrapling_ext.py`
- Move: `scrapling_pick.py` → `gamba_pick/scrapling_pick.py`
- Move: `selectors_generic.py` → `gamba_pick/selectors_generic.py`
- Modify: `gamba_pick/casino.py` — update internal import
- Modify: `gamba_pick/scrapling_ext.py` — update internal imports

> **Note:** Files like `casino_template.py`, `casino_configs_examples.py`, `casino_selector_discovery.py`, `casino_template_parameterized.py`, `chumba_casino.py`, `luckybird.py` are dev/template/exploration files that are NOT part of the framework. Leave them at the repo root untouched in this plan. They can be cleaned up in a follow-up.

- [ ] **Step 1: Create the package init file**

```python
# gamba_pick/__init__.py
"""gamba_pick — declarative multi-step browser automation framework.

Public surface re-exports for convenience:
    from gamba_pick import casino, scrapling_ext

Top-level entrypoints:
    - gamba_pick.cli.main  — the ``gamba-pick`` console script
"""

__version__ = "0.6.0"  # bumped to 0.7.0 in Phase 8 Task 21
```

```bash
mkdir gamba_pick
# write the file above
```

- [ ] **Step 2: Move the four framework files into the package**

```bash
git mv casino.py gamba_pick/casino.py
git mv scrapling_ext.py gamba_pick/scrapling_ext.py
git mv scrapling_pick.py gamba_pick/scrapling_pick.py
git mv selectors_generic.py gamba_pick/selectors_generic.py
```

- [ ] **Step 3: Update the `casino.py` import to use the new package path**

In `gamba_pick/casino.py`, find:

```python
from selectors_generic import (
```

Change to:

```python
from gamba_pick.selectors_generic import (
```

(There is exactly one such import in `casino.py`. The import block continues across multiple lines — only the `from` line needs changing.)

- [ ] **Step 4: Update `scrapling_ext.py` imports**

In `gamba_pick/scrapling_ext.py`, look for any `from casino import ...`, `from selectors_generic import ...`, or `from scrapling_pick import ...` and rewrite them as `from gamba_pick.casino import ...`, etc.

```bash
grep -nE "^(from|import) (casino|selectors_generic|scrapling_pick|scrapling_ext)\b" gamba_pick/scrapling_ext.py
```

Edit each match to add the `gamba_pick.` prefix.

- [ ] **Step 5: Verify the package imports**

```bash
uv run python -c "from gamba_pick import casino, scrapling_ext, scrapling_pick, selectors_generic; print('ok')"
```

Expected output: `ok`

If any `ModuleNotFoundError` appears, fix the import in the named module by adding the `gamba_pick.` prefix.

- [ ] **Step 6: Commit**

```bash
git add gamba_pick/ casino.py scrapling_ext.py scrapling_pick.py selectors_generic.py
git commit -m "refactor: move framework modules into gamba_pick/ package"
```

(The deleted root-level files appear in `git add` because of the `git mv`; the `gamba_pick/` directory is the new home.)

---

## Task 2: Set up pytest + smoke-test the package

**Files:**
- Modify: `pyproject.toml` — add `[project.optional-dependencies] test = [...]`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Add pytest to optional-dependencies in pyproject.toml**

In `pyproject.toml`, find:

```toml
[project.optional-dependencies]
shell = [
    "IPython>=8.3.0",     # The last version that supports Python 3.10
    "markdownify>=1.2.0",
]
```

Add a new `test` group below it:

```toml
[project.optional-dependencies]
shell = [
    "IPython>=8.3.0",     # The last version that supports Python 3.10
    "markdownify>=1.2.0",
]
test = [
    "pytest>=8.0",
    "pytest-mock>=3.12",
]
```

- [ ] **Step 2: Sync the new dep**

```bash
uv sync --extra test
```

Expected: pytest gets installed.

- [ ] **Step 3: Create a tests directory + smoke test**

```python
# tests/__init__.py
# (empty file — marks tests/ as a package so pytest finds conftest.py)
```

```python
# tests/conftest.py
"""Shared pytest fixtures for the gamba_pick test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_root() -> Path:
    """The repository root, useful for tests that read fixture data."""
    return REPO_ROOT
```

```python
# tests/test_smoke.py
"""Smoke tests: the package imports and core surface is reachable."""

from __future__ import annotations


def test_package_imports():
    import gamba_pick
    assert hasattr(gamba_pick, "__version__")


def test_framework_modules_import():
    from gamba_pick import casino, scrapling_ext, scrapling_pick, selectors_generic
    # No assertions — the import succeeding is the test.
    _ = (casino, scrapling_ext, scrapling_pick, selectors_generic)
```

- [ ] **Step 4: Run the smoke test**

```bash
uv run pytest tests/ -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/
git commit -m "test: add pytest harness + smoke tests for gamba_pick package"
```

---

## Task 3: Move reference configs to `reference_configs/`

**Files:**
- Move: `stake_us.py` → `reference_configs/stake_us.py`
- Move: `spinquest.py` → `reference_configs/spinquest.py`
- Move: `shuffle_us.py` → `reference_configs/shuffle_us.py`
- Modify: each of the above — update root-level imports to `gamba_pick.*`
- Modify: `runner.py` — add `reference_configs/` to module-resolution search path

- [ ] **Step 1: Move the three reference config files**

```bash
mkdir reference_configs
git mv stake_us.py reference_configs/stake_us.py
git mv spinquest.py reference_configs/spinquest.py
git mv shuffle_us.py reference_configs/shuffle_us.py
```

- [ ] **Step 2: Update imports in each reference config**

For each of the three files, find imports like `from casino import ...`, `from scrapling_ext import ...`, `from scrapling_pick import ...`, `from selectors_generic import ...` and change the prefix to `gamba_pick.`.

```bash
grep -nE "^(from|import) (casino|scrapling_ext|scrapling_pick|selectors_generic)\b" reference_configs/*.py
```

For each match, edit the file to change e.g. `from casino import ...` → `from gamba_pick.casino import ...`.

- [ ] **Step 3: Update `runner.py` to find modules in `reference_configs/`**

In `runner.py`, find `_resolve_module_path`:

```python
def _resolve_module_path(module: str, config_dir: Optional[Path]) -> Optional[Path]:
    if config_dir is not None:
        external = (config_dir / f"{module}.py").resolve()
        if external.exists():
            return external
    in_tree = ROOT / f"{module}.py"
    if in_tree.exists():
        return in_tree
    return None
```

Replace it with:

```python
REFERENCE_CONFIGS_DIR = ROOT / "reference_configs"


def _resolve_module_path(module: str, config_dir: Optional[Path]) -> Optional[Path]:
    """Find ``<module>.py`` in (a) the external --config-dir, (b) the
    bundled reference_configs/, then give up.

    Returns the first existing path or ``None``.
    """
    if config_dir is not None:
        external = (config_dir / f"{module}.py").resolve()
        if external.exists():
            return external
    bundled = REFERENCE_CONFIGS_DIR / f"{module}.py"
    if bundled.exists():
        return bundled
    return None
```

- [ ] **Step 4: Add a child-import smoke test**

In `tests/test_smoke.py`, append:

```python
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
```

- [ ] **Step 5: Run the test**

```bash
uv run pytest tests/test_smoke.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add reference_configs/ runner.py tests/test_smoke.py
git commit -m "refactor: move reference configs to reference_configs/ + update resolution"
```

---

# Phase 2 — Stub CLI entrypoint (preserves dev workflow)

## Task 4: Create `gamba_pick/cli.py` as a thin shim around `runner.main()`

This is intentionally a stub — the real CLI rewrite happens in Phase 4. We do this here so the `pyproject.toml` console-script declaration becomes valid and `uv run gamba-pick ...` works for everyone immediately.

**Files:**
- Create: `gamba_pick/cli.py`
- Modify: `tests/test_smoke.py`

- [ ] **Step 1: Create the shim**

```python
# gamba_pick/cli.py
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
    from runner import main as runner_main
    return runner_main()
```

- [ ] **Step 2: Add a CLI-entrypoint smoke test**

Append to `tests/test_smoke.py`:

```python
def test_cli_entrypoint_resolves():
    """The ``gamba-pick`` console script entrypoint imports + is callable."""
    from gamba_pick import cli
    assert callable(cli.main)
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/ -v
```

Expected: 4 passed.

- [ ] **Step 4: Verify the console script works end-to-end**

```bash
uv sync   # picks up the entry-point declaration
uv run gamba-pick --list
```

Expected: prints the runner's "Plan: N site(s)..." block and lists working sites. (Same output as `python runner.py --list` today.)

- [ ] **Step 5: Commit**

```bash
git add gamba_pick/cli.py tests/test_smoke.py
git commit -m "feat(cli): add gamba_pick.cli shim around runner.main"
```

> **Phase 2 milestone:** `gamba-pick` console script works; tests pass; dev workflow unchanged. Could ship as `0.7.0-alpha1`.

---

# Phase 3 — Site config: `primary_currency` field

## Task 5: Add `primary_currency` to the `Site` dataclass and seed entries

**Files:**
- Modify: `runner.py` — add `primary_currency` field to `Site`
- Modify: `sites_seed.toml` — populate for working sites
- Create: `tests/test_seed.py`

- [ ] **Step 1: Write a failing test for the new field**

```python
# tests/test_seed.py
"""Tests for sites_seed.toml loading and the Site dataclass."""

from __future__ import annotations

from pathlib import Path

from runner import Site, load_sites


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
```

- [ ] **Step 2: Run the test, expect failure**

```bash
uv run pytest tests/test_seed.py -v
```

Expected: 2 failed (`AttributeError: 'Site' object has no attribute 'primary_currency'` or similar).

- [ ] **Step 3: Add the field to the `Site` dataclass**

In `runner.py`, find:

```python
@dataclass
class Site:
    id: str
    name: str
    url: str
    status: str
    auth: str = "oauth"
    module: Optional[str] = None
    affiliate_link: Optional[str] = None
    timeout_s: Optional[int] = None
```

Add `primary_currency` immediately after `timeout_s`:

```python
@dataclass
class Site:
    id: str
    name: str
    url: str
    status: str
    auth: str = "oauth"
    module: Optional[str] = None
    affiliate_link: Optional[str] = None
    timeout_s: Optional[int] = None
    # Currency code that goes into the ``balance`` / ``currency`` columns
    # of claims.csv. Other parsed currencies land in
    # ``secondary_balances``. Defaults to "SC" — the redeemable currency
    # in the sweepstakes-casino model that covers most of our sites.
    # Override per-site for non-SC sites (e.g. "FC" for FortuneWins,
    # "USDT" for Stake.us).
    primary_currency: str = "SC"
```

- [ ] **Step 4: Add `primary_currency` to working seed entries**

In `sites_seed.toml`, the three working sites already have entries. Add a `primary_currency` line to each. For all three (`spinquest`, `stake_us`, `shuffle_us`), the redeemable currency is `SC` — but `stake_us` actually deals in crypto-equivalents under its `USDT`-denominated balance reads, so use `USDT` for that one. Spinquest and shuffle_us use `SC`.

For `spinquest`:

```toml
[[site]]
id = "spinquest"
name = "SpinQuest"
url = "https://spinquest.com"
status = "working"
auth = "form"
module = "spinquest"
primary_currency = "SC"
```

For `stake_us`:

```toml
[[site]]
id = "stake_us"
name = "Stake.us"
url = "https://stake.us"
affiliate_link = "https://stake.us/?c=vXzVKL4j"
status = "working"
auth = "form"
module = "stake_us"
primary_currency = "USDT"
```

For `shuffle_us`:

```toml
[[site]]
id = "shuffle_us"
name = "Shuffle.us"
url = "https://shuffle.us"
status = "working"
auth = "form"
module = "shuffle_us"
primary_currency = "SC"
```

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/test_seed.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add runner.py sites_seed.toml tests/test_seed.py
git commit -m "feat(seed): add primary_currency field per site"
```

---

# Phase 4 — CSV writer + summary view

## Task 6: Create `gamba_pick/csv_writer.py`

**Files:**
- Create: `gamba_pick/csv_writer.py`
- Create: `tests/test_csv_writer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_csv_writer.py
"""Tests for the claims.csv writer."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gamba_pick.csv_writer import (
    CLAIMS_CSV_HEADER,
    ClaimRow,
    append_claim_row,
    read_last_balance_for_site,
)


def _make_row(
    site: str = "sportzino",
    balance: float | None = 3.84,
    currency: str = "SC",
    secondary: dict[str, float] | None = None,
    duration_s: float = 9.2,
    success: bool = True,
    outcome: str = "claimed",
    run_ts: str = "2026-05-02T12:00:00+00:00",
) -> ClaimRow:
    return ClaimRow(
        run_ts=run_ts,
        date="2026-05-02",
        site=site,
        balance=balance,
        currency=currency,
        secondary_balances=secondary or {},
        duration_s=duration_s,
        success=success,
        claim_outcome=outcome,
    )


def test_first_append_creates_file_with_header(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    append_claim_row(_make_row(), csv_path=csv_path)

    with open(csv_path, newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == CLAIMS_CSV_HEADER
    assert len(rows) == 2  # header + 1 data row


def test_subsequent_appends_do_not_rewrite_header(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    append_claim_row(_make_row(balance=3.84), csv_path=csv_path)
    append_claim_row(_make_row(balance=4.34), csv_path=csv_path)

    with open(csv_path, newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == CLAIMS_CSV_HEADER
    assert len(rows) == 3  # header + 2 data rows


def test_delta_first_row_is_empty(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    append_claim_row(_make_row(balance=3.84), csv_path=csv_path)

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["delta"] == ""


def test_delta_uses_most_recent_prior_row_for_same_site(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    append_claim_row(
        _make_row(balance=3.34, run_ts="2026-05-01T12:00:00+00:00"),
        csv_path=csv_path,
    )
    append_claim_row(
        _make_row(balance=3.84, run_ts="2026-05-02T12:00:00+00:00"),
        csv_path=csv_path,
    )

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert rows[0]["delta"] == ""           # first row for sportzino
    assert rows[1]["delta"] == "0.5"        # 3.84 - 3.34


def test_delta_per_site_independent(tmp_path: Path):
    """A different site's prior row must not leak into the new row's delta."""
    csv_path = tmp_path / "claims.csv"
    append_claim_row(_make_row(site="sportzino", balance=3.34), csv_path=csv_path)
    append_claim_row(_make_row(site="zula_casino", balance=12.10), csv_path=csv_path)

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert rows[1]["delta"] == ""  # first zula_casino row, no prior


def test_secondary_balances_serialized_as_json(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    append_claim_row(
        _make_row(secondary={"GC": 43562260.0}),
        csv_path=csv_path,
    )

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert json.loads(row["secondary_balances"]) == {"GC": 43562260.0}


def test_empty_balance_writes_blank(tmp_path: Path):
    """If the run crashed before any balance parsed, balance/currency are blank."""
    csv_path = tmp_path / "claims.csv"
    append_claim_row(
        _make_row(balance=None, currency="", secondary={}),
        csv_path=csv_path,
    )

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["balance"] == ""
    assert row["currency"] == ""


def test_success_serialized_as_lowercase_string(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    append_claim_row(_make_row(success=True), csv_path=csv_path)
    append_claim_row(_make_row(success=False, balance=3.34), csv_path=csv_path)

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert rows[0]["success"] == "true"
    assert rows[1]["success"] == "false"


def test_read_last_balance_for_site_with_no_file(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    assert read_last_balance_for_site(csv_path, "sportzino") is None


def test_read_last_balance_for_site_with_no_matching_rows(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    append_claim_row(_make_row(site="zula_casino", balance=12.10), csv_path=csv_path)
    assert read_last_balance_for_site(csv_path, "sportzino") is None
```

- [ ] **Step 2: Run tests, expect failure**

```bash
uv run pytest tests/test_csv_writer.py -v
```

Expected: All fail with `ModuleNotFoundError: No module named 'gamba_pick.csv_writer'`.

- [ ] **Step 3: Implement the writer**

```python
# gamba_pick/csv_writer.py
"""Append-only writer for ``claims.csv`` — the customer-facing balance log.

Schema (one row per site per run):

    run_ts, date, site, balance, currency, delta, secondary_balances,
    duration_s, success, claim_outcome

``delta`` is computed at write time as the new ``balance`` minus the most
recent prior ``balance`` for the same ``site``. Empty for the first row
of a site, or if the new row's balance is missing.

``secondary_balances`` is a JSON object literal (CSV-escaped) of the
non-primary currencies parsed for this run, e.g. ``{"GC": 43562260.0}``.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CLAIMS_CSV_HEADER = [
    "run_ts",
    "date",
    "site",
    "balance",
    "currency",
    "delta",
    "secondary_balances",
    "duration_s",
    "success",
    "claim_outcome",
]


@dataclass
class ClaimRow:
    """One row in claims.csv. ``delta`` is computed by ``append_claim_row``."""

    run_ts: str                          # ISO-8601 UTC, e.g. "2026-05-02T12:00:00+00:00"
    date: str                            # "YYYY-MM-DD"
    site: str
    balance: Optional[float]             # None → blank in the CSV
    currency: str                        # "" when balance is None
    secondary_balances: dict = field(default_factory=dict)
    duration_s: float = 0.0
    success: bool = False
    claim_outcome: str = "unknown"


def read_last_balance_for_site(csv_path: Path, site: str) -> Optional[float]:
    """Return the balance from the most recent prior row for ``site``, or None."""
    if not csv_path.exists():
        return None
    last_balance: Optional[float] = None
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["site"] != site:
                continue
            raw = row["balance"]
            if raw == "":
                # crashed-run row; doesn't shift the delta basis
                continue
            try:
                last_balance = float(raw)
            except ValueError:
                continue
    return last_balance


def append_claim_row(row: ClaimRow, *, csv_path: Path) -> None:
    """Append ``row`` to ``csv_path``, computing ``delta`` against prior history.

    Creates the file with a header if it doesn't exist. Open-write-close
    per call (fsync via context-manager close) so partial state is never
    visible to readers.
    """
    prior = read_last_balance_for_site(csv_path, row.site)
    if row.balance is None or prior is None:
        delta_str = ""
    else:
        delta = row.balance - prior
        # Strip trailing zeros for readability without surprising users
        # who expect decimals — `0.5` not `0.50000000000000004`.
        delta_str = f"{round(delta, 6):g}"

    file_exists = csv_path.exists()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(CLAIMS_CSV_HEADER)
        writer.writerow([
            row.run_ts,
            row.date,
            row.site,
            "" if row.balance is None else f"{row.balance:g}",
            row.currency,
            delta_str,
            json.dumps(row.secondary_balances, sort_keys=True),
            f"{round(row.duration_s, 2):g}",
            "true" if row.success else "false",
            row.claim_outcome,
        ])
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_csv_writer.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add gamba_pick/csv_writer.py tests/test_csv_writer.py
git commit -m "feat(csv): add append-only claims.csv writer with per-site delta"
```

---

## Task 7: Wire the CSV writer into the runner

**Files:**
- Modify: `runner.py` — call `append_claim_row` after each subprocess returns; add `--no-csv` flag
- Modify: `tests/test_csv_writer.py` — add an integration-shaped test

- [ ] **Step 1: Add the `--no-csv` flag and the wiring**

In `runner.py`, find the argparse block that adds `--stream` and add `--no-csv` next to it:

```python
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help=(
            "Skip the claims.csv append for this run. JSONL history "
            "still writes — that's the debugging artifact, not the "
            "customer-facing balance log."
        ),
    )
```

Add a new constant near the other path constants at module top:

```python
CLAIMS_CSV = ROOT / "claims.csv"
```

In the main loop, after `append_history(result, opts.log_file)` (the JSONL append inside the per-site loop), add the CSV append. Find:

```python
        if not opts.stream:
            append_history(result, opts.log_file)
```

Replace with:

```python
        if not opts.stream:
            append_history(result, opts.log_file)
            if not opts.no_csv:
                _append_csv_row(site, result, opts.claims_csv)
```

Add a `--claims-csv` flag (parallel to `--log-file`):

```python
    parser.add_argument(
        "--claims-csv",
        type=Path,
        default=CLAIMS_CSV,
        help="Append-only customer-facing balance log (CSV).",
    )
```

Add the helper near `append_history`:

```python
def _append_csv_row(site: Site, result: RunResult, csv_path: Path) -> None:
    """Translate a runner ``RunResult`` into a ``ClaimRow`` and append.

    Picks the primary currency value from ``result.balances`` according
    to ``site.primary_currency``; everything else lands in
    ``secondary_balances``. If the primary isn't in the parsed balances
    (e.g. a crash before the Account State line emitted), ``balance``
    and ``currency`` are blank and *all* parsed balances go into
    ``secondary_balances``.
    """
    from gamba_pick.csv_writer import ClaimRow, append_claim_row

    primary_code = site.primary_currency
    primary_value = result.balances.get(primary_code)
    if primary_value is not None:
        secondary = {
            code: val for code, val in result.balances.items()
            if code != primary_code
        }
        currency = primary_code
    else:
        secondary = dict(result.balances)
        currency = ""

    # ``ts`` on RunResult is already ISO-8601 UTC; date is the calendar
    # day in UTC. Don't try to localize — claim_history is UTC and the
    # CSV should match.
    date = result.ts.split("T", 1)[0] if "T" in result.ts else result.ts

    row = ClaimRow(
        run_ts=result.ts,
        date=date,
        site=site.id,
        balance=primary_value,
        currency=currency,
        secondary_balances=secondary,
        duration_s=result.duration_s,
        success=result.ok,
        claim_outcome=result.claim_outcome,
    )
    append_claim_row(row, csv_path=csv_path)
```

- [ ] **Step 2: Add an integration test**

Append to `tests/test_csv_writer.py`:

```python
def test_runner_helper_picks_primary_and_routes_secondary(tmp_path: Path):
    """The runner's _append_csv_row picks site.primary_currency for the
    primary balance and routes the rest into secondary_balances."""
    from runner import Site, RunResult, _append_csv_row

    site = Site(
        id="sportzino",
        name="Sportzino",
        url="https://sportzino.com",
        status="working",
        auth="form",
        module="sportzino",
        primary_currency="SC",
    )
    result = RunResult(
        ts="2026-05-02T12:00:00+00:00",
        site_id="sportzino",
        module="sportzino",
        ok=True,
        exit_code=0,
        duration_s=9.2,
        timed_out=False,
        balances={"SC": 3.84, "GC": 43562260.0},
        claim_outcome="claimed",
        stdout_tail="",
        stderr_tail="",
    )
    csv_path = tmp_path / "claims.csv"

    _append_csv_row(site, result, csv_path)

    import csv as _csv
    with open(csv_path, newline="") as f:
        row = next(_csv.DictReader(f))
    assert row["balance"] == "3.84"
    assert row["currency"] == "SC"
    assert json.loads(row["secondary_balances"]) == {"GC": 43562260.0}


def test_runner_helper_with_missing_primary_routes_all_to_secondary(tmp_path: Path):
    """If the primary currency isn't in the balances, balance/currency are blank
    and ALL balances go into secondary_balances."""
    from runner import Site, RunResult, _append_csv_row

    site = Site(
        id="fortune_wins",
        name="Fortune Wins",
        url="https://fortunewins.com",
        status="working",
        auth="form",
        module="fortune_wins",
        primary_currency="FC",
    )
    result = RunResult(
        ts="2026-05-02T12:00:00+00:00",
        site_id="fortune_wins",
        module="fortune_wins",
        ok=True,
        exit_code=0,
        duration_s=9.2,
        timed_out=False,
        balances={"SC": 3.84, "GC": 43562260.0},  # no FC parsed
        claim_outcome="claimed",
        stdout_tail="",
        stderr_tail="",
    )
    csv_path = tmp_path / "claims.csv"

    _append_csv_row(site, result, csv_path)

    import csv as _csv
    with open(csv_path, newline="") as f:
        row = next(_csv.DictReader(f))
    assert row["balance"] == ""
    assert row["currency"] == ""
    assert json.loads(row["secondary_balances"]) == {"SC": 3.84, "GC": 43562260.0}
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/test_csv_writer.py -v
```

Expected: 12 passed (10 from before + 2 new).

- [ ] **Step 4: Commit**

```bash
git add runner.py tests/test_csv_writer.py
git commit -m "feat(runner): write claims.csv per run + add --no-csv flag"
```

---

## Task 8: Implement `--summary` view

**Files:**
- Create: `gamba_pick/csv_summary.py`
- Modify: `runner.py` — add `--summary` flag handling
- Create: `tests/test_csv_summary.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_csv_summary.py
"""Tests for the claims.csv --summary rollup view."""

from __future__ import annotations

import csv
from pathlib import Path

from gamba_pick.csv_summary import build_summary_table, render_summary
from gamba_pick.csv_writer import ClaimRow, append_claim_row


def _row(
    site: str,
    date: str,
    run_ts: str,
    balance: float | None = 3.84,
    currency: str = "SC",
    success: bool = True,
    outcome: str = "claimed",
) -> ClaimRow:
    return ClaimRow(
        run_ts=run_ts,
        date=date,
        site=site,
        balance=balance,
        currency=currency,
        secondary_balances={},
        duration_s=8.0,
        success=success,
        claim_outcome=outcome,
    )


def test_empty_csv_returns_empty_summary(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    rows = build_summary_table(csv_path, days=30)
    assert rows == []


def test_one_row_per_date_site_pair(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    append_claim_row(
        _row("sportzino", "2026-05-02", "2026-05-02T09:00:00+00:00", balance=3.34),
        csv_path=csv_path,
    )
    append_claim_row(
        _row("sportzino", "2026-05-02", "2026-05-02T15:00:00+00:00", balance=3.84),
        csv_path=csv_path,
    )

    rows = build_summary_table(csv_path, days=30)
    assert len(rows) == 1
    assert rows[0]["site"] == "sportzino"
    assert rows[0]["date"] == "2026-05-02"
    assert rows[0]["balance"] == "3.84"     # last run wins
    assert rows[0]["runs"] == 2


def test_delta_today_uses_yesterdays_last_balance(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    append_claim_row(
        _row("sportzino", "2026-05-01", "2026-05-01T09:00:00+00:00", balance=3.34),
        csv_path=csv_path,
    )
    append_claim_row(
        _row("sportzino", "2026-05-02", "2026-05-02T09:00:00+00:00", balance=3.84),
        csv_path=csv_path,
    )

    rows = build_summary_table(csv_path, days=30)
    today = next(r for r in rows if r["date"] == "2026-05-02")
    assert today["delta_today"] == "+0.5"


def test_failed_row_shows_error_in_balance_column(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    append_claim_row(
        _row(
            "pulsz", "2026-05-02", "2026-05-02T09:00:00+00:00",
            balance=None, currency="", success=False, outcome="error",
        ),
        csv_path=csv_path,
    )

    rows = build_summary_table(csv_path, days=30)
    assert rows[0]["balance"] == "(error)"
    assert rows[0]["delta_today"] == "—"


def test_render_summary_emits_header_and_data(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    append_claim_row(
        _row("sportzino", "2026-05-02", "2026-05-02T09:00:00+00:00", balance=3.84),
        csv_path=csv_path,
    )

    text = render_summary(csv_path, days=30)
    assert "date" in text
    assert "site" in text
    assert "sportzino" in text
    assert "2026-05-02" in text


def test_render_summary_empty_file_message(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    text = render_summary(csv_path, days=30)
    assert "no claim history" in text.lower() or "empty" in text.lower()
```

- [ ] **Step 2: Run tests, expect failure**

```bash
uv run pytest tests/test_csv_summary.py -v
```

Expected: ModuleNotFoundError on the import.

- [ ] **Step 3: Implement the summary module**

```python
# gamba_pick/csv_summary.py
"""``--summary`` view: collapse claims.csv into one row per (date, site).

Used by ``gamba-pick --summary [N]`` (and the underlying runner CLI).
Read-only — never mutates claims.csv.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date as _date, timedelta
from pathlib import Path
from typing import List, Optional


def _parse_balance(raw: str) -> Optional[float]:
    if raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _format_delta(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:+g}"


def build_summary_table(csv_path: Path, *, days: int) -> List[dict]:
    """Return the rollup as a list of dicts, sorted newest date first.

    Each dict has keys: ``date``, ``site``, ``balance``, ``currency``,
    ``delta_today``, ``outcome``, ``runs``.

    ``delta_today`` is today's last balance minus the previous calendar
    day's last balance for that site (NOT intra-day). Returns "—" when
    no prior calendar-day row exists or when either end is missing.

    A group whose last-by-run_ts row has ``success=false`` shows
    ``"(error)"`` in ``balance`` and ``"—"`` in ``delta_today``.
    """
    if not csv_path.exists():
        return []

    # Read all rows.
    with open(csv_path, newline="") as f:
        all_rows = list(csv.DictReader(f))
    if not all_rows:
        return []

    # Filter by date window — relative to the most recent date in the file
    # (more honest than "today" when the user is reviewing offline).
    all_dates = sorted({r["date"] for r in all_rows})
    cutoff = (_date.fromisoformat(all_dates[-1]) - timedelta(days=days - 1)).isoformat()
    rows = [r for r in all_rows if r["date"] >= cutoff]

    # Group by (date, site) → list of rows in run_ts order.
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["date"], r["site"])].append(r)

    # For each group, take the last run_ts row.
    summary: dict[tuple[str, str], dict] = {}
    for key, group in groups.items():
        group.sort(key=lambda r: r["run_ts"])
        summary[key] = {
            "last": group[-1],
            "runs": len(group),
        }

    # Compute delta_today: for each (date, site), find previous calendar
    # day's last balance for the same site (using ALL rows, not just
    # within the window).
    by_site_date: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in all_rows:
        # If multiple rows on same date, keep the one with latest run_ts
        existing = by_site_date[r["site"]].get(r["date"])
        if existing is None or r["run_ts"] > existing["run_ts"]:
            by_site_date[r["site"]][r["date"]] = r

    # Build output sorted newest date first, then alphabetical site.
    out: list[dict] = []
    for (d, site), info in sorted(summary.items(), key=lambda kv: (kv[0][0], kv[0][1]), reverse=True):
        last = info["last"]
        success = last["success"] == "true"
        balance_val = _parse_balance(last["balance"])

        if not success:
            balance_display = "(error)"
            delta_display = "—"
        else:
            balance_display = "" if balance_val is None else f"{balance_val:g}"
            # find previous calendar day's last row for this site
            site_dates = sorted(by_site_date[site].keys())
            try:
                idx = site_dates.index(d)
            except ValueError:
                idx = -1
            prev_balance: Optional[float] = None
            if idx > 0:
                prev_row = by_site_date[site][site_dates[idx - 1]]
                prev_balance = _parse_balance(prev_row["balance"])
            if balance_val is not None and prev_balance is not None:
                delta_display = _format_delta(balance_val - prev_balance)
            else:
                delta_display = "—"

        out.append({
            "date": d,
            "site": site,
            "balance": balance_display,
            "currency": last["currency"] if success else "—",
            "delta_today": delta_display,
            "outcome": last["claim_outcome"],
            "runs": info["runs"],
        })

    return out


def render_summary(csv_path: Path, *, days: int) -> str:
    """Format the rollup as a plain-ASCII table for stdout."""
    rows = build_summary_table(csv_path, days=days)
    if not rows:
        return "No claim history yet (claims.csv is empty or missing)."

    headers = ["date", "site", "balance", "currency", "delta_today", "outcome", "runs"]
    table_rows = [headers] + [
        [r["date"], r["site"], r["balance"], r["currency"], r["delta_today"],
         r["outcome"], str(r["runs"])]
        for r in rows
    ]

    widths = [max(len(row[i]) for row in table_rows) for i in range(len(headers))]
    sep = "  ".join("-" * w for w in widths)

    def _format_row(row: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    lines = [
        f"Last {days} day(s), one row per (date, site) — most recent run per day shown:",
        "",
        _format_row(headers),
        sep,
    ]
    for r in table_rows[1:]:
        lines.append(_format_row(r))
    return "\n".join(lines)
```

- [ ] **Step 4: Wire `--summary` into the runner CLI**

In `runner.py`, add the flag near the other CLI flags:

```python
    parser.add_argument(
        "--summary",
        type=int,
        nargs="?",
        const=30,
        default=None,
        metavar="N",
        help=(
            "Print the one-row-per-day-per-site rollup of claims.csv "
            "for the last N days (default 30). Read-only; no execution."
        ),
    )
```

In `main()`, after `opts = parser.parse_args()` and before any other branch, add:

```python
    if opts.summary is not None:
        from gamba_pick.csv_summary import render_summary
        print(render_summary(opts.claims_csv, days=opts.summary))
        return 0
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_csv_summary.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Smoke-test `--summary`**

```bash
# Use a temp claims.csv to avoid touching real data
TMPDIR=$(mktemp -d) && cd "$TMPDIR" && touch claims.csv && \
  cd - && uv run gamba-pick --summary 7 --claims-csv "$TMPDIR/claims.csv"
```

Expected: prints "No claim history yet (claims.csv is empty or missing)." and exits 0.

- [ ] **Step 7: Commit**

```bash
git add gamba_pick/csv_summary.py runner.py tests/test_csv_summary.py
git commit -m "feat(csv): add --summary rollup view"
```

> **Phase 4 milestone:** CSV writer + summary view shipping. Customers using the dev install path get the new UX. Could ship as `0.7.0-alpha2`.

---

# Phase 5 — Crypto / `.gpcat` format

## Task 9: Add `cryptography` and `zstandard` deps

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the new deps**

In `pyproject.toml`, find the `dependencies = [...]` block and append:

```toml
    "cryptography>=42",
    "zstandard>=0.22",
```

- [ ] **Step 2: Resolve and lock**

```bash
uv sync
uv lock
```

- [ ] **Step 3: Smoke-import**

```bash
uv run python -c "from cryptography.hazmat.primitives.ciphers.aead import AESGCM; from cryptography.hazmat.primitives.kdf.hkdf import HKDF; import zstandard; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add cryptography + zstandard for .gpcat encryption"
```

---

## Task 10: Implement `gamba_pick/gpcat.py` — format, KDF, encrypt/decrypt

**Files:**
- Create: `gamba_pick/gpcat.py`
- Create: `tests/test_gpcat.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gpcat.py
"""Tests for the .gpcat format + encryption layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from gamba_pick.gpcat import (
    GpcatHeader,
    canonicalize_license,
    derive_key,
    encrypt_to_bytes,
    decrypt_bytes,
    parse_header,
    GpcatFormatError,
    GpcatLicenseError,
    MAGIC,
    FORMAT_VER,
)


# ---- license-string canonicalization ----

def test_canonicalize_strips_dashes_and_whitespace():
    raw = "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567"
    assert canonicalize_license(raw) == "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def test_canonicalize_uppercases():
    assert canonicalize_license("abcd-efgh-ijkl-mnop-qrst-uvwx-yz23-4567") == \
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def test_canonicalize_strips_internal_whitespace():
    assert canonicalize_license("  ABCD efgh ijklmnop qrstuvwxyz234567  ") == \
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


# ---- key derivation ----

def test_derive_key_is_deterministic():
    license_str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    bundle_id = b"\x00" * 16
    k1 = derive_key(license_str, bundle_id)
    k2 = derive_key(license_str, bundle_id)
    assert k1 == k2
    assert len(k1) == 32  # AES-256


def test_derive_key_changes_with_bundle_id():
    license_str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    k1 = derive_key(license_str, b"\x00" * 16)
    k2 = derive_key(license_str, b"\x01" * 16)
    assert k1 != k2


def test_derive_key_changes_with_license():
    bundle_id = b"\x00" * 16
    k1 = derive_key("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", bundle_id)
    k2 = derive_key("BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB", bundle_id)
    assert k1 != k2


# ---- round-trip ----

def test_round_trip_encrypt_decrypt():
    plaintext = b"hello, world\n" * 1000
    license_str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    bundle_id = b"\x42" * 16

    blob = encrypt_to_bytes(plaintext, license_str=license_str, bundle_id=bundle_id)
    out = decrypt_bytes(blob, license_str=license_str)
    assert out == plaintext


def test_decrypt_with_wrong_license_raises():
    plaintext = b"secret catalog contents"
    bundle_id = b"\x01" * 16

    blob = encrypt_to_bytes(
        plaintext,
        license_str="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567",
        bundle_id=bundle_id,
    )
    with pytest.raises(GpcatLicenseError):
        decrypt_bytes(blob, license_str="WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW")


def test_decrypt_garbage_raises_format_error():
    with pytest.raises(GpcatFormatError):
        decrypt_bytes(b"not a gpcat", license_str="X" * 32)


def test_decrypt_wrong_magic_raises_format_error():
    blob = b"WRONG" + b"\x00" * 100
    with pytest.raises(GpcatFormatError):
        decrypt_bytes(blob, license_str="X" * 32)


def test_parse_header_round_trip():
    header = GpcatHeader(
        magic=MAGIC,
        format_ver=FORMAT_VER,
        bundle_id=b"\x99" * 16,
        nonce=b"\x77" * 12,
    )
    raw = header.pack()
    parsed = parse_header(raw)
    assert parsed.magic == MAGIC
    assert parsed.format_ver == FORMAT_VER
    assert parsed.bundle_id == b"\x99" * 16
    assert parsed.nonce == b"\x77" * 12
```

- [ ] **Step 2: Run tests, expect failure**

```bash
uv run pytest tests/test_gpcat.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `gpcat.py`**

```python
# gamba_pick/gpcat.py
"""``.gpcat`` format: encrypted, compressed catalog bundles.

File layout (binary, network byte order for fixed fields):

    [ 4 bytes  ] magic       = b"GPCT"
    [ 1 byte   ] format_ver  = 0x01
    [16 bytes  ] bundle_id   = random per build; HKDF salt
    [12 bytes  ] nonce       = AES-GCM nonce, random per build
    [ N bytes  ] ciphertext  = AES-256-GCM(plaintext = tar.zst of catalog)
    [16 bytes  ] tag         = AES-GCM auth tag (trailing)

Key derivation:

    key = HKDF-SHA256(
        ikm    = utf8(canonicalize_license(license_string)),
        salt   = bundle_id,
        info   = b"gamba-pick.gpcat.v1",
        length = 32,
    )

License-string canonicalization: strip dashes and whitespace, uppercase.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"GPCT"
FORMAT_VER = 0x01
BUNDLE_ID_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16
HEADER_LEN = 4 + 1 + BUNDLE_ID_LEN + NONCE_LEN  # 33 bytes
HKDF_INFO = b"gamba-pick.gpcat.v1"


class GpcatError(Exception):
    """Base class for .gpcat handling errors."""


class GpcatFormatError(GpcatError):
    """Raised when a .gpcat blob is malformed (wrong magic, truncated, etc.)."""


class GpcatLicenseError(GpcatError):
    """Raised when AES-GCM tag verification fails (wrong license key)."""


@dataclass(frozen=True)
class GpcatHeader:
    magic: bytes
    format_ver: int
    bundle_id: bytes
    nonce: bytes

    def pack(self) -> bytes:
        if len(self.magic) != 4:
            raise ValueError("magic must be 4 bytes")
        if len(self.bundle_id) != BUNDLE_ID_LEN:
            raise ValueError(f"bundle_id must be {BUNDLE_ID_LEN} bytes")
        if len(self.nonce) != NONCE_LEN:
            raise ValueError(f"nonce must be {NONCE_LEN} bytes")
        return (
            self.magic
            + struct.pack("!B", self.format_ver)
            + self.bundle_id
            + self.nonce
        )


def parse_header(raw: bytes) -> GpcatHeader:
    if len(raw) < HEADER_LEN:
        raise GpcatFormatError(
            f".gpcat truncated: header needs {HEADER_LEN} bytes, got {len(raw)}"
        )
    magic = raw[:4]
    if magic != MAGIC:
        raise GpcatFormatError(
            f".gpcat magic mismatch: expected {MAGIC!r}, got {magic!r}"
        )
    format_ver = raw[4]
    if format_ver != FORMAT_VER:
        raise GpcatFormatError(
            f".gpcat format_ver {format_ver} not supported by this build "
            f"(supported: {FORMAT_VER})"
        )
    bundle_id = raw[5 : 5 + BUNDLE_ID_LEN]
    nonce = raw[5 + BUNDLE_ID_LEN : HEADER_LEN]
    return GpcatHeader(magic=magic, format_ver=format_ver, bundle_id=bundle_id, nonce=nonce)


def canonicalize_license(raw: str) -> str:
    """Strip dashes, whitespace, and uppercase. Fail-soft on weird input.

    Customers paste with dashes (XXXX-XXXX-...); we don't care about
    spacing or case. The KDF is over the canonical form.
    """
    return "".join(ch for ch in raw if not ch.isspace() and ch != "-").upper()


def derive_key(license_str: str, bundle_id: bytes) -> bytes:
    """HKDF-SHA256 key derivation. ``license_str`` is canonicalized first."""
    canonical = canonicalize_license(license_str)
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=bundle_id,
        info=HKDF_INFO,
        backend=default_backend(),
    )
    return hkdf.derive(canonical.encode("utf-8"))


def encrypt_to_bytes(
    plaintext: bytes,
    *,
    license_str: str,
    bundle_id: bytes,
    nonce: bytes | None = None,
) -> bytes:
    """Build a .gpcat blob from raw plaintext bytes.

    ``plaintext`` should already be the compressed (zstd) catalog tarball;
    this function just AES-GCM-encrypts it. The build script
    (``tools/build_gpcat.py``) is responsible for the tar+zstd step.

    ``nonce`` is randomized when omitted (the normal path).
    """
    if len(bundle_id) != BUNDLE_ID_LEN:
        raise ValueError(f"bundle_id must be {BUNDLE_ID_LEN} bytes")
    if nonce is None:
        import os
        nonce = os.urandom(NONCE_LEN)
    if len(nonce) != NONCE_LEN:
        raise ValueError(f"nonce must be {NONCE_LEN} bytes")

    header = GpcatHeader(magic=MAGIC, format_ver=FORMAT_VER, bundle_id=bundle_id, nonce=nonce)
    key = derive_key(license_str, bundle_id)
    aesgcm = AESGCM(key)
    # AESGCM.encrypt returns ciphertext || tag.
    ct_with_tag = aesgcm.encrypt(nonce, plaintext, associated_data=header.pack())
    # Per the format, header || ciphertext || tag — but AESGCM.encrypt
    # already concatenates ciphertext + tag, so we just append.
    return header.pack() + ct_with_tag


def decrypt_bytes(blob: bytes, *, license_str: str) -> bytes:
    """Decrypt a .gpcat blob and return the raw plaintext bytes.

    Raises GpcatFormatError if the header is malformed; GpcatLicenseError
    if the AES-GCM tag fails (wrong license, truncation, tampering).
    """
    header = parse_header(blob)
    ct_with_tag = blob[HEADER_LEN:]
    if len(ct_with_tag) < TAG_LEN:
        raise GpcatFormatError("ciphertext too short to contain auth tag")
    key = derive_key(license_str, header.bundle_id)
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(header.nonce, ct_with_tag, associated_data=header.pack())
    except Exception as e:  # cryptography raises InvalidTag
        raise GpcatLicenseError(f"AES-GCM decrypt failed: {e}") from e
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_gpcat.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add gamba_pick/gpcat.py tests/test_gpcat.py
git commit -m "feat(gpcat): add encrypted catalog format + AES-GCM via HKDF"
```

---

## Task 11: Implement `tools/build_gpcat.py` (sale-time bundle builder)

**Files:**
- Create: `tools/__init__.py` (empty — marks it as a package)
- Create: `tools/build_gpcat.py`
- Create: `tests/test_build_gpcat.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_build_gpcat.py
"""Tests for the build_gpcat sale-time bundler."""

from __future__ import annotations

import io
import secrets
import tarfile
from pathlib import Path

import zstandard

from gamba_pick.gpcat import decrypt_bytes
from tools.build_gpcat import (
    build_gpcat,
    generate_license_string,
    pack_catalog_to_zstd_tar,
)


def test_generate_license_string_format():
    s = generate_license_string()
    # 32 chars (no dashes) of Crockford base32 alphabet
    assert len(s.replace("-", "")) == 32
    # When dashed: 8 groups of 4 separated by dashes
    assert s.count("-") == 7
    for grp in s.split("-"):
        assert len(grp) == 4


def test_pack_catalog_round_trip(tmp_path: Path):
    src = tmp_path / "catalog"
    src.mkdir()
    (src / "manifest.toml").write_text("[meta]\nversion = '1'\n")
    (src / "configs").mkdir()
    (src / "configs" / "site_a.py").write_text("# site_a config\n")

    blob = pack_catalog_to_zstd_tar(src)
    # Decompress + untar in memory and check contents.
    dctx = zstandard.ZstdDecompressor()
    raw = dctx.decompress(blob)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tf:
        names = sorted(tf.getnames())
    assert "manifest.toml" in names
    assert "configs/site_a.py" in names


def test_build_gpcat_round_trip(tmp_path: Path):
    src = tmp_path / "catalog"
    src.mkdir()
    (src / "manifest.toml").write_text("[meta]\nversion = '1'\n")

    out = tmp_path / "test.gpcat"
    license_str = "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567"
    bundle_id = secrets.token_bytes(16)

    build_gpcat(
        catalog_dir=src,
        output_path=out,
        license_str=license_str,
        bundle_id=bundle_id,
    )

    blob = out.read_bytes()
    plaintext = decrypt_bytes(blob, license_str=license_str)
    # Plaintext is the zstd-compressed tarball.
    dctx = zstandard.ZstdDecompressor()
    raw = dctx.decompress(plaintext)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tf:
        names = tf.getnames()
    assert "manifest.toml" in names
```

- [ ] **Step 2: Run tests, expect failure**

```bash
uv run pytest tests/test_build_gpcat.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement the builder**

```python
# tools/__init__.py
# (empty — marks tools/ as a package so tests can import from it)
```

```python
# tools/build_gpcat.py
"""Sale-time builder for ``.gpcat`` catalog bundles.

This script lives in the gamba_pick repo but is NOT shipped in the
customer zip. It's the operator's tool: feed it a catalog directory and
optionally a license string + bundle_id, and it emits an encrypted
.gpcat plus prints the credentials you email the customer.

Usage:

    uv run python -m tools.build_gpcat \\
        --catalog-dir ./build/casino-buddy-2026-05/ \\
        --output       ./build/casino-buddy-2026-05.gpcat

    # With explicit credentials (idempotent rebuilds):
    uv run python -m tools.build_gpcat \\
        --catalog-dir   ./build/casino-buddy-2026-05/ \\
        --output        ./build/casino-buddy-2026-05.gpcat \\
        --license       ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567 \\
        --bundle-id-hex 0123456789abcdef0123456789abcdef
"""

from __future__ import annotations

import argparse
import io
import secrets
import sys
import tarfile
from pathlib import Path

import zstandard

from gamba_pick.gpcat import encrypt_to_bytes

# Crockford base32 alphabet (RFC 4648 base32 minus I, L, O, U for visual
# disambiguation — kept simple here using the standard alphabet; can
# tighten later if customer complaints come in).
_BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def generate_license_string() -> str:
    """Return a 32-char Crockford-base32 license string with dashes every 4 chars."""
    raw = secrets.token_bytes(20)  # 20 bytes = 160 bits = 32 base32 chars
    # Encode to base32 manually using the alphabet (avoids "=" padding).
    out_chars: list[str] = []
    bits = 0
    bit_count = 0
    for byte in raw:
        bits = (bits << 8) | byte
        bit_count += 8
        while bit_count >= 5:
            bit_count -= 5
            idx = (bits >> bit_count) & 0b11111
            out_chars.append(_BASE32_ALPHABET[idx])
    if bit_count > 0:
        idx = (bits << (5 - bit_count)) & 0b11111
        out_chars.append(_BASE32_ALPHABET[idx])
    s = "".join(out_chars)[:32]
    # Re-group into 8 quads separated by dashes.
    return "-".join(s[i : i + 4] for i in range(0, 32, 4))


def pack_catalog_to_zstd_tar(catalog_dir: Path) -> bytes:
    """Tar the directory contents, then zstd-compress. Returns the compressed bytes."""
    if not catalog_dir.exists():
        raise FileNotFoundError(catalog_dir)
    if not catalog_dir.is_dir():
        raise NotADirectoryError(catalog_dir)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        # arcname relative to catalog_dir so paths in the tar are clean.
        tf.add(catalog_dir, arcname=".")
    raw = buf.getvalue()
    cctx = zstandard.ZstdCompressor(level=10)
    return cctx.compress(raw)


def build_gpcat(
    *,
    catalog_dir: Path,
    output_path: Path,
    license_str: str,
    bundle_id: bytes,
) -> None:
    plaintext = pack_catalog_to_zstd_tar(catalog_dir)
    blob = encrypt_to_bytes(
        plaintext,
        license_str=license_str,
        bundle_id=bundle_id,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(blob)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an encrypted .gpcat catalog bundle.",
    )
    parser.add_argument(
        "--catalog-dir",
        type=Path,
        required=True,
        help="Directory holding the catalog tree (manifest.toml, configs/, ...)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output .gpcat file path.",
    )
    parser.add_argument(
        "--license",
        default=None,
        help="License string (XXXX-XXXX-...). Auto-generated if omitted.",
    )
    parser.add_argument(
        "--bundle-id-hex",
        default=None,
        help="32-char hex bundle_id. Auto-generated if omitted.",
    )
    opts = parser.parse_args(argv)

    license_str = opts.license or generate_license_string()
    if opts.bundle_id_hex:
        bundle_id = bytes.fromhex(opts.bundle_id_hex)
        if len(bundle_id) != 16:
            print("error: --bundle-id-hex must decode to 16 bytes", file=sys.stderr)
            return 2
    else:
        bundle_id = secrets.token_bytes(16)

    build_gpcat(
        catalog_dir=opts.catalog_dir,
        output_path=opts.output,
        license_str=license_str,
        bundle_id=bundle_id,
    )

    import hashlib
    sha = hashlib.sha256(opts.output.read_bytes()).hexdigest()

    print(f"Wrote {opts.output} ({opts.output.stat().st_size:,} bytes)")
    print()
    print(f"  license_string : {license_str}")
    print(f"  bundle_id_hex  : {bundle_id.hex()}")
    print(f"  sha256         : {sha}")
    print()
    print("Email the customer the license_string and the .gpcat file.")
    print("Log the {license_string, bundle_id, sha256, customer} tuple in your sales record.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_build_gpcat.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Smoke-test the CLI end-to-end**

```bash
TMPDIR=$(mktemp -d)
mkdir -p "$TMPDIR/catalog/configs"
echo "[meta]\nversion = '1'\n" > "$TMPDIR/catalog/manifest.toml"
echo "# fake config" > "$TMPDIR/catalog/configs/site_x.py"

uv run python -m tools.build_gpcat \
    --catalog-dir "$TMPDIR/catalog" \
    --output      "$TMPDIR/test.gpcat"
```

Expected: prints the license string, bundle_id, sha256, and "Wrote .../test.gpcat (...)". The license string format is `XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX`.

- [ ] **Step 6: Commit**

```bash
git add tools/ tests/test_build_gpcat.py
git commit -m "feat(tools): add build_gpcat for sale-time catalog packaging"
```

---

## Task 12: License-key store (read/write `picks.env`)

**Files:**
- Create: `gamba_pick/license_store.py`
- Create: `tests/test_license_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_license_store.py
"""Tests for picks.env read/write of the GAMBA_PICK_LICENSE entry."""

from __future__ import annotations

from pathlib import Path

from gamba_pick.license_store import (
    LICENSE_ENV_VAR,
    read_license,
    write_license,
)


def test_read_returns_none_when_file_missing(tmp_path: Path):
    assert read_license(tmp_path / "picks.env") is None


def test_read_returns_none_when_var_missing(tmp_path: Path):
    env = tmp_path / "picks.env"
    env.write_text("FOO=bar\nBAZ=qux\n")
    assert read_license(env) is None


def test_read_returns_value_when_present(tmp_path: Path):
    env = tmp_path / "picks.env"
    env.write_text(f"FOO=bar\n{LICENSE_ENV_VAR}=ABCD-EFGH-IJKL\nBAZ=qux\n")
    assert read_license(env) == "ABCD-EFGH-IJKL"


def test_read_handles_quoted_value(tmp_path: Path):
    env = tmp_path / "picks.env"
    env.write_text(f'{LICENSE_ENV_VAR}="ABCD-EFGH-IJKL"\n')
    assert read_license(env) == "ABCD-EFGH-IJKL"


def test_write_creates_file_when_missing(tmp_path: Path):
    env = tmp_path / "picks.env"
    write_license(env, "ABCD-EFGH-IJKL")
    text = env.read_text()
    assert f"{LICENSE_ENV_VAR}=ABCD-EFGH-IJKL" in text


def test_write_updates_existing_var(tmp_path: Path):
    env = tmp_path / "picks.env"
    env.write_text(f"FOO=bar\n{LICENSE_ENV_VAR}=OLD-VALUE\nBAZ=qux\n")
    write_license(env, "ABCD-EFGH-IJKL")

    text = env.read_text()
    assert "FOO=bar" in text
    assert "BAZ=qux" in text
    assert f"{LICENSE_ENV_VAR}=ABCD-EFGH-IJKL" in text
    assert "OLD-VALUE" not in text


def test_write_appends_when_var_absent(tmp_path: Path):
    env = tmp_path / "picks.env"
    env.write_text("FOO=bar\nBAZ=qux\n")
    write_license(env, "ABCD-EFGH-IJKL")

    text = env.read_text()
    assert "FOO=bar" in text
    assert "BAZ=qux" in text
    assert f"{LICENSE_ENV_VAR}=ABCD-EFGH-IJKL" in text
```

- [ ] **Step 2: Run tests, expect failure**

```bash
uv run pytest tests/test_license_store.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement the license store**

```python
# gamba_pick/license_store.py
"""Read/write of ``GAMBA_PICK_LICENSE`` in ``picks.env``.

Treats picks.env as a flat KEY=VALUE file. We don't parse it as a real
shell file — we just look for / write the one variable we care about,
preserving the rest of the file. That's enough for our use case
(occasional one-time write from the license prompt).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

LICENSE_ENV_VAR = "GAMBA_PICK_LICENSE"


def read_license(env_path: Path) -> Optional[str]:
    """Return the license string from picks.env, or None if absent.

    Strips surrounding quotes ("..." or '...') if present.
    """
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != LICENSE_ENV_VAR:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value
    return None


def write_license(env_path: Path, license_str: str) -> None:
    """Persist ``license_str`` as ``GAMBA_PICK_LICENSE`` in picks.env.

    - If the file doesn't exist: create it.
    - If the variable already exists: replace that line in place.
    - Else: append the variable on a new trailing line.

    Does not modify any other lines.
    """
    new_line = f"{LICENSE_ENV_VAR}={license_str}"
    env_path.parent.mkdir(parents=True, exist_ok=True)

    if not env_path.exists():
        env_path.write_text(new_line + "\n")
        return

    lines = env_path.read_text().splitlines()
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{LICENSE_ENV_VAR}="):
            lines[i] = new_line
            found = True
            break

    if not found:
        lines.append(new_line)

    env_path.write_text("\n".join(lines) + "\n")
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_license_store.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add gamba_pick/license_store.py tests/test_license_store.py
git commit -m "feat(license): add picks.env read/write for GAMBA_PICK_LICENSE"
```

---

## Task 13: Catalog discovery + decrypt-to-tempdir

**Files:**
- Create: `gamba_pick/catalog.py`
- Create: `tests/test_catalog.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_catalog.py
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
```

- [ ] **Step 2: Run tests, expect failure**

```bash
uv run pytest tests/test_catalog.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement the catalog module**

```python
# gamba_pick/catalog.py
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
            # Use ``filter='data'`` (Python 3.12+) to refuse absolute
            # paths and other tar shenanigans. The .gpcat builder we
            # control writes only relative paths, so this is purely
            # defense-in-depth against a hostile blob with a known key.
            tf.extractall(path=out_dir, filter="data")
    except Exception:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    return out_dir
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_catalog.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add gamba_pick/catalog.py tests/test_catalog.py
git commit -m "feat(catalog): add .gpcat discovery + decrypt-to-tempdir"
```

> **Phase 5 milestone:** encryption layer complete. Round-trip works through every layer (build_gpcat → on-disk .gpcat → discover → decrypt → tarball → tempdir). Could ship as `0.7.0-alpha3`.

---

# Phase 6 — Detect-and-instruct preflight

## Task 14: Preflight checks (catalog state)

**Files:**
- Create: `gamba_pick/preflight.py`
- Create: `tests/test_preflight.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_preflight.py
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
    # no env vars set
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
```

- [ ] **Step 2: Run tests, expect failure**

```bash
uv run pytest tests/test_preflight.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement preflight checks**

```python
# gamba_pick/preflight.py
"""Detect-and-instruct preflight: figure out what's missing for the
customer's install before we touch the browser, and produce a
remediation list they can act on.

The preflight has three pieces:

- ``check_catalog(catalog_dir)`` — "do we have a catalog, and is it
  in a valid state to proceed?" Returns a ``PreflightAction`` telling
  the CLI what to do next (proceed with reference configs only,
  decrypt this .gpcat, or fail with a multi-catalog error).

- ``check_site_credentials(...)`` — for one site, "are the credentials
  / browser profile set up?" Returns a ``RemediationItem`` if the
  customer needs to do something, or ``None`` if the site is ready.

- ``run_preflight(...)`` (in Task 16) — the aggregator that walks all
  configured sites and produces the full remediation list.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gamba_pick.catalog import CatalogState, discover_catalog


@dataclass(frozen=True)
class PreflightAction:
    """Result of ``check_catalog``. Tells the CLI what to do next.

    Possible kinds:
    - "info"     — print ``message``, continue with reference configs only
    - "decrypt"  — proceed to decrypt ``gpcat_path``
    - "error"    — print ``message``, exit non-zero
    """
    kind: str
    message: str = ""
    gpcat_path: Optional[Path] = None


@dataclass(frozen=True)
class RemediationItem:
    """One thing the customer needs to do before the next run will succeed."""
    site_id: str
    reason: str    # short human-readable summary (e.g. "needs credentials")
    action: str    # the exact instruction to print


def check_catalog(catalog_dir: Path) -> PreflightAction:
    state, path = discover_catalog(catalog_dir)
    if state is CatalogState.MULTI:
        return PreflightAction(
            kind="error",
            message=(
                "Multiple .gpcat files found in catalog/ — keep only the most "
                "recent one and re-run."
            ),
        )
    if state is CatalogState.ONE:
        assert path is not None
        return PreflightAction(kind="decrypt", gpcat_path=path)
    return PreflightAction(
        kind="info",
        message=(
            "No catalog found. Running with public reference configs only. "
            "Drop your .gpcat into catalog/ to enable your purchased sites."
        ),
    )


def check_site_credentials(
    *,
    site_id: str,
    auth: str,
    env: dict,
    profiles_dir: Path,
) -> Optional[RemediationItem]:
    """Return a RemediationItem describing what's missing, or None if ready.

    For ``auth="form"``: checks for <SITE_ID>_USERNAME and <SITE_ID>_PASSWORD.
    For ``auth="oauth"``: checks for a non-empty ``profiles/<site_id>/`` dir.
    """
    site_key = site_id.upper()
    if auth == "form":
        username_key = f"{site_key}_USERNAME"
        password_key = f"{site_key}_PASSWORD"
        missing = [k for k in (username_key, password_key) if not env.get(k)]
        if not missing:
            return None
        return RemediationItem(
            site_id=site_id,
            reason="missing credentials",
            action=(
                f"Edit picks.env and add: {username_key}=...; {password_key}=..."
            ),
        )
    if auth == "oauth":
        profile = profiles_dir / site_id
        if profile.exists() and profile.is_dir() and any(profile.iterdir()):
            return None
        return RemediationItem(
            site_id=site_id,
            reason="OAuth profile not bootstrapped",
            action=f"Bootstrap login for {site_id}: ./run.sh --setup {site_id}",
        )
    # Unknown auth value — not a remediation issue, just a configuration smell.
    # Don't block the run on this; the site script will fail with a clearer
    # error when it tries to log in.
    return None
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_preflight.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add gamba_pick/preflight.py tests/test_preflight.py
git commit -m "feat(preflight): add catalog + per-site credential checks"
```

---

## Task 15: Preflight aggregator + remediation formatter

**Files:**
- Modify: `gamba_pick/preflight.py` — add `run_preflight` + `format_remediation`
- Modify: `tests/test_preflight.py` — add aggregator tests

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_preflight.py`:

```python
def test_run_preflight_all_ready_returns_empty(tmp_path: Path):
    from gamba_pick.preflight import run_preflight, RunPreflightInput

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
            action="Edit picks.env and add: SPORTZINO_USERNAME=...; SPORTZINO_PASSWORD=...",
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
```

- [ ] **Step 2: Run, expect failure**

```bash
uv run pytest tests/test_preflight.py -v
```

Expected: 4 new tests fail with ImportError on `run_preflight` / `format_remediation`.

- [ ] **Step 3: Implement aggregator + formatter**

Append to `gamba_pick/preflight.py`:

```python
def run_preflight(
    *,
    sites: list[dict],
    env: dict,
    profiles_dir: Path,
) -> list[RemediationItem]:
    """Walk every site and aggregate missing-setup items.

    ``sites`` is a list of dicts with ``id`` and ``auth`` keys (subset of
    Site fields — keeps preflight independent of the runner's dataclass).
    """
    items: list[RemediationItem] = []
    for site in sites:
        item = check_site_credentials(
            site_id=site["id"],
            auth=site.get("auth", "oauth"),
            env=env,
            profiles_dir=profiles_dir,
        )
        if item is not None:
            items.append(item)
    return items


def format_remediation(items: list[RemediationItem]) -> str:
    """Format the remediation list as a human-readable block.

    Empty list → empty string. Otherwise: a numbered list of actions
    followed by the "Setup incomplete: N step(s) remaining" trailer
    and the "When done, ..." nudge.
    """
    if not items:
        return ""
    lines = ["Setup is not complete. Take these steps and re-run:", ""]
    for i, item in enumerate(items, start=1):
        lines.append(f"  {i}. [{item.site_id}] {item.action}")
    lines.append("")
    lines.append(f"Setup incomplete: {len(items)} step(s) remaining.")
    lines.append("When done, run ./run.sh again to start your daily claim.")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_preflight.py -v
```

Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add gamba_pick/preflight.py tests/test_preflight.py
git commit -m "feat(preflight): add aggregator + remediation formatter"
```

---

# Phase 7 — New CLI

## Task 16: Replace the `cli.py` shim with the real entrypoint

**Files:**
- Modify: `gamba_pick/cli.py` — full rewrite
- Modify: `runner.py` — expose internals + remove its `main()` flag-parsing duplication
- Create: `tests/test_cli.py`

> **Note:** This task is bigger than most. We're moving the orchestration logic from `runner.main()` into `gamba_pick.cli.main()` and reshaping the flag surface. The per-site subprocess loop (`run_site`, `parse_outcome`, `append_history`) stays in `runner.py` — those are reusable utilities that the CLI calls. We'll remove `runner.main()` entirely once `cli.main()` is wired up; `runner.py` becomes a utilities module.

- [ ] **Step 1: Write the CLI tests first**

```python
# tests/test_cli.py
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
```

- [ ] **Step 2: Run tests, expect failure**

```bash
uv run pytest tests/test_cli.py -v
```

Expected: most fail because `build_parser`, `load_env_for_preflight`, etc. don't exist yet.

- [ ] **Step 3: Replace the cli.py shim with the real entrypoint**

Replace the entire contents of `gamba_pick/cli.py`:

```python
# gamba_pick/cli.py
"""``gamba-pick`` console-script entrypoint.

The default invocation (no flags) is the customer-facing daily-claim
sweep:

    1. Load sites_seed.toml.
    2. Discover and decrypt the catalog (if any) via gamba_pick.catalog.
    3. Run the detect-and-instruct preflight (gamba_pick.preflight).
       If anything is missing, print the remediation list and exit 0.
    4. Otherwise, run the per-site subprocess sweep
       (runner.run_site → claim_history.jsonl + claims.csv).
    5. Print a summary line.

Other flags:
    --setup [<site>]   bootstrap browser profile(s)
    --summary [N]      print one-row-per-day-per-site rollup
    --list             show plan, no execution
    --show             show config snapshot
    --no-csv           skip claims.csv append
    --show-browser     suppress headless mode (debug aid)
    --only / --skip    site filters
    --version          version banner
"""

from __future__ import annotations

import argparse
import atexit
import os
import shutil
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from gamba_pick import __version__
from gamba_pick.catalog import decrypt_catalog_to_tempdir
from gamba_pick.csv_summary import render_summary
from gamba_pick.gpcat import GpcatLicenseError
from gamba_pick.license_store import read_license, write_license
from gamba_pick.preflight import (
    PreflightAction,
    check_catalog,
    format_remediation,
    run_preflight,
)

# Default paths — assume cwd is the gamba-pick install root (where the
# bootstrap script cd's to).
DEFAULT_ROOT = Path.cwd()
DEFAULT_SEED = DEFAULT_ROOT / "sites_seed.toml"
DEFAULT_CATALOG_DIR = DEFAULT_ROOT / "catalog"
DEFAULT_PROFILES_DIR = DEFAULT_ROOT / "profiles"
DEFAULT_CLAIMS_CSV = DEFAULT_ROOT / "claims.csv"
DEFAULT_LOG_FILE = DEFAULT_ROOT / "claim_history.jsonl"
DEFAULT_PICKS_ENV = DEFAULT_ROOT / "picks.env"
MAX_LICENSE_ATTEMPTS = 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gamba-pick",
        description="Daily claim sweep across configured sites.",
    )
    p.add_argument("--version", action="version", version=f"gamba-pick {__version__}")

    # Modes (mutually-exclusive only at runtime; argparse can't model
    # "summary OR setup OR list OR show OR sweep" cleanly).
    p.add_argument(
        "--summary",
        type=int, nargs="?", const=30, default=None, metavar="N",
        help="Print the one-row-per-day-per-site rollup of claims.csv "
             "for the last N days (default 30). Read-only.",
    )
    p.add_argument(
        "--setup",
        nargs="?", const="", default=None, metavar="SITE_ID",
        help="Bootstrap browser profile for a site. No arg = walk every "
             "unbootstrapped OAuth site one at a time. Interactive.",
    )
    p.add_argument(
        "--list", action="store_true",
        help="Show what would run; no execution.",
    )
    p.add_argument(
        "--show", action="store_true",
        help="Show config snapshot (catalog, sites, license fingerprint).",
    )

    # Sweep behavior modifiers.
    p.add_argument(
        "--no-csv", action="store_true",
        help="Skip claims.csv append. JSONL still writes.",
    )
    p.add_argument(
        "--show-browser", action="store_true",
        help="Run with a visible browser. Suppresses default --headless.",
    )
    p.add_argument(
        "--only", nargs="+", default=None, metavar="IDS",
        help="Only run these site ids (comma- or space-separated).",
    )
    p.add_argument(
        "--skip", nargs="+", default=None, metavar="IDS",
        help="Skip these site ids.",
    )

    # Paths (defaults derived from cwd).
    p.add_argument("--seed-file", type=Path, default=DEFAULT_SEED)
    p.add_argument("--catalog-dir", type=Path, default=DEFAULT_CATALOG_DIR)
    p.add_argument("--profiles-dir", type=Path, default=DEFAULT_PROFILES_DIR)
    p.add_argument("--claims-csv", type=Path, default=DEFAULT_CLAIMS_CSV)
    p.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE)
    p.add_argument("--picks-env", type=Path, default=DEFAULT_PICKS_ENV)
    p.add_argument(
        "--timeout-per-site", type=int, default=300,
        help="Default per-site subprocess timeout (seconds).",
    )

    # Hidden / advanced flags retained for the dev workflow:
    p.add_argument("--config-dir", type=Path, default=None,
                   help=argparse.SUPPRESS)
    p.add_argument("--google-oauth", action="store_true",
                   help=argparse.SUPPRESS)
    p.add_argument("--stream", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--pause-on-stuck", action="store_true",
                   help=argparse.SUPPRESS)
    return p


def load_env_for_preflight(picks_env: Path) -> dict:
    """Return a flat ``KEY=VALUE`` dict from picks.env merged with os.environ.

    os.environ wins on conflict (so a shell-exported override takes effect).
    """
    env: dict = {}
    if picks_env.exists():
        for line in picks_env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            env[k.strip()] = v
    env.update(os.environ)
    return env


def _prompt_license(picks_env: Path) -> Optional[str]:
    """Three-strikes license-key prompt. Returns the entered string or None
    if the user gave up (we let the caller treat that as fatal)."""
    print("Catalog detected. Please enter your license key.")
    print("(format: XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX — paste, dashes optional)")
    try:
        s = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not s:
        return None
    write_license(picks_env, s)
    return s


def _decrypt_with_retry(
    gpcat_path: Path,
    *,
    picks_env: Path,
) -> Path:
    """Look up the license, decrypt; on failure, prompt up to MAX_LICENSE_ATTEMPTS times.

    Returns the temp-dir Path. Registers an atexit cleanup. Raises
    SystemExit(1) if the customer can't produce a valid license.
    """
    license_str = read_license(picks_env)
    attempts_remaining = MAX_LICENSE_ATTEMPTS

    while True:
        if license_str is None:
            license_str = _prompt_license(picks_env)
            if license_str is None:
                print("No license key provided. See the support email line in README.", file=sys.stderr)
                raise SystemExit(1)
        try:
            tmp = decrypt_catalog_to_tempdir(gpcat_path, license_str=license_str)
            atexit.register(shutil.rmtree, str(tmp), ignore_errors=True)
            return tmp
        except GpcatLicenseError:
            attempts_remaining -= 1
            if attempts_remaining <= 0:
                print(
                    "License key didn't match the catalog after several tries. "
                    "If you believe this is a bug, contact support.",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            print(f"License key invalid for this catalog ({attempts_remaining} attempts left). Re-enter:")
            license_str = None  # force re-prompt


def _load_seed(path: Path) -> list[dict]:
    """Load sites_seed.toml as a list of dicts (lighter than runner.Site)."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return list(data.get("site", []))


def _merge_seeds(reference: list[dict], bundle: list[dict]) -> list[dict]:
    """Bundle entries override reference entries on shared site id."""
    by_id = {s["id"]: dict(s) for s in reference}
    for s in bundle:
        by_id[s["id"]] = dict(s)
    return list(by_id.values())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Quick read-only modes.
    if args.summary is not None:
        print(render_summary(args.claims_csv, days=args.summary))
        return 0

    # Catalog discovery + decrypt (if any).
    catalog_action: PreflightAction = check_catalog(args.catalog_dir)
    extra_config_dir: Optional[Path] = None

    if catalog_action.kind == "error":
        print(catalog_action.message, file=sys.stderr)
        return 1
    if catalog_action.kind == "decrypt":
        assert catalog_action.gpcat_path is not None
        try:
            extra_config_dir = _decrypt_with_retry(
                catalog_action.gpcat_path,
                picks_env=args.picks_env,
            )
        except SystemExit as e:
            return int(e.code) if e.code is not None else 1
    elif catalog_action.message:
        print(catalog_action.message)

    # Load seed (reference). If catalog has its own seed, merge.
    sites = _load_seed(args.seed_file)
    if extra_config_dir is not None:
        bundle_seed = extra_config_dir / "sites_seed.toml"
        if bundle_seed.exists():
            sites = _merge_seeds(sites, _load_seed(bundle_seed))

    working = [s for s in sites if s.get("status") == "working"]

    # Filter --only / --skip.
    if args.only:
        keep: set[str] = set()
        for tok in args.only:
            for s in tok.split(","):
                if s.strip():
                    keep.add(s.strip())
        working = [s for s in working if s["id"] in keep]
    if args.skip:
        skip: set[str] = set()
        for tok in args.skip:
            for s in tok.split(","):
                if s.strip():
                    skip.add(s.strip())
        working = [s for s in working if s["id"] not in skip]

    # --list early-exit.
    if args.list:
        print(f"Plan: {len(working)} site(s):")
        for s in working:
            print(f"  - {s['id']:20}  {s.get('module','?')}.py  ({s.get('auth','oauth')})")
        return 0

    # --show early-exit (config snapshot).
    if args.show:
        print(f"gamba-pick {__version__}")
        if extra_config_dir is not None:
            print(f"Catalog: present (decrypted to {extra_config_dir})")
        else:
            print("Catalog: none — running with reference configs only")
        print(f"Sites configured: {len(working)}")
        return 0

    # --setup early-exit (browser-profile bootstrap; delegates to runner.run_site
    # with the existing --setup flag).
    if args.setup is not None:
        return _do_setup(args, working, extra_config_dir)

    # Detect-and-instruct preflight.
    env = load_env_for_preflight(args.picks_env)
    items = run_preflight(
        sites=working,
        env=env,
        profiles_dir=args.profiles_dir,
    )
    if items:
        print(format_remediation(items))
        return 0

    # Run the sweep.
    return _do_sweep(args, working, extra_config_dir)


def _do_setup(args, working: list[dict], extra_config_dir: Optional[Path]) -> int:
    """Run --setup mode: open a real browser per site, save profile.

    Delegates to runner.run_site with --setup forwarded.
    """
    import runner as runner_mod
    target = args.setup
    sites_to_setup = (
        [s for s in working if s["id"] == target]
        if target
        else working
    )
    if target and not sites_to_setup:
        print(f"Site '{target}' not in working set.", file=sys.stderr)
        return 1
    if not sites_to_setup:
        print("No sites to set up.")
        return 0

    # Build a runner-style namespace from cli args. The runner expects an
    # argparse.Namespace, so we build one explicitly with the fields it
    # references in run_site.
    import argparse as _ap
    runner_opts = _ap.Namespace(
        headless=False,                  # interactive setup needs a visible browser
        skip_claim=False,
        setup=True,
        google_oauth=args.google_oauth,
        timeout_per_site=args.timeout_per_site,
        stream=True,                     # blocks on user; needs live stdout
        pause_on_stuck=False,
        config_dir=extra_config_dir or args.config_dir,
        seed_file=args.seed_file,
    )

    for site_dict in sites_to_setup:
        site = runner_mod.Site(**site_dict)
        print(f"=== {site.id} (setup) ===", flush=True)
        runner_mod.run_site(site, runner_opts)
    return 0


def _do_sweep(args, working: list[dict], extra_config_dir: Optional[Path]) -> int:
    """Run the daily sweep. Mirrors runner.main()'s loop, but with the
    new defaults (headless on by default; CSV append on by default)."""
    import argparse as _ap
    import runner as runner_mod
    from gamba_pick import csv_writer

    runner_opts = _ap.Namespace(
        headless=not args.show_browser,
        skip_claim=False,
        setup=False,
        google_oauth=args.google_oauth,
        timeout_per_site=args.timeout_per_site,
        stream=args.stream,
        pause_on_stuck=args.pause_on_stuck,
        config_dir=extra_config_dir or args.config_dir,
        seed_file=args.seed_file,
        log_file=args.log_file,
        claims_csv=args.claims_csv,
        no_csv=args.no_csv,
    )

    print(f"gamba-pick {__version__} — daily claim sweep")
    if extra_config_dir is not None:
        print(f"Catalog: loaded ({len(working)} site(s) configured)")
    else:
        print(f"Reference configs only ({len(working)} site(s) configured)")
    print()

    results = []
    for i, site_dict in enumerate(working, start=1):
        site = runner_mod.Site(**site_dict)
        print(f"[{i}/{len(working)}] {site.id} ...", flush=True)
        result = runner_mod.run_site(site, runner_opts)
        if not runner_opts.stream:
            runner_mod.append_history(result, runner_opts.log_file)
            if not runner_opts.no_csv:
                runner_mod._append_csv_row(site, result, runner_opts.claims_csv)
        flag = "ok" if result.ok else ("timeout" if result.timed_out else "fail")
        bal = ""
        if result.balances:
            bal = "  " + " ".join(
                f"{c}={v}" for c, v in sorted(result.balances.items())
            )
        print(
            f"      {flag}  {result.duration_s}s  claim={result.claim_outcome}{bal}",
            flush=True,
        )
        results.append(result)

    print()
    ok_count = sum(r.ok for r in results)
    if runner_opts.stream:
        print(f"Summary: {ok_count}/{len(results)} ok  (history not written: --stream)")
    else:
        print(
            f"Summary: {ok_count}/{len(results)} ok  "
            f"(claims.csv updated, history -> {runner_opts.log_file})"
        )
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Update runner.py to remove its own `main()` and CLI parsing**

The simpler path is to leave `runner.py`'s `main()` in place but stop using it. The new entrypoint (`gamba_pick.cli.main`) does its own parsing. Verify the console-script declaration still points at `gamba_pick.cli:main` (it does — set in Phase 2 Task 4).

For now, leave `runner.py`'s `main()` — it's reachable as `python runner.py` for the dev path. Remove it in Phase 8 Task 24.

- [ ] **Step 5: Run all tests**

```bash
uv run pytest tests/ -v
```

Expected: all pass. The new `tests/test_cli.py` adds ~6 cases.

- [ ] **Step 6: Smoke-test the new CLI**

```bash
# 1. version
uv run gamba-pick --version
# expected: gamba-pick 0.6.0  (the bump to 0.7.0 happens in Phase 8 Task 21)

# 2. summary against an empty path
uv run gamba-pick --summary 7 --claims-csv /tmp/no-such.csv
# expected: "No claim history yet (claims.csv is empty or missing)."

# 3. list
uv run gamba-pick --list --catalog-dir /tmp/empty-dir
# expected: "Plan: 3 site(s):" (or however many "working" entries are in the seed)

# 4. show
uv run gamba-pick --show --catalog-dir /tmp/empty-dir
# expected: prints version, catalog: none, sites configured: 3
```

- [ ] **Step 7: Commit**

```bash
git add gamba_pick/cli.py tests/test_cli.py
git commit -m "feat(cli): replace shim with full customer-facing entrypoint"
```

> **Phase 7 milestone:** the new CLI is functional via the dev install path. `uv run gamba-pick` does everything the customer would expect minus the bootstrap wrapper. Could ship as `0.7.0-rc1`.

---

# Phase 8 — Distribution wrapper + finalization

## Task 17: Add `run.sh` POSIX bootstrap

**Files:**
- Create: `run.sh`
- Modify: `pyproject.toml` — ensure `requires-python = ">=3.12"` so `uv sync` resolves a Python 3.12 install

> **Pinned `uv` installer:** for Phase-1 we pin to a specific `uv` release. Update the version number and SHA256 below to whatever's current at the time you ship. To regenerate the hash:
>
> ```bash
> curl -fsSL https://astral.sh/uv/0.5.0/install.sh -o /tmp/uv-install.sh
> sha256sum /tmp/uv-install.sh
> ```

- [ ] **Step 1: Create `run.sh`**

```bash
#!/usr/bin/env bash
# gamba-pick bootstrap (POSIX). Installs uv if missing, syncs the
# locked Python environment, fetches browsers on first run, and
# execs the gamba-pick CLI with whatever args you passed.
#
# Pinned uv version + SHA256 — bump these together when you bump uv.

set -euo pipefail

UV_VERSION="0.5.0"
UV_INSTALL_URL="https://astral.sh/uv/${UV_VERSION}/install.sh"
UV_INSTALL_SHA256="REPLACE_WITH_ACTUAL_SHA256_FROM_RELEASE_NOTES"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 1. Detect / install uv.
if ! command -v uv >/dev/null 2>&1; then
    if [[ ! -x "$HOME/.local/bin/uv" ]]; then
        echo "Installing uv ${UV_VERSION}..."
        TMP_INSTALLER="$(mktemp)"
        trap 'rm -f "$TMP_INSTALLER"' EXIT
        curl -fsSL "$UV_INSTALL_URL" -o "$TMP_INSTALLER"
        ACTUAL_SHA="$(sha256sum "$TMP_INSTALLER" | awk '{print $1}')"
        if [[ "$ACTUAL_SHA" != "$UV_INSTALL_SHA256" ]]; then
            echo "ERROR: uv installer hash mismatch." >&2
            echo "  expected: $UV_INSTALL_SHA256" >&2
            echo "  actual:   $ACTUAL_SHA" >&2
            echo "Refusing to run installer. See README.txt for manual install." >&2
            exit 1
        fi
        sh "$TMP_INSTALLER"
    fi
    export PATH="$HOME/.local/bin:$PATH"
fi

# 2. Sync deps (auto-installs Python 3.12 if missing).
uv sync --frozen

# 3. First-run browser fetch.
SENTINEL="$SCRIPT_DIR/.bootstrap-done"
if [[ ! -f "$SENTINEL" ]]; then
    echo "First-run setup: fetching Camoufox + Patchright Chromium (~300 MB)..."
    uv run python -m camoufox fetch
    uv run python -m patchright install chromium
    touch "$SENTINEL"
fi

# 4. Hand off to the CLI.
exec uv run gamba-pick "$@"
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x run.sh
```

- [ ] **Step 3: Update Python requirement**

In `pyproject.toml`, change:

```toml
requires-python = ">=3.11"
```

to:

```toml
requires-python = ">=3.12"
```

- [ ] **Step 4: Smoke-test the bootstrap (skips real install on a dev machine that already has uv)**

```bash
./run.sh --version
```

Expected: prints `gamba-pick 0.6.0` (or whatever the pyproject version currently is). The first invocation in a fresh env will also do the browser fetch on the first call; subsequent calls skip past the sentinel quickly.

- [ ] **Step 5: Commit**

```bash
git add run.sh pyproject.toml
git commit -m "feat(dist): add run.sh POSIX bootstrap with pinned uv installer"
```

---

## Task 18: Add `run.cmd` Windows bootstrap

**Files:**
- Create: `run.cmd`

> **Manual test only.** This file can't be smoke-tested on the dev's Linux box. The smoke test is "the script syntactically parses + the customer reports it works." Mirror `run.sh` exactly in logic — same uv version, same SHA256, same sentinel name.

- [ ] **Step 1: Create `run.cmd`**

```bat
@echo off
REM gamba-pick bootstrap (Windows). Installs uv if missing, syncs the
REM locked Python environment, fetches browsers on first run, and
REM exec's the gamba-pick CLI.

setlocal EnableDelayedExpansion

set "UV_VERSION=0.5.0"
set "UV_INSTALL_URL=https://astral.sh/uv/%UV_VERSION%/install.ps1"
set "UV_INSTALL_SHA256=REPLACE_WITH_ACTUAL_PS1_SHA256"

cd /d "%~dp0"

REM 1. Detect / install uv.
where uv >nul 2>&1
if errorlevel 1 (
    if not exist "%USERPROFILE%\.local\bin\uv.exe" (
        echo Installing uv %UV_VERSION%...
        set "TMP_INSTALLER=%TEMP%\uv-install.ps1"
        powershell -NoProfile -Command "Invoke-WebRequest -UseBasicParsing -Uri '%UV_INSTALL_URL%' -OutFile '%TMP_INSTALLER%'"
        if errorlevel 1 (
            echo ERROR: failed to download uv installer.
            exit /b 1
        )
        REM SHA256 verification.
        for /f "delims=" %%H in ('powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -Path '%TMP_INSTALLER%').Hash.ToLower()"') do set "ACTUAL_SHA=%%H"
        if /i not "%ACTUAL_SHA%"=="%UV_INSTALL_SHA256%" (
            echo ERROR: uv installer hash mismatch.
            echo   expected: %UV_INSTALL_SHA256%
            echo   actual:   %ACTUAL_SHA%
            exit /b 1
        )
        powershell -NoProfile -ExecutionPolicy Bypass -File "%TMP_INSTALLER%"
        del "%TMP_INSTALLER%"
    )
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

REM 2. Sync deps.
uv sync --frozen
if errorlevel 1 exit /b 1

REM 3. First-run browser fetch.
if not exist ".bootstrap-done" (
    echo First-run setup: fetching Camoufox + Patchright Chromium (~300 MB)...
    uv run python -m camoufox fetch
    if errorlevel 1 exit /b 1
    uv run python -m patchright install chromium
    if errorlevel 1 exit /b 1
    type nul > .bootstrap-done
)

REM 4. Hand off.
uv run gamba-pick %*
```

- [ ] **Step 2: Commit**

```bash
git add run.cmd
git commit -m "feat(dist): add run.cmd Windows bootstrap"
```

---

## Task 19: Add `picks.env.template` and `README.txt`

**Files:**
- Create: `picks.env.template`
- Create: `README.txt`

- [ ] **Step 1: Create `picks.env.template`**

```bash
# gamba-pick credentials and license file.
#
# Copy this file to ``picks.env`` (drop the ``.template`` suffix) and
# fill in the lines for the sites you have accounts on. Lines you don't
# need can stay commented out — gamba-pick only reads what it needs.
#
# WARNING: this file contains your passwords and license key in
# plaintext. Don't commit it, don't share it, don't email it.

# ---- License key (provided when you purchased your catalog) ----
# GAMBA_PICK_LICENSE=XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX

# ---- Reference configs (free, public sites) ----
# SPINQUEST_USERNAME=
# SPINQUEST_PASSWORD=
# STAKEUS_USERNAME=
# STAKEUS_PASSWORD=
# SHUFFLEUS_USERNAME=
# SHUFFLEUS_PASSWORD=

# ---- Commercial catalog sites (uncomment if you bought a catalog) ----
# Each site uses <SITE_ID>_USERNAME / <SITE_ID>_PASSWORD where SITE_ID
# is uppercased and matches the id in sites_seed.toml.
#
# SPORTZINO_USERNAME=
# SPORTZINO_PASSWORD=
# ZULA_CASINO_USERNAME=
# ZULA_CASINO_PASSWORD=
# ... etc
#
# After you fill in credentials, run:  ./run.sh --list
# to confirm the runner sees them. For OAuth-login sites, use:
#   ./run.sh --setup <site_id>
# to bootstrap the browser profile interactively.
```

- [ ] **Step 2: Create `README.txt` (customer-facing one-pager)**

```text
gamba-pick — daily claim runner
===============================

Quick start
-----------

  1. Drop your purchased .gpcat into ./catalog/
  2. Copy picks.env.template to picks.env
  3. Run: ./run.sh        (POSIX/macOS)
          run.cmd         (Windows: double-click or run from cmd)

  First-run setup downloads ~600 MB (Python + browsers). Subsequent
  runs are subsecond to start.

  When prompted, paste your license key. It saves to picks.env so
  you don't enter it again.

  Then follow the on-screen "Setup is not complete" instructions to
  fill in any missing credentials and bootstrap any OAuth sites.

How to read the output
----------------------

  After every run, claims.csv contains one row per site per run:

      run_ts, date, site, balance, currency, delta,
      secondary_balances, duration_s, success, claim_outcome

  Quick rollup view:

      ./run.sh --summary 7

  ...prints the last 7 days, one row per (date, site).

  If a site repeatedly errors, look at claim_history.jsonl — it has
  the last 20 lines of stderr per run for post-mortem.

Scheduling daily runs
---------------------

  gamba-pick does not include a scheduler. Use your OS:

  Windows (Task Scheduler):
      Open Task Scheduler. Create Basic Task. Name it "gamba-pick daily".
      Trigger: Daily at e.g. 9:00 AM. Action: Start a program. Program:
      full path to run.cmd. Check "Run whether user is logged on or not".

  macOS (launchd):
      Create ~/Library/LaunchAgents/com.gambapick.daily.plist with
      ProgramArguments pointing at /full/path/to/run.sh and a
      StartCalendarInterval block (Hour=9, Minute=0). Load with:
          launchctl load ~/Library/LaunchAgents/com.gambapick.daily.plist

  Linux (cron):
      crontab -e   →   0 9 * * * /full/path/to/run.sh >> /full/path/to/cron.log 2>&1

Updates
-------

  When a new gamba-pick zip ships, unzip it next to the existing
  install (or overwrite). DO NOT delete: picks.env, profiles/,
  claims.csv, claim_history.jsonl, catalog/. Those are your data.

Manual fallback (if uv installer is rejected)
---------------------------------------------

  If run.sh / run.cmd refuses to run because of an installer-hash
  mismatch (Astral changed the installer), install uv yourself:

      pip install uv
      uv sync
      uv run python -m camoufox fetch
      uv run python -m patchright install chromium
      uv run gamba-pick

  ...then resume normal use of run.sh / run.cmd next time we ship.

Windows SmartScreen
-------------------

  First time you run run.cmd, Windows may show "Windows protected
  your PC". Click "More info" → "Run anyway". This is unsigned-script
  friction; it doesn't mean anything is wrong with the file.
```

- [ ] **Step 3: Commit**

```bash
git add picks.env.template README.txt
git commit -m "docs(dist): add picks.env.template + customer README.txt"
```

---

## Task 20: Remove `runner.py`'s `main()` (clean up dead duplicate parser)

**Files:**
- Modify: `runner.py` — strip `main()` and the argparse block; keep the helpers

- [ ] **Step 1: Edit runner.py to remove main()**

In `runner.py`, find the `def main() -> int:` line and delete from there to the end of the file (including the `if __name__ == "__main__":` block). The rest of the module (`Site`, `RunResult`, `_resolve_module_path`, `parse_outcome`, `run_site`, `append_history`, `_append_csv_row`, the regex constants, etc.) stays — `gamba_pick.cli.main` calls them.

After the edit, the bottom of `runner.py` should end at the `_append_csv_row` helper.

- [ ] **Step 2: Verify nothing references the old runner.main**

```bash
grep -RnE "runner\.main|from runner import main" gamba_pick/ tests/ tools/
```

Expected: no matches.

- [ ] **Step 3: Run all tests**

```bash
uv run pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 4: Verify `python runner.py` no longer works (intended)**

```bash
uv run python runner.py 2>&1 | head -5 || true
```

Expected: nothing happens (no `main()` to call), or an `AttributeError` if anything tries to. That's fine — the dev path is now `uv run gamba-pick`.

- [ ] **Step 5: Commit**

```bash
git add runner.py
git commit -m "refactor: remove runner.main; gamba_pick.cli.main is the entrypoint"
```

---

## Task 21: Final smoke-test pass + version bump to 0.7.0

**Files:**
- Modify: `pyproject.toml` — bump version
- Modify: `gamba_pick/__init__.py` — bump version

- [ ] **Step 1: Run the full test suite + linter equivalent**

```bash
uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 2: End-to-end smoke without a real catalog**

```bash
mkdir -p /tmp/gp-smoke/catalog /tmp/gp-smoke/profiles
cd /tmp/gp-smoke

# A picks.env that has STAKEUS creds (these can be empty for the smoke;
# we only check that preflight finishes correctly).
cat > picks.env <<'EOF'
SPINQUEST_USERNAME=
SPINQUEST_PASSWORD=
STAKEUS_USERNAME=
STAKEUS_PASSWORD=
SHUFFLEUS_USERNAME=
SHUFFLEUS_PASSWORD=
EOF

# Point cli at this scratch dir.
SEED=/home/lothrop/src/gamba_pick/sites_seed.toml

# 1. With no creds at all → preflight prints remediation list.
unset SPINQUEST_USERNAME SPINQUEST_PASSWORD STAKEUS_USERNAME STAKEUS_PASSWORD SHUFFLEUS_USERNAME SHUFFLEUS_PASSWORD
cd /home/lothrop/src/gamba_pick
uv run gamba-pick \
  --seed-file "$SEED" \
  --catalog-dir /tmp/gp-smoke/catalog \
  --profiles-dir /tmp/gp-smoke/profiles \
  --picks-env /tmp/gp-smoke/picks.env \
  --claims-csv /tmp/gp-smoke/claims.csv \
  --log-file /tmp/gp-smoke/claim_history.jsonl
```

Expected: prints "Setup is not complete..." with three sites' remediation actions, and exits 0.

```bash
# 2. --summary still works.
uv run gamba-pick --summary 7 --claims-csv /tmp/gp-smoke/claims.csv
# Expected: "No claim history yet..."

# 3. --list still works.
uv run gamba-pick --list \
  --seed-file "$SEED" \
  --catalog-dir /tmp/gp-smoke/catalog
# Expected: "Plan: 3 site(s):" with spinquest, stake_us, shuffle_us.
```

- [ ] **Step 3: Bump version**

In `pyproject.toml`:

```toml
version = "0.6.0"
```

→

```toml
version = "0.7.0"
```

In `gamba_pick/__init__.py`:

```python
__version__ = "0.6.0"
```

→

```python
__version__ = "0.7.0"
```

- [ ] **Step 4: Verify the bump propagated**

```bash
uv run gamba-pick --version
```

Expected: `gamba-pick 0.7.0`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml gamba_pick/__init__.py
git commit -m "chore: bump version to 0.7.0"
```

> **Phase 8 milestone:** distribution wrapper + final cleanup complete. The customer zip is ready to be assembled (the `run.sh`/`run.cmd`/`picks.env.template`/`README.txt` are in place; `gamba_pick/`, `reference_configs/`, `sites_seed.toml`, `pyproject.toml`, `uv.lock` are all in repo). **Ships as 0.7.0.**

---

# Self-review checklist (the engineer should re-run this before opening a PR)

- [ ] All tests pass: `uv run pytest tests/ -v`
- [ ] `uv run gamba-pick --version` prints `gamba-pick 0.7.0`
- [ ] `uv run gamba-pick --summary 7` runs against an absent CSV without crashing
- [ ] `uv run gamba-pick --list` shows the working sites
- [ ] `./run.sh --version` (after one full bootstrap on a fresh machine) prints `gamba-pick 0.7.0`
- [ ] `tools/build_gpcat.py` end-to-end produces a `.gpcat` that the runner can decrypt
- [ ] No reference to a deleted/moved file remains: `grep -RnE "from (casino|scrapling_ext|scrapling_pick|selectors_generic) import"` returns no results outside `reference_configs/` and `gamba_pick/`
- [ ] `runner.main` is gone: `grep -RnE "runner\.main|from runner import main"` returns no results

---

# Out of scope (separate plans)

- GUI / icon-driven launcher
- Auto-update channel
- License revocation, expiry, online check
- Code signing for Windows / macOS
- Multi-tenant install (one customer = one install only)
- Removing the dev-only top-level files (`casino_template.py`, `casino_configs_examples.py`, `casino_selector_discovery.py`, `casino_template_parameterized.py`, `chumba_casino.py`, `luckybird.py`)
- `claims.csv` / `claim_history.jsonl` rotation
- Per-site auth-mode override in `picks.env` (e.g. `SPORTZINO_AUTH=form`)
- Headless validation, SOCKS5, GeoComply spoofing

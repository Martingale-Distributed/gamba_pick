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


def _load_runner():
    """Load runner.py from the repo root and register it as sys.modules['runner'].

    The console-script entry point doesn't put the repo root on sys.path, so
    `import runner` fails by default. This helper makes runner importable
    by absolute path. Idempotent — safe to call multiple times.
    """
    import importlib.util
    import sys
    if "runner" in sys.modules:
        return sys.modules["runner"]
    repo_root = Path(__file__).resolve().parent.parent
    runner_path = repo_root / "runner.py"
    spec = importlib.util.spec_from_file_location("runner", runner_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load runner module from {runner_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["runner"] = module
    spec.loader.exec_module(module)
    return module


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
    runner_mod = _load_runner()
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
    runner_mod = _load_runner()

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

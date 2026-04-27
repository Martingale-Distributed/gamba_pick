"""Daily-claim runner for working casino sites.

Reads ``sites_seed.toml``, filters to ``status="working"`` sites, and
runs each one's ``<module>.py`` script as a subprocess with a per-site
timeout. Captures stdout/stderr, parses out balance + claim outcome,
and appends one JSONL record per run to ``claim_history.jsonl``.

Subprocess isolation matters: a hang in one site's browser teardown
(see e.g. the Camoufox redirect-chain hang fixed in this codebase)
won't take the rest of the run with it — the runner times the child
out and moves on.

Intended cron usage:

    # Daily claim sweep at 9am local
    0 9 * * * cd /home/lothrop/src/gamba_pick && uv run python runner.py --headless

CLI:

    python runner.py                          # run all working sites, non-headless
    python runner.py --headless               # same, headless
    python runner.py --only sportzino zula_casino
    python runner.py --skip stake_us
    python runner.py --skip-claim             # login + balance read, no claim
    python runner.py --dry-run                # print the plan, don't execute
    python runner.py --timeout-per-site 600   # default 300s
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).parent
SEED_FILE = ROOT / "sites_seed.toml"
LOG_FILE = ROOT / "claim_history.jsonl"

DEFAULT_TIMEOUT_S = 300  # 5 min per site

# Patterns for extracting outcome from a casino script's stdout.
#
# Each claim factory in ``casino.py`` emits its own log vocabulary. The
# regexes below cover the actual strings they emit — kept in one place
# so adding a new claim factory means adding the line to one regex
# rather than editing the runner.
#
# Vocabulary by factory:
#   MTB         (make_mtb_claim_button)              "Daily bonus claimed.",
#                                                    "Daily bonus already claimed.",
#                                                    "Exception occurred while claiming daily bonus: ..."
#   SimpleClaim (make_simple_claim_button)           "Clicked claim button",
#                                                    "Claim button not visible; ...",
#                                                    "Claim button disabled; ...",
#                                                    "Claim button click failed: ..."
#   Generic     (make_generic_accept_or_close_modals) "Successfully processed all modals!",
#                                                    "Initial alerts popups timed out, ...
#                                                     this likely means the daily bonus has
#                                                     already been claimed.",
#                                                    "Error clicking button for daily: ..."
_RX_ACCOUNT = re.compile(r"Account State: SC: ([\d.]+), GC: ([\d.]+), VIP: (\S+)")
_RX_CLAIMED = re.compile(
    r"daily bonus claimed\."                # MTB canonical line ("Daily bonus claimed.")
    r"|Clicked claim button"                # SimpleClaim
    r"|Successfully processed all modals",  # Generic
    re.I,
)
_RX_ALREADY = re.compile(
    r"already (?:been )?claimed"             # MTB ("already claimed") + Generic ("already been claimed")
    r"|no daily bonus available"
    r"|claim button (?:not visible|disabled)",  # SimpleClaim
    re.I,
)
_RX_ERROR = re.compile(
    r"exception occurred while claiming"     # MTB
    r"|claim button click failed"            # SimpleClaim
    r"|error clicking button for daily",     # Generic
    re.I,
)
# Emitted when --skip-claim is passed (e.g. login + balance read only).
# Distinguishes a deliberate skip from "we don't know what happened".
_RX_SKIPPED = re.compile(r"Skipping daily bonus claim", re.I)
_RX_DONE = re.compile(r"Casino action completed successfully")


@dataclass
class Site:
    id: str
    name: str
    url: str
    status: str
    # Auth method for the site's CLI script. ``oauth`` adds
    # ``--google-oauth``; ``form`` reads <SITEID>_USERNAME /
    # <SITEID>_PASSWORD from .env via ``casino.get_credentials``.
    # Default keeps backward compat with seed entries that predate
    # this field.
    auth: str = "oauth"
    module: Optional[str] = None
    affiliate_link: Optional[str] = None


@dataclass
class RunResult:
    ts: str
    site_id: str
    module: str
    ok: bool
    exit_code: int
    duration_s: float
    timed_out: bool
    sc_balance: Optional[float]
    gc_balance: Optional[float]
    claim_outcome: str  # claimed | already_claimed | error | unknown
    stdout_tail: str
    stderr_tail: str


def load_sites(path: Path = SEED_FILE) -> List[Site]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return [Site(**s) for s in data["site"]]


def parse_outcome(stdout: str) -> tuple[Optional[float], Optional[float], str]:
    """Pull SC/GC balance + claim outcome out of a captured stdout blob.

    Outcome categories, checked in priority order:
      - ``error``           — claim attempted but the click or framework raised.
      - ``claimed``         — the daily bonus was successfully claimed today.
      - ``already_claimed`` — claim attempted, the button was disabled / the
        flow logged "already claimed today" / "no daily bonus available".
      - ``skipped``         — ``--skip-claim`` was passed; the script
        deliberately did not attempt a claim (login + balance read only).
      - ``unknown``         — none of the above patterns matched (probably
        the script crashed before reaching the claim step, or used a log
        line we don't recognize yet).
    """
    m = _RX_ACCOUNT.search(stdout)
    sc = float(m.group(1)) if m else None
    gc = float(m.group(2)) if m else None
    if _RX_ERROR.search(stdout):
        outcome = "error"
    elif _RX_ALREADY.search(stdout):
        outcome = "already_claimed"
    elif _RX_CLAIMED.search(stdout):
        outcome = "claimed"
    elif _RX_SKIPPED.search(stdout):
        outcome = "skipped"
    else:
        outcome = "unknown"
    return sc, gc, outcome


def run_site(site: Site, opts: argparse.Namespace) -> RunResult:
    """Invoke ``<module>.py`` as a subprocess and capture the result.

    Uses ``sys.executable`` rather than ``python``/``uv run`` so the
    child runs with the same interpreter as the runner — which means
    cron entries that already activate the venv (or use ``uv run``)
    inherit cleanly.

    On timeout, ``subprocess.run`` SIGKILLs the child. Browser
    subprocesses (Camoufox/Patchright) may linger as orphans for a
    few seconds before their own watchdogs clean up — acceptable
    for a daily cron, not so much for high-frequency runs.
    """
    assert site.module, f"site {site.id} has no module"
    cmd = [sys.executable, f"{site.module}.py"]
    if site.auth == "oauth":
        cmd.append("--google-oauth")
    # ``form`` auth: nothing extra on the CLI; the site script reads
    # credentials from .env via casino.get_credentials.
    if opts.headless:
        cmd.append("--headless")
    if opts.skip_claim:
        cmd.append("--skip-claim")

    start = time.monotonic()
    timed_out = False
    stdout = ""
    stderr = ""
    exit_code = -1
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=opts.timeout_per_site,
        )
        exit_code = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.TimeoutExpired as e:
        timed_out = True
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        stderr += f"\n[runner] timed out after {opts.timeout_per_site}s"

    duration = time.monotonic() - start
    # casino.py's logger uses ``logging.StreamHandler()`` which defaults
    # to stderr, so all the INFO lines we want to grep are over there.
    # Parse the union — keep the streams separate in the record so a
    # debugger can still tell what came from where.
    combined = f"{stdout}\n{stderr}"
    sc, gc, outcome = parse_outcome(combined)
    # Relaxed completion criterion: we treat the run as ok if it
    # reached the canonical ``Casino action completed successfully``
    # marker, regardless of post-action exit code.
    #
    # Rationale: scrapling's ``_process_response_history`` iterates
    # redirect responses on session teardown and calls methods like
    # ``Response.all_headers()`` / ``Response.body()``; on Camoufox /
    # Firefox these raise ``TargetClosedError`` when the underlying
    # page is already closing. The crash exits the child with code 1
    # AFTER all meaningful work (login, balance read, claim) has
    # completed and the DONE marker has been logged. Failing the
    # daily run on a teardown artifact is more noise than signal.
    #
    # The non-zero ``exit_code`` is still captured in the JSONL
    # record so a monitoring layer can flag the teardown frequency
    # without paging on it. Future plan: vendor-patch scrapling to
    # swallow the cleanup ``TargetClosedError`` at the source.
    ok = (
        not timed_out
        and bool(_RX_DONE.search(combined))
    )

    return RunResult(
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        site_id=site.id,
        module=site.module,
        ok=ok,
        exit_code=exit_code,
        duration_s=round(duration, 2),
        timed_out=timed_out,
        sc_balance=sc,
        gc_balance=gc,
        claim_outcome=outcome,
        stdout_tail="\n".join(stdout.splitlines()[-20:]),
        stderr_tail="\n".join(stderr.splitlines()[-20:]),
    )


def append_history(result: RunResult, log_path: Path = LOG_FILE) -> None:
    with open(log_path, "a") as f:
        f.write(json.dumps(asdict(result)) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run daily claims across working casino sites."
    )
    parser.add_argument(
        "--only", nargs="+", metavar="ID", help="Only run these site ids."
    )
    parser.add_argument(
        "--skip", nargs="+", metavar="ID", help="Skip these site ids."
    )
    parser.add_argument(
        "--headless", action="store_true", help="Pass --headless to each site."
    )
    parser.add_argument(
        "--skip-claim",
        action="store_true",
        help="Pass --skip-claim to each site (login + balance read, no claim).",
    )
    parser.add_argument(
        "--timeout-per-site",
        type=int,
        default=DEFAULT_TIMEOUT_S,
        help=f"Per-site subprocess timeout in seconds (default {DEFAULT_TIMEOUT_S}).",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the plan without running."
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=LOG_FILE,
        help="JSONL append-only history file.",
    )
    parser.add_argument(
        "--seed-file",
        type=Path,
        default=SEED_FILE,
        help="Sites seed TOML.",
    )
    opts = parser.parse_args()

    all_sites = load_sites(opts.seed_file)
    sites = [s for s in all_sites if s.status == "working" and s.module]
    if opts.only:
        keep = set(opts.only)
        sites = [s for s in sites if s.id in keep]
        missing = keep - {s.id for s in sites}
        if missing:
            print(f"warn: --only ids not in working set: {sorted(missing)}")
    if opts.skip:
        skip = set(opts.skip)
        sites = [s for s in sites if s.id not in skip]

    if not sites:
        print("No sites match the filters; nothing to do.")
        return 1

    print(
        f"Plan: {len(sites)} site(s) sequentially, "
        f"timeout {opts.timeout_per_site}s each, "
        f"{'headless' if opts.headless else 'visible'}, "
        f"{'no-claim' if opts.skip_claim else 'with-claim'}:"
    )
    for s in sites:
        print(f"  - {s.id:20}  {s.module}.py  ({s.auth})")
    print()

    if opts.dry_run:
        return 0

    results: List[RunResult] = []
    for site in sites:
        print(f"=== {site.id} ===", flush=True)
        result = run_site(site, opts)
        append_history(result, opts.log_file)
        flag = "ok" if result.ok else ("timeout" if result.timed_out else "fail")
        bal = ""
        if result.sc_balance is not None or result.gc_balance is not None:
            bal = f"  SC={result.sc_balance} GC={result.gc_balance}"
        print(
            f"  -> {flag}  {result.duration_s}s  claim={result.claim_outcome}{bal}",
            flush=True,
        )
        results.append(result)

    print()
    ok_count = sum(r.ok for r in results)
    print(f"Summary: {ok_count}/{len(results)} ok  (history -> {opts.log_file})")
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

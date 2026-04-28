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

Per-site timeout overrides live in ``sites_seed.toml`` as the optional
``timeout_s`` field; site-level values take precedence over
``--timeout-per-site``.
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
from typing import Dict, List, Optional

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
# Account State line shapes (emitted by ``CasinoAccountState.__str__``):
#
#     Account State: SC: 3.34, GC: 43562260.00, VIP: None
#     Account State: FC: 3.37, GC: 832889071.00, VIP: None     # FortuneWins
#     Account State: VIP: None                                 # empty balances
#
# The balance segment is optional — when a script crashes before
# any currency parses, the line still gets emitted with just the
# VIP suffix and we want to keep parsing it (empty balances dict +
# whatever outcome class fell out). We extract every
# ``<CODE>: <number>`` pair from the optional segment into a
# balances dict, generic over currency types so adding a new code
# (FC, anything in the future) requires no runner change.
_RX_ACCOUNT_LINE = re.compile(r"Account State: (?:(.*?), )?VIP: (\S+)")
_RX_ACCOUNT_PAIR = re.compile(r"\b([A-Z]{2,4}): ([\d.]+)\b")
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
    # Per-site subprocess timeout override in seconds. ``None``
    # means use the runner's ``--timeout-per-site`` global default.
    # Useful for sites whose teardown predictably hangs (e.g. zula
    # via the scrapling/Camoufox redirect-response interaction) —
    # set this just above the typical work duration so SIGKILL
    # fires shortly after the DONE marker rather than waiting out
    # the global default.
    timeout_s: Optional[int] = None


@dataclass
class RunResult:
    ts: str
    site_id: str
    module: str
    ok: bool
    exit_code: int
    duration_s: float
    timed_out: bool
    # Currency-code → value (e.g. ``{"SC": 3.34, "GC": 43562260.0}``).
    # Generic over currency types — adds FC for FortuneWins, etc.
    # Empty dict if the script crashed before emitting an Account State
    # line.
    balances: Dict[str, float]
    claim_outcome: str  # claimed | already_claimed | skipped | error | unknown
    stdout_tail: str
    stderr_tail: str


def load_sites(path: Path = SEED_FILE) -> List[Site]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return [Site(**s) for s in data["site"]]


def parse_outcome(stdout: str) -> tuple[Dict[str, float], str]:
    """Pull all currency balances + claim outcome out of a stdout blob.

    Returns ``({code: value, ...}, outcome)``. Currency codes come
    straight from the site's ``Currency.code`` config (e.g. ``SC``,
    ``GC``, ``FC``) — generic over types, so adding a new currency
    code in a site config requires no runner change.

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
    balances: Dict[str, float] = {}
    line_match = _RX_ACCOUNT_LINE.search(stdout)
    # group(1) is the optional balance segment — None when the line
    # had no balances (e.g. ``Account State: VIP: None``).
    if line_match and line_match.group(1):
        for code, raw_value in _RX_ACCOUNT_PAIR.findall(line_match.group(1)):
            try:
                balances[code] = float(raw_value)
            except ValueError:
                continue
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
    return balances, outcome


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

    timeout_s = site.timeout_s if site.timeout_s is not None else opts.timeout_per_site

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
            timeout=timeout_s,
        )
        exit_code = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.TimeoutExpired as e:
        timed_out = True
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        stderr += f"\n[runner] timed out after {timeout_s}s"

    duration = time.monotonic() - start
    # casino.py's logger uses ``logging.StreamHandler()`` which defaults
    # to stderr, so all the INFO lines we want to grep are over there.
    # Parse the union — keep the streams separate in the record so a
    # debugger can still tell what came from where.
    combined = f"{stdout}\n{stderr}"
    balances, outcome = parse_outcome(combined)
    # Relaxed completion criterion: we treat the run as ok purely
    # on whether it reached the canonical ``Casino action completed
    # successfully`` marker, regardless of post-action exit code or
    # subprocess timeout.
    #
    # Rationale: scrapling's ``_process_response_history`` iterates
    # redirect responses on session teardown and calls methods like
    # ``Response.all_headers()`` / ``Response.body()``; on Camoufox /
    # Firefox these either raise ``TargetClosedError`` (exit_code=1)
    # or block on per-redirect retries that exhaust the subprocess
    # timeout (timed_out=True). Both modes happen AFTER all meaningful
    # work (login, balance read, claim, DONE marker) has completed —
    # failing the daily run on a teardown artifact is more noise
    # than signal.
    #
    # ``exit_code`` and ``timed_out`` are still captured in the JSONL
    # record so monitoring can flag frequency without paging on it.
    # Future plan: vendor-patch scrapling to short-circuit the
    # cleanup loop / swallow ``TargetClosedError`` at the source.
    ok = bool(_RX_DONE.search(combined))

    return RunResult(
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        site_id=site.id,
        module=site.module,
        ok=ok,
        exit_code=exit_code,
        duration_s=round(duration, 2),
        timed_out=timed_out,
        balances=balances,
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
        help=(
            f"Default per-site subprocess timeout in seconds (default "
            f"{DEFAULT_TIMEOUT_S}). Individual sites may override via "
            f"the ``timeout_s`` field in the seed TOML."
        ),
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
        f"default timeout {opts.timeout_per_site}s, "
        f"{'headless' if opts.headless else 'visible'}, "
        f"{'no-claim' if opts.skip_claim else 'with-claim'}:"
    )
    for s in sites:
        timeout_note = f"  [timeout {s.timeout_s}s]" if s.timeout_s is not None else ""
        print(f"  - {s.id:20}  {s.module}.py  ({s.auth}){timeout_note}")
    print()

    if opts.dry_run:
        return 0

    results: List[RunResult] = []
    for site in sites:
        print(f"=== {site.id} ===", flush=True)
        result = run_site(site, opts)
        append_history(result, opts.log_file)
        flag = "ok" if result.ok else ("timeout" if result.timed_out else "fail")
        # Render balances in alphabetical order for deterministic
        # output (matches CasinoAccountState.__str__).
        bal = ""
        if result.balances:
            bal = "  " + " ".join(
                f"{code}={value}" for code, value in sorted(result.balances.items())
            )
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

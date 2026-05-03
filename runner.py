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
import os
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
CLAIMS_CSV = ROOT / "claims.csv"

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
    # Currency code that goes into the ``balance`` / ``currency`` columns
    # of claims.csv. Other parsed currencies land in
    # ``secondary_balances``. Defaults to "SC" — the redeemable currency
    # in the sweepstakes-casino model that covers most of our sites.
    # Override per-site for non-SC sites (e.g. "FC" for FortuneWins,
    # "USDT" for Stake.us).
    primary_currency: str = "SC"


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
    # ``claimed | already_claimed | skipped | error | unknown`` for the
    # default capturing path (parsed from the child's stdout/stderr); set
    # to ``streamed`` when the runner is invoked with ``--stream`` and
    # the child's output went straight to the terminal — in that mode
    # we have no parsed outcome and skip the JSONL history append.
    claim_outcome: str
    stdout_tail: str
    stderr_tail: str


def load_sites(path: Path = SEED_FILE) -> List[Site]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return [Site(**s) for s in data["site"]]


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


def _expand_id_args(raw: List[str]) -> set[str]:
    """Flatten ``--only`` / ``--skip`` arguments into a set of site ids.

    argparse hands these in as a list of whitespace-split tokens
    (``nargs="+"``). Each token may itself be a comma-delimited list, so
    ``--skip spinquest,zula_casino fortune_wins`` collapses to
    ``{"spinquest", "zula_casino", "fortune_wins"}``. Empty tokens (from
    stray commas) are dropped.
    """
    out: set[str] = set()
    for arg in raw:
        for sid in arg.split(","):
            sid = sid.strip()
            if sid:
                out.add(sid)
    return out


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
    # ``--config-dir`` lets the runner load site modules from outside
    # the in-tree set (e.g. the Casino Buddy commercial bundle, where
    # the closed catalog lives in a private repo / decrypted bundle
    # rather than alongside ``casino.py``). When the script is invoked
    # by absolute path, Python's ``sys.path[0]`` is the *script's*
    # directory — not ``cwd`` — so a bare ``from casino import ...`` in
    # an external module wouldn't resolve. We keep ``cwd=ROOT`` (so
    # relative paths like ``profiles/<site>`` still work) and inject
    # ROOT into the child's ``PYTHONPATH`` so framework imports land.
    #
    # Resolution order when ``--config-dir`` is set: external dir first,
    # then ROOT as fallback. This lets a single seed (e.g. the Casino
    # Buddy comprehensive seed) reference both bundle modules and the
    # framework's public reference configs (spinquest, stake_us)
    # without forcing the bundle to ship duplicates.
    module_path = _resolve_module_path(site.module, opts.config_dir)
    # Use a real exception rather than ``assert`` so the guard still
    # fires under ``python -O`` (which strips asserts). This branch is
    # purely defensive — preflight already checked module presence —
    # but a stripped assert would surface as a less clear failure
    # downstream (subprocess exec on a None path).
    if module_path is None:
        raise FileNotFoundError(
            f"site {site.id} module {site.module}.py vanished between "
            f"preflight and run"
        )
    cmd = [sys.executable, str(module_path)]
    # ``--google-oauth`` resolves from either the seed's per-site
    # ``auth="oauth"`` default or the runner-level ``--google-oauth``
    # override. The override exists because the seed's ``auth`` field
    # is the current user's default (per-user, not site-inherent —
    # both surfaces are typically supported on each site), so a user
    # whose accounts use OAuth on a form-default site should be able
    # to flip it without editing the seed.
    if site.auth == "oauth" or opts.google_oauth:
        cmd.append("--google-oauth")
    # ``form`` auth (and no override): nothing extra on the CLI; the
    # site script reads credentials from .env via casino.get_credentials.
    if opts.headless:
        cmd.append("--headless")
    if opts.skip_claim:
        cmd.append("--skip-claim")
    if opts.setup:
        cmd.append("--setup")

    timeout_s = site.timeout_s if site.timeout_s is not None else opts.timeout_per_site
    # Setup mode runs the login interactively (it blocks until the
    # user finishes Google OAuth / 2FA / etc. in the browser), so the
    # 5-minute default is too tight. Auto-bump to 20 minutes when
    # ``--setup`` is set unless the user explicitly overrode
    # ``--timeout-per-site``. Per-site ``timeout_s`` overrides in the
    # seed (for sites with predictable teardown hangs) take precedence
    # over both — they're a property of the site, not the run mode.
    if opts.setup and site.timeout_s is None and opts.timeout_per_site == DEFAULT_TIMEOUT_S:
        timeout_s = 1200

    # Prepend ROOT to PYTHONPATH so external site modules (loaded via
    # ``--config-dir``) can import the framework. Inheriting the rest
    # of the env keeps anything else the child relies on (HOME, USER,
    # PATH, the venv's PATH side effects).
    child_env = dict(os.environ)
    existing_pp = child_env.get("PYTHONPATH", "")
    child_env["PYTHONPATH"] = (
        f"{ROOT}{os.pathsep}{existing_pp}" if existing_pp else str(ROOT)
    )
    # Stuck-detection / Inspector pause flag. ``casino.safe_click`` reads
    # ``GAMBA_PICK_PAUSE_ON_STUCK`` at import time; propagate from the
    # runner CLI flag into per-site subprocess env so the user can opt
    # in for one run without editing source. Refused under --headless
    # (Inspector window needs a display).
    if getattr(opts, "pause_on_stuck", False) and not opts.headless:
        child_env["GAMBA_PICK_PAUSE_ON_STUCK"] = "1"

    start = time.monotonic()
    timed_out = False
    stdout = ""
    stderr = ""
    exit_code = -1
    if opts.stream:
        # Dev mode: inherit stdout/stderr so the user sees logs live.
        # We lose the ability to parse balances + claim outcome, and
        # ``main`` skips the JSONL append so dev runs don't pollute
        # production history. Useful when iterating on a single site
        # via ``--only <id>``.
        try:
            result = subprocess.run(
                cmd, cwd=ROOT, env=child_env, timeout=timeout_s
            )
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
    else:
        try:
            result = subprocess.run(
                cmd,
                cwd=ROOT,
                env=child_env,
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
    if opts.stream:
        # No captured output to parse; trust the exit code and skip
        # ok-marker / outcome / balance parsing. ``claim_outcome``
        # is set to "streamed" so post-hoc readers can tell this run
        # bypassed the parser (though we also skip the JSONL append).
        return RunResult(
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            site_id=site.id,
            module=site.module,
            ok=(exit_code == 0 and not timed_out),
            exit_code=exit_code,
            duration_s=round(duration, 2),
            timed_out=timed_out,
            balances={},
            claim_outcome="streamed",
            stdout_tail="",
            stderr_tail="",
        )
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run daily claims across working casino sites."
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="IDS",
        help=(
            "Only run these site ids. Accepts space-separated "
            "(``--only spinquest stake_us``) and/or comma-delimited "
            "(``--only spinquest,stake_us``) — the two can be mixed."
        ),
    )
    parser.add_argument(
        "--skip",
        nargs="+",
        metavar="IDS",
        help=(
            "Skip these site ids. Accepts space-separated "
            "(``--skip spinquest zula_casino``) and/or comma-delimited "
            "(``--skip spinquest,zula_casino``) — the two can be mixed."
        ),
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
        "--pause-on-stuck",
        action="store_true",
        help=(
            "When ``casino.safe_click`` detects a click intercept (an "
            "overlay sitting on top of the target), drop into Playwright's "
            "Inspector via ``page.pause()`` so the user can interactively "
            "inspect the DOM, dismiss the blocker, and resume. Sets "
            "``GAMBA_PICK_PAUSE_ON_STUCK=1`` in the per-site subprocess "
            "env. Ignored under ``--headless`` (Inspector needs a display)."
        ),
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help=(
            "Pass --setup to each site so the script runs interactive "
            "login once to bootstrap the persistent browser profile, "
            "then exits without claim. Typically paired with "
            "``--only <id>`` for one-site-at-a-time bootstrapping. "
            "Implies ``--stream`` (setup blocks on user input — the "
            "live stdout/stderr is required so the user can see "
            "prompts). When ``--timeout-per-site`` is at its default, "
            "the subprocess timeout is auto-bumped to 1200s so the "
            "user has time to complete OAuth / 2FA in the browser."
        ),
    )
    parser.add_argument(
        "--google-oauth",
        action="store_true",
        help=(
            "Force Google OAuth on every site this run, overriding "
            "the seed's per-site ``auth`` field. Useful when this "
            "user has Google-OAuth accounts on sites the seed "
            "defaults to form-login (the ``auth`` field is "
            "per-user-default, not site-inherent — both surfaces are "
            "typically available on each site)."
        ),
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
        "--stream",
        action="store_true",
        help=(
            "Dev mode: inherit child stdout/stderr (live output) "
            "instead of capturing them, and skip the JSONL history "
            "append. Pair with ``--only <id>`` to iterate on a "
            "single site config and see logs in real time. The "
            "outcome/balance parser is bypassed (it needs captured "
            "output) — use the canonical capturing path for "
            "production sweeps."
        ),
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help=(
            "Skip the claims.csv append for this run. JSONL history "
            "still writes — that's the debugging artifact, not the "
            "customer-facing balance log."
        ),
    )
    parser.add_argument(
        "--claims-csv",
        type=Path,
        default=CLAIMS_CSV,
        help="Append-only customer-facing balance log (CSV).",
    )
    parser.add_argument(
        "--dry-run",
        "--list",
        action="store_true",
        help=(
            "Print the plan without running. ``--list`` is an alias — handy "
            "when paired with ``--only``/``--skip`` to preview which sites "
            "the next sweep would hit."
        ),
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
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help=(
            "Directory holding additional site modules (e.g. the "
            "Casino Buddy commercial catalog at ``./configs/``). The "
            "runner searches this dir first, then falls back to the "
            "in-tree framework path — so a single seed can list both "
            "bundle modules and the public reference configs "
            "(spinquest, stake_us) without forcing the bundle to "
            "ship duplicates. Pair with ``--seed-file`` pointing at "
            "the bundle's TOML."
        ),
    )
    opts = parser.parse_args()

    # ``--setup`` implies ``--stream``: setup mode blocks on user
    # input (OAuth flow / "press Enter when done" prompts), so the
    # subprocess must inherit stdout/stderr — captured output would
    # buffer the prompts and the user would be typing blind.
    if opts.setup and not opts.stream:
        opts.stream = True

    all_sites = load_sites(opts.seed_file)
    sites = [s for s in all_sites if s.status == "working" and s.module]
    if opts.only:
        keep = _expand_id_args(opts.only)
        sites = [s for s in sites if s.id in keep]
        missing = keep - {s.id for s in sites}
        if missing:
            print(f"warn: --only ids not in working set: {sorted(missing)}")
    if opts.skip:
        skip = _expand_id_args(opts.skip)
        sites = [s for s in sites if s.id not in skip]

    # Preflight: warn early on any seed entry whose module file isn't
    # reachable in either the external ``--config-dir`` or in ROOT.
    # Catches the "I removed a .py without updating the seed" mistake
    # early instead of letting it surface as a subprocess no-op.
    valid: List[Site] = []
    for s in sites:
        module_path = _resolve_module_path(s.module, opts.config_dir)
        if module_path is None:
            search = (
                f"{opts.config_dir} or {ROOT}" if opts.config_dir else str(ROOT)
            )
            print(
                f"warn: site '{s.id}' module {s.module}.py not found in "
                f"{search} — skipping."
            )
            continue
        valid.append(s)
    sites = valid

    if not sites:
        print("No sites match the filters; nothing to do.")
        return 1

    location_note = f", configs from {opts.config_dir}" if opts.config_dir else ""
    print(
        f"Plan: {len(sites)} site(s) sequentially, "
        f"default timeout {opts.timeout_per_site}s, "
        f"{'headless' if opts.headless else 'visible'}, "
        f"{'no-claim' if opts.skip_claim else 'with-claim'}{location_note}:"
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
        # Skip history append in --stream mode: those runs lack
        # parsed outcome/balances and are dev-only. Production sweeps
        # always go through the capturing path.
        if not opts.stream:
            append_history(result, opts.log_file)
            if not opts.no_csv:
                _append_csv_row(site, result, opts.claims_csv)
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
    # --stream mode skips the JSONL append (see the loop above), so the
    # "history -> ..." trailer would lie. Drop it in that case.
    if opts.stream:
        print(f"Summary: {ok_count}/{len(results)} ok  (history not written: --stream)")
    else:
        print(f"Summary: {ok_count}/{len(results)} ok  (history -> {opts.log_file})")
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

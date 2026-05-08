# Customer-bundle distribution, CLI UX, and CSV output

**Status:** Design / pre-implementation
**Date:** 2026-05-02
**Scope:** Phase-1 commercial deliverable — turn the dev-grade `runner.py` workflow into a packaged customer experience. Adds CSV output, encrypted catalog format, license-key flow, and a no-Python-install distribution wrapper. The dev install path (`pip install -e .`) stays intact.

---

## 1. Goals & non-goals

**Goals:**

- Customer experience: download one zip → drop in their purchased `.gpcat` → run one command → daily claims execute and results land in `claims.csv` plus the terminal.
- No Python preinstall required on the customer machine.
- Single customer-facing flag-free invocation: `./run.sh` with no arguments runs the daily sweep with sensible defaults.
- Encrypted catalog format with per-customer license key.
- Append-only audit log (`claims.csv`) with a one-row-per-day-per-site rollup view (`--summary`).

**Non-goals (this spec):**

- No GUI — CLI only.
- No auto-update; customers fetch new zips manually.
- No license revocation, no expiry, no online check. Leaked license + `.gpcat` works forever; per-customer fingerprinting is the only deterrent.
- No code signing; Windows SmartScreen warning on first run is acceptable.
- No multi-tenant install — one install = one customer.
- No second post-claim balance read; CSV uses single-balance + delta semantics.
- No CSV / JSONL rotation. Manual rename if customer cares.
- No bundled scheduler; README documents OS-native scheduling (Task Scheduler / launchd / cron).
- No headless validation, SOCKS5, or GeoComply spoofing — separate roadmap items.

---

## 2. Audience & deliverable

**Audience profile:** non-technical end user as the headline UX. Tech-savvy hobbyists / power users (existing dev install) remain supported but are not the focus of the new artifacts.

**Deliverable:** `gamba-pick-vX.Y.Z.zip`. Layout when unzipped:

```
gamba-pick/
├── run.sh                    # POSIX bootstrap (mac/Linux)
├── run.cmd                   # Windows bootstrap
├── pyproject.toml            # locked deps for `uv sync`
├── uv.lock                   # reproducible resolve
├── README.txt                # 1-page how-to-run + how-to-schedule
├── picks.env.template        # commented template for credentials + license
├── gamba_pick/               # framework package: cli.py (entrypoint), casino.py, scrapling_ext.py, selectors_generic.py, ...
├── reference_configs/        # public reference site configs (stake_us.py, spinquest.py, shuffle_us.py)
├── sites_seed.toml           # registry of known sites
├── selectors_generic.py
└── catalog/                  # empty; customer drops their .gpcat here
    └── .gitkeep
```

Customer-created files (appear on first use, never overwritten by upgrades):

```
gamba-pick/
├── picks.env                 # credentials + GAMBA_PICK_LICENSE
├── profiles/<site>/          # browser-profile data per OAuth site
├── claims.csv                # customer-facing append-only balance log
├── claim_history.jsonl       # debugging artifact (existing format, unchanged)
└── .bootstrap-done           # sentinel for first-run camoufox/playwright fetch
```

---

## 3. Customer journey

1. Unzip `gamba-pick-vX.Y.Z.zip` to anywhere on disk.
2. Drop the purchased `.gpcat` into `gamba-pick/catalog/`.
3. Run `./run.sh` (or double-click `run.cmd` on Windows).
4. **First run only**: bootstrap installs `uv` if missing, downloads Python 3.12, syncs dependencies, fetches Camoufox. ~30-90 s, network required.
5. Runner detects encrypted catalog → prompts for license key → saves to `picks.env` as `GAMBA_PICK_LICENSE=...`.
6. Runner runs the **detect-and-instruct preflight** (§7) and prints a remediation list of missing credentials + sites needing OAuth bootstrap.
7. Customer edits `picks.env` (template provided) and runs `./run.sh --setup <site>` per OAuth site.
8. Once preflight passes, `./run.sh` runs the sweep: terminal output + appends to `claims.csv` + `claim_history.jsonl`.
9. Customer wires `./run.sh` into Task Scheduler / cron / launchd themselves following the README.

---

## 4. Distribution & bootstrap

**Choice rationale:** `uv`-bootstrap shell wrapper, no per-OS binaries, no PyInstaller/Nuitka. Reasons:

- `uv` installs Python itself — customer doesn't need Python pre-installed.
- `uv.lock` guarantees the same versions land on every machine.
- Wrapper is ~50 lines per platform; easy to audit and update.
- Frozen sync after first run is fast (subsecond) — daily runs don't pay the install cost.
- No code-signing or per-OS-binary maintenance.

**Bootstrap script behavior** (`run.sh` and `run.cmd` mirror each other):

1. `cd` to the directory containing the script (so it runs from anywhere).
2. Detect `uv`:
   - If `uv --version` works, continue.
   - Else download the official `uv` installer to a temp file, verify SHA256 against a hash baked into the script, run installer (installs to `~/.local/bin` or `%USERPROFILE%\.local\bin`).
3. `uv sync --frozen` (uses `uv.lock`; auto-installs Python 3.12 if missing).
4. First-run only (sentinel: absence of `.bootstrap-done`):
   - `uv run python -m camoufox fetch` (Firefox-based stealth backend; ~150 MB)
   - `uv run python -m patchright install chromium` (Chromium-based stealth backend used by some sites; ~150 MB — Patchright is already a hard dep in `pyproject.toml`)
   - `touch .bootstrap-done`
5. `exec uv run gamba-pick "$@"` — forward all flags to the entrypoint.

**Security:** the `uv` installer URL and SHA256 are pinned in the script. Refuse to proceed if the hash doesn't match — protects against a supply-chain swap on the install endpoint. Manual-fallback line in the README: `pip install uv` if the customer has Python anyway.

**Updates:** customer downloads a new zip, unzips next to / over the old install. Their `picks.env`, `profiles/`, `claims.csv`, `claim_history.jsonl`, and `catalog/` survive because they're outside the parts that change. README upgrade section says: "don't delete `picks.env`, `profiles/`, `claims.csv`, `claim_history.jsonl`, or `catalog/`."

---

## 5. Catalog format & encryption

### 5.1 `.gpcat` file format

A single binary file with a small header followed by an encrypted, compressed tarball:

```
[ 4 bytes  ] magic       = "GPCT"
[ 1 byte   ] format_ver  = 0x01
[16 bytes  ] bundle_id   = random per release; identifies which catalog version this is
[12 bytes  ] nonce       = AES-GCM nonce, random per build
[ N bytes  ] ciphertext  = AES-256-GCM(plaintext = tar.zst of the catalog tree)
[16 bytes  ] tag         = AES-GCM auth tag (trailing)
```

The plaintext (after decrypt + zstd-decompress + untar) is:

```
catalog/
├── manifest.toml          # version, list of included site modules, build date, bundle_id
├── sites_seed.toml        # the bundle's seed (extends the framework's reference seed)
├── configs/               # one .py per site (sportzino.py, pulsz.py, ...)
└── selectors/             # bundle-specific selector overrides (optional)
```

### 5.2 Key derivation

```
key = HKDF-SHA256(
    ikm    = utf8(license_string_dashes_stripped),
    salt   = bundle_id,                         # 16 bytes from the .gpcat header
    info   = b"gamba-pick.gpcat.v1",
    length = 32,
)
```

**License-string format:** 32 characters, Crockford base32, displayed as `XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX` (dashes are visual only — strip before KDF). 32 chars × 5 bits = 160 bits of entropy.

**Wrong license:** AES-GCM tag verification fails → runner reports `License key invalid for this catalog` and re-prompts. No oracle; failure is binary.

### 5.3 Sale-time / build-time pipeline (your side, not customer's)

1. Build a fresh catalog tree on disk (`configs/`, `sites_seed.toml`, `manifest.toml`).
2. Tar + zstd-compress → plaintext blob.
3. For each customer purchase:
   1. `license_string = base32(secrets.token_bytes(20))`
   2. `bundle_id = secrets.token_bytes(16)`
   3. Derive key via HKDF (above).
   4. AES-GCM encrypt the plaintext.
   5. Write `.gpcat` with header + ciphertext + tag.
   6. Email customer: license string + `.gpcat` download link.
   7. Log `{customer, license_string, bundle_id, sha256(.gpcat)}` to your sales record.

A small CLI tool ships in this same repo (not in the customer zip) for steps 3a-e: `tools/build_gpcat.py`.

### 5.4 Runtime decrypt path (in the runner)

1. On startup, scan `catalog/` for `*.gpcat` files.
2. If exactly one `.gpcat` is present:
   1. Read header (magic, version, `bundle_id`, nonce).
   2. Read `GAMBA_PICK_LICENSE` from `picks.env`. If missing → prompt + persist.
   3. Derive key, attempt AES-GCM decrypt. On tag failure → re-prompt (after 3 failures, exit with the support-email line).
   4. Decompress + untar to a per-process temp dir under `tempfile.gettempdir()`.
   5. Set `--config-dir` to that temp dir + `--seed-file` to its `sites_seed.toml`.
   6. On process exit (success or crash) → `shutil.rmtree(temp_dir)` via `atexit` + `try/finally`.
3. If zero `.gpcat` files: run only the public reference configs — degraded but functional, useful for evaluation.
4. If more than one `.gpcat`: error, "Multiple catalogs in `catalog/` — keep only the most recent one."

### 5.5 Threat model & limitations

- **Protects against:** casual sharing, search-engine indexing of leaked bundles, "I emailed it to a friend."
- **Does NOT protect against:** a determined attacker — the decrypt key sits in customer RAM, plaintext is on disk in the temp dir during runs.
- **Per-customer fingerprinting:** each `.gpcat` is built with a unique `bundle_id` embedded in `manifest.toml` (cleartext after decrypt). Leaked plaintext is traceable. License-string-only leaks (without the matching `.gpcat`) are useless because keys are HKDF'd with the per-customer `bundle_id`.
- **No revocation, no expiry.** Phase-1 acceptable; future work if compromise becomes a problem.

---

## 6. Runner CLI surface

**Entrypoint:** `gamba_pick/cli.py:main`, registered as the `gamba-pick` console script (already declared in `pyproject.toml`). The bootstrap forwards everything: `./run.sh foo bar` ≡ `uv run gamba-pick foo bar`.

**Default invocation: `./run.sh` with no flags.**

1. Loads `gamba-pick/sites_seed.toml` (framework reference seed).
2. Decrypts the `.gpcat` in `catalog/` (if present); merges its `sites_seed.toml` over the reference one. Bundle wins on collision.
3. Filters to `status="working"` sites.
4. Runs the detect-and-instruct preflight (§7). If anything is missing, prints remediation list and exits **without touching the browser**.
5. If preflight passes, runs the daily sweep (`--headless` by default), appends to `claims.csv` and `claim_history.jsonl`, prints terminal summary.

**Headless is the default.** Customers do not want a Camoufox window flashing during their daily run. There is a `--show-browser` flag for the rare reverse case.

**Flag surface (intentionally small — headline UX):**

| Flag | Purpose |
|---|---|
| (none) | Daily sweep with all configured sites. |
| `--setup [<site_id>]` | Bootstrap browser profile for a site. No arg = walk every unbootstrapped OAuth site one at a time. With arg = just that one. Interactive (real browser). |
| `--list` | Show what would run (sites + auth mode + per-site timeouts). No execution. |
| `--summary [N]` | Print the one-row-per-day-per-site rollup of `claims.csv` for the last N days (default 30). Read-only. |
| `--show` | Show config snapshot: catalog loaded, sites it contains, which are configured, which need credentials, license-key fingerprint (hash, not full key). |
| `--no-csv` | Skip the `claims.csv` append for this run. JSONL still writes. |
| `--show-browser` | Run with a visible browser. Suppresses the runner's default `--headless` pass-through to per-site subprocesses. Useful for debugging a misbehaving site. |
| `--only <ids>` / `--skip <ids>` | Pass-through filters. Power users; in README appendix. |
| `--version` | Print `gamba-pick X.Y.Z`. |

**Hidden / advanced flags** (still reachable, below `--help` fold or under a future `gamba-pick advanced ...` subcommand): `--config-dir`, `--seed-file`, `--stream`, `--dry-run`, `--pause-on-stuck`, `--google-oauth`, `--timeout-per-site`. Not deleted — the dev workflow still uses them.

**Sample first-real-run output (everything configured):**

```
$ ./run.sh
gamba-pick 0.7.0 — daily claim sweep
Catalog: casino-buddy-2026-05  (12 sites configured, license OK)

[1/12] sportzino    ............. claimed       SC=3.84 (+0.50)  9.2s
[2/12] zula_casino  ............. already_claimed  SC=12.10 (—)  6.8s
[3/12] pulsz        ............. error           —              42.1s  (see claim_history.jsonl)
...
[12/12] stake_us    ............. claimed        USDT=0.06 (+0.01)  11.3s

Summary: 11/12 ok, 1 error  (claims.csv updated, history -> claim_history.jsonl)
```

---

## 7. Detect-and-instruct preflight

The preflight inspects the world and produces an aggregated remediation list (so the customer sees one coherent to-do list, not interleaved messages). It runs before any subprocess is spawned.

**Order of checks:**

1. **Catalog state**
   - Zero `.gpcat` in `catalog/` → info: `Running with public reference configs only. Drop your .gpcat into catalog/ to enable your purchased sites.` (Continue.)
   - Multiple `.gpcat` → ERROR: `Multiple catalogs found; keep only the most recent.` (Stop.)
   - `.gpcat` present + no `GAMBA_PICK_LICENSE` in `picks.env` → PROMPT for license, persist on success.
   - `.gpcat` present + license set + decrypt fails → ERROR: `License key doesn't match this catalog. Re-enter:` (re-prompt; on third failure exit with support-email line).

2. **Per-site credentials** (after catalog merge, for every site in the run set):
   - `auth="form"` + missing `<SITE_ID>_USERNAME` / `<SITE_ID>_PASSWORD` in `picks.env` → add to remediation: `Edit picks.env and add: SPORTZINO_USERNAME=...; SPORTZINO_PASSWORD=...`
   - `auth="oauth"` + `profiles/<site>/` missing or empty → add to remediation: `Bootstrap login for sportzino: ./run.sh --setup sportzino`

3. **Print the remediation list** if non-empty. Exit `0` with status `Setup incomplete: N step(s) remaining` — non-zero would feel like an error to a non-tech user when really they have homework. Print: `When done, run ./run.sh again to start your daily claim.`

If the remediation list is empty, fall through to the daily sweep.

---

## 8. Output: CSV, summary view, JSONL

### 8.1 `claims.csv` (new, customer-facing)

Append-only, lives at `gamba-pick/claims.csv`. Header written once when the file is created.

```csv
run_ts,date,site,balance,currency,delta,secondary_balances,duration_s,success,claim_outcome
```

| Column | Type | Notes |
|---|---|---|
| `run_ts` | ISO-8601 UTC timestamp | Exact run time. Lets multiple same-day rows be ordered. |
| `date` | `YYYY-MM-DD` (UTC) | Calendar key for the `--summary` rollup. |
| `site` | string | Site id (e.g. `sportzino`, `stake_us`). |
| `balance` | float | Primary currency value. Empty if the run crashed before reading any balance. |
| `currency` | string | Primary currency code (`SC`, `FC`, `USDT`, ...). From `primary_currency` in `sites_seed.toml`. |
| `delta` | float | `balance` minus the most recent prior `balance` for the same `site`. Empty if no prior row. |
| `secondary_balances` | JSON object as string | E.g. `{"GC": 43562260.00}`. Empty `{}` for single-currency sites or when no balances parsed. CSV-quoted. |
| `duration_s` | float, 2 dp | Wall-clock seconds. |
| `success` | `true` / `false` | Existing `ok` flag (`Casino action completed successfully` marker present). |
| `claim_outcome` | enum | `claimed` / `already_claimed` / `skipped` / `error` / `unknown`. |

**Writer behavior:**

- One row per site per run. Append after the run finishes — never partial state.
- Open, write, fsync, close per run. Cheap; means the file is safe to read at any point.
- Header written only when creating a new file.
- `delta` computed by reading the last existing row for that site at write time. If no prior row, empty.
- `--no-csv` skips the append entirely; JSONL still writes.

### 8.2 `--summary [N]` view

Read-only, default N=30 days. Plain ASCII table to stdout.

```
$ ./run.sh --summary 7
Last 7 days, one row per (date, site) — most recent run per day shown:

date        site          balance    currency  delta_today  outcome           runs
----------  ------------  ---------  --------  -----------  ----------------  ----
2026-05-02  sportzino     3.84       SC        +0.50        claimed           1
2026-05-02  zula_casino   12.10      SC        +0.00        already_claimed   2
2026-05-02  pulsz         (error)    —         —            error             1
2026-05-01  sportzino     3.34       SC        +0.50        claimed           1
...
```

Rules:
- Group by `(date, site)`.
- For each group: take the **last** run by `run_ts` for displayed `balance` / `currency` / `outcome`.
- `delta_today` = today's last `balance` minus the previous calendar day's last `balance` for that site (not intra-day delta).
- `runs` = how many rows in the group (lets the customer see "I ran twice today").
- Any group whose last row has `success=false` shows `(error)` in `balance`.
- 30 days × ~12 sites = ~360 rows max — fits without paging.

### 8.3 `claim_history.jsonl` (existing, retained as-is)

- Same path: `gamba-pick/claim_history.jsonl`.
- Same record shape: `{ts, site_id, module, ok, exit_code, duration_s, timed_out, balances, claim_outcome, stdout_tail, stderr_tail}`.
- Always writes (no opt-out — it's the debugging artifact, customer never asked to disable).
- Different audience, different content. README mentions once: "If a site repeatedly errors, look at `claim_history.jsonl` — it has the last 20 lines of stderr per run."

### 8.4 File-size hygiene

- `claims.csv`: ~13 k rows / ~1.5 MB / yr at 12 sites × 365 days × 2-3 rows/day. Fine.
- `claim_history.jsonl` with stderr tails: ~50 MB / yr. Fine.
- README: "If you want to archive old data, just rename `claims.csv` to `claims-2026.csv` — the runner creates a fresh file." No automatic rotation.

---

## 9. Site-config additions

New optional field per `[[site]]` entry in `sites_seed.toml`:

```toml
[[site]]
id = "sportzino"
name = "Sportzino"
url = "https://sportzino.com"
status = "working"
auth = "form"
module = "sportzino"
primary_currency = "SC"   # NEW
```

**Defaulting:** if `primary_currency` is omitted, the CSV writer uses `"SC"`. If `"SC"` isn't in the parsed balances for that run, `balance` and `currency` are left empty and everything goes into `secondary_balances`. Site authors set this explicitly for non-SC sites (`FC` for FortuneWins, `USDT` for Stake.us, etc.).

**No other site-config changes.** Existing per-site fields (`auth`, `module`, `timeout_s`, `affiliate_link`) are unchanged.

---

## 10. Scheduling guidance (README content)

Single page, `README.txt` in the unzipped dir. Three OS-specific copy-pasteable snippets:

- **Windows (Task Scheduler):** "Open Task Scheduler → Create Basic Task → name it 'gamba-pick daily' → trigger 'Daily' at e.g. 9:00 AM → action 'Start a program' → program/script: full path to `run.cmd` → check 'Run whether user is logged on or not'."
- **macOS (launchd):** snippet of `~/Library/LaunchAgents/com.gambapick.daily.plist` with `ProgramArguments` and `StartCalendarInterval` for 9 AM. Customer pastes, runs `launchctl load`.
- **Linux (cron):** `0 9 * * * /home/you/gamba-pick/run.sh >> /home/you/gamba-pick/cron.log 2>&1`.

We do not validate the customer wires the scheduler. They run `./run.sh --list` to confirm the install works, then they wire the scheduler. Manual-only is also fine — many customers will run it by hand.

---

## 11. Component map (status vs. today)

| Component | Status |
|---|---|
| Source-tree reorganization: move `runner.py`, `casino.py`, `scrapling_ext.py`, `selectors_generic.py`, etc. into a proper `gamba_pick/` Python package | CHANGED — currently these live at repo root; `pyproject.toml` already declares the `gamba_pick*` package layout but no `gamba_pick/` dir exists yet |
| `gamba_pick/cli.py` console-script entrypoint (`main()`) | NEW — the existing `runner.py` `main()` logic moves here and is rewritten around the zero-arg default + detect-and-instruct flow |
| `runner.py` (root) | REMOVED — superseded by `gamba_pick/cli.py`; reference-config Python files (stake_us.py, spinquest.py, shuffle_us.py) move to `reference_configs/` |
| `run.sh` / `run.cmd` bootstrap wrapper | NEW |
| Detect-and-instruct preflight | NEW |
| Headless-by-default behavior | CHANGED (was opt-in via `--headless`; now always-on, opt-out via `--show-browser`) |
| `.gpcat` format + AES-GCM-via-HKDF encryption + decrypt path | NEW |
| `tools/build_gpcat.py` (sale-time bundle builder, NOT shipped to customer) | NEW |
| License-key prompt + storage in `picks.env` | NEW |
| `claims.csv` writer (append-only, multi-currency) | NEW |
| `--summary` view | NEW |
| `primary_currency` field in `sites_seed.toml` | NEW |
| `claim_history.jsonl` | UNCHANGED |
| Browser-profile `--setup` flow | UNCHANGED (already works per-site) |
| Scheduling | OUT OF SCOPE — README only |
| Per-OS binary / PyInstaller / code signing | OUT OF SCOPE |

---

## 12. Risks

- **uv installer endpoint change.** If Astral changes the install URL or hash, the pinned bootstrap breaks until we ship a new zip. Mitigation: pin URL + SHA256, document `pip install uv` fallback.
- **Camoufox fetch network flakiness.** First-run download is large; failure mid-fetch leaves the bootstrap in a half-state. Mitigation: `.bootstrap-done` sentinel only writes after fetch returns 0, so re-running retries.
- **Encrypted-temp-dir leak window.** Decrypted catalog sits on disk during runs. If the process is killed in a way that bypasses `atexit`, the temp dir survives until next reboot. Acceptable for Phase-1.
- **`picks.env` plaintext license.** Anyone with read access to the customer's home dir gets it. Threat model excludes local-machine compromise. README: "don't share `picks.env`."
- **First-run install size.** uv + Python + camoufox + chromium ≈ 600 MB. Customers on metered connections will notice. README mentions it.
- **Windows SmartScreen friction.** Unsigned `run.cmd` triggers a "Windows protected your PC" dialog the first time. README has the "click 'More info → Run anyway'" line.
- **License-string typos in copy-paste.** Mitigation: dashes are display-only and stripped before KDF; we accept any whitespace; we trim. After 3 failed decrypts we print the support-email line.
- **Per-customer auth-mode mismatch.** The seed's `auth` field encodes the bundle author's default (form vs. OAuth) for each site, but the actual auth surface is per-user — both surfaces are typically available on each site. A customer with form-auth on a seed-OAuth site (or vice versa) hits a wrong-mode failure on the daily run. Mitigation in scope: the existing global `--google-oauth` flag is preserved (under hidden/advanced flags) so a customer can flip the whole sweep. Out of scope: per-site override (e.g., `SPORTZINO_AUTH=form` in `picks.env`). Leave as future work; document in the README that customers whose accounts use the opposite auth method should run with `./run.sh --google-oauth` (or omit it) and report it as a bundle-fix request.

---

## 13. Versioning

Per project policy (`project_versioning_policy.md`):

- This work touches the **shared surface** (`runner.py` → `cli.py` refactor, new `claims.csv` schema, new `primary_currency` field in seed, new `.gpcat` format). That puts it in **minor-bump** territory.
- Target version for this release: **0.7.0** (current branch already exists at this version).
- The `.gpcat` format itself has its own internal version byte (`format_ver = 0x01`); future format changes bump that, not the project version.
- The pyproject already carries `version = "0.6.0"`; this work bumps to `0.7.0` as part of its release commit (per "bump in-PR, not post-merge").

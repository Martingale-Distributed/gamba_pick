# gamba_pick

A Python framework for declarative multi-step browser automation against authenticated web apps. Built around scrapling/Camoufox/Playwright with stable factory primitives for login flows (form + Google OAuth + Cloudflare Turnstile), balance scraping, popup-stack dismissal, and modal-tab-button claim chains. Originally built for sweepstakes-casino daily bonuses but the framework itself is site-agnostic — anything with a recurring-claim or daily-task flow fits.

## What's in here

```
casino.py           - factories, configs, dismissal helpers, login chains
scrapling_ext.py    - scrapling wiring + the make_casino_automation entrypoint
runner.py           - daily-sweep harness; reads a seed TOML, runs each
                      site's module as a subprocess, appends JSONL history
sites_seed.toml     - registry of known sites with implementation status
selectors_generic.py - fallback CSS for common widgets (login, OAuth, MUI close)

stake_us.py         - reference site config: form login + side-panel claim modal
spinquest.py        - reference site config: form login + custom balance parser
                      (toggle pattern, format-based currency classification)
```

The site configs are *just data plus a few selectors*; once you've read the two reference modules the framework's surface should be obvious.

## Install

```bash
pip install -e .
playwright install            # if you don't already have a browser
.venv/bin/camoufox fetch      # for the Camoufox / Firefox backend
```

Site modules read their credentials from a local `picks.env`:

```
SPINQUEST_USERNAME=...
SPINQUEST_PASSWORD=...
STAKEUS_USERNAME=...
STAKEUS_PASSWORD=...
```

## Run a single site

```bash
python spinquest.py             # form auth, reads picks.env
python stake_us.py --skip-claim # login + balance read only
python stake_us.py --headless   # CI-friendly
```

Each site script accepts the framework's standard argparse: `--headless`, `--skip-claim`, `--google-oauth`, `--setup` (interactive first-time auth), `--user-data-dir <path>`.

## Daily sweep

```bash
python runner.py --list                          # preview the plan
python runner.py --headless                      # run everything in the seed
python runner.py --only stake_us,spinquest       # subset (comma or space-separated)
python runner.py --skip stake_us                 # exclusion
```

Results append to `claim_history.jsonl` — one record per site per run, with balances, claim outcome (`claimed` / `already_claimed` / `error`), duration, and the last 20 lines of stdout/stderr for post-mortem.

## External config catalog

The runner accepts `--config-dir <path>` and `--seed-file <path>` so you can point it at a curated catalog living outside this repo:

```bash
python runner.py \
  --config-dir /path/to/catalog/configs \
  --seed-file  /path/to/catalog/sites_seed.toml \
  --headless
```

This is the integration point for [**Casino Buddy**](https://casinobuddy.app), the maintained sweepstakes-casino catalog that ships as a separate commercial product (≈10–30 site configs kept current as the landscape evolves). This framework runs the catalog's configs the same way it runs the public reference configs in this repo — point `--config-dir` at an unpacked bundle (available as an encrypted purchase) or at a directory of configs you've authored yourself.

Sites in the seed with `status="commercial"` are catalog entries — listed for visibility, ignored by the open-source runner. Run them by pointing at the catalog as shown above.

## Versioning

Pre-1.0. Patch = config-only additions; minor = framework / runner / public-config-field changes; 1.0 will freeze the public Python API. See `project_versioning_policy.md` in the development memory.

## License

[License GPL-3.0-or-later]

## Disclaimer

This project provides general-purpose browser-automation primitives. You are responsible for compliance with the terms of service of any site you automate against. The maintainers do not run automation on behalf of users and do not condone violation of operator ToS.

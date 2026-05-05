gamba-pick — daily claim runner
===============================

Quick start
-----------

  1. Drop your purchased .gpcat into ./catalog/
  2. Copy .env.template to .env
  3. Run: ./run.sh        (POSIX/macOS)
          run.cmd         (Windows: double-click or run from cmd)

  First-run setup downloads ~600 MB (Python + browsers). Subsequent
  runs are subsecond to start.

  When prompted, paste your license key. It saves to .env so
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
  install (or overwrite). DO NOT delete: .env, profiles/,
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

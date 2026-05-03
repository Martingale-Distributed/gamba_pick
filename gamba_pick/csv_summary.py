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

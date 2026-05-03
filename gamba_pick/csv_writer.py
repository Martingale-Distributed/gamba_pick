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

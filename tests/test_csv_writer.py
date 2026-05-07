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


def test_runner_helper_picks_primary_and_routes_secondary(tmp_path: Path):
    """The runner's _append_csv_row picks site.primary_currency for the
    primary balance and routes the rest into secondary_balances."""
    from gamba_pick.runner import Site, RunResult, _append_csv_row

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
    from gamba_pick.runner import Site, RunResult, _append_csv_row

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

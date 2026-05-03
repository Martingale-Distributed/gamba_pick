"""Tests for the claims.csv --summary rollup view."""

from __future__ import annotations

import csv
from pathlib import Path

from gamba_pick.csv_summary import build_summary_table, render_summary
from gamba_pick.csv_writer import ClaimRow, append_claim_row


def _row(
    site: str,
    date: str,
    run_ts: str,
    balance: float | None = 3.84,
    currency: str = "SC",
    success: bool = True,
    outcome: str = "claimed",
) -> ClaimRow:
    return ClaimRow(
        run_ts=run_ts,
        date=date,
        site=site,
        balance=balance,
        currency=currency,
        secondary_balances={},
        duration_s=8.0,
        success=success,
        claim_outcome=outcome,
    )


def test_empty_csv_returns_empty_summary(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    rows = build_summary_table(csv_path, days=30)
    assert rows == []


def test_one_row_per_date_site_pair(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    append_claim_row(
        _row("sportzino", "2026-05-02", "2026-05-02T09:00:00+00:00", balance=3.34),
        csv_path=csv_path,
    )
    append_claim_row(
        _row("sportzino", "2026-05-02", "2026-05-02T15:00:00+00:00", balance=3.84),
        csv_path=csv_path,
    )

    rows = build_summary_table(csv_path, days=30)
    assert len(rows) == 1
    assert rows[0]["site"] == "sportzino"
    assert rows[0]["date"] == "2026-05-02"
    assert rows[0]["balance"] == "3.84"     # last run wins
    assert rows[0]["runs"] == 2


def test_delta_today_uses_yesterdays_last_balance(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    append_claim_row(
        _row("sportzino", "2026-05-01", "2026-05-01T09:00:00+00:00", balance=3.34),
        csv_path=csv_path,
    )
    append_claim_row(
        _row("sportzino", "2026-05-02", "2026-05-02T09:00:00+00:00", balance=3.84),
        csv_path=csv_path,
    )

    rows = build_summary_table(csv_path, days=30)
    today = next(r for r in rows if r["date"] == "2026-05-02")
    assert today["delta_today"] == "+0.5"


def test_failed_row_shows_error_in_balance_column(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    append_claim_row(
        _row(
            "pulsz", "2026-05-02", "2026-05-02T09:00:00+00:00",
            balance=None, currency="", success=False, outcome="error",
        ),
        csv_path=csv_path,
    )

    rows = build_summary_table(csv_path, days=30)
    assert rows[0]["balance"] == "(error)"
    assert rows[0]["delta_today"] == "—"


def test_render_summary_emits_header_and_data(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    append_claim_row(
        _row("sportzino", "2026-05-02", "2026-05-02T09:00:00+00:00", balance=3.84),
        csv_path=csv_path,
    )

    text = render_summary(csv_path, days=30)
    assert "date" in text
    assert "site" in text
    assert "sportzino" in text
    assert "2026-05-02" in text


def test_render_summary_empty_file_message(tmp_path: Path):
    csv_path = tmp_path / "claims.csv"
    text = render_summary(csv_path, days=30)
    assert "no claim history" in text.lower() or "empty" in text.lower()

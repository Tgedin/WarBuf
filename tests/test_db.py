"""Tests for db.py — SQLite persistence layer.

Uses in-memory SQLite (:memory:) so tests are fast and leave no files.
"""
from __future__ import annotations

import json

import pytest

from db import Database, rules_hash


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def _buy(db: Database, ticker: str = "AAPL", qty: float = 10.0, price: float = 100.0) -> None:
    db.record_trade(ticker=ticker, side="buy", qty=qty, price_usd=price,
                    fees_usd=0.35, net_cost_basis=qty * price + 0.35)


# ── record_decision ───────────────────────────────────────────────────────────

def test_decision_ticker_stored(db):
    db.record_decision("AAPL", "BUY", 0.75, "abc123")
    row = db._conn.execute("SELECT ticker FROM decisions").fetchone()
    assert row["ticker"] == "AAPL"


def test_decision_action_stored(db):
    db.record_decision("AAPL", "VETO", 0.75, "abc123")
    row = db._conn.execute("SELECT action FROM decisions").fetchone()
    assert row["action"] == "VETO"


def test_decision_score_stored(db):
    db.record_decision("AAPL", "BUY", 0.82, "abc123")
    row = db._conn.execute("SELECT score FROM decisions").fetchone()
    assert abs(row["score"] - 0.82) < 1e-9


def test_decision_vetoed_stored_as_integer(db):
    db.record_decision("AAPL", "VETO", 0.5, "abc123", vetoed=True)
    row = db._conn.execute("SELECT vetoed FROM decisions").fetchone()
    assert row["vetoed"] == 1


def test_decision_not_vetoed_stored_as_zero(db):
    db.record_decision("AAPL", "BUY", 0.5, "abc123", vetoed=False)
    row = db._conn.execute("SELECT vetoed FROM decisions").fetchone()
    assert row["vetoed"] == 0


def test_decision_data_gaps_stored_as_json(db):
    db.record_decision("AAPL", "BUY", 0.5, "abc123", data_gaps=["gap1", "gap2"])
    row = db._conn.execute("SELECT data_gaps FROM decisions").fetchone()
    assert json.loads(row["data_gaps"]) == ["gap1", "gap2"]


def test_decision_empty_data_gaps_stored_as_empty_list(db):
    db.record_decision("AAPL", "BUY", 0.5, "abc123")
    row = db._conn.execute("SELECT data_gaps FROM decisions").fetchone()
    assert json.loads(row["data_gaps"]) == []


def test_decision_bull_case_stored(db):
    db.record_decision("AAPL", "BUY", 0.5, "abc123", bull_case="Strong moat.")
    row = db._conn.execute("SELECT bull_case FROM decisions").fetchone()
    assert row["bull_case"] == "Strong moat."


def test_decision_self_critique_stored(db):
    db.record_decision("AAPL", "BUY", 0.5, "abc123", self_critique="May anchor on growth.")
    row = db._conn.execute("SELECT self_critique FROM decisions").fetchone()
    assert row["self_critique"] == "May anchor on growth."


def test_decision_algorithm_feedback_stored(db):
    db.record_decision("AAPL", "BUY", 0.5, "abc123",
                        algorithm_feedback="Momentum factor unreliable for large-caps.")
    row = db._conn.execute("SELECT algorithm_feedback FROM decisions").fetchone()
    assert row["algorithm_feedback"] == "Momentum factor unreliable for large-caps."


# ── record_trade + position updates ──────────────────────────────────────────

def test_first_buy_creates_position(db):
    _buy(db, "MSFT")
    positions = db.get_positions()
    assert "MSFT" in positions


def test_first_buy_sets_qty(db):
    _buy(db, "MSFT", qty=5.0)
    assert db.get_positions()["MSFT"]["qty"] == pytest.approx(5.0)


def test_second_buy_accumulates_qty(db):
    _buy(db, "MSFT", qty=5.0)
    _buy(db, "MSFT", qty=3.0)
    assert db.get_positions()["MSFT"]["qty"] == pytest.approx(8.0)


def test_second_buy_averages_cost_basis(db):
    _buy(db, "MSFT", qty=10.0, price=100.0)
    _buy(db, "MSFT", qty=10.0, price=200.0)
    # avg = (100*10 + 200*10) / 20 = 150
    assert db.get_positions()["MSFT"]["avg_cost_basis"] == pytest.approx(150.0)


def test_partial_sell_reduces_qty(db):
    _buy(db, "MSFT", qty=10.0)
    db.record_trade("MSFT", "sell", 4.0, 110.0, 0.35, 10.0 * 110.0 - 0.35)
    assert db.get_positions()["MSFT"]["qty"] == pytest.approx(6.0)


def test_full_sell_removes_position(db):
    _buy(db, "MSFT", qty=10.0)
    db.record_trade("MSFT", "sell", 10.0, 110.0, 0.35, 10.0 * 110.0 - 0.35)
    assert "MSFT" not in db.get_positions()


def test_sell_without_position_does_not_crash(db):
    db.record_trade("GHOST", "sell", 1.0, 100.0, 0.35, 99.65)
    assert "GHOST" not in db.get_positions()


def test_fees_accumulate_across_buys(db):
    _buy(db, "AAPL")
    _buy(db, "AAPL")
    assert db.get_positions()["AAPL"]["total_fees_paid"] == pytest.approx(0.70)


# ── EUR fields ────────────────────────────────────────────────────────────────

def test_buy_stores_avg_cost_basis_eur(db):
    """avg_cost_basis_eur = price_usd / eur_usd_rate on first buy."""
    db.record_trade("NVDA", "buy", 2.0, 108.0, 0.40, 216.40, eur_usd_rate=1.08)
    pos = db.get_positions()["NVDA"]
    assert pos["avg_cost_basis_eur"] == pytest.approx(100.0, rel=1e-4)


def test_buy_stores_total_fees_eur(db):
    """total_fees_eur = fees_usd / eur_usd_rate."""
    db.record_trade("NVDA", "buy", 2.0, 108.0, 1.08, 217.08, eur_usd_rate=1.08)
    pos = db.get_positions()["NVDA"]
    assert pos["total_fees_eur"] == pytest.approx(1.0, rel=1e-4)


def test_record_trade_stores_price_eur(db):
    """trades.price_eur column must be populated."""
    db.record_trade("MSFT", "buy", 1.0, 216.0, 0.50, 216.50, eur_usd_rate=1.08)
    row = db._conn.execute("SELECT price_eur, eur_usd_rate FROM trades").fetchone()
    assert row["price_eur"] == pytest.approx(200.0, rel=1e-4)
    assert row["eur_usd_rate"] == pytest.approx(1.08, rel=1e-6)


# ── record_performance ────────────────────────────────────────────────────────

def test_performance_record_stored(db):
    db.record_performance(1000.0, 950.0, 100.0)
    rows = db.get_performance_history()
    assert len(rows) == 1


def test_performance_portfolio_value_stored(db):
    db.record_performance(1234.0, 950.0, 100.0)
    rows = db.get_performance_history()
    assert rows[0]["portfolio_value_eur"] == pytest.approx(1234.0)


def test_performance_history_respects_limit(db):
    # Insert rows with different dates directly — record_performance uses TODAY as key.
    dates = ["2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26", "2026-02-02"]
    for i, date in enumerate(dates):
        db._conn.execute(
            "INSERT OR REPLACE INTO performance (date, portfolio_value_eur, benchmark_value, cash_eur) VALUES (?, ?, ?, ?)",
            (date, float(i), 0.0, 0.0),
        )
    db._conn.commit()
    rows = db.get_performance_history(limit=3)
    assert len(rows) == 3


# ── forecasts ─────────────────────────────────────────────────────────────────

def test_save_forecast_stored(db):
    db.save_forecast("2026-05", 1.0, 3.0)
    f = db.get_forecast("2026-05")
    assert f is not None


def test_save_forecast_expected_low_stored(db):
    db.save_forecast("2026-05", 1.5, 3.5)
    assert db.get_forecast("2026-05")["expected_low"] == pytest.approx(1.5)


def test_save_forecast_expected_high_stored(db):
    db.save_forecast("2026-05", 1.5, 3.5)
    assert db.get_forecast("2026-05")["expected_high"] == pytest.approx(3.5)


def test_get_forecast_returns_none_for_missing_month(db):
    assert db.get_forecast("2099-01") is None


def test_update_forecast_actual_stored(db):
    db.save_forecast("2026-05", 1.0, 3.0)
    db.update_forecast_actual("2026-05", 2.1, 1.8)
    assert db.get_forecast("2026-05")["actual"] == pytest.approx(2.1)


def test_update_forecast_benchmark_actual_stored(db):
    db.save_forecast("2026-05", 1.0, 3.0)
    db.update_forecast_actual("2026-05", 2.1, 1.8)
    assert db.get_forecast("2026-05")["benchmark_actual"] == pytest.approx(1.8)


# ── rules_hash ────────────────────────────────────────────────────────────────

def test_rules_hash_is_16_chars(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("key: value\n")
    assert len(rules_hash(f)) == 16


def test_rules_hash_is_hex(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("key: value\n")
    assert all(c in "0123456789abcdef" for c in rules_hash(f))


def test_rules_hash_changes_on_content_change(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("key: value\n")
    h1 = rules_hash(f)
    f.write_text("key: other\n")
    h2 = rules_hash(f)
    assert h1 != h2


def test_rules_hash_stable_for_same_content(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("key: value\n")
    assert rules_hash(f) == rules_hash(f)


# ── get_recent_decisions ──────────────────────────────────────────────────────

def test_get_recent_decisions_returns_empty_for_unknown_ticker(db):
    assert db.get_recent_decisions("UNKNOWN") == []


def test_get_recent_decisions_returns_inserted_decision(db):
    db.record_decision("AAPL", "BUY", 0.80, "abc123", bull_case="moat")
    rows = db.get_recent_decisions("AAPL")
    assert len(rows) == 1


def test_get_recent_decisions_respects_limit(db):
    for _ in range(5):
        db.record_decision("AAPL", "BUY", 0.80, "abc123")
    rows = db.get_recent_decisions("AAPL", limit=3)
    assert len(rows) == 3


def test_get_recent_decisions_data_gaps_is_list(db):
    db.record_decision("AAPL", "BUY", 0.80, "abc123", data_gaps=["gap1", "gap2"])
    rows = db.get_recent_decisions("AAPL")
    assert isinstance(rows[0]["data_gaps"], list)


def test_get_recent_decisions_data_gaps_values_intact(db):
    db.record_decision("AAPL", "BUY", 0.80, "abc123", data_gaps=["FCF missing"])
    rows = db.get_recent_decisions("AAPL")
    assert rows[0]["data_gaps"] == ["FCF missing"]

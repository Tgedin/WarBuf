"""Tests for pure helper functions in main.py."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from main import _detect_sell_trigger, _is_nyse_holiday, _is_nyse_trading_hours, intraday_job

# ── _detect_sell_trigger ──────────────────────────────────────────────────────

TICKER        = "AAPL"
QTY           = 10.0
PRICE         = 150.0
STOP_LOSS_PCT = 15.0
COLLAPSE      = 0.25


def test_no_trigger_returns_none():
    result = _detect_sell_trigger(
        TICKER, QTY, PRICE,
        return_pct=-5.0,       # not a loss big enough
        current_score=0.8,
        prev_score=0.9,        # delta 0.1 — below collapse threshold
        stop_loss_pct=STOP_LOSS_PCT,
        collapse_delta=COLLAPSE,
    )
    assert result is None


def test_stop_loss_triggers_when_return_at_threshold():
    result = _detect_sell_trigger(
        TICKER, QTY, PRICE,
        return_pct=-15.0,
        current_score=0.5,
        prev_score=0.5,
        stop_loss_pct=STOP_LOSS_PCT,
        collapse_delta=COLLAPSE,
    )
    assert result is not None


def test_stop_loss_triggers_when_return_below_threshold():
    result = _detect_sell_trigger(
        TICKER, QTY, PRICE,
        return_pct=-20.0,
        current_score=0.5,
        prev_score=0.5,
        stop_loss_pct=STOP_LOSS_PCT,
        collapse_delta=COLLAPSE,
    )
    assert result is not None


def test_stop_loss_does_not_trigger_above_threshold():
    result = _detect_sell_trigger(
        TICKER, QTY, PRICE,
        return_pct=-14.9,
        current_score=0.5,
        prev_score=0.5,
        stop_loss_pct=STOP_LOSS_PCT,
        collapse_delta=COLLAPSE,
    )
    assert result is None


def test_stop_loss_notional_is_full_position():
    alert_text, notional = _detect_sell_trigger(
        TICKER, QTY, PRICE,
        return_pct=-20.0,
        current_score=0.5,
        prev_score=0.5,
        stop_loss_pct=STOP_LOSS_PCT,
        collapse_delta=COLLAPSE,
    )
    assert notional == pytest.approx(QTY * PRICE)


def test_stop_loss_alert_contains_ticker():
    alert_text, _ = _detect_sell_trigger(
        TICKER, QTY, PRICE,
        return_pct=-20.0,
        current_score=0.5,
        prev_score=0.5,
        stop_loss_pct=STOP_LOSS_PCT,
        collapse_delta=COLLAPSE,
    )
    assert TICKER in alert_text


def test_score_collapse_triggers_when_delta_exceeded():
    result = _detect_sell_trigger(
        TICKER, QTY, PRICE,
        return_pct=-5.0,
        current_score=0.5,
        prev_score=0.8,        # delta 0.3 > 0.25
        stop_loss_pct=STOP_LOSS_PCT,
        collapse_delta=COLLAPSE,
    )
    assert result is not None


def test_score_collapse_notional_is_half_position():
    _, notional = _detect_sell_trigger(
        TICKER, QTY, PRICE,
        return_pct=-5.0,
        current_score=0.5,
        prev_score=0.8,
        stop_loss_pct=STOP_LOSS_PCT,
        collapse_delta=COLLAPSE,
    )
    assert notional == pytest.approx(QTY * PRICE / 2)


def test_score_collapse_does_not_trigger_at_threshold():
    result = _detect_sell_trigger(
        TICKER, QTY, PRICE,
        return_pct=-5.0,
        current_score=0.55,
        prev_score=0.8,        # delta exactly 0.25 — not exceeded
        stop_loss_pct=STOP_LOSS_PCT,
        collapse_delta=COLLAPSE,
    )
    assert result is None


def test_stop_loss_takes_priority_over_score_collapse():
    alert_text, notional = _detect_sell_trigger(
        TICKER, QTY, PRICE,
        return_pct=-20.0,      # stop-loss
        current_score=0.3,
        prev_score=0.9,        # score collapse too
        stop_loss_pct=STOP_LOSS_PCT,
        collapse_delta=COLLAPSE,
    )
    # Full exit, not 50% trim
    assert notional == pytest.approx(QTY * PRICE)
    assert "STOP-LOSS" in alert_text


def test_no_trigger_when_prev_score_is_none():
    result = _detect_sell_trigger(
        TICKER, QTY, PRICE,
        return_pct=-5.0,
        current_score=0.3,
        prev_score=None,
        stop_loss_pct=STOP_LOSS_PCT,
        collapse_delta=COLLAPSE,
    )
    assert result is None


def test_no_trigger_when_return_pct_is_none_and_no_score_collapse():
    result = _detect_sell_trigger(
        TICKER, QTY, PRICE,
        return_pct=None,
        current_score=0.8,
        prev_score=0.9,
        stop_loss_pct=STOP_LOSS_PCT,
        collapse_delta=COLLAPSE,
    )
    assert result is None


# ── _is_nyse_holiday ──────────────────────────────────────────────────────────

HOLIDAYS = ["2026-01-01", "2026-05-25", "2026-12-25"]


def test_is_nyse_holiday_match():
    assert _is_nyse_holiday(date(2026, 1, 1), HOLIDAYS) is True


def test_is_nyse_holiday_non_holiday():
    assert _is_nyse_holiday(date(2026, 1, 2), HOLIDAYS) is False


def test_is_nyse_holiday_empty_list():
    assert _is_nyse_holiday(date(2026, 5, 25), []) is False


def test_is_nyse_holiday_last_in_list():
    assert _is_nyse_holiday(date(2026, 12, 25), HOLIDAYS) is True


# ── _is_nyse_trading_hours ────────────────────────────────────────────────────

# Wednesday 2026-06-10 (summer, EDT = UTC-4)
# 13:30 UTC = 09:30 ET  → market open
_OPEN_UTC  = datetime(2026, 6, 10, 13, 30, 0, tzinfo=timezone.utc)
# 20:00 UTC = 16:00 ET  → market close
_CLOSE_UTC = datetime(2026, 6, 10, 20, 0, 0, tzinfo=timezone.utc)
# 07:00 UTC = 03:00 ET  → pre-market
_PRE_UTC   = datetime(2026, 6, 10, 7, 0, 0, tzinfo=timezone.utc)
# Saturday 2026-06-13 14:00 UTC
_WEEKEND_UTC = datetime(2026, 6, 13, 14, 0, 0, tzinfo=timezone.utc)
# Winter: Wednesday 2026-01-07, 15:00 UTC = 10:00 EST (UTC-5) → open
_WINTER_UTC = datetime(2026, 1, 7, 15, 0, 0, tzinfo=timezone.utc)


def test_trading_hours_at_open():
    assert _is_nyse_trading_hours(_OPEN_UTC) is True


def test_trading_hours_mid_session():
    mid = datetime(2026, 6, 10, 17, 0, 0, tzinfo=timezone.utc)  # 13:00 ET
    assert _is_nyse_trading_hours(mid) is True


def test_trading_hours_at_close():
    assert _is_nyse_trading_hours(_CLOSE_UTC) is True


def test_trading_hours_pre_market():
    assert _is_nyse_trading_hours(_PRE_UTC) is False


def test_trading_hours_after_close():
    after = datetime(2026, 6, 10, 21, 0, 0, tzinfo=timezone.utc)  # 17:00 ET
    assert _is_nyse_trading_hours(after) is False


def test_trading_hours_weekend():
    assert _is_nyse_trading_hours(_WEEKEND_UTC) is False


def test_trading_hours_winter_session():
    assert _is_nyse_trading_hours(_WINTER_UTC) is True


# ── intraday_job ───────────────────────────────────────────────────────────

def test_intraday_job_noop_outside_trading_hours(monkeypatch, tmp_path):
    """intraday_job returns immediately when NYSE is closed."""
    monkeypatch.chdir(tmp_path)
    called = []
    monkeypatch.setattr("main._is_nyse_trading_hours", lambda: False)
    monkeypatch.setattr("main.Database", lambda *a, **kw: called.append(1))
    intraday_job()
    # Database should never be opened when outside trading hours
    assert called == []


def test_intraday_job_noop_on_holiday(monkeypatch, tmp_path):
    """intraday_job returns immediately on NYSE holidays."""
    monkeypatch.chdir(tmp_path)
    called = []
    monkeypatch.setattr("main._is_nyse_trading_hours", lambda: True)
    monkeypatch.setattr("main._load_rules", lambda: {"nyse_holidays": ["2026-01-01"], "paper_mode": True})
    monkeypatch.setattr("main._is_nyse_holiday", lambda d, h: True)
    monkeypatch.setattr("main.Database", lambda *a, **kw: called.append(1))
    intraday_job()
    assert called == []

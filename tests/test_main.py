"""Tests for pure helper functions in main.py."""
from __future__ import annotations

import pytest

from main import _detect_sell_trigger

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

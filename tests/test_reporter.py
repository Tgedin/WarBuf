"""Tests for reporter.py — email body construction and DB side effects.

SMTP is mocked — no real email is sent.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from db import Database
from reporter import send_forecast_vs_actual, send_monthly_forecast, send_weekly_digest


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def _positions():
    return [{"ticker": "AAPL", "pct": 10, "action": "HOLD", "score": 0.82}]


def _patch_smtp():
    return patch("reporter.smtplib.SMTP_SSL")


def _patch_env(monkeypatch):
    monkeypatch.setenv("EMAIL_FROM", "bot@example.com")
    monkeypatch.setenv("EMAIL_TO", "me@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "pass")


# ── send_weekly_digest ────────────────────────────────────────────────────────

def test_weekly_digest_calls_smtp_send(db, monkeypatch):
    _patch_env(monkeypatch)
    with _patch_smtp() as mock_smtp:
        mock_smtp.return_value.__enter__ = lambda s: MagicMock()
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_weekly_digest(
            db=db, positions=_positions(), alerts=[], news=[],
            next_action="Full analysis: May 04",
            spy_price=520.0, spy_sma=500.0,
            portfolio_eur=1000.0, mtd_pct=2.1, spy_mtd_pct=1.8,
        )
    assert mock_smtp.called


def test_weekly_digest_subject_contains_warbuf(db, monkeypatch):
    _patch_env(monkeypatch)
    sent_subjects = []

    def capture_send(msg):
        sent_subjects.append(msg["Subject"])

    with _patch_smtp() as mock_smtp:
        smtp_instance = MagicMock()
        smtp_instance.send_message.side_effect = capture_send
        mock_smtp.return_value.__enter__ = lambda s: smtp_instance
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_weekly_digest(
            db=db, positions=_positions(), alerts=["test alert"], news=[],
            next_action="none", spy_price=500.0, spy_sma=490.0,
            portfolio_eur=1000.0, mtd_pct=1.0, spy_mtd_pct=1.0,
        )

    assert any("WarBuf" in s for s in sent_subjects)


def test_weekly_digest_includes_alert_in_body(db, monkeypatch):
    _patch_env(monkeypatch)
    sent_bodies = []

    def capture_send(msg):
        sent_bodies.append(msg.get_content())

    with _patch_smtp() as mock_smtp:
        smtp_instance = MagicMock()
        smtp_instance.send_message.side_effect = capture_send
        mock_smtp.return_value.__enter__ = lambda s: smtp_instance
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_weekly_digest(
            db=db, positions=_positions(), alerts=["AAPL score collapse"], news=[],
            next_action="none", spy_price=500.0, spy_sma=490.0,
            portfolio_eur=1000.0, mtd_pct=1.0, spy_mtd_pct=1.0,
        )

    assert any("AAPL score collapse" in b for b in sent_bodies)


# ── send_monthly_forecast ─────────────────────────────────────────────────────

def test_monthly_forecast_saves_to_db(db, monkeypatch):
    _patch_env(monkeypatch)
    with _patch_smtp() as mock_smtp:
        mock_smtp.return_value.__enter__ = lambda s: MagicMock()
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_monthly_forecast(
            month_label="May 2026",
            macro_regime="Risk-On",
            expected_low=1.0,
            expected_high=3.0,
            downside=-5.0,
            key_risk="Rate hike",
            position_outlooks=[{"ticker": "AAPL", "outlook": "bullish", "note": "strong FCF"}],
            planned_action="Buy MSFT",
            db=db,
        )
    forecast = db.get_forecast("2026-05")
    assert forecast is not None


def test_monthly_forecast_stores_expected_low(db, monkeypatch):
    _patch_env(monkeypatch)
    with _patch_smtp() as mock_smtp:
        mock_smtp.return_value.__enter__ = lambda s: MagicMock()
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_monthly_forecast(
            month_label="May 2026", macro_regime="Risk-On",
            expected_low=1.5, expected_high=3.5, downside=-5.0,
            key_risk="Rate hike",
            position_outlooks=[], planned_action="none", db=db,
        )
    assert db.get_forecast("2026-05")["expected_low"] == pytest.approx(1.5)


# ── send_forecast_vs_actual ───────────────────────────────────────────────────

def test_forecast_vs_actual_updates_db(db, monkeypatch):
    _patch_env(monkeypatch)
    db.save_forecast("2026-04", 1.0, 3.0)
    with _patch_smtp() as mock_smtp:
        mock_smtp.return_value.__enter__ = lambda s: MagicMock()
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_forecast_vs_actual(
            month_label="April 2026",
            expected_low=1.0, expected_high=3.0,
            actual=2.5, benchmark_actual=1.8,
            miss_note="", db=db,
        )
    assert db.get_forecast("2026-04")["actual"] == pytest.approx(2.5)


def test_forecast_vs_actual_sends_email(db, monkeypatch):
    _patch_env(monkeypatch)
    db.save_forecast("2026-04", 1.0, 3.0)
    with _patch_smtp() as mock_smtp:
        mock_smtp.return_value.__enter__ = lambda s: MagicMock()
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_forecast_vs_actual(
            month_label="April 2026",
            expected_low=1.0, expected_high=3.0,
            actual=2.5, benchmark_actual=1.8,
            miss_note="", db=db,
        )
    assert mock_smtp.called


# ── EUR rate in weekly digest ─────────────────────────────────────────────────

def test_weekly_digest_body_contains_eur_rate(db, monkeypatch):
    """eur_usd_rate must appear in the digest header line."""
    _patch_env(monkeypatch)
    sent_bodies = []

    def capture_send(msg):
        sent_bodies.append(msg.get_content())

    with _patch_smtp() as mock_smtp:
        smtp_instance = MagicMock()
        smtp_instance.send_message.side_effect = capture_send
        mock_smtp.return_value.__enter__ = lambda s: smtp_instance
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_weekly_digest(
            db=db, positions=_positions(), alerts=[], news=[],
            next_action="none", spy_price=520.0, spy_sma=500.0,
            portfolio_eur=1000.0, mtd_pct=1.0, spy_mtd_pct=1.0,
            eur_usd_rate=1.0850,
        )

    assert any("1.0850" in b for b in sent_bodies)


def test_weekly_digest_body_contains_dashboard_url(db, monkeypatch):
    """dashboard_url must appear in the body when provided."""
    _patch_env(monkeypatch)
    sent_bodies = []

    def capture_send(msg):
        sent_bodies.append(msg.get_content())

    with _patch_smtp() as mock_smtp:
        smtp_instance = MagicMock()
        smtp_instance.send_message.side_effect = capture_send
        mock_smtp.return_value.__enter__ = lambda s: smtp_instance
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_weekly_digest(
            db=db, positions=_positions(), alerts=[], news=[],
            next_action="none", spy_price=520.0, spy_sma=500.0,
            portfolio_eur=1000.0, mtd_pct=1.0, spy_mtd_pct=1.0,
            dashboard_url="https://example.com",
        )

    assert any("https://example.com" in b for b in sent_bodies)


# ── send_monthly_forecast with dashboard_url ──────────────────────────────────

def test_monthly_forecast_with_dashboard_url_sends_email(db, monkeypatch):
    _patch_env(monkeypatch)
    with _patch_smtp() as mock_smtp:
        mock_smtp.return_value.__enter__ = lambda s: MagicMock()
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_monthly_forecast(
            month_label="May 2026", macro_regime="Risk-On",
            expected_low=1.0, expected_high=3.0, downside=-5.0,
            key_risk="Rate hike", position_outlooks=[],
            planned_action="none", db=db,
            dashboard_url="https://dash.example.com",
        )
    assert mock_smtp.called


def test_monthly_forecast_body_contains_dashboard_url(db, monkeypatch):
    _patch_env(monkeypatch)
    sent_bodies = []

    def capture_send(msg):
        sent_bodies.append(msg.get_content())

    with _patch_smtp() as mock_smtp:
        smtp_instance = MagicMock()
        smtp_instance.send_message.side_effect = capture_send
        mock_smtp.return_value.__enter__ = lambda s: smtp_instance
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_monthly_forecast(
            month_label="May 2026", macro_regime="Risk-On",
            expected_low=1.0, expected_high=3.0, downside=-5.0,
            key_risk="Rate hike", position_outlooks=[],
            planned_action="none", db=db,
            dashboard_url="https://dash.example.com",
        )
    assert any("https://dash.example.com" in b for b in sent_bodies)


def test_monthly_forecast_body_contains_key_risk(db, monkeypatch):
    _patch_env(monkeypatch)
    sent_bodies = []

    def capture_send(msg):
        sent_bodies.append(msg.get_content())

    with _patch_smtp() as mock_smtp:
        smtp_instance = MagicMock()
        smtp_instance.send_message.side_effect = capture_send
        mock_smtp.return_value.__enter__ = lambda s: smtp_instance
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_monthly_forecast(
            month_label="May 2026", macro_regime="Risk-On",
            expected_low=1.0, expected_high=3.0, downside=-5.0,
            key_risk="Rate spike scenario", position_outlooks=[],
            planned_action="none", db=db,
        )
    assert any("Rate spike scenario" in b for b in sent_bodies)


def test_monthly_forecast_without_url_still_sends(db, monkeypatch):
    _patch_env(monkeypatch)
    with _patch_smtp() as mock_smtp:
        mock_smtp.return_value.__enter__ = lambda s: MagicMock()
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_monthly_forecast(
            month_label="May 2026", macro_regime="Risk-On",
            expected_low=1.0, expected_high=3.0, downside=-5.0,
            key_risk="Rate hike", position_outlooks=[],
            planned_action="none", db=db,
        )
    assert mock_smtp.called

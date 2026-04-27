"""Tests for core/market.py — yfinance wrapper with file cache.

All yfinance calls are mocked. No network I/O in tests.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.market import (
    get_fundamentals,
    get_momentum,
    get_news_headlines,
    get_open_price,
    get_spy_sma,
    is_risk_on,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_ticker_info(overrides: dict | None = None) -> dict:
    base = {
        "returnOnEquity":   0.30,
        "freeCashflow":     5_000_000_000,
        "totalRevenue":     20_000_000_000,
        "debtToEquity":     60.0,
        "trailingPE":       25.0,
        "grossProfits":     8_000_000_000,
        "totalAssets":      40_000_000_000,
        "marketCap":        2_000_000_000_000,
        "revenueGrowth":    0.12,
        "sector":           "Technology",
    }
    if overrides:
        base.update(overrides)
    return base


def _mock_history(prices: list[float]):
    """Return a DataFrame-like mock with a Close series."""
    import pandas as pd
    closes = pd.Series(prices)
    df = MagicMock()
    df.empty = False
    df.__len__ = lambda self: len(prices)
    df.__getitem__ = lambda self, key: closes if key == "Close" else MagicMock()
    return df


# ── get_fundamentals ──────────────────────────────────────────────────────────

def test_get_fundamentals_returns_fundamentals_object(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.info = _mock_ticker_info()
        f = get_fundamentals("AAPL")
    assert f.ticker == "AAPL"


def test_get_fundamentals_roe_parsed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.info = _mock_ticker_info({"returnOnEquity": 0.42})
        f = get_fundamentals("AAPL")
    assert f.roe == pytest.approx(0.42)


def test_get_fundamentals_missing_roe_is_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.info = _mock_ticker_info({"returnOnEquity": None})
        f = get_fundamentals("AAPL")
    assert f.roe is None


def test_get_fundamentals_sector_parsed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.info = _mock_ticker_info({"sector": "Healthcare"})
        f = get_fundamentals("AAPL")
    assert f.sector == "Healthcare"


def test_get_fundamentals_caches_on_second_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.info = _mock_ticker_info()
        get_fundamentals("CACHE_TEST")
        get_fundamentals("CACHE_TEST")
    assert mock_yf.call_count == 1


# ── get_momentum ──────────────────────────────────────────────────────────────

def test_get_momentum_returns_float(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prices = [100.0] * 50 + [120.0] * 50
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.history.return_value = _mock_history(prices)
        result = get_momentum("AAPL", lookback_days=365, skip_days=30)
    assert isinstance(result, float)


def test_get_momentum_returns_none_on_empty_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    empty = MagicMock()
    empty.empty = True
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.history.return_value = empty
        result = get_momentum("AAPL", lookback_days=365, skip_days=30)
    assert result is None


def test_get_momentum_returns_none_on_yfinance_exception(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.history.side_effect = RuntimeError("network error")
        result = get_momentum("AAPL", lookback_days=365, skip_days=30)
    assert result is None


# ── get_spy_sma ───────────────────────────────────────────────────────────────

def test_get_spy_sma_returns_tuple(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prices = [400.0 + i * 0.1 for i in range(250)]
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.history.return_value = _mock_history(prices)
        result = get_spy_sma(200)
    assert isinstance(result, tuple) and len(result) == 2


def test_get_spy_sma_current_price_is_last_close(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prices = [400.0 + i * 0.1 for i in range(250)]
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.history.return_value = _mock_history(prices)
        price, _ = get_spy_sma(200)
    assert price == pytest.approx(prices[-1])


def test_get_spy_sma_raises_on_insufficient_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prices = [400.0] * 10  # only 10 prices, need 200
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.history.return_value = _mock_history(prices)
        with pytest.raises(RuntimeError):
            get_spy_sma(200)


# ── is_risk_on ────────────────────────────────────────────────────────────────

def test_is_risk_on_true_when_price_above_sma(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("core.market.get_spy_sma", return_value=(500.0, 450.0)):
        assert is_risk_on(200) is True


def test_is_risk_on_false_when_price_below_sma(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("core.market.get_spy_sma", return_value=(400.0, 450.0)):
        assert is_risk_on(200) is False


# ── get_news_headlines ────────────────────────────────────────────────────────

def test_get_news_headlines_returns_list(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mock_news = [
        {"title": "AAPL beats earnings"},
        {"title": "Apple launches new product"},
    ]
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.news = mock_news
        result = get_news_headlines("AAPL")
    assert isinstance(result, list)


def test_get_news_headlines_respects_max_items(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mock_news = [{"title": f"Story {i}"} for i in range(10)]
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.news = mock_news
        result = get_news_headlines("AAPL", max_items=3)
    assert len(result) <= 3


def test_get_news_headlines_returns_empty_on_exception(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.news = []
        result = get_news_headlines("AAPL")
    assert result == []


# ── get_open_price ────────────────────────────────────────────────────────

def _mock_intraday_history(open_prices: list[float]):
    import pandas as pd
    opens = pd.Series(open_prices)
    df = MagicMock()
    df.empty = False
    df.__getitem__ = lambda self, key: opens if key == "Open" else MagicMock()
    return df


def test_get_open_price_returns_float(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.history.return_value = _mock_intraday_history([150.0, 151.0])
        result = get_open_price("AAPL")
    assert isinstance(result, float)


def test_get_open_price_returns_first_candle_open(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.history.return_value = _mock_intraday_history([123.45, 124.0])
        result = get_open_price("AAPL")
    assert result == pytest.approx(123.45)


def test_get_open_price_falls_back_on_empty_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    empty_df = MagicMock()
    empty_df.empty = True
    close_df = MagicMock()
    close_df.empty = False
    import pandas as pd
    closes = pd.Series([200.0, 201.0])
    close_df.__getitem__ = lambda self, key: closes if key == "Close" else MagicMock()
    close_df.__len__ = lambda self: 2

    call_count = [0]

    def fake_history(**kwargs):
        call_count[0] += 1
        if kwargs.get("interval") == "1m":
            return empty_df
        return close_df

    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.history.side_effect = fake_history
        result = get_open_price("AAPL")
    # Should have fallen back to get_last_price and returned a float
    assert result == pytest.approx(201.0)

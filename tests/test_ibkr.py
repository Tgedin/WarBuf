"""Tests for broker/ibkr.py — IBKR Client Portal Web API broker.

All HTTP calls are mocked via requests.Session. No network I/O.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from broker.ibkr import IBKRBroker


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_broker(gateway_url: str = "https://localhost:5000") -> IBKRBroker:
    return IBKRBroker(gateway_url=gateway_url, account_id="U12345")


# Standard session-management responses that are required by the new methods.
_SESSION_STUBS = {
    "/tickle":             {"session": "alive"},
    "/portfolio/accounts": [{"id": "U12345"}],
    "/iserver/auth/status": {"authenticated": True, "competing": False, "connected": True},
    "/iserver/questions/suppress": {"status": "submitted"},
}


def _mock_session(responses: dict[str, object]) -> MagicMock:
    """
    Return a mock requests.Session where each URL substring maps to JSON data.
    Session-management stubs are included automatically so tests only need to
    declare the domain-specific endpoints they care about.
    """
    all_responses = {**_SESSION_STUBS, **responses}
    session = MagicMock()

    def fake_get(url, **kwargs):
        for path, data in all_responses.items():
            if path in url:
                resp = MagicMock()
                resp.ok = True
                resp.raise_for_status = MagicMock()
                resp.json.return_value = data
                return resp
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception(f"unexpected GET {url}")
        return resp

    def fake_post(url, **kwargs):
        for path, data in all_responses.items():
            if path in url:
                resp = MagicMock()
                resp.ok = True
                resp.raise_for_status = MagicMock()
                resp.json.return_value = data
                return resp
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception(f"unexpected POST {url}")
        return resp

    session.get.side_effect  = fake_get
    session.post.side_effect = fake_post
    return session


def _valid_conid_response(ticker: str, conid: int = 999) -> dict:
    return {
        ticker: [{
            "instrument_type": "STK",
            "contracts": [{"conid": conid, "exchange": "NASDAQ"}],
        }]
    }


# ── SSL configuration ─────────────────────────────────────────────────────────

def test_ssl_verification_disabled_for_localhost():
    broker = _make_broker("https://localhost:5000")
    assert broker._session.verify is False


def test_ssl_verification_enabled_for_remote_host():
    broker = _make_broker("https://api.example.com")
    assert broker._session.verify is True


def test_ssl_verification_disabled_for_127_0_0_1():
    broker = _make_broker("https://127.0.0.1:5000")
    assert broker._session.verify is False


# ── get_positions ─────────────────────────────────────────────────────────────

def test_get_positions_returns_dict():
    broker = _make_broker()
    broker._session = _mock_session({"/positions/": [{"ticker": "AAPL", "position": 10.0, "mktValue": 1500.0}]})
    assert isinstance(broker.get_positions(), dict)


def test_get_positions_parses_ticker():
    broker = _make_broker()
    broker._session = _mock_session({"/positions/": [{"ticker": "MSFT", "position": 5.0, "mktValue": 2000.0}]})
    assert "MSFT" in broker.get_positions()


def test_get_positions_parses_qty():
    broker = _make_broker()
    broker._session = _mock_session({"/positions/": [{"ticker": "MSFT", "position": 7.0, "mktValue": 2000.0}]})
    assert broker.get_positions()["MSFT"]["qty"] == pytest.approx(7.0)


# ── get_cash_usd ──────────────────────────────────────────────────────────────

def test_get_cash_usd_returns_float():
    broker = _make_broker()
    broker._session = _mock_session({"/ledger": {"USD": {"cashbalance": 5000.0}}})
    assert isinstance(broker.get_cash_usd(), float)


def test_get_cash_usd_parses_balance():
    broker = _make_broker()
    broker._session = _mock_session({"/ledger": {"USD": {"cashbalance": 12345.67}}})
    assert broker.get_cash_usd() == pytest.approx(12345.67)


# ── place_order ───────────────────────────────────────────────────────────────

def test_place_order_returns_order_result():
    from broker.base import OrderResult
    broker = _make_broker()
    broker._session = _mock_session({
        "/trsrv/stocks": _valid_conid_response("AAPL"),
        "/orders":        [{"order_id": "abc123", "price": 150.0}],
    })
    with patch("broker.ibkr.get_last_price", return_value=150.0):
        result = broker.place_order("AAPL", "buy", 750.0)
    assert isinstance(result, OrderResult)


def test_place_order_invalid_side_raises():
    broker = _make_broker()
    with pytest.raises(ValueError, match="side must be"):
        broker.place_order("AAPL", "hold", 750.0)


def test_place_order_negative_notional_raises():
    broker = _make_broker()
    with pytest.raises(ValueError, match="notional_usd must be positive"):
        broker.place_order("AAPL", "buy", -100.0)


def test_place_order_ticker_in_result():
    broker = _make_broker()
    broker._session = _mock_session({
        "/trsrv/stocks": _valid_conid_response("NVDA"),
        "/orders":        [{"order_id": "xyz", "price": 400.0}],
    })
    with patch("broker.ibkr.get_last_price", return_value=400.0):
        result = broker.place_order("NVDA", "buy", 1000.0)
    assert result.ticker == "NVDA"


def test_place_order_handles_order_reply_message():
    """An order reply message triggers a confirmation POST; result is the ack."""
    from broker.base import OrderResult
    broker = _make_broker()

    reply_msg  = [{"id": "reply-abc", "message": ["Price check"], "messageIds": ["o163"]}]
    reply_ack  = {"order_id": "789", "order_status": "Submitted"}
    all_resp   = {
        **_SESSION_STUBS,
        "/trsrv/stocks": _valid_conid_response("AAPL"),
        "/orders":        reply_msg,
        "/iserver/reply/": reply_ack,
    }
    broker._session = _mock_session(all_resp)
    with patch("broker.ibkr.get_last_price", return_value=180.0):
        result = broker.place_order("AAPL", "buy", 900.0)
    assert isinstance(result, OrderResult)
    assert result.order_id == "789"


# ── _ensure_brokerage_session ─────────────────────────────────────────────────

def test_ensure_brokerage_session_raises_when_not_authenticated():
    broker = _make_broker()
    broker._session = _mock_session({
        "/iserver/auth/status": {"authenticated": False, "competing": False},
    })
    with pytest.raises(RuntimeError, match="not authenticated"):
        broker._ensure_brokerage_session()


def test_ensure_brokerage_session_raises_when_competing():
    broker = _make_broker()
    broker._session = _mock_session({
        "/iserver/auth/status": {"authenticated": True, "competing": True},
    })
    with pytest.raises(RuntimeError, match="competing session"):
        broker._ensure_brokerage_session()


# ── _resolve_conid ────────────────────────────────────────────────────────────

def test_resolve_conid_raises_on_empty_response():
    broker = _make_broker()
    broker._session = _mock_session({"/trsrv/stocks": {}})
    with pytest.raises(RuntimeError, match="cannot resolve IBKR contract ID"):
        broker._resolve_conid("UNKNOWN")


def test_resolve_conid_raises_when_no_us_exchange():
    broker = _make_broker()
    response = {
        "BARC": [{
            "instrument_type": "STK",
            "contracts": [{"conid": 42, "exchange": "LSE"}],
        }]
    }
    broker._session = _mock_session({"/trsrv/stocks": response})
    with pytest.raises(RuntimeError):
        broker._resolve_conid("BARC")

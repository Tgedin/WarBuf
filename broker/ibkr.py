"""
IBKR Client Portal Web API broker.

Connects to the IBKR Client Portal Gateway (a local Java process / Docker container).
No third-party IBKR libraries — plain HTTP via requests.

Key protocol facts (from https://ibkrcampus.com/api/web-api-trading/):

  • Orders use `quantity` (share count), not `cashQty`.
    We compute qty = notional / yfinance_price before submitting.

  • Order submissions can return an "order reply message" (fat-finger warning)
    instead of a plain acknowledgment.  We confirm them via POST /iserver/reply/{id}.
    Common message IDs are suppressed at session start via /iserver/questions/suppress.

  • The gateway session must be kept alive with GET /tickle before each request batch.
    Without it the session expires after ~5 minutes of inactivity.

  • GET /iserver/auth/status verifies the session is connected and authenticated.
    If `competing: true` another session (e.g. TWS) is active — close it first.

  • GET /portfolio/accounts must be called once before any /portfolio/* endpoint.

  • IServer resets daily at ~01:00 local time — no impact for Monday 09:00 runs.

  • Global rate limit: 10 req/s.  Some endpoints have stricter limits
    (e.g. /iserver/orders GET: 1 req/5s).  Our order cadence is far below this.

  • SSL: the gateway uses a self-signed cert.  We disable verification for
    localhost connections only — never for a remote host.

Gateway setup: https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import requests
import urllib3

from broker.base import BrokerInterface, OrderResult
from core.fees import compute_fees
from core.market import get_last_price

logger = logging.getLogger(__name__)

# Suppress the InsecureRequestWarning for the self-signed localhost cert only.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_LOCALHOST_PREFIXES = ("https://localhost", "https://127.0.0.1")

# Maximum rounds of order reply confirmation before giving up.
_MAX_REPLY_ROUNDS = 5

# Order reply messageIds to suppress at session start.
# These cover the most common fat-finger warnings for MKT orders on liquid
# US equities traded in an automated context.
# Reference: https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/#order-reply-suppression-28
_SUPPRESS_MESSAGE_IDS = [
    "o163",  # price exceeds percentage constraint
    "o354",  # order size exceeds soft limit
    "o451",  # order may be marketable
    "o2137", # order submitted outside regular trading hours
]


class IBKRBroker(BrokerInterface):
    """
    Wraps the IBKR Client Portal Web API gateway.

    The gateway must be running and the user must be authenticated before any
    method on this class is called.  Session keepalive, portfolio pre-call, and
    order reply confirmation are handled transparently.
    """

    def __init__(
        self,
        gateway_url: str | None = None,
        account_id: str | None = None,
        stop_loss_pct: float = 15.0,
    ) -> None:
        self._base    = (gateway_url or os.environ["IBKR_GATEWAY_URL"]).rstrip("/")
        self._account = account_id or os.environ["IBKR_ACCOUNT_ID"]
        self._stop_loss_pct = stop_loss_pct

        is_localhost = any(self._base.startswith(p) for p in _LOCALHOST_PREFIXES)
        self._session = requests.Session()
        self._session.verify = False if is_localhost else True

        # Track one-time-per-session calls.
        self._portfolio_accounts_fetched  = False
        self._reply_messages_suppressed   = False

    # ── BrokerInterface ───────────────────────────────────────────────────────

    def get_positions(self) -> dict[str, dict]:
        self._tickle()
        self._ensure_portfolio_accounts()
        data = self._get(f"/v1/api/portfolio/{self._account}/positions/0")
        result: dict[str, dict] = {}
        for item in data:
            ticker = item.get("ticker") or item.get("contractDesc", "")
            result[ticker] = {
                "qty":              float(item.get("position", 0)),
                "market_value_usd": float(item.get("mktValue", 0)),
            }
        return result

    def get_cash_usd(self) -> float:
        self._tickle()
        self._ensure_portfolio_accounts()
        data = self._get(f"/v1/api/portfolio/{self._account}/ledger")
        usd_ledger = data.get("USD", {})
        return float(usd_ledger.get("cashbalance", 0))

    def place_order(self, ticker: str, side: str, notional_usd: float) -> OrderResult:
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got '{side}'")
        if notional_usd <= 0:
            raise ValueError(f"notional_usd must be positive, got {notional_usd}")

        self._tickle()
        self._ensure_brokerage_session()
        self._suppress_order_reply_messages()

        conid = self._resolve_conid(ticker)
        price = get_last_price(ticker)
        qty   = round(notional_usd / price, 4)

        payload = {
            "orders": [{
                "conid":     conid,
                "orderType": "MKT",
                "side":      side.upper(),
                "tif":       "DAY",
                "quantity":  qty,
            }]
        }

        response   = self._post(f"/v1/api/iserver/account/{self._account}/orders", payload)
        order_data = self._confirm_order_replies(response)

        order_id     = str(order_data.get("order_id", uuid.uuid4().hex[:8]))
        # Fall back to the yfinance pre-price if the gateway doesn't echo the fill.
        filled_price = float(order_data.get("price", price))
        fees         = compute_fees(side, qty, notional_usd)

        logger.info(
            "IBKRBroker.place_order: %s %s %.4f shares @ %.2f USD (order_id=%s, fees=%.4f)",
            side.upper(), ticker, qty, filled_price, order_id, fees,
        )

        # Submit a server-side GTC stop-loss order immediately after a buy so that
        # IBKR will execute the stop intraday even if the bot is offline.
        if side == "buy":
            self._submit_stop_loss(ticker, conid, qty, filled_price)

        return OrderResult(
            ticker=ticker,
            side=side,
            qty=qty,
            filled_price_usd=filled_price,
            order_id=order_id,
        )

    # ── Session management ────────────────────────────────────────────────────

    def _submit_stop_loss(self, ticker: str, conid: int, qty: float, fill_price: float) -> None:
        """Submit a GTC stop order at fill_price * (1 - stop_loss_pct/100) after a buy.

        The order is held on IBKR's servers so the stop executes intraday even
        if the bot is down between weekly checks.
        Failures are logged as warnings — a failed stop order must not block the
        buy record from being committed.
        """
        stop_price = round(fill_price * (1.0 - self._stop_loss_pct / 100.0), 2)
        payload = {
            "orders": [{
                "conid":     conid,
                "orderType": "STP",
                "side":      "SELL",
                "tif":       "GTC",
                "quantity":  qty,
                "price":     stop_price,
            }]
        }
        try:
            response   = self._post(f"/v1/api/iserver/account/{self._account}/orders", payload)
            order_data = self._confirm_order_replies(response)
            logger.info(
                "IBKRBroker: STP order submitted for %s — stop @ %.2f USD (order_id=%s)",
                ticker, stop_price, order_data.get("order_id", "unknown"),
            )
        except Exception as exc:
            logger.warning(
                "IBKRBroker: failed to submit STP order for %s @ %.2f: %s",
                ticker, stop_price, exc,
            )

    # ── Session management ────────────────────────────────────────────────────

    def _tickle(self) -> None:
        """
        Heartbeat to prevent the gateway session from expiring.
        Call this before any batch of API requests.
        Raises RuntimeError if the gateway is unreachable after two attempts.
        """
        for attempt in range(2):
            try:
                self._get("/v1/api/tickle")
                return
            except Exception as exc:
                logger.warning("IBKRBroker._tickle attempt %d failed: %s", attempt + 1, exc)
        raise RuntimeError(
            "IBKRBroker: gateway did not respond to keepalive after 2 attempts. "
            "Ensure the IBKR CP Gateway is running at: %s" % self._base
        )

    def _ensure_brokerage_session(self) -> None:
        """
        Verify the brokerage session is authenticated and not competing.
        Raises RuntimeError if the session is not ready for trading.
        """
        status = self._get("/v1/api/iserver/auth/status")

        if not status.get("authenticated"):
            raise RuntimeError(
                "IBKRBroker: gateway session is not authenticated. "
                "Log in via https://localhost:5000 and try again."
            )
        if status.get("competing"):
            raise RuntimeError(
                "IBKRBroker: a competing session is active for this username. "
                "Close Trader Workstation or the other session before using the API."
            )

        logger.info(
            "IBKRBroker: brokerage session ready (authenticated=True, competing=False, "
            "connected=%s, established=%s)",
            status.get("connected"),
            status.get("authenticated"),
        )

    def _ensure_portfolio_accounts(self) -> None:
        """
        Call GET /portfolio/accounts once before any /portfolio/* endpoint.
        Required by IBKR — portfolio endpoints return empty data without it.
        """
        if self._portfolio_accounts_fetched:
            return
        self._get("/v1/api/portfolio/accounts")
        self._portfolio_accounts_fetched = True

    def _suppress_order_reply_messages(self) -> None:
        """
        Suppress common fat-finger order reply messages for this brokerage session
        so that automated MKT orders on liquid US equities are not blocked.
        Must be called after _ensure_brokerage_session(), once per session.
        """
        if self._reply_messages_suppressed:
            return
        self._post(
            "/v1/api/iserver/questions/suppress",
            {"messageIds": _SUPPRESS_MESSAGE_IDS},
        )
        self._reply_messages_suppressed = True
        logger.info(
            "IBKRBroker: suppressed order reply message IDs for this session: %s",
            _SUPPRESS_MESSAGE_IDS,
        )

    # ── Order reply handling ──────────────────────────────────────────────────

    def _confirm_order_replies(self, response: Any) -> dict:
        """
        After an order submission IBKR may respond with one or more "order reply
        messages" that require explicit confirmation before the order is accepted.

        This method loops until we receive a plain order acknowledgment or
        exhaust _MAX_REPLY_ROUNDS attempts.

        Order reply shape:     [{"id": "...", "message": [...], "messageIds": [...]}]
        Acknowledgment shape:  {"order_id": "...", "order_status": "..."}
        """
        data = response[0] if isinstance(response, list) else response

        for attempt in range(_MAX_REPLY_ROUNDS):
            if "order_id" in data:
                return data  # acknowledgment received

            if "id" not in data:
                raise RuntimeError(
                    f"IBKRBroker._confirm_order_replies: unexpected response "
                    f"at attempt {attempt + 1}: {data}"
                )

            message_id   = data["id"]
            message_text = " | ".join(data.get("message", []))
            logger.warning(
                "IBKRBroker: order reply message (attempt %d/%d): id=%s  msg=%r",
                attempt + 1, _MAX_REPLY_ROUNDS, message_id, message_text,
            )

            confirm = self._post(
                f"/v1/api/iserver/reply/{message_id}",
                {"confirmed": True},
            )
            data = confirm[0] if isinstance(confirm, list) else confirm

        raise RuntimeError(
            f"IBKRBroker._confirm_order_replies: exceeded {_MAX_REPLY_ROUNDS} confirmation "
            "rounds without receiving an order acknowledgment. "
            "Add the relevant messageIds to _SUPPRESS_MESSAGE_IDS."
        )

    # ── Contract resolution ───────────────────────────────────────────────────

    def _resolve_conid(self, ticker: str) -> int:
        """Resolve a ticker symbol to an IBKR contract ID (conid) for US equities."""
        data = self._get(f"/v1/api/trsrv/stocks?symbols={ticker}")
        try:
            contracts = data[ticker]
            us_contract = next(
                c for c in contracts
                if c.get("instrument_type") == "STK"
                and any(
                    ex.get("exchange") in ("NASDAQ", "NYSE")
                    for ex in c.get("contracts", [])
                )
            )
            return int(us_contract["contracts"][0]["conid"])
        except (KeyError, StopIteration, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"IBKRBroker._resolve_conid: cannot resolve IBKR contract ID "
                f"for {ticker!r}: {exc}"
            ) from exc

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get(self, path: str) -> dict | list:
        resp = self._session.get(f"{self._base}{path}", timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict) -> dict | list:
        resp = self._session.post(f"{self._base}{path}", json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

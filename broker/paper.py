"""
Paper broker — simulates execution by writing to portfolio.db.
No real orders. No external API calls beyond fetching the last close price.

Use this during the mandatory paper trading phase before going live.
"""
from __future__ import annotations

import uuid

import yfinance as yf

from broker.base import BrokerInterface, OrderResult
from core.fees import compute_fees
from db import Database


class PaperBroker(BrokerInterface):
    """Simulates trade execution. All orders are logged to the database."""

    def __init__(self, db: Database, eur_usd_rate: float = 1.0) -> None:
        self._db = db
        self._eur_usd_rate = eur_usd_rate

    def get_positions(self) -> dict[str, dict]:
        return self._db.get_positions()

    def get_cash_usd(self) -> float:
        return self._db.get_cash_eur() * self._eur_usd_rate

    def place_order(self, ticker: str, side: str, notional_usd: float) -> OrderResult:
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got '{side}'")
        if notional_usd <= 0:
            raise ValueError(f"notional_usd must be positive, got {notional_usd}")

        price = self._last_close_price(ticker)
        qty = notional_usd / price
        fees = compute_fees(side, qty, notional_usd)
        net = notional_usd + fees.total_usd if side == "buy" else notional_usd - fees.total_usd
        order_id = f"PAPER-{uuid.uuid4().hex[:8].upper()}"

        self._db.record_trade(
            ticker=ticker,
            side=side,
            qty=qty,
            price_usd=price,
            fees_usd=fees.total_usd,
            net_cost_basis=net,
            ibkr_order_id=order_id,
            eur_usd_rate=self._eur_usd_rate,
        )

        print(
            f"[PAPER] {side.upper():4} {qty:.4f} {ticker} "
            f"@ ${price:.2f}  fees=${fees.total_usd:.4f}  id={order_id}"
        )
        return OrderResult(
            ticker=ticker,
            side=side,
            qty=qty,
            filled_price_usd=price,
            order_id=order_id,
        )

    @staticmethod
    def _last_close_price(ticker: str) -> float:
        hist = yf.Ticker(ticker).history(period="2d")
        if hist.empty:
            raise RuntimeError(f"Cannot fetch price for {ticker}")
        return float(hist["Close"].iloc[-1])

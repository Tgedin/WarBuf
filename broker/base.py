"""Abstract broker interface. All concrete brokers implement this contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class OrderResult:
    ticker: str
    side: str             # "buy" | "sell"
    qty: float
    filled_price_usd: float
    order_id: str


class BrokerInterface(ABC):
    @abstractmethod
    def get_positions(self) -> dict[str, dict]:
        """Returns {ticker: {qty, avg_cost_basis, ...}} from the broker."""

    @abstractmethod
    def place_order(self, ticker: str, side: str, notional_usd: float) -> OrderResult:
        """Place a fractional share order by notional USD amount."""

    @abstractmethod
    def get_cash_usd(self) -> float:
        """Current cash balance in USD."""

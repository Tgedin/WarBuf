"""
IBKR fee calculator. All values are in USD. Pure functions, no I/O.

Sources:
  SEC fee   — sec.gov Section 31 fee rate
  FINRA TAF — finra.org trading activity fee
  IBKR PRO  — interactivebrokers.com/en/trading/commissions.php
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Fee constants ─────────────────────────────────────────────────────────────
# Update these when regulators announce rate changes.

SEC_FEE_RATE = 0.000008              # per $1 of notional — sells only
FINRA_TAF_RATE_PER_SHARE = 0.000166  # per share — sells only
FINRA_TAF_MAX_USD = 8.30             # cap per order
IBKR_PRO_RATE_PER_SHARE = 0.0035    # per share
IBKR_PRO_MIN_USD = 0.35             # minimum per order
IBKR_PRO_MAX_PCT = 0.01             # 1% of trade value cap


@dataclass(frozen=True)
class TradeFees:
    commission_usd: float
    regulatory_usd: float   # SEC + FINRA (0 for buys)
    total_usd: float


def compute_fees(side: str, shares: float, notional_usd: float) -> TradeFees:
    """
    Compute all-in IBKR PRO fees for a single order.

    Args:
        side:         "buy" or "sell"
        shares:       number of shares (fractional OK)
        notional_usd: total trade value in USD (shares × price)
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got '{side}'")
    if shares <= 0:
        raise ValueError(f"shares must be positive, got {shares}")
    if notional_usd <= 0:
        raise ValueError(f"notional_usd must be positive, got {notional_usd}")

    # Commission: $0.0035/share, min $0.35, max 1% of trade value
    commission = max(shares * IBKR_PRO_RATE_PER_SHARE, IBKR_PRO_MIN_USD)
    commission = min(commission, notional_usd * IBKR_PRO_MAX_PCT)

    # Regulatory fees apply only to sells
    if side == "sell":
        sec_fee = notional_usd * SEC_FEE_RATE
        finra_fee = min(shares * FINRA_TAF_RATE_PER_SHARE, FINRA_TAF_MAX_USD)
        regulatory = sec_fee + finra_fee
    else:
        regulatory = 0.0

    total = commission + regulatory
    return TradeFees(
        commission_usd=round(commission, 6),
        regulatory_usd=round(regulatory, 6),
        total_usd=round(total, 6),
    )


def net_proceeds(side: str, shares: float, price_usd: float) -> tuple[float, TradeFees]:
    """
    Compute net amount and fees for a completed order.

    Returns (net_amount_usd, fees):
      buy:  net_amount = notional + fees  (total cost)
      sell: net_amount = notional - fees  (actual proceeds)
    """
    notional = shares * price_usd
    fees = compute_fees(side, shares, notional)
    if side == "buy":
        return round(notional + fees.total_usd, 6), fees
    return round(notional - fees.total_usd, 6), fees

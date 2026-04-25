"""
3-tier screening pipeline.

Tier 1: Hard filters   — instant, free, no LLM
Tier 2: Factor scoring — computed, no LLM, top N kept
Tier 3: LLM veto       — caller's responsibility (see core/agent.py)

The pipeline is the only place that touches market.py (I/O boundary).
scorer.py and fees.py stay pure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from core.market import get_fundamentals, get_momentum
from core.scorer import Fundamentals, FactorWeights, ScoredTicker, score_watchlist


@dataclass(frozen=True)
class HardFilters:
    min_market_cap_b: float
    max_pe_ratio: float
    min_revenue_growth_pct: float   # expressed as %, e.g. 5 = 5%
    max_debt_to_equity: float       # expressed as %, e.g. 150 = 150%
    sectors_excluded: list[str]


def passes_hard_filters(f: Fundamentals, filters: HardFilters) -> bool:
    """Return True if the ticker clears all Tier 1 hard filters. Fail fast."""
    if f.market_cap_b is not None and f.market_cap_b < filters.min_market_cap_b:
        return False

    # earnings_yield = 1/PE → PE = 1/earnings_yield
    if f.earnings_yield is not None and f.earnings_yield > 0:
        pe = 1.0 / f.earnings_yield
        if pe > filters.max_pe_ratio:
            return False

    if (
        f.revenue_growth is not None
        and f.revenue_growth < filters.min_revenue_growth_pct / 100.0
    ):
        return False

    if f.debt_to_equity is not None and f.debt_to_equity > filters.max_debt_to_equity:
        return False

    if f.sector and f.sector in filters.sectors_excluded:
        return False

    return True


def _build_fundamentals(
    ticker: str,
    momentum_lookback_days: int,
    momentum_skip_days: int,
) -> Fundamentals:
    """Fetch and merge static fundamentals + momentum for one ticker."""
    f = get_fundamentals(ticker)
    momentum = get_momentum(ticker, momentum_lookback_days, momentum_skip_days)
    return Fundamentals(
        ticker=f.ticker,
        roe=f.roe,
        fcf_margin=f.fcf_margin,
        debt_to_equity=f.debt_to_equity,
        earnings_yield=f.earnings_yield,
        gross_profit_to_assets=f.gross_profit_to_assets,
        momentum_12_1=momentum,
        market_cap_b=f.market_cap_b,
        revenue_growth=f.revenue_growth,
        sector=f.sector,
    )


def run_tier1_tier2(
    watchlist: Sequence[str],
    filters: HardFilters,
    weights: FactorWeights,
    momentum_lookback_days: int = 365,
    momentum_skip_days: int = 30,
    top_n: int = 5,
) -> tuple[list[ScoredTicker], list[str]]:
    """
    Run Tier 1 (hard filters) then Tier 2 (factor scoring).

    Returns:
        (top_n_scored, rejected_tickers)

    Tickers that fail data fetching are added to rejected.
    Tickers that fail hard filters are added to rejected.
    """
    rejected: list[str] = []
    passed: list[Fundamentals] = []

    for ticker in watchlist:
        try:
            f = _build_fundamentals(ticker, momentum_lookback_days, momentum_skip_days)
        except Exception as exc:
            print(f"[SCREENER] Data fetch failed for {ticker}: {exc}")
            rejected.append(ticker)
            continue

        if passes_hard_filters(f, filters):
            passed.append(f)
        else:
            rejected.append(ticker)

    if not passed:
        return [], rejected

    scored = score_watchlist(passed, weights)
    return scored[:top_n], rejected

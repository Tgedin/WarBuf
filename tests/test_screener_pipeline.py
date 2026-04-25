"""Tests for core/screener.py — run_tier1_tier2 pipeline.

Mocks market.py I/O so the pipeline logic is tested without network calls.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.scorer import Fundamentals, FactorWeights
from core.screener import HardFilters, run_tier1_tier2

WEIGHTS = FactorWeights(quality=0.35, value=0.25, momentum=0.25, profitability=0.15)

FILTERS = HardFilters(
    min_market_cap_b=1.0,
    max_pe_ratio=50.0,
    min_revenue_growth_pct=0.0,
    max_debt_to_equity=200.0,
    sectors_excluded=[],
)


def _fundamentals(ticker: str, **overrides) -> Fundamentals:
    defaults = dict(
        ticker=ticker,
        roe=0.20, fcf_margin=0.15, debt_to_equity=50.0,
        earnings_yield=0.04, gross_profit_to_assets=0.25,
        momentum_12_1=0.12, market_cap_b=10.0,
        revenue_growth=0.10, sector="Technology",
    )
    defaults.update(overrides)
    return Fundamentals(**defaults)


def _patch_market(fundamentals_map: dict[str, Fundamentals], momentum: float = 0.1):
    """Patch market.py so run_tier1_tier2 uses provided fundamentals."""
    def fake_fundamentals(ticker):
        return fundamentals_map[ticker]

    return (
        patch("core.screener.get_fundamentals", side_effect=fake_fundamentals),
        patch("core.screener.get_momentum", return_value=momentum),
    )


# ── run_tier1_tier2 ───────────────────────────────────────────────────────────

def test_pipeline_returns_passing_candidates():
    f = {"AAPL": _fundamentals("AAPL")}
    p1, p2 = _patch_market(f)
    with p1, p2:
        top, rejected = run_tier1_tier2(["AAPL"], FILTERS, WEIGHTS)
    assert any(s.ticker == "AAPL" for s in top)


def test_pipeline_rejects_filtered_ticker():
    # market cap below filter minimum
    f = {"AAPL": _fundamentals("AAPL", market_cap_b=0.1)}
    p1, p2 = _patch_market(f)
    with p1, p2:
        top, rejected = run_tier1_tier2(["AAPL"], FILTERS, WEIGHTS)
    assert "AAPL" in rejected


def test_pipeline_respects_top_n():
    tickers = [f"T{i}" for i in range(10)]
    f_map = {t: _fundamentals(t) for t in tickers}
    p1, p2 = _patch_market(f_map)
    with p1, p2:
        top, _ = run_tier1_tier2(tickers, FILTERS, WEIGHTS, top_n=3)
    assert len(top) <= 3


def test_pipeline_empty_watchlist_returns_empty():
    top, rejected = run_tier1_tier2([], FILTERS, WEIGHTS)
    assert top == []
    assert rejected == []


def test_pipeline_data_fetch_failure_goes_to_rejected():
    with (
        patch("core.screener.get_fundamentals", side_effect=RuntimeError("network")),
        patch("core.screener.get_momentum", return_value=0.1),
    ):
        top, rejected = run_tier1_tier2(["FAIL"], FILTERS, WEIGHTS)
    assert "FAIL" in rejected


def test_pipeline_results_sorted_descending():
    # Give ticker A a much better score than B
    f_map = {
        "A": _fundamentals("A", roe=0.50, fcf_margin=0.40, earnings_yield=0.08),
        "B": _fundamentals("B", roe=0.01, fcf_margin=0.01, earnings_yield=0.01),
    }
    p1, p2 = _patch_market(f_map)
    with p1, p2:
        top, _ = run_tier1_tier2(["A", "B"], FILTERS, WEIGHTS)
    assert top[0].score >= top[-1].score

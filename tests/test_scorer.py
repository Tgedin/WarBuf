"""Tests for core/scorer.py. Tests behavior, not implementation."""
import math
import pytest

from core.scorer import (
    Fundamentals,
    FactorWeights,
    _cross_rank,
    score_watchlist,
)

DEFAULT_WEIGHTS = FactorWeights(quality=0.35, value=0.25, momentum=0.25, profitability=0.15)


def make_f(
    ticker: str = "TEST",
    roe: float = 0.20,
    fcf: float = 0.15,
    de: float = 50.0,
    ey: float = 0.04,     # P/E = 25
    gpa: float = 0.25,
    mom: float = 0.12,
    mc: float = 20.0,
    rg: float = 0.10,
    sector: str = "Technology",
) -> Fundamentals:
    return Fundamentals(
        ticker=ticker,
        roe=roe,
        fcf_margin=fcf,
        debt_to_equity=de,
        earnings_yield=ey,
        gross_profit_to_assets=gpa,
        momentum_12_1=mom,
        market_cap_b=mc,
        revenue_growth=rg,
        sector=sector,
    )


# ── FactorWeights ─────────────────────────────────────────────────────────────

class TestFactorWeights:
    def test_valid_weights_accepted(self):
        w = FactorWeights(quality=0.35, value=0.25, momentum=0.25, profitability=0.15)
        assert math.isclose(w.quality + w.value + w.momentum + w.profitability, 1.0)

    def test_weights_not_summing_to_one_raise(self):
        with pytest.raises(ValueError, match="sum to 1.0"):
            FactorWeights(quality=0.4, value=0.4, momentum=0.4, profitability=0.4)

    def test_weights_are_immutable(self):
        w = FactorWeights(quality=0.35, value=0.25, momentum=0.25, profitability=0.15)
        with pytest.raises(Exception):
            w.quality = 0.5  # type: ignore[misc]


# ── _cross_rank ───────────────────────────────────────────────────────────────

class TestCrossRank:
    def test_two_values_lowest_gets_zero_highest_gets_one(self):
        ranks = _cross_rank([10.0, 20.0])
        assert ranks[0] == pytest.approx(0.0)
        assert ranks[1] == pytest.approx(1.0)

    def test_three_values_middle_gets_half(self):
        ranks = _cross_rank([1.0, 2.0, 3.0])
        assert ranks[1] == pytest.approx(0.5)

    def test_none_receives_zero_rank(self):
        ranks = _cross_rank([None, 10.0, 20.0])
        assert ranks[0] == 0.0
        assert ranks[2] > ranks[1]

    def test_single_valid_value_gets_midpoint(self):
        ranks = _cross_rank([5.0])
        assert ranks[0] == pytest.approx(0.5)

    def test_all_none_returns_all_zeros(self):
        assert _cross_rank([None, None, None]) == [0.0, 0.0, 0.0]

    def test_empty_input_returns_empty(self):
        assert _cross_rank([]) == []

    def test_nan_treated_as_none(self):
        ranks = _cross_rank([float("nan"), 5.0])
        assert ranks[0] == 0.0
        assert ranks[1] == pytest.approx(0.5)


# ── score_watchlist ───────────────────────────────────────────────────────────

class TestScoreWatchlist:
    def test_strong_ticker_scores_higher_than_weak(self):
        weak   = make_f("WEAK",   roe=0.01, fcf=0.01, mom=-0.30, ey=0.01, gpa=0.01)
        strong = make_f("STRONG", roe=0.50, fcf=0.40, mom=0.50,  ey=0.08, gpa=0.50)
        results = score_watchlist([weak, strong], DEFAULT_WEIGHTS)
        assert results[0].ticker == "STRONG"
        assert results[0].score > results[1].score

    def test_results_sorted_descending(self):
        tickers = [make_f(f"T{i}", roe=float(i) * 0.05) for i in range(5)]
        results = score_watchlist(tickers, DEFAULT_WEIGHTS)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_all_scores_in_0_1_range(self):
        tickers = [make_f(f"T{i}") for i in range(6)]
        for r in score_watchlist(tickers, DEFAULT_WEIGHTS):
            assert 0.0 <= r.score <= 1.0

    def test_empty_input_returns_empty(self):
        assert score_watchlist([], DEFAULT_WEIGHTS) == []

    def test_single_ticker_returns_neutral_score(self):
        results = score_watchlist([make_f("SOLO")], DEFAULT_WEIGHTS)
        assert len(results) == 1
        assert results[0].ticker == "SOLO"
        # single ticker gets 0.5 on every ranked factor
        assert results[0].score == pytest.approx(0.5)

    def test_all_none_fields_does_not_crash(self):
        null_f = Fundamentals(
            ticker="NULL",
            roe=None, fcf_margin=None, debt_to_equity=None,
            earnings_yield=None, gross_profit_to_assets=None,
            momentum_12_1=None, market_cap_b=None,
            revenue_growth=None, sector=None,
        )
        results = score_watchlist([null_f, make_f("GOOD")], DEFAULT_WEIGHTS)
        assert len(results) == 2
        # GOOD should beat NULL
        assert results[0].ticker == "GOOD"

    def test_result_contains_individual_factor_ranks(self):
        results = score_watchlist([make_f("A"), make_f("B")], DEFAULT_WEIGHTS)
        for r in results:
            assert 0.0 <= r.quality_rank <= 1.0
            assert 0.0 <= r.value_rank <= 1.0
            assert 0.0 <= r.momentum_rank <= 1.0
            assert 0.0 <= r.profitability_rank <= 1.0

    def test_high_debt_penalises_quality(self):
        low_debt  = make_f("LOW_D",  de=20.0)
        high_debt = make_f("HIGH_D", de=180.0)
        results = score_watchlist([low_debt, high_debt], DEFAULT_WEIGHTS)
        # low debt should have higher quality rank
        low_d_result  = next(r for r in results if r.ticker == "LOW_D")
        high_d_result = next(r for r in results if r.ticker == "HIGH_D")
        assert low_d_result.quality_rank > high_d_result.quality_rank

"""Tests for core/screener.py — Tier 1 hard filters (pure logic, no I/O)."""

from core.scorer import Fundamentals
from core.screener import HardFilters, passes_hard_filters

FILTERS = HardFilters(
    min_market_cap_b=5.0,
    max_pe_ratio=40.0,
    min_revenue_growth_pct=5.0,
    max_debt_to_equity=150.0,
    sectors_excluded=["Leveraged ETFs", "Crypto"],
)


def make_f(**kwargs) -> Fundamentals:
    defaults = dict(
        ticker="TEST",
        roe=0.20,
        fcf_margin=0.15,
        debt_to_equity=50.0,
        earnings_yield=0.04,   # P/E = 25
        gross_profit_to_assets=0.25,
        momentum_12_1=0.12,
        market_cap_b=20.0,
        revenue_growth=0.10,
        sector="Technology",
    )
    defaults.update(kwargs)
    return Fundamentals(**defaults)


class TestPassesHardFilters:
    def test_all_good_values_pass(self):
        assert passes_hard_filters(make_f(), FILTERS) is True

    def test_market_cap_below_min_rejected(self):
        assert passes_hard_filters(make_f(market_cap_b=1.0), FILTERS) is False

    def test_market_cap_exactly_at_min_passes(self):
        assert passes_hard_filters(make_f(market_cap_b=5.0), FILTERS) is True

    def test_pe_above_max_rejected(self):
        # earnings_yield = 1/50 = 0.02 → P/E = 50 > 40
        assert passes_hard_filters(make_f(earnings_yield=0.02), FILTERS) is False

    def test_pe_exactly_at_max_passes(self):
        # earnings_yield = 1/40 = 0.025 → P/E = 40
        assert passes_hard_filters(make_f(earnings_yield=0.025), FILTERS) is True

    def test_revenue_growth_below_min_rejected(self):
        # 3% < 5% minimum
        assert passes_hard_filters(make_f(revenue_growth=0.03), FILTERS) is False

    def test_revenue_growth_exactly_at_min_passes(self):
        assert passes_hard_filters(make_f(revenue_growth=0.05), FILTERS) is True

    def test_debt_to_equity_above_max_rejected(self):
        assert passes_hard_filters(make_f(debt_to_equity=200.0), FILTERS) is False

    def test_debt_to_equity_at_max_passes(self):
        assert passes_hard_filters(make_f(debt_to_equity=150.0), FILTERS) is True

    def test_excluded_sector_rejected(self):
        assert passes_hard_filters(make_f(sector="Crypto"), FILTERS) is False

    def test_non_excluded_sector_passes(self):
        assert passes_hard_filters(make_f(sector="Healthcare"), FILTERS) is True

    def test_none_market_cap_not_rejected(self):
        # Missing data should not penalise — we can't know
        assert passes_hard_filters(make_f(market_cap_b=None), FILTERS) is True

    def test_none_revenue_growth_not_rejected(self):
        assert passes_hard_filters(make_f(revenue_growth=None), FILTERS) is True

    def test_none_debt_to_equity_not_rejected(self):
        assert passes_hard_filters(make_f(debt_to_equity=None), FILTERS) is True

    def test_none_earnings_yield_not_rejected_on_pe(self):
        # If PE unknown, don't reject
        assert passes_hard_filters(make_f(earnings_yield=None), FILTERS) is True

    def test_none_sector_not_rejected(self):
        assert passes_hard_filters(make_f(sector=None), FILTERS) is True

    def test_negative_pe_not_rejected(self):
        # Negative PE (loss-making) doesn't trigger the PE filter (earnings_yield <= 0)
        assert passes_hard_filters(make_f(earnings_yield=-0.02), FILTERS) is True

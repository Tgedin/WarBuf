"""Tests for core/fees.py."""
import pytest

from core.fees import (
    IBKR_PRO_MAX_PCT,
    IBKR_PRO_MIN_USD,
    compute_fees,
    net_proceeds,
)


class TestComputeFees:
    def test_buy_has_no_regulatory_fees(self):
        fees = compute_fees("buy", 10.0, 1_000.0)
        assert fees.regulatory_usd == 0.0

    def test_sell_has_positive_regulatory_fees(self):
        fees = compute_fees("sell", 10.0, 1_000.0)
        assert fees.regulatory_usd > 0.0

    def test_commission_is_at_least_minimum(self):
        # 1 share × $0.0035 = $0.0035 < $0.35 minimum
        # notional=$100 keeps 1%-cap ($1.00) above the minimum ($0.35), so minimum governs
        fees = compute_fees("buy", 1.0, 100.0)
        assert fees.commission_usd == pytest.approx(IBKR_PRO_MIN_USD)

    def test_commission_capped_at_1_pct_of_notional(self):
        # 100 000 shares × $0.0035 = $350, but 1% of $1 000 = $10
        fees = compute_fees("buy", 100_000.0, 1_000.0)
        assert fees.commission_usd <= 1_000.0 * IBKR_PRO_MAX_PCT + 1e-9

    def test_total_equals_sum_of_components(self):
        fees = compute_fees("sell", 50.0, 3_000.0)
        assert fees.total_usd == pytest.approx(fees.commission_usd + fees.regulatory_usd)

    def test_invalid_side_raises_value_error(self):
        with pytest.raises(ValueError, match="side must be"):
            compute_fees("hold", 10.0, 1_000.0)

    def test_zero_shares_raises_value_error(self):
        with pytest.raises(ValueError, match="positive"):
            compute_fees("buy", 0.0, 1_000.0)

    def test_negative_shares_raises_value_error(self):
        with pytest.raises(ValueError, match="positive"):
            compute_fees("buy", -5.0, 1_000.0)

    def test_zero_notional_raises_value_error(self):
        with pytest.raises(ValueError, match="positive"):
            compute_fees("buy", 10.0, 0.0)

    def test_fractional_shares_accepted(self):
        fees = compute_fees("buy", 0.5, 100.0)
        assert fees.total_usd > 0

    def test_fees_are_non_negative(self):
        for side in ("buy", "sell"):
            fees = compute_fees(side, 10.0, 500.0)
            assert fees.commission_usd >= 0
            assert fees.regulatory_usd >= 0
            assert fees.total_usd >= 0


class TestNetProceeds:
    def test_buy_cost_is_notional_plus_fees(self):
        net, fees = net_proceeds("buy", 10.0, 100.0)
        assert net == pytest.approx(1_000.0 + fees.total_usd)

    def test_sell_proceeds_is_notional_minus_fees(self):
        net, fees = net_proceeds("sell", 10.0, 100.0)
        assert net == pytest.approx(1_000.0 - fees.total_usd)

    def test_sell_proceeds_always_less_than_notional(self):
        net, _ = net_proceeds("sell", 20.0, 50.0)
        assert net < 20.0 * 50.0

    def test_buy_cost_always_more_than_notional(self):
        net, _ = net_proceeds("buy", 20.0, 50.0)
        assert net > 20.0 * 50.0

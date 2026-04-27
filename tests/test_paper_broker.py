"""Tests for broker/paper.py using an in-memory SQLite database."""
import pytest
from unittest.mock import patch

from broker.paper import PaperBroker
from db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    yield d
    d.close()


@pytest.fixture
def broker(db):
    return PaperBroker(db)


class TestPaperBroker:
    def test_buy_records_position(self, broker, db):
        # fill_price = 100.0 * 1.001 = 100.1; qty = int(500 / 100.1) = 4
        with patch.object(PaperBroker, "_last_close_price", return_value=100.0):
            result = broker.place_order("AAPL", "buy", 500.0)

        assert result.ticker == "AAPL"
        assert result.side == "buy"
        assert result.qty == 4
        assert result.order_id.startswith("PAPER-")

        positions = db.get_positions()
        assert "AAPL" in positions
        assert positions["AAPL"]["qty"] == 4

    def test_second_buy_averages_cost_basis(self, broker, db):
        # buy1: fill=100.1, qty=int(500/100.1)=4
        # buy2: fill=200.2, qty=int(400/200.2)=1 → total 5 shares
        with patch.object(PaperBroker, "_last_close_price", return_value=100.0):
            broker.place_order("AAPL", "buy", 500.0)
        with patch.object(PaperBroker, "_last_close_price", return_value=200.0):
            broker.place_order("AAPL", "buy", 400.0)

        positions = db.get_positions()
        assert positions["AAPL"]["qty"] == 5
        assert positions["AAPL"]["avg_cost_basis"] == pytest.approx(
            (4 * 100.1 + 1 * 200.2) / 5, rel=1e-3
        )

    def test_sell_reduces_position(self, broker, db):
        # buy: fill=100.1, qty=int(1000/100.1)=9
        # sell: fill=99.9,  qty=int(500/99.9)=5  → 4 remaining
        with patch.object(PaperBroker, "_last_close_price", return_value=100.0):
            broker.place_order("AAPL", "buy", 1_000.0)
            broker.place_order("AAPL", "sell", 500.0)

        assert db.get_positions()["AAPL"]["qty"] == 4

    def test_sell_all_removes_position(self, broker, db):
        with patch.object(PaperBroker, "_last_close_price", return_value=100.0):
            broker.place_order("AAPL", "buy", 500.0)
            broker.place_order("AAPL", "sell", 500.0)

        assert "AAPL" not in db.get_positions()

    def test_fees_accumulated_in_position(self, broker, db):
        with patch.object(PaperBroker, "_last_close_price", return_value=100.0):
            broker.place_order("MSFT", "buy", 500.0)

        fees_paid = db.get_positions()["MSFT"]["total_fees_paid"]
        assert fees_paid > 0

    def test_invalid_side_raises(self, broker):
        with pytest.raises(ValueError, match="side must be"):
            broker.place_order("AAPL", "hold", 500.0)

    def test_negative_notional_raises(self, broker):
        with pytest.raises(ValueError, match="positive"):
            broker.place_order("AAPL", "buy", -100.0)

    def test_get_cash_usd_returns_zero_before_seed(self, broker):
        assert broker.get_cash_usd() == pytest.approx(0.0)

    def test_get_cash_usd_reflects_seeded_amount(self, broker, db):
        db.seed_cash(3000.0)
        # PaperBroker default eur_usd_rate=1.0
        assert broker.get_cash_usd() == pytest.approx(3000.0)

    def test_buy_reduces_cash(self, broker, db):
        db.seed_cash(3000.0)
        with patch.object(PaperBroker, "_last_close_price", return_value=100.0):
            broker.place_order("AAPL", "buy", 500.0)
        # cash should be < 3000 (exact amount depends on fees)
        assert db.get_cash_eur() < 3000.0

    def test_sell_increases_cash(self, broker, db):
        db.seed_cash(3000.0)
        with patch.object(PaperBroker, "_last_close_price", return_value=100.0):
            broker.place_order("AAPL", "buy", 500.0)
            cash_after_buy = db.get_cash_eur()
            broker.place_order("AAPL", "sell", 300.0)
        assert db.get_cash_eur() > cash_after_buy

    def test_each_order_gets_unique_id(self, broker):
        ids = set()
        with patch.object(PaperBroker, "_last_close_price", return_value=50.0):
            for _ in range(5):
                result = broker.place_order("MSFT", "buy", 300.0)
                ids.add(result.order_id)
        assert len(ids) == 5

    def test_eur_rate_propagates_to_db(self, db):
        """PaperBroker should accept eur_usd_rate and persist it via record_trade."""
        broker_eur = PaperBroker(db, eur_usd_rate=1.08)
        with patch.object(PaperBroker, "_last_close_price", return_value=108.0):
            broker_eur.place_order("GOOG", "buy", 540.0)
        row = db._conn.execute("SELECT eur_usd_rate, price_eur FROM trades").fetchone()
        assert row["eur_usd_rate"] == pytest.approx(1.08)
        assert row["price_eur"] == pytest.approx(100.0, rel=1e-3)

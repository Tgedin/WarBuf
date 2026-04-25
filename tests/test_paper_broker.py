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
        with patch.object(PaperBroker, "_last_close_price", return_value=100.0):
            result = broker.place_order("AAPL", "buy", 500.0)

        assert result.ticker == "AAPL"
        assert result.side == "buy"
        assert result.qty == pytest.approx(5.0)
        assert result.order_id.startswith("PAPER-")

        positions = db.get_positions()
        assert "AAPL" in positions
        assert positions["AAPL"]["qty"] == pytest.approx(5.0)

    def test_second_buy_averages_cost_basis(self, broker, db):
        with patch.object(PaperBroker, "_last_close_price", return_value=100.0):
            broker.place_order("AAPL", "buy", 500.0)
        with patch.object(PaperBroker, "_last_close_price", return_value=200.0):
            broker.place_order("AAPL", "buy", 400.0)

        positions = db.get_positions()
        # 5 shares @ $100 + 2 shares @ $200 = 7 shares, avg ~$142.86
        assert positions["AAPL"]["qty"] == pytest.approx(7.0)
        assert positions["AAPL"]["avg_cost_basis"] == pytest.approx(
            (500.0 + 400.0) / 7.0, rel=1e-3
        )

    def test_sell_reduces_position(self, broker, db):
        with patch.object(PaperBroker, "_last_close_price", return_value=100.0):
            broker.place_order("AAPL", "buy", 1_000.0)   # 10 shares
            broker.place_order("AAPL", "sell", 500.0)    # 5 shares

        assert db.get_positions()["AAPL"]["qty"] == pytest.approx(5.0)

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

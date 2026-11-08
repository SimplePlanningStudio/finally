"""Tests for PriceUpdate dataclass."""

import pytest

from app.market.models import PriceUpdate


class TestPriceUpdate:
    """Unit tests for the PriceUpdate model."""

    def test_price_update_creation(self):
        """Test basic PriceUpdate creation."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert update.previous_price == 190.00
        assert update.timestamp == 1234567890.0

    def test_change_calculation(self):
        """Test price change calculation."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)
        assert update.change == 0.50

    def test_change_negative(self):
        """Test negative price change."""
        update = PriceUpdate(ticker="AAPL", price=189.50, previous_price=190.00, timestamp=1234567890.0)
        assert update.change == -0.50

    def test_change_percent_up(self):
        """Test percentage change calculation (up)."""
        update = PriceUpdate(ticker="AAPL", price=190.00, previous_price=100.00, timestamp=1234567890.0)
        assert update.change_percent == 90.0

    def test_change_percent_down(self):
        """Test percentage change calculation (down)."""
        update = PriceUpdate(ticker="AAPL", price=100.00, previous_price=200.00, timestamp=1234567890.0)
        assert update.change_percent == -50.0

    def test_change_percent_zero_previous(self):
        """Test percentage change with zero previous price."""
        update = PriceUpdate(ticker="AAPL", price=100.00, previous_price=0.00, timestamp=1234567890.0)
        assert update.change_percent == 0.0

    def test_direction_up(self):
        """Test direction calculation (up)."""
        update = PriceUpdate(ticker="AAPL", price=191.00, previous_price=190.00, timestamp=1234567890.0)
        assert update.direction == "up"

    def test_direction_down(self):
        """Test direction calculation (down)."""
        update = PriceUpdate(ticker="AAPL", price=189.00, previous_price=190.00, timestamp=1234567890.0)
        assert update.direction == "down"

    def test_direction_flat(self):
        """Test direction calculation (flat)."""
        update = PriceUpdate(ticker="AAPL", price=190.00, previous_price=190.00, timestamp=1234567890.0)
        assert update.direction == "flat"

    def test_to_dict(self):
        """Test serialization to dictionary."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)
        result = update.to_dict()

        assert result["ticker"] == "AAPL"
        assert result["price"] == 190.50
        assert result["previous_price"] == 190.00
        assert result["timestamp"] == 1234567890.0
        assert result["change"] == 0.50
        assert result["change_percent"] == 0.2632  # (0.50 / 190.00) * 100
        assert result["direction"] == "up"

    def test_immutability(self):
        """Test that PriceUpdate is immutable."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)

        with pytest.raises(AttributeError):
            update.price = 200.00  # Should raise error


class TestSessionOpen:
    """open_price / session_change_percent (PLAN §6)."""

    def test_session_change_percent_uses_open_price(self):
        u = PriceUpdate("AAPL", price=191.0, previous_price=190.5, timestamp=1.0, open_price=190.0)
        assert u.session_change_percent == 0.5263
        assert u.change_percent == 0.2625

    def test_session_change_percent_negative(self):
        u = PriceUpdate("AAPL", price=180.0, previous_price=181.0, timestamp=1.0, open_price=200.0)
        assert u.session_change_percent == -10.0

    def test_session_change_percent_zero_open(self):
        u = PriceUpdate("AAPL", price=10.0, previous_price=0.0, timestamp=1.0, open_price=0.0)
        assert u.session_change_percent == 0.0

    def test_open_price_defaults_to_previous_price(self):
        u = PriceUpdate("AAPL", price=191.0, previous_price=190.0, timestamp=1.0)
        assert u.open_price is None
        assert u.session_open == 190.0
        assert u.to_dict()["open_price"] == 190.0

    def test_to_dict_full_shape(self):
        d = PriceUpdate("AAPL", 190.5, 190.42, 1762600000.123, open_price=189.7).to_dict()
        assert d == {
            "ticker": "AAPL",
            "price": 190.5,
            "previous_price": 190.42,
            "open_price": 189.7,
            "timestamp": 1762600000.123,
            "change": 0.08,
            "change_percent": 0.042,
            "session_change_percent": 0.4217,
            "direction": "up",
        }

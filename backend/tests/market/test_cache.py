"""Tests for PriceCache."""

from app.market.cache import PriceCache


class TestPriceCache:
    """Unit tests for the PriceCache."""

    def test_update_and_get(self):
        """Test updating and getting a price."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert cache.get("AAPL") == update

    def test_first_update_is_flat(self):
        """Test that the first update has flat direction."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.direction == "flat"
        assert update.previous_price == 190.50

    def test_direction_up(self):
        """Test price update with upward direction."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 191.00)
        assert update.direction == "up"
        assert update.change == 1.00

    def test_direction_down(self):
        """Test price update with downward direction."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 189.00)
        assert update.direction == "down"
        assert update.change == -1.00

    def test_remove(self):
        """Test removing a ticker from cache."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.remove("AAPL")
        assert cache.get("AAPL") is None

    def test_remove_nonexistent(self):
        """Test removing a ticker that doesn't exist."""
        cache = PriceCache()
        cache.remove("AAPL")  # Should not raise

    def test_get_all(self):
        """Test getting all prices."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("GOOGL", 175.00)
        all_prices = cache.get_all()
        assert set(all_prices.keys()) == {"AAPL", "GOOGL"}

    def test_version_increments(self):
        """Test that version counter increments."""
        cache = PriceCache()
        v0 = cache.version
        cache.update("AAPL", 190.00)
        assert cache.version == v0 + 1
        cache.update("AAPL", 191.00)
        assert cache.version == v0 + 2

    def test_get_price_convenience(self):
        """Test the convenience get_price method."""
        cache = PriceCache()
        cache.update("AAPL", 190.50)
        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("NOPE") is None

    def test_len(self):
        """Test __len__ method."""
        cache = PriceCache()
        assert len(cache) == 0
        cache.update("AAPL", 190.00)
        assert len(cache) == 1
        cache.update("GOOGL", 175.00)
        assert len(cache) == 2

    def test_contains(self):
        """Test __contains__ method."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        assert "AAPL" in cache
        assert "GOOGL" not in cache

    def test_custom_timestamp(self):
        """Test updating with a custom timestamp."""
        cache = PriceCache()
        custom_ts = 1234567890.0
        update = cache.update("AAPL", 190.50, timestamp=custom_ts)
        assert update.timestamp == custom_ts

    def test_price_rounding(self):
        """Test that prices are rounded to 2 decimal places."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.12345)
        assert update.price == 190.12


class TestPriceCacheSessionOpen:
    """Session open carry-forward and remove() versioning."""

    def test_first_update_sets_open(self):
        c = PriceCache()
        u = c.update("AAPL", 190.123)
        assert u.open_price == 190.12
        assert u.session_change_percent == 0.0

    def test_open_price_set_on_first_update_and_kept(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        c.update("AAPL", 195.0)
        u = c.update("AAPL", 185.0)
        assert u.open_price == 190.0
        assert u.previous_price == 195.0
        assert u.direction == "down"
        assert u.session_change_percent == -2.6316

    def test_open_is_per_ticker(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        c.update("MSFT", 420.0)
        c.update("AAPL", 191.0)
        assert c.get("AAPL").open_price == 190.0
        assert c.get("MSFT").open_price == 420.0

    def test_readd_after_remove_resets_open(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        c.update("AAPL", 191.0)
        c.remove("AAPL")
        u = c.update("AAPL", 200.0)
        assert u.open_price == 200.0
        assert u.previous_price == 200.0

    def test_remove_bumps_version_only_when_present(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        v = c.version
        c.remove("AAPL")
        assert c.version == v + 1
        c.remove("AAPL")
        assert c.version == v + 1

    def test_concurrent_updates_are_consistent(self):
        import threading

        c = PriceCache()

        def worker(t: str) -> None:
            for i in range(500):
                c.update(t, 100.0 + i)

        threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B", "C", "D")]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert c.version == 2000
        assert all(c.get(t).open_price == 100.0 for t in "ABCD")
        assert all(c.get_price(t) == 599.0 for t in "ABCD")

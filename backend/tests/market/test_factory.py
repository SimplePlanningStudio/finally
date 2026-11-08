"""Tests for market data source factory."""

import os
from unittest.mock import patch

import pytest

from app.market.cache import PriceCache
from app.market.factory import create_market_data_source
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


class TestFactory:
    """Tests for create_market_data_source factory."""

    def test_creates_simulator_when_no_api_key(self):
        """Test that simulator is created when MASSIVE_API_KEY is not set."""
        cache = PriceCache()

        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    def test_creates_simulator_when_api_key_empty(self):
        """Test that simulator is created when MASSIVE_API_KEY is empty."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": ""}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    def test_creates_simulator_when_api_key_whitespace(self):
        """Test that simulator is created when MASSIVE_API_KEY is whitespace."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "   "}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    def test_creates_massive_when_api_key_set(self):
        """Test that Massive client is created when MASSIVE_API_KEY is set."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)

    def test_massive_receives_api_key(self):
        """Test that Massive client receives the API key."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key-123"}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)
        assert source._api_key == "test-key-123"

    def test_simulator_receives_cache(self):
        """Test that simulator receives the cache reference."""
        cache = PriceCache()

        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)
        assert source._cache is cache

    def test_massive_receives_cache(self):
        """Test that Massive client receives the cache reference."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)
        assert source._cache is cache


class TestPollInterval:
    """MASSIVE_POLL_SECONDS parsing (PLAN §5)."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("", 15.0),
            ("  ", 15.0),
            ("5", 5.0),
            (" 7.5 ", 7.5),
            ("0.5", 2.0),
            ("-3", 2.0),
            ("abc", 15.0),
            ("nan", 15.0),
        ],
    )
    def test_poll_interval(self, raw, expected):
        env = {"MASSIVE_API_KEY": "k", "MASSIVE_POLL_SECONDS": raw}
        with patch.dict(os.environ, env, clear=True):
            src = create_market_data_source(PriceCache())
        assert isinstance(src, MassiveDataSource)
        assert src._interval == expected

    def test_poll_interval_unset(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "k"}, clear=True):
            src = create_market_data_source(PriceCache())
        assert src._interval == 15.0

    def test_poll_interval_ignored_for_simulator(self):
        with patch.dict(os.environ, {"MASSIVE_POLL_SECONDS": "5"}, clear=True):
            src = create_market_data_source(PriceCache())
        assert isinstance(src, SimulatorDataSource)

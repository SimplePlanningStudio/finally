"""Tests for MarketService (tracked-set sync)."""

import pytest

from app.market import DEFAULT_TICKERS, MarketService
from app.market.cache import PriceCache
from app.market.interface import MarketDataSource
from app.market.simulator import GBMSimulator, SimulatorDataSource


class FakeSource(MarketDataSource):
    """Records calls; seeds a price on add like the simulator does."""

    def __init__(self, cache: PriceCache) -> None:
        self.cache = cache
        self.tickers: list[str] = []
        self.calls: list[tuple[str, str]] = []
        self.started = 0
        self.stopped = 0

    async def start(self, tickers: list[str]) -> None:
        self.started += 1
        self.tickers = list(tickers)

    async def stop(self) -> None:
        self.stopped += 1

    async def add_ticker(self, ticker: str) -> None:
        self.calls.append(("add", ticker))
        self.tickers.append(ticker)
        self.cache.update(ticker, 100.0)

    async def remove_ticker(self, ticker: str) -> None:
        self.calls.append(("rm", ticker))
        self.tickers.remove(ticker)
        self.cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self.tickers)


def _service() -> tuple[MarketService, FakeSource]:
    cache = PriceCache()
    source = FakeSource(cache)
    return MarketService(cache, source), source


@pytest.mark.asyncio
class TestMarketService:
    async def test_start_dedupes_and_sorts(self):
        market, source = _service()
        await market.start(["MSFT", "AAPL", "AAPL"])
        assert source.tickers == ["AAPL", "MSFT"]
        assert market.started

    async def test_start_and_stop_are_idempotent(self):
        market, source = _service()
        await market.start(["AAPL"])
        await market.start(["AAPL"])
        await market.stop()
        await market.stop()
        assert (source.started, source.stopped) == (1, 1)

    async def test_sync_adds_and_removes_diff_only(self):
        market, source = _service()
        await market.start(["AAPL", "MSFT"])
        await market.sync({"AAPL", "PYPL"})
        assert source.calls == [("add", "PYPL"), ("rm", "MSFT")]
        await market.sync({"AAPL", "PYPL"})  # idempotent
        assert len(source.calls) == 2
        assert sorted(market.tracked()) == ["AAPL", "PYPL"]

    async def test_held_ticker_stays_tracked_after_watchlist_remove(self):
        market, _ = _service()
        await market.start(["AAPL", "PYPL"])
        watchlist, positions = {"AAPL"}, {"PYPL"}  # PYPL removed from watchlist but held
        await market.sync(watchlist | positions)
        assert "PYPL" in market.tracked()

    async def test_closed_position_off_watchlist_is_dropped_from_cache(self):
        market, _ = _service()
        await market.start(["AAPL"])
        await market.sync({"AAPL", "PYPL"})
        assert market.get_price("PYPL") == 100.0
        await market.sync({"AAPL"})
        assert market.get_price("PYPL") is None
        assert "PYPL" not in market.cache

    async def test_sync_before_start_is_noop(self):
        market, source = _service()
        await market.sync({"AAPL"})
        assert source.calls == []

    async def test_from_env_builds_unstarted_simulator(self, monkeypatch):
        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
        market = MarketService.from_env()
        assert not market.started
        assert isinstance(market._source, SimulatorDataSource)

    async def test_with_real_simulator(self):
        cache = PriceCache()
        market = MarketService(cache, SimulatorDataSource(cache, update_interval=60.0))
        await market.start(DEFAULT_TICKERS)
        try:
            await market.sync([*DEFAULT_TICKERS, "PYPL"])
            assert market.get_price("PYPL") is not None  # simulator seeds immediately
            await market.sync(DEFAULT_TICKERS)
            assert "PYPL" not in cache
            assert sorted(market.tracked()) == sorted(DEFAULT_TICKERS)
        finally:
            await market.stop()


def test_default_tickers():
    assert DEFAULT_TICKERS == ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]


def test_full_default_set_plus_unknown_tickers_cholesky_ok():
    assert len(GBMSimulator([*DEFAULT_TICKERS, "PYPL", "ZZZ"]).step()) == 12

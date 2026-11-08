"""Tests for MarketService (tracked tickers = watchlist ∪ positions)."""

import pytest

from app.market import DEFAULT_TICKERS, MarketService
from app.market.cache import PriceCache
from app.market.interface import MarketDataSource
from app.market.simulator import GBMSimulator, SimulatorDataSource


class FakeSource(MarketDataSource):
    def __init__(self, cache: PriceCache) -> None:
        self.cache = cache
        self.t: list[str] = []
        self.calls: list[tuple[str, object]] = []

    async def start(self, tickers):
        self.calls.append(("start", list(tickers)))
        self.t = list(tickers)

    async def stop(self):
        self.calls.append(("stop", None))

    async def add_ticker(self, t):
        self.calls.append(("add", t))
        self.t.append(t)
        self.cache.update(t, 100.0)

    async def remove_ticker(self, t):
        self.calls.append(("rm", t))
        self.t.remove(t)
        self.cache.remove(t)

    def get_tickers(self):
        return list(self.t)


@pytest.fixture
def svc():
    cache = PriceCache()
    src = FakeSource(cache)
    return MarketService(cache, src), src


@pytest.mark.asyncio
class TestMarketService:
    async def test_start_dedupes_and_sorts(self, svc):
        m, src = svc
        await m.start(["MSFT", "AAPL", "MSFT"])
        assert src.calls == [("start", ["AAPL", "MSFT"])]
        assert m.started

    async def test_start_twice_is_noop(self, svc):
        m, src = svc
        await m.start(["AAPL"])
        await m.start(["MSFT"])
        assert src.calls == [("start", ["AAPL"])]

    async def test_sync_adds_and_removes_diff_only(self, svc):
        m, src = svc
        await m.start(["AAPL", "MSFT"])
        src.calls.clear()
        await m.sync({"AAPL", "PYPL"})
        assert src.calls == [("add", "PYPL"), ("rm", "MSFT")]
        await m.sync({"AAPL", "PYPL"})  # idempotent
        assert len(src.calls) == 2
        assert sorted(m.tracked()) == ["AAPL", "PYPL"]

    async def test_held_ticker_stays_tracked_after_watchlist_remove(self, svc):
        m, _ = svc
        await m.start(["AAPL", "PYPL"])
        watchlist, positions = {"AAPL"}, {"PYPL"}  # PYPL removed from watchlist but held
        await m.sync(watchlist | positions)
        assert "PYPL" in m.tracked()
        await m.sync(watchlist)  # position closed
        assert "PYPL" not in m.tracked()
        assert m.get_price("PYPL") is None

    async def test_sync_before_start_is_noop(self, svc):
        m, src = svc
        await m.sync({"AAPL"})
        assert src.calls == []

    async def test_sync_to_empty(self, svc):
        m, _ = svc
        await m.start(["AAPL"])
        await m.sync(set())
        assert m.tracked() == []

    async def test_stop_then_sync_is_noop(self, svc):
        m, src = svc
        await m.start(["AAPL"])
        await m.stop()
        await m.stop()  # safe twice
        src.calls.clear()
        await m.sync({"MSFT"})
        assert src.calls == []
        assert not m.started

    async def test_concurrent_syncs_serialised(self, svc):
        import asyncio

        m, _ = svc
        await m.start(["AAPL"])
        await asyncio.gather(m.sync({"AAPL", "PYPL"}), m.sync({"AAPL", "PYPL", "SQ"}))
        assert sorted(m.tracked()) == ["AAPL", "PYPL", "SQ"]

    async def test_get_price_reads_cache(self, svc):
        m, _ = svc
        await m.start([])
        await m.sync({"PYPL"})
        assert m.get_price("PYPL") == 100.0

    async def test_from_env_uses_simulator_by_default(self, monkeypatch):
        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
        m = MarketService.from_env()
        assert isinstance(m._source, SimulatorDataSource)
        assert m._source._cache is m.cache

    async def test_with_real_simulator_add_prices_immediately(self):
        cache = PriceCache()
        m = MarketService(cache, SimulatorDataSource(cache, update_interval=60))
        await m.start(DEFAULT_TICKERS)
        try:
            await m.sync(set(DEFAULT_TICKERS) | {"PYPL"})
            assert m.get_price("PYPL") is not None  # "add then buy" works in one turn
            await m.sync(DEFAULT_TICKERS)
            assert "PYPL" not in cache
        finally:
            await m.stop()


def test_default_tickers():
    assert DEFAULT_TICKERS == [
        "AAPL",
        "GOOGL",
        "MSFT",
        "AMZN",
        "TSLA",
        "NVDA",
        "META",
        "JPM",
        "V",
        "NFLX",
    ]


def test_full_default_set_plus_unknown_cholesky_ok():
    assert len(GBMSimulator(DEFAULT_TICKERS + ["PYPL", "ZZZ"]).step()) == 12

"""MarketService: owns the price cache and data source, keeps the tracked set in sync."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


class MarketService:
    """Facade over PriceCache + MarketDataSource.

    The tracked set (watchlist ∪ open positions) is changed only through sync().
    Callers compute the desired set from the database and pass it in; the
    service diffs it against what the source tracks and applies the difference.
    """

    def __init__(self, cache: PriceCache, source: MarketDataSource) -> None:
        self.cache = cache
        self._source = source
        self._lock = asyncio.Lock()  # serialise concurrent start/stop/sync calls
        self._started = False

    @classmethod
    def from_env(cls) -> MarketService:
        """Build an unstarted service with the source chosen from the environment."""
        cache = PriceCache()
        return cls(cache, create_market_data_source(cache))

    @property
    def started(self) -> bool:
        return self._started

    async def start(self, tickers: Iterable[str]) -> None:
        async with self._lock:
            if self._started:
                return
            await self._source.start(sorted(set(tickers)))
            self._started = True

    async def stop(self) -> None:
        async with self._lock:
            if not self._started:
                return
            await self._source.stop()
            self._started = False

    async def sync(self, desired: Iterable[str]) -> None:
        """Make the source track exactly `desired`. Idempotent; no-op before start()."""
        desired_set = set(desired)
        async with self._lock:
            if not self._started:
                return
            current = set(self._source.get_tickers())
            to_add = sorted(desired_set - current)
            to_remove = sorted(current - desired_set)
            for ticker in to_add:
                await self._source.add_ticker(ticker)
            for ticker in to_remove:
                await self._source.remove_ticker(ticker)  # also drops it from the cache
            if to_add or to_remove:
                logger.info("Tracked tickers synced: +%s -%s", to_add, to_remove)

    def tracked(self) -> list[str]:
        """Tickers the source is currently tracking."""
        return self._source.get_tickers()

    def get_price(self, ticker: str) -> float | None:
        """Latest price for a ticker, or None if it has no price yet."""
        return self.cache.get_price(ticker)

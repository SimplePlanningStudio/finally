"""MarketService: owns the cache + source and keeps the tracked set in sync."""

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

    The tracked set (PLAN §6: watchlist ∪ open positions) is changed ONLY
    through sync(). Callers compute the desired set from the DB and pass it in;
    the service diffs it against what the source currently tracks.
    """

    def __init__(self, cache: PriceCache, source: MarketDataSource) -> None:
        self.cache = cache
        self._source = source
        self._lock = asyncio.Lock()  # serialise concurrent syncs
        self._started = False

    @classmethod
    def from_env(cls) -> MarketService:
        """Build an unstarted service with the source chosen by environment variables."""
        cache = PriceCache()
        return cls(cache, create_market_data_source(cache))

    @property
    def started(self) -> bool:
        return self._started

    async def start(self, tickers: Iterable[str]) -> None:
        """Start the source with the initial tracked set. No-op if already started."""
        async with self._lock:
            if self._started:
                return
            await self._source.start(sorted(set(tickers)))
            self._started = True

    async def stop(self) -> None:
        """Stop the source. Safe to call more than once."""
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
            added = sorted(desired_set - current)
            removed = sorted(current - desired_set)
            for ticker in added:
                await self._source.add_ticker(ticker)
            for ticker in removed:
                await self._source.remove_ticker(ticker)  # also drops it from the cache
            if added or removed:
                logger.info("Tracked tickers synced: +%s -%s", added, removed)

    def tracked(self) -> list[str]:
        """Tickers the source currently tracks."""
        return self._source.get_tickers()

    def get_price(self, ticker: str) -> float | None:
        """Latest cached price, or None if the ticker has no price yet."""
        return self.cache.get_price(ticker)

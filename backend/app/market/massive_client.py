"""Massive (Polygon.io) API client for real market data."""

from __future__ import annotations

import asyncio
import logging

from massive import RESTClient
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive (Polygon.io) REST API.

    Polls GET /v2/snapshot/locale/us/markets/stocks/tickers for all watched
    tickers in a single API call, then writes results to the PriceCache.

    Rate limits:
      - Free tier: 5 req/min → poll every 15s (default)
      - Paid tiers: higher limits → poll every 2-5s
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._client: RESTClient | None = None

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(tickers)

        # Do an immediate first poll so the cache has data right away
        await self._poll_once()

        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval",
            len(tickers),
            self._interval,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            logger.info("Massive: added ticker %s (will appear on next poll)", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        logger.info("Massive: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Internal ---

    async def _poll_loop(self) -> None:
        """Poll on interval. First poll already happened in start()."""
        while True:
            await asyncio.sleep(self._interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        """Execute one poll cycle: fetch snapshots, update cache."""
        if not self._tickers or not self._client:
            return

        requested = list(self._tickers)  # copy for the worker thread
        try:
            # The Massive RESTClient is synchronous: run it in a thread so the
            # event loop is not blocked.
            snapshots = await asyncio.to_thread(self._fetch_snapshots, requested)
        except Exception as e:
            # 401 (bad key), 429 (rate limit), network errors: retry next interval.
            # Log only the type: HTTP errors can include the URL (and so the key).
            logger.error("Massive poll failed: %s", type(e).__name__)
            return

        still_tracked = set(self._tickers)  # may have changed during the await
        processed = 0
        for snap in snapshots:
            ticker = getattr(snap, "ticker", None)
            if ticker not in still_tracked:
                continue  # removed while the request was in flight
            try:
                price = snap.last_trade.price
                # Massive timestamps are Unix milliseconds -> convert to seconds
                timestamp = snap.last_trade.timestamp / 1000.0
                self._cache.update(ticker=ticker, price=price, timestamp=timestamp)
                processed += 1
            except (AttributeError, TypeError) as e:
                logger.warning("Skipping snapshot for %s: %s", ticker, type(e).__name__)
        logger.debug("Massive poll: updated %d/%d tickers", processed, len(requested))

    def _fetch_snapshots(self, tickers: list[str]) -> list:
        """Synchronous call to the Massive REST API. Runs in a thread."""
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=tickers,
        )

"""Factory for creating market data sources."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 15.0
MIN_POLL_SECONDS = 2.0


def _poll_interval() -> float:
    """Parse MASSIVE_POLL_SECONDS: 15s if missing or invalid, never below 2s."""
    raw = os.environ.get("MASSIVE_POLL_SECONDS", "").strip()
    if not raw:
        return DEFAULT_POLL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Invalid MASSIVE_POLL_SECONDS=%r; using %.0fs", raw, DEFAULT_POLL_SECONDS
        )
        return DEFAULT_POLL_SECONDS
    if value != value:  # NaN
        return DEFAULT_POLL_SECONDS
    return max(value, MIN_POLL_SECONDS)


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Create the appropriate market data source based on environment variables.

    - MASSIVE_API_KEY set and non-empty → MassiveDataSource (real market data),
      polling every MASSIVE_POLL_SECONDS (default 15)
    - Otherwise → SimulatorDataSource (GBM simulation)

    Returns an unstarted source. Caller must await source.start(tickers).
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        interval = _poll_interval()
        logger.info("Market data source: Massive API (poll every %.1fs)", interval)
        return MassiveDataSource(
            api_key=api_key, price_cache=price_cache, poll_interval=interval
        )

    logger.info("Market data source: GBM Simulator")
    return SimulatorDataSource(price_cache=price_cache)

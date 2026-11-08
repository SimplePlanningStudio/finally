"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Latest price per ticker, plus previous price and session open.

    Writers: the active MarketDataSource (simulator loop or Massive poller).
    Readers: SSE stream, trade execution, portfolio valuation, snapshot task.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version: int = 0  # Bumped on every mutation (update or effective remove)

    def update(self, ticker: str, price: float, timestamp: float | None = None) -> PriceUpdate:
        """Record a new price. The first update for a ticker sets its session open.

        Direction and change are computed from the previous price. On the first
        update previous_price == open_price == price (direction='flat').
        """
        with self._lock:
            ts = timestamp or time.time()
            prev = self._prices.get(ticker)
            rounded = round(price, 2)
            previous_price = prev.price if prev else rounded
            open_price = prev.session_open if prev else rounded

            update = PriceUpdate(
                ticker=ticker,
                price=rounded,
                previous_price=previous_price,
                timestamp=ts,
                open_price=open_price,
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        """Get the latest price for a single ticker, or None if unknown."""
        with self._lock:
            return self._prices.get(ticker)

    def get_all(self) -> dict[str, PriceUpdate]:
        """Snapshot of all current prices. Returns a shallow copy."""
        with self._lock:
            return dict(self._prices)

    def get_price(self, ticker: str) -> float | None:
        """Convenience: get just the price float, or None."""
        update = self.get(ticker)
        return update.price if update else None

    def remove(self, ticker: str) -> None:
        """Drop a ticker. Bumps version if it was present; re-adding starts a new session open."""
        with self._lock:
            if self._prices.pop(ticker, None) is not None:
                self._version += 1

    @property
    def version(self) -> int:
        """Current version counter. Useful for SSE change detection."""
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices

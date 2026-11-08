"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time."""

    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds
    open_price: float | None = None  # Session open; None -> treat previous_price as open

    @property
    def session_open(self) -> float:
        """First price seen this session (falls back to previous_price)."""
        return self.open_price if self.open_price is not None else self.previous_price

    @property
    def change(self) -> float:
        """Absolute price change from the previous tick."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Percent change from the previous tick (drives the flash animation)."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def session_change_percent(self) -> float:
        """Percent change since session open (shown in the watchlist)."""
        base = self.session_open
        if base == 0:
            return 0.0
        return round((self.price - base) / base * 100, 4)

    @property
    def direction(self) -> str:
        """'up', 'down', or 'flat' relative to the previous tick."""
        if self.price > self.previous_price:
            return "up"
        elif self.price < self.previous_price:
            return "down"
        return "flat"

    def to_dict(self) -> dict:
        """Serialize for JSON / SSE transmission."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "open_price": self.session_open,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "session_change_percent": self.session_change_percent,
            "direction": self.direction,
        }

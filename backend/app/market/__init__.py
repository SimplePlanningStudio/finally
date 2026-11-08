"""Market data subsystem for FinAlly.

Public API:
    PriceUpdate         - Immutable price snapshot dataclass
    PriceCache          - Thread-safe in-memory price store
    MarketDataSource    - Abstract interface for data providers
    MarketService       - Facade that keeps tracked tickers = watchlist + positions
    DEFAULT_TICKERS     - The default watchlist (seed and reset)
    create_market_data_source - Factory that selects simulator or Massive
    create_stream_router - FastAPI router factory for SSE endpoint
"""

from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import PriceUpdate
from .seed_prices import DEFAULT_TICKERS
from .service import MarketService
from .stream import create_stream_router

__all__ = [
    "PriceUpdate",
    "PriceCache",
    "MarketDataSource",
    "MarketService",
    "DEFAULT_TICKERS",
    "create_market_data_source",
    "create_stream_router",
]

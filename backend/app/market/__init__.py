"""Market data subsystem for FinAlly.

Public API:
    PriceUpdate         - Immutable price snapshot dataclass
    PriceCache          - Thread-safe in-memory price store
    MarketDataSource    - Abstract interface for data providers
    create_market_data_source - Factory that selects simulator or Massive
    MarketService       - Owns cache + source; sync() keeps the tracked set right
    create_stream_router - FastAPI router factory for SSE endpoint
    DEFAULT_TICKERS     - The default watchlist
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
    "create_market_data_source",
    "create_stream_router",
    "MarketService",
    "DEFAULT_TICKERS",
]

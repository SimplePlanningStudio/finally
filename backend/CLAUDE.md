# Backend — Developer Guide

## Project Setup

```bash
cd backend
uv sync --extra dev   # Install all dependencies including test/lint tools
```

## Market Data API

The market data subsystem lives in `app/market/`. Use these imports:

```python
from app.market import (
    DEFAULT_TICKERS, MarketDataSource, MarketService, PriceCache, PriceUpdate,
    create_market_data_source, create_stream_router,
)
```

### Core Types

- **`PriceUpdate`** — Immutable dataclass: `ticker`, `price`, `previous_price`, `timestamp`, `open_price` (session open), plus properties `change`, `change_percent`, `direction` ("up"/"down"/"flat", all relative to the previous tick), `session_open`, `session_change_percent` (since session open), and `to_dict()` for JSON serialization (includes `open_price` and `session_change_percent`).

- **`PriceCache`** — Thread-safe in-memory store. Key methods:
  - `update(ticker, price, timestamp=None) -> PriceUpdate` (first update sets the session open)
  - `get(ticker) -> PriceUpdate | None`
  - `get_price(ticker) -> float | None`
  - `get_all() -> dict[str, PriceUpdate]`
  - `remove(ticker)` — bumps `version` if present; re-adding starts a new session open
  - `version` property — monotonic counter, increments on every update or effective remove (for SSE change detection)

- **`MarketDataSource`** — Abstract interface implemented by `SimulatorDataSource` and `MassiveDataSource`. Lifecycle: `start(tickers)` -> `add_ticker()` / `remove_ticker()` -> `stop()`.

- **`create_market_data_source(cache)`** — Factory. Returns `MassiveDataSource` if `MASSIVE_API_KEY` is set (polling every `MASSIVE_POLL_SECONDS`, default 15, min 2), otherwise `SimulatorDataSource`.

- **`MarketService`** — Facade over the cache and source; the **only** way to change the tracked set. `MarketService.from_env()` builds it unstarted; `await start(tickers)` in the lifespan; after any watchlist/position/reset change, call `await market.sync(tracked_tickers(conn))` with the desired set (watchlist ∪ open positions). `sync` is idempotent and diffs against the source. Also `market.cache`, `market.get_price(t)`, `market.tracked()`. Routes must not call the source directly.

- **`DEFAULT_TICKERS`** — The 10 default watchlist tickers (DB seed and reset).

### SSE Streaming

```python
from app.market import create_stream_router

router = create_stream_router(price_cache)  # Returns FastAPI APIRouter
# Endpoint: GET /api/stream/prices (text/event-stream)
```

Sends one `data:` event with all tracked tickers whenever the cache version changes (`{}` when nothing is tracked), plus a `: ping` keep-alive after 15s of silence. Each call returns a fresh router.

See `planning/MARKET_DATA_DESIGN.md` §9–10 for the FastAPI lifespan wiring and how watchlist, trade and reset routes use `MarketService`.

### Seed Data

Default tickers: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX. Seed prices and per-ticker volatility/drift params are in `app/market/seed_prices.py`.

## Running Tests

```bash
uv run --extra dev pytest -v              # All tests
uv run --extra dev pytest --cov=app       # With coverage
uv run --extra dev ruff check app/ tests/ # Lint
```

## Demo

```bash
uv run market_data_demo.py   # Live terminal dashboard with simulated prices
```

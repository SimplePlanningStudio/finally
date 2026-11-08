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

Full design, including how routes should use this: `planning/MARKET_DATA_DESIGN.md`.

### Core Types

- **`PriceUpdate`** — Immutable dataclass: `ticker`, `price`, `previous_price`, `timestamp`, `open_price` (session open), plus properties `change`, `change_percent`, `direction` ("up"/"down"/"flat") relative to the previous tick, `session_change_percent` relative to the session open, and `to_dict()` for JSON serialization.

- **`PriceCache`** — Thread-safe in-memory store. Key methods:
  - `update(ticker, price, timestamp=None) -> PriceUpdate` — the first update for a ticker sets its session open
  - `get(ticker) -> PriceUpdate | None`
  - `get_price(ticker) -> float | None`
  - `get_all() -> dict[str, PriceUpdate]`
  - `remove(ticker)` — bumps `version` if the ticker was present; re-adding starts a new session open
  - `version` property — monotonic counter, increments on every update or effective remove (for SSE change detection)

- **`MarketDataSource`** — Abstract interface implemented by `SimulatorDataSource` and `MassiveDataSource`. Lifecycle: `start(tickers)` -> `add_ticker()` / `remove_ticker()` -> `stop()`.

- **`create_market_data_source(cache)`** — Factory. Returns `MassiveDataSource` if `MASSIVE_API_KEY` is set (polling every `MASSIVE_POLL_SECONDS`, default 15, minimum 2), otherwise `SimulatorDataSource`.

- **`MarketService`** — Owns the cache and the source. Use it instead of calling the source directly:
  - `MarketService.from_env()` — build an unstarted service (no tasks or I/O)
  - `await start(tickers)` / `await stop()` — call from the FastAPI lifespan
  - `await sync(desired)` — make the tracked set exactly `desired`. Pass watchlist ∪ open positions (from the DB) after every watchlist change, sell and reset. Idempotent
  - `cache`, `get_price(ticker)`, `tracked()`

### SSE Streaming

```python
from app.market import create_stream_router

router = create_stream_router(market.cache)  # Returns a new FastAPI APIRouter each call
# Endpoint: GET /api/stream/prices (text/event-stream)
```

Sends one event with all tracked tickers whenever the cache changes (`data: {}` when nothing is tracked), and a `: ping` comment after 15 s of silence.

### Seed Data

Default tickers (`DEFAULT_TICKERS`): AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX. Seed prices and per-ticker volatility/drift params are in `app/market/seed_prices.py`.

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

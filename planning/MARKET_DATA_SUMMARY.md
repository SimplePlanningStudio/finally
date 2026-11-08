# Market Data Backend — Summary

**Status:** Complete, tested, reviewed. The PLAN.md follow-up changes from `MARKET_DATA_DESIGN.md` (checklist steps 1–8) are implemented: session open price, `MASSIVE_POLL_SECONDS`, Massive poll safety, `MarketService`, and the SSE stream fixes. The DB, `app/main.py` and route wiring (step 9) belong to the Backend agent.

## What Was Built

A complete market data subsystem in `backend/app/market/` (9 modules) providing live price simulation and real market data via a unified interface.

### Architecture

```
MarketDataSource (ABC)
├── SimulatorDataSource  →  GBM simulator (default, no API key needed)
└── MassiveDataSource    →  Polygon.io REST poller (when MASSIVE_API_KEY set)
        │
        ▼
   PriceCache (thread-safe, in-memory)
        │
        ├──→ SSE stream endpoint (/api/stream/prices)
        ├──→ Portfolio valuation
        └──→ Trade execution
```

### Modules

| File | Purpose |
|------|---------|
| `models.py` | `PriceUpdate` — immutable frozen dataclass (ticker, price, previous_price, timestamp, open_price; change, change_percent, session_change_percent, direction) |
| `interface.py` | `MarketDataSource` — abstract base class defining `start/stop/add_ticker/remove_ticker/get_tickers` |
| `cache.py` | `PriceCache` — thread-safe price store; records the session open on first update; version counter (bumped on update and remove) for SSE change detection |
| `seed_prices.py` | `DEFAULT_TICKERS`, realistic seed prices, per-ticker GBM params (drift/volatility), correlation groups |
| `simulator.py` | `GBMSimulator` (Geometric Brownian Motion with Cholesky-correlated moves) + `SimulatorDataSource` |
| `massive_client.py` | `MassiveDataSource` — REST polling client for Polygon.io via the `massive` package; ignores tickers removed mid-poll; logs only exception types |
| `factory.py` | `create_market_data_source()` — selects simulator or Massive based on `MASSIVE_API_KEY`; reads `MASSIVE_POLL_SECONDS` |
| `service.py` | `MarketService` — owns cache + source; `sync(desired)` keeps tracked tickers = watchlist ∪ positions |
| `stream.py` | `create_stream_router()` — fresh FastAPI SSE router per call; one event with all tickers on each version change (`{}` when empty); 15s keep-alive |

### Key Design Decisions

- **Strategy pattern** — both data sources implement the same ABC; downstream code is source-agnostic
- **PriceCache as single point of truth** — producers write, consumers read; no direct coupling
- **GBM with correlated moves** — Cholesky decomposition of sector-based correlation matrix; tech stocks correlate at 0.6, finance at 0.5, cross-sector at 0.3
- **Random shock events** — ~0.1% chance per tick per ticker of a 2-5% move for visual drama
- **SSE over WebSockets** — simpler, one-way push, universal browser support

## Test Suite

**128 tests, all passing.** 8 test modules in `backend/tests/market/`.

| Module | Tests | Coverage |
|--------|-------|----------|
| test_models.py | 16 | models.py: 100% |
| test_cache.py | 19 | cache.py: 100% |
| test_simulator.py | 19 | simulator.py: 97% |
| test_simulator_source.py | 10 | (integration tests) |
| test_factory.py | 17 | factory.py: 100% |
| test_massive.py | 21 | massive_client.py: 100% |
| test_service.py | 13 | service.py: 100% |
| test_stream.py | 13 | stream.py: 93% |

Overall coverage: 98%.

## Code Review & Fixes Applied

A comprehensive code review identified 7 issues. All were resolved:

1. **pyproject.toml build config** — added `[tool.hatch.build.targets.wheel] packages = ["app"]`
2. **Lazy imports removed** — `massive` is a core dependency; imports moved to top level
3. **SSE return type fixed** — `_generate_events` annotated as `AsyncGenerator[str, None]`
4. **Public `get_tickers()`** — added to `GBMSimulator` to avoid private attribute access
5. **Correlation constants cleaned up** — removed unused `DEFAULT_CORR`, consolidated into `CROSS_GROUP_CORR`
6. **Unused test imports removed** — `pytest`, `math`, `asyncio` cleaned from 4 test files
7. **Massive test mocks fixed** — `source._client` set in tests, patches target correct names

## Demo

A Rich terminal demo is available at `backend/market_data_demo.py`:

```bash
cd backend
uv run market_data_demo.py
```

Displays a live-updating dashboard with all 10 tickers, sparklines, color-coded direction arrows, and an event log for notable price moves. Runs 60 seconds or until Ctrl+C.

## Usage for Downstream Code

```python
from app.market import DEFAULT_TICKERS, MarketService, create_stream_router

# App creation (no tasks yet), then start in the lifespan
market = MarketService.from_env()            # Reads MASSIVE_API_KEY, MASSIVE_POLL_SECONDS
app.include_router(create_stream_router(market.cache))
await market.start(tracked_tickers(conn))    # watchlist ∪ positions, from the DB

# Read prices
update = market.cache.get("AAPL")   # PriceUpdate or None (has open_price, session_change_percent)
price = market.get_price("AAPL")    # float or None
all_prices = market.cache.get_all() # dict[str, PriceUpdate]

# After any watchlist / trade / reset change
await market.sync(tracked_tickers(conn))     # idempotent diff

# Shutdown
await market.stop()
```

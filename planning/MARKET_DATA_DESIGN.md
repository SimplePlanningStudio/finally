# Market Data Backend — Implementation Design

**Audience:** the Backend agent that builds the rest of FinAlly on top of `backend/app/market/`.
**Status:** checklist steps 1–8 (§13) are implemented in `app/market/` with 110 passing tests. Step 9 (DB, `main.py`, routes) belongs to the Backend agent. This document records each change against PLAN.md (§6, §7 Reset, §8 trade rules, §13) and shows how the rest of the backend should use the market layer.

The older design in `planning/archive/MARKET_DATA_DESIGN.md` describes the code as it was first written. Where the two differ, follow this document.

---

## Contents

1. [What exists, what changes](#1-what-exists-what-changes)
2. [Module map](#2-module-map)
3. [Data model: `PriceUpdate` with session open](#3-data-model-priceupdate-with-session-open)
4. [`PriceCache` changes](#4-pricecache-changes)
5. [Data sources: small fixes](#5-data-sources-small-fixes)
6. [Factory: `MASSIVE_POLL_SECONDS`](#6-factory-massive_poll_seconds)
7. [Tracked tickers: `MarketService`](#7-tracked-tickers-marketservice)
8. [SSE stream](#8-sse-stream)
9. [FastAPI wiring (lifespan and dependencies)](#9-fastapi-wiring-lifespan-and-dependencies)
10. [How other routes use the market layer](#10-how-other-routes-use-the-market-layer)
11. [Tests to add or update](#11-tests-to-add-or-update)
12. [Edge cases and decisions](#12-edge-cases-and-decisions)
13. [Implementation checklist](#13-implementation-checklist)

---

## 1. What exists, what changes

| Area | Current code | PLAN.md requirement | Change |
|---|---|---|---|
| `PriceUpdate` | ticker, price, previous_price, timestamp, change, change_percent, direction | Also `open_price` and `session_change_percent`, both in `to_dict()` (§6) | Add a field and a property |
| `PriceCache.update` | Keeps only the previous price | Record `open_price` on a ticker's first update (§6) | Carry the open price forward |
| `PriceCache.remove` | Does not bump `version` | A ticker missing from an event is dropped by the frontend (§6) | Bump `version` so the SSE stream sends the smaller set |
| Tracked set | Routes call `source.add_ticker/remove_ticker` directly | Tracked = watchlist ∪ open positions (§6) | New `MarketService.sync()` is the only way to change the set |
| Factory | Massive always polls every 15 s | Read `MASSIVE_POLL_SECONDS` (§5, §6) | Parse the env var and pass `poll_interval` |
| Massive poll | Writes every returned snapshot; logs `str(e)` | Never log the key (§5) | Skip tickers removed during a poll; log only the exception type |
| SSE | Skips the event when the cache is empty; router created at module level | One event with all tracked tickers, sent whenever the cache changes (§6) | Send `{}` too; create the router inside the factory; add a keep-alive comment |
| Reset | — | Sync the tracked tickers to the defaults (§7) | `MarketService.sync(DEFAULT_TICKERS)` |

The rest stays as it is: the GBM maths, the correlation matrix, seed prices, the `MarketDataSource` ABC and the Massive snapshot call.

---

## 2. Module map

```
backend/app/
├── main.py                 # NEW  FastAPI app, lifespan, static mount (§9)
├── deps.py                 # NEW  get_price_cache / get_market dependencies (§9)
├── db/                     # NEW  (Backend agent) schema, seed, tracked_tickers() (§7)
└── market/
    ├── __init__.py         # CHANGE  export MarketService
    ├── models.py           # CHANGE  open_price, session_change_percent
    ├── cache.py            # CHANGE  open_price carry-forward; remove() bumps version
    ├── interface.py        #   —     unchanged
    ├── seed_prices.py      # CHANGE  add DEFAULT_TICKERS
    ├── simulator.py        #   —     unchanged (one optional tidy-up, §5.1)
    ├── massive_client.py   # CHANGE  stale-ticker guard, safe logging, list copy
    ├── factory.py          # CHANGE  MASSIVE_POLL_SECONDS
    ├── service.py          # NEW     MarketService (tracked set = watchlist ∪ positions)
    └── stream.py           # CHANGE  router per call, empty events, keep-alive
```

Data flow:

```
              ┌──────────── MarketService ─────────────┐
 DB ─────────▶│ sync(watchlist ∪ positions)             │
 (routes call │   ├─ source.add_ticker / remove_ticker  │
  after every │   └─ asyncio.Lock serialises changes    │
  change)     └───────────────┬────────────────────────┘
                              ▼
           SimulatorDataSource │ MassiveDataSource      (one, chosen by factory)
                              │ writes
                              ▼
                         PriceCache  (latest, previous, open, ts, version)
                 ┌────────────┼──────────────┬───────────────┐
                 ▼            ▼              ▼               ▼
          SSE /api/stream  trade exec   GET /api/portfolio  snapshot task
            /prices        get_price()   get_price()         get_price()
```

---

## 3. Data model: `PriceUpdate` with session open

`open_price` is the first price the cache saw for the ticker since the server started or since the ticker was added. It gets a default so the existing tests, which build `PriceUpdate` with four arguments, still pass. When it is missing, the session open falls back to `previous_price`.

**`app/market/models.py`**

```python
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
    open_price: float | None = None  # Session open; None → treat previous_price as open

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
        if self.price < self.previous_price:
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
```

Example values (percentages are in percent units, so `0.42` means 0.42%):

```python
u = PriceUpdate("AAPL", price=190.50, previous_price=190.42, timestamp=1762600000.123, open_price=189.70)
u.to_dict()
# {"ticker": "AAPL", "price": 190.5, "previous_price": 190.42, "open_price": 189.7,
#  "timestamp": 1762600000.123, "change": 0.08, "change_percent": 0.042,
#  "session_change_percent": 0.4217, "direction": "up"}
```

---

## 4. `PriceCache` changes

Two changes:

1. `update()` keeps the previous entry's `open_price` and sets it on the first update.
2. `remove()` bumps `version` when it actually removes a ticker. Without this, removing a ticker under Massive (polled every 15 s) leaves it on screen until the next poll. If the cache becomes empty, the frontend would never be told.

`version` is also read under the lock, which keeps it correct on free-threaded Python (archived review §3.4).

**`app/market/cache.py`** (whole file)

```python
"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Latest price per ticker, plus previous price and session open.

    Writers: the active MarketDataSource (simulator loop or Massive poller thread result).
    Readers: SSE stream, trade execution, portfolio valuation, snapshot task.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version: int = 0  # Bumped on every mutation (update or effective remove)

    def update(self, ticker: str, price: float, timestamp: float | None = None) -> PriceUpdate:
        """Record a new price. The first update for a ticker sets its session open."""
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
        with self._lock:
            return self._prices.get(ticker)

    def get_all(self) -> dict[str, PriceUpdate]:
        """Shallow copy of all current prices."""
        with self._lock:
            return dict(self._prices)

    def get_price(self, ticker: str) -> float | None:
        update = self.get(ticker)
        return update.price if update else None

    def remove(self, ticker: str) -> None:
        """Drop a ticker. Re-adding it later starts a new session open."""
        with self._lock:
            if self._prices.pop(ticker, None) is not None:
                self._version += 1

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices
```

Behaviour example:

```python
cache = PriceCache()
cache.update("AAPL", 190.00)          # open 190.00, prev 190.00, flat
cache.update("AAPL", 190.50)          # open 190.00, prev 190.00, up,  session +0.2632%
u = cache.update("AAPL", 189.00)      # open 190.00, prev 190.50, down, session -0.5263%
cache.remove("AAPL")                  # version += 1
cache.update("AAPL", 200.00).open_price   # 200.0 — new session open after re-add
```

> **Note:** `previous_price` used to be `round(prev.price, 2)`. It is now `prev.price`, which is already rounded, so the result is the same.

---

## 5. Data sources: small fixes

### 5.1 Simulator (`simulator.py`) — no functional change

`SimulatorDataSource` already does what the plan asks:

- `start()` seeds the cache straight away, so the first SSE event has prices.
- `add_ticker()` seeds the new ticker's price at once (PLAN §6: "The simulator seeds a price into the cache as soon as a ticker is added").
- `remove_ticker()` removes the ticker from the simulation and from the cache.
- Unknown symbols start at a random price between $50 and $300 and use `DEFAULT_PARAMS`.

Because the loop runs on the event loop thread and `step()` is synchronous, a removal can't race with a step.

Optional tidy-up: `add_ticker` logs "added" even when the ticker was already there. That is harmless, since `MarketService` only adds missing tickers.

### 5.2 Massive (`massive_client.py`) — three fixes

1. **Race on removal.** `_fetch_snapshots` runs in a thread. If `remove_ticker("X")` runs while a poll is in flight, the poll writes X back into the cache after it was removed, and X shows up again until the next poll. Fix: after fetching, write only tickers that are still tracked.
2. **List mutation.** The thread reads `self._tickers` while the event loop may replace it. Pass a copy into the thread.
3. **Key safety.** HTTP exceptions can include the request URL. PLAN §5 says the key is never logged, so log only `type(e).__name__`.

```python
    async def _poll_once(self) -> None:
        """Execute one poll cycle: fetch snapshots, update cache."""
        if not self._tickers or not self._client:
            return

        requested = list(self._tickers)  # snapshot for the worker thread
        try:
            snapshots = await asyncio.to_thread(self._fetch_snapshots, requested)
        except Exception as e:  # 401 / 429 / network — retry next interval
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
                timestamp = snap.last_trade.timestamp / 1000.0  # ms → s
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
```

> The existing tests mock `source._fetch_snapshots = MagicMock(return_value=[...])`. A `MagicMock` accepts the new `tickers` argument, so those tests keep passing. Any assertion on the call arguments needs `assert_called_once_with([...])`.

Massive behaviour that stays the same and that callers must expect:

- `start()` does one poll right away (blocking for one HTTP round-trip). Later polls run every `poll_interval`.
- `add_ticker()` gives no price until the next poll, up to one interval later. A trade on that ticker in the meantime is rejected with "No price available" (PLAN §8, §9).
- An unknown symbol never gets a price. It stays tracked and shows "—" in the UI.

---

## 6. Factory: `MASSIVE_POLL_SECONDS`

**`app/market/factory.py`**

```python
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
    """Parse MASSIVE_POLL_SECONDS; fall back to 15s on missing/invalid; clamp to ≥2s."""
    raw = os.environ.get("MASSIVE_POLL_SECONDS", "").strip()
    if not raw:
        return DEFAULT_POLL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid MASSIVE_POLL_SECONDS=%r; using %.0fs", raw, DEFAULT_POLL_SECONDS)
        return DEFAULT_POLL_SECONDS
    return max(value, MIN_POLL_SECONDS)


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Return an unstarted source: Massive if MASSIVE_API_KEY is set, else the simulator."""
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        interval = _poll_interval()
        logger.info("Market data source: Massive API (poll every %.1fs)", interval)
        return MassiveDataSource(api_key=api_key, price_cache=price_cache, poll_interval=interval)

    logger.info("Market data source: GBM simulator")
    return SimulatorDataSource(price_cache=price_cache)
```

| `MASSIVE_POLL_SECONDS` | Result |
|---|---|
| unset / empty | 15.0 |
| `"5"` | 5.0 |
| `"0.5"` | 2.0 (clamped) |
| `"abc"` | 15.0 + warning |

---

## 7. Tracked tickers: `MarketService`

### 7.1 Why it is needed

PLAN §6: *tracked tickers = watchlist ∪ tickers with an open position.* Three different places change one side of that union: the watchlist routes, trade execution (manual and AI), and reset. If each one called `source.add_ticker/remove_ticker` itself, every caller would need to know the rule, and one mistake would stop pricing a held position.

Instead, keep the rule in one place. After any DB change, the caller computes the desired set with one SQL query and hands it to `MarketService.sync()`. The service works out the difference from what the source is tracking and applies it.

```
desired  = tracked_tickers(conn)          # SQL union — the rule lives here
current  = set(source.get_tickers())
add      = desired - current              # → source.add_ticker(t)
remove   = current - desired              # → source.remove_ticker(t)  (also drops it from the cache)
```

`sync` is idempotent, so callers can call it after every write without checking whether anything changed.

### 7.2 The DB side (owned by the Backend agent, `app/db/`)

```python
# app/db/queries.py
TRACKED_TICKERS_SQL = """
    SELECT ticker FROM watchlist WHERE user_id = :uid
    UNION
    SELECT ticker FROM positions WHERE user_id = :uid
"""


def tracked_tickers(conn: sqlite3.Connection, user_id: str = "default") -> set[str]:
    """Tickers that must be priced: watchlist ∪ open positions."""
    return {row[0] for row in conn.execute(TRACKED_TICKERS_SQL, {"uid": user_id})}
```

Position rows are deleted when the quantity reaches 0 (PLAN §7), so "has a row" means "is held". No `quantity > 0` filter is needed.

### 7.3 `app/market/service.py` (new)

```python
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

    The tracked set is changed ONLY through sync(); callers pass the desired set
    (watchlist ∪ open positions, computed from the DB) and the service diffs it
    against what the source currently tracks.
    """

    def __init__(self, cache: PriceCache, source: MarketDataSource) -> None:
        self.cache = cache
        self._source = source
        self._lock = asyncio.Lock()  # serialise concurrent syncs (two requests at once)
        self._started = False

    @classmethod
    def from_env(cls) -> MarketService:
        cache = PriceCache()
        return cls(cache, create_market_data_source(cache))

    async def start(self, tickers: Iterable[str]) -> None:
        async with self._lock:
            await self._source.start(sorted(set(tickers)))
            self._started = True

    async def stop(self) -> None:
        async with self._lock:
            await self._source.stop()
            self._started = False

    async def sync(self, desired: Iterable[str]) -> None:
        """Make the source track exactly `desired`. Idempotent."""
        desired_set = set(desired)
        async with self._lock:
            if not self._started:
                return
            current = set(self._source.get_tickers())
            for ticker in sorted(desired_set - current):
                await self._source.add_ticker(ticker)
            for ticker in sorted(current - desired_set):
                await self._source.remove_ticker(ticker)  # also drops it from the cache
            if desired_set != current:
                logger.info(
                    "Tracked tickers synced: +%s -%s",
                    sorted(desired_set - current),
                    sorted(current - desired_set),
                )

    def tracked(self) -> list[str]:
        return self._source.get_tickers()

    def get_price(self, ticker: str) -> float | None:
        return self.cache.get_price(ticker)
```

Export it from `app/market/__init__.py`:

```python
from .service import MarketService

__all__ = [..., "MarketService"]
```

And put the default list in one place, `app/market/seed_prices.py`, which DB seeding and reset both import:

```python
DEFAULT_TICKERS: list[str] = list(SEED_PRICES)  # AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX
```

### 7.4 Scenarios

| Step | Watchlist | Positions | Tracked after `sync` |
|---|---|---|---|
| Start | 10 defaults | — | 10 defaults |
| `POST /api/watchlist {PYPL}` | +PYPL | — | +PYPL (simulator: priced at once) |
| Buy 5 PYPL | … | PYPL | unchanged |
| `DELETE /api/watchlist/PYPL` | −PYPL | PYPL | **PYPL still tracked** (held) |
| Sell 5 PYPL (row deleted) | … | — | **PYPL removed** → cache drops it → next SSE event omits it |
| `POST /api/reset` | 10 defaults | — | 10 defaults |

---

## 8. SSE stream

Changes from the current `stream.py`:

- **Router created inside the factory.** The module-level `router` registered the route again on every call (archived review §3.6). That matters as soon as tests build more than one app.
- **Send when the version changes, even if the cache is empty.** The frontend drops any ticker missing from an event, so `{}` is a valid and needed event once the last ticker is removed. On connect, the first event is always sent (`last_version = -1`).
- **Keep-alive comment** (`: ping`) after 15 s of silence. With Massive, the cache can stay unchanged for 15 s or longer (or forever outside market hours), and some proxies close idle streams. `EventSource` ignores comment lines.
- **Logging at debug level** for connects and disconnects, so that each browser tab doesn't add log noise.

**`app/market/stream.py`**

```python
"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.5       # seconds between cache checks
KEEPALIVE_SECONDS = 15.0  # send a comment line if nothing was sent for this long


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    """Create a fresh SSE router bound to `price_cache`."""
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _generate_events(price_cache, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


def format_event(price_cache: PriceCache) -> str:
    """One SSE `data:` frame containing every tracked ticker."""
    data = {ticker: u.to_dict() for ticker, u in price_cache.get_all().items()}
    return f"data: {json.dumps(data, separators=(',', ':'))}\n\n"


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
    interval: float = POLL_INTERVAL,
    keepalive: float = KEEPALIVE_SECONDS,
) -> AsyncGenerator[str, None]:
    yield "retry: 1000\n\n"  # browser reconnects after 1s

    last_version = -1
    last_sent = time.monotonic()
    try:
        while not await request.is_disconnected():
            version = price_cache.version
            if version != last_version:
                last_version = version
                yield format_event(price_cache)
                last_sent = time.monotonic()
            elif time.monotonic() - last_sent >= keepalive:
                yield ": ping\n\n"
                last_sent = time.monotonic()
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass
    logger.debug("SSE client disconnected")
```

Wire format (one frame; pretty-printed here):

```
retry: 1000

data: {"AAPL":{"ticker":"AAPL","price":190.5,"previous_price":190.42,"open_price":189.7,
       "timestamp":1762600000.123,"change":0.08,"change_percent":0.042,
       "session_change_percent":0.4217,"direction":"up"},"GOOGL":{...}}

: ping

data: {}
```

The frontend reads it like this (for reference; the Frontend agent owns this code):

```ts
const es = new EventSource("/api/stream/prices");
es.onopen = () => setStatus("connected");
es.onerror = () => setStatus(es.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
es.onmessage = (e) => {
  const batch: Record<string, PriceUpdate> = JSON.parse(e.data);
  // replace the price map with `batch` (drop tickers not in it), append to per-ticker buffers (cap 1000)
};
```

> **Version and value changes:** the simulator bumps `version` on every tick (all tickers, every 500 ms), even when a rounded price didn't move, so in practice the stream sends roughly every 500 ms. That is what we want: one batched event per tick.

---

## 9. FastAPI wiring (lifespan and dependencies)

Startup order (PLAN §7): **DB init → market start (needs the tracked set) → snapshot task → serve.**

**The ordering problem:** routers must be registered before `StaticFiles` is mounted at `/` (PLAN §11), but the SSE router needs the `PriceCache`. To solve it, **build** the `MarketService` when the app is created and **start** it in the lifespan. Building it does nothing (no tasks, no I/O), so this is safe. Tests can also pass in their own `MarketService(PriceCache(), FakeSource())`.

**`app/main.py`**

```python
"""FinAlly FastAPI application."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import connect, init_db, tracked_tickers       # Backend agent
from app.market import MarketService, create_stream_router
from app.portfolio.snapshots import snapshot_loop          # Backend agent

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)  # real env vars win (PLAN §5)
logging.basicConfig(level=logging.INFO)


def create_app(market: MarketService | None = None) -> FastAPI:
    market = market or MarketService.from_env()   # unstarted: no tasks yet

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()                                          # 1. schema + seed if empty
        with connect() as conn:
            await market.start(tracked_tickers(conn))      # 2. watchlist ∪ positions
        snapshots = asyncio.create_task(                   # 3. startup + every 30s
            snapshot_loop(market), name="portfolio-snapshots"
        )
        try:
            yield
        finally:
            snapshots.cancel()
            await asyncio.gather(snapshots, return_exceptions=True)
            await market.stop()

    app = FastAPI(title="FinAlly", lifespan=lifespan)
    app.state.market = market

    app.include_router(create_stream_router(market.cache))
    # app.include_router(portfolio_router); watchlist_router; chat_router; system_router

    static_dir = Path(__file__).resolve().parents[1] / "static"
    if static_dir.is_dir():                                # absent in local dev / unit tests
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")  # LAST
    return app


app = create_app()   # uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`python-dotenv` must be added to `pyproject.toml` (`uv add python-dotenv`).

**`app/deps.py`**

```python
from fastapi import Request

from app.market import MarketService, PriceCache


def get_market(request: Request) -> MarketService:
    return request.app.state.market


def get_price_cache(request: Request) -> PriceCache:
    return request.app.state.market.cache
```

---

## 10. How other routes use the market layer

The market layer provides three things to the rest of the backend: `cache.get_price(t)`, `cache.get(t)` / `get_all()`, and `market.sync(desired)`. Nothing outside `app/market/` should call the source directly.

### 10.1 Watchlist routes

```python
@router.post("/api/watchlist", status_code=201)
async def add_watchlist(body: TickerIn, market: MarketService = Depends(get_market)):
    ticker = body.ticker  # Pydantic validator: strip/upper + ^[A-Z][A-Z0-9.\-]{0,9}$ → 422
    with connect() as conn:
        if not db.add_watchlist(conn, ticker):          # PK conflict
            raise HTTPException(409, f"{ticker} is already on the watchlist")
        desired = tracked_tickers(conn)
    await market.sync(desired)
    return {"ticker": ticker}


@router.delete("/api/watchlist/{ticker}", status_code=204)
async def remove_watchlist(ticker: str, market: MarketService = Depends(get_market)):
    ticker = normalize_ticker(ticker)
    with connect() as conn:
        if not db.remove_watchlist(conn, ticker):
            raise HTTPException(404, f"{ticker} is not on the watchlist")
        desired = tracked_tickers(conn)
    await market.sync(desired)          # still tracked if held
```

### 10.2 Trade execution (shared by manual and AI trades)

The price is read **once**, at request time, and that same value is used for the cash check, the average cost and the `trades` row.

```python
async def execute_trade(conn, market: MarketService, ticker: str, side: str, qty: float) -> dict:
    price = market.get_price(ticker)
    if price is None:
        raise TradeError(f"No price available for {ticker} — add it to the watchlist first")

    with conn:  # one SQLite transaction: cash, position upsert/delete, trades row
        trade = db.apply_trade(conn, ticker, side, round(qty, 4), price)
    if side == "sell":
        await market.sync(tracked_tickers(conn))   # position may have closed
    return trade
```

A buy can't change the tracked set: you can only buy a ticker that has a price, so it is already tracked. Calling `sync` after every trade costs almost nothing, so call it unconditionally if that's simpler.

For AI chat: run all watchlist changes first, then trades (PLAN §9 step 6). With the simulator, `sync` after a watchlist `add` seeds the price at once, so "add PYPL then buy PYPL" in one turn works. With Massive the buy fails with the "No price available" message, as the plan expects.

### 10.3 Portfolio valuation and snapshots

```python
def value_portfolio(conn, cache: PriceCache) -> dict:
    cash = db.get_cash(conn)
    positions = []
    for p in db.get_positions(conn):
        current = cache.get_price(p.ticker)
        if current is None:          # Massive: held ticker not yet polled after restart
            current = p.avg_cost     # fall back so value isn't 0; P&L shows 0
        mv = p.quantity * current
        positions.append({
            "ticker": p.ticker, "quantity": p.quantity, "avg_cost": p.avg_cost,
            "current_price": current, "market_value": round(mv, 2),
            "unrealized_pnl": round(mv - p.quantity * p.avg_cost, 2),
            "unrealized_pnl_percent": round((current / p.avg_cost - 1) * 100, 2) if p.avg_cost else 0.0,
        })
    total = cash + sum(x["market_value"] for x in positions)
    return {"cash_balance": round(cash, 2), "total_value": round(total, 2),
            "unrealized_pnl": round(sum(x["unrealized_pnl"] for x in positions), 2),
            "positions": positions}


async def snapshot_loop(market: MarketService, every: float = 30.0) -> None:
    while True:
        with connect() as conn:
            db.record_snapshot(conn, value_portfolio(conn, market.cache)["total_value"])
        await asyncio.sleep(every)   # first iteration = the startup snapshot
```

### 10.4 Reset

```python
@router.post("/api/reset")
async def reset(market: MarketService = Depends(get_market)):
    with connect() as conn:
        db.reset_to_seed(conn, DEFAULT_TICKERS)          # one transaction (PLAN §7)
        await market.sync(DEFAULT_TICKERS)               # no positions → desired = defaults
        db.record_snapshot(conn, value_portfolio(conn, market.cache)["total_value"])
        return {"portfolio": value_portfolio(conn, market.cache), "watchlist": DEFAULT_TICKERS}
```

Prices for default tickers that stay tracked keep their current values and session open. Only tickers that are dropped and added again start a new session open. This matches the definition in PLAN §6.

---

## 11. Tests to add or update

All of these go in `backend/tests/market/` and use the existing pytest-asyncio auto mode.

### 11.1 `test_models.py` — add

```python
def test_session_change_percent_uses_open_price():
    u = PriceUpdate("AAPL", price=191.0, previous_price=190.5, timestamp=1.0, open_price=190.0)
    assert u.session_change_percent == 0.5263
    assert u.change_percent == 0.2625


def test_open_price_defaults_to_previous_price():
    u = PriceUpdate("AAPL", price=191.0, previous_price=190.0, timestamp=1.0)
    assert u.session_open == 190.0
    assert u.to_dict()["open_price"] == 190.0


def test_to_dict_has_session_fields():
    d = PriceUpdate("AAPL", 190.5, 190.0, 1.0, open_price=189.0).to_dict()
    assert {"open_price", "session_change_percent"} <= d.keys()
```

### 11.2 `test_cache.py` — add

```python
def test_open_price_set_on_first_update_and_kept():
    c = PriceCache()
    c.update("AAPL", 190.0)
    c.update("AAPL", 195.0)
    u = c.update("AAPL", 185.0)
    assert u.open_price == 190.0
    assert u.previous_price == 195.0


def test_readd_after_remove_resets_open():
    c = PriceCache()
    c.update("AAPL", 190.0)
    c.remove("AAPL")
    assert c.update("AAPL", 200.0).open_price == 200.0


def test_remove_bumps_version_only_when_present():
    c = PriceCache()
    c.update("AAPL", 190.0)
    v = c.version
    c.remove("AAPL")
    assert c.version == v + 1
    c.remove("AAPL")
    assert c.version == v + 1
```

### 11.3 `test_factory.py` — add

```python
@pytest.mark.parametrize("raw,expected", [("", 15.0), ("5", 5.0), ("0.5", 2.0), ("abc", 15.0)])
def test_poll_interval(monkeypatch, raw, expected):
    monkeypatch.setenv("MASSIVE_API_KEY", "k")
    monkeypatch.setenv("MASSIVE_POLL_SECONDS", raw)
    src = create_market_data_source(PriceCache())
    assert src._interval == expected
```

### 11.4 `test_massive.py` — add

```python
async def test_ticker_removed_during_poll_is_not_rewritten():
    cache = PriceCache()
    src = MassiveDataSource(api_key="k", price_cache=cache)
    src._client = MagicMock()
    src._tickers = ["AAPL", "PYPL"]

    def fetch(tickers):
        src._tickers = ["AAPL"]            # simulate remove_ticker mid-flight
        return [_snap("AAPL", 190.0), _snap("PYPL", 60.0)]

    src._fetch_snapshots = fetch
    await src._poll_once()
    assert "AAPL" in cache and "PYPL" not in cache


async def test_poll_failure_logs_type_only(caplog):
    src = MassiveDataSource(api_key="SECRET", price_cache=PriceCache())
    src._client = MagicMock()
    src._tickers = ["AAPL"]
    src._fetch_snapshots = MagicMock(side_effect=RuntimeError("https://x?apiKey=SECRET"))
    await src._poll_once()
    assert "SECRET" not in caplog.text
    assert "RuntimeError" in caplog.text
```

(`_snap(ticker, price)` builds a `MagicMock` with `.ticker`, `.last_trade.price` and `.last_trade.timestamp`, like the existing helpers.)

### 11.5 `test_service.py` — new

```python
class FakeSource(MarketDataSource):
    def __init__(self, cache): self.cache, self.t, self.calls = cache, [], []
    async def start(self, tickers): self.t = list(tickers)
    async def stop(self): pass
    async def add_ticker(self, t): self.calls.append(("add", t)); self.t.append(t); self.cache.update(t, 100.0)
    async def remove_ticker(self, t): self.calls.append(("rm", t)); self.t.remove(t); self.cache.remove(t)
    def get_tickers(self): return list(self.t)


async def test_sync_adds_and_removes_diff_only():
    cache = PriceCache(); src = FakeSource(cache); m = MarketService(cache, src)
    await m.start(["AAPL", "MSFT"])
    await m.sync({"AAPL", "PYPL"})
    assert src.calls == [("add", "PYPL"), ("rm", "MSFT")]
    await m.sync({"AAPL", "PYPL"})               # idempotent
    assert len(src.calls) == 2


async def test_held_ticker_stays_tracked_after_watchlist_remove():
    # desired = watchlist ∪ positions; caller computed {"AAPL"} ∪ {"PYPL"}
    cache = PriceCache(); src = FakeSource(cache); m = MarketService(cache, src)
    await m.start(["AAPL", "PYPL"])
    await m.sync({"AAPL"} | {"PYPL"})
    assert "PYPL" in m.tracked()


async def test_sync_before_start_is_noop():
    cache = PriceCache(); src = FakeSource(cache); m = MarketService(cache, src)
    await m.sync({"AAPL"})
    assert src.calls == []


async def test_full_default_set_cholesky_ok():
    from app.market.simulator import GBMSimulator
    from app.market.seed_prices import DEFAULT_TICKERS
    assert len(GBMSimulator(DEFAULT_TICKERS + ["PYPL", "ZZZ"]).step()) == 12
```

### 11.6 `test_stream.py` — new (the generator is tested directly, so no server is needed)

```python
class FakeRequest:
    def __init__(self, polls: int): self.n = polls
    async def is_disconnected(self):
        self.n -= 1
        return self.n < 0


async def collect(cache, polls, **kw):
    return [f async for f in _generate_events(cache, FakeRequest(polls), interval=0, **kw)]


async def test_first_frame_is_retry_then_data():
    c = PriceCache(); c.update("AAPL", 190.0)
    frames = await collect(c, 1)
    assert frames[0] == "retry: 1000\n\n"
    payload = json.loads(frames[1].removeprefix("data: "))
    assert payload["AAPL"]["session_change_percent"] == 0.0


async def test_no_duplicate_frame_without_change():
    c = PriceCache(); c.update("AAPL", 190.0)
    frames = await collect(c, 3, keepalive=999)
    assert sum(f.startswith("data:") for f in frames) == 1


async def test_empty_cache_after_remove_sends_empty_object():
    c = PriceCache(); c.update("AAPL", 190.0); c.remove("AAPL")
    frames = await collect(c, 1)
    assert frames[1] == "data: {}\n\n"


async def test_keepalive_comment():
    c = PriceCache(); c.update("AAPL", 190.0)
    frames = await collect(c, 3, keepalive=0)
    assert ": ping\n\n" in frames


def test_router_factory_is_fresh_each_call():
    assert len(create_stream_router(PriceCache()).routes) == 1
    assert len(create_stream_router(PriceCache()).routes) == 1
```

Run them with:

```bash
cd backend
uv run --extra dev pytest tests/market -v
uv run --extra dev ruff check app/ tests/
```

---

## 12. Edge cases and decisions

| Case | Behaviour |
|---|---|
| Empty tracked set at startup | Both sources start with no tickers. The SSE stream sends `{}` once and then keep-alives. |
| Unknown symbol, simulator | Random seed price between $50 and $300 with default sigma/mu. It can be traded immediately. |
| Unknown symbol, Massive | Tracked but never priced. The UI shows "—" and trades are rejected with "No price available". |
| Ticker removed from the watchlist while held | Still tracked and priced. It drops out only when the position closes. |
| Restart | Prices are not persisted. The simulator starts again from seed prices and the session open is reset, so P&L can jump. This is accepted (PLAN §6). |
| Massive 401/429/network error | Logged as the exception type only. The cache keeps the last prices and the next interval retries. |
| Massive outside market hours | `last_trade` is the last print, so the cache barely changes and keep-alives hold the stream open. |
| Two requests sync at once | `asyncio.Lock` in `MarketService` serialises them. Each one calls `sync` with the state it read from the DB. The last one wins, and because both are full desired sets, the result is correct. |
| Multiple SSE clients | Each has its own generator reading the shared cache. The cost is O(clients × tickers) every 500 ms, which is fine here. |
| Ticker case and whitespace | Normalised at the API boundary (strip, upper, regex). The market layer assumes tickers are already normalised. |
| `timestamp=0.0` passed to `cache.update` | Treated as "now" (`timestamp or time.time()`). Not reachable in practice. |

---

## 13. Implementation checklist

Do these in order. Each step leaves the test suite green.

1. [x] `models.py`: add `open_price`, `session_open`, `session_change_percent`, and update `to_dict()` (§3). Add the tests in §11.1.
2. [x] `cache.py`: carry `open_price` forward, make `remove()` bump `version`, read `version` under the lock (§4). Add the tests in §11.2.
3. [x] `seed_prices.py`: add `DEFAULT_TICKERS` (§7.3).
4. [x] `factory.py`: add `_poll_interval()` and `MASSIVE_POLL_SECONDS` (§6). Add the tests in §11.3.
5. [x] `massive_client.py`: pass a ticker-list copy to the thread, add the stale-ticker guard, log only the exception type (§5.2). Add the tests in §11.4.
6. [x] `service.py`: add `MarketService` and export it from `__init__.py` (§7.3). Add the tests in §11.5.
7. [x] `stream.py`: create the router per call, send empty events, add the keep-alive (§8). Add the tests in §11.6.
8. [x] Update `backend/CLAUDE.md` "Market Data API" to mention `MarketService`, `open_price` and `session_change_percent`.
9. [ ] Backend agent: `app/db/tracked_tickers()`, `app/main.py` with `create_app()`, `app/deps.py`, and calls to `market.sync()` from the watchlist, trade and reset paths (§9, §10).
10. [ ] Check by hand: `uv run uvicorn app.main:app`, then `curl -N localhost:8000/api/stream/prices` shows `session_change_percent`. Add a ticker and then remove it, and check that it appears in the stream and then disappears.

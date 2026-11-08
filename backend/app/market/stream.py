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

POLL_INTERVAL = 0.5  # seconds between cache checks
KEEPALIVE_SECONDS = 15.0  # send a comment line if nothing was sent for this long


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    """Create a fresh SSE router bound to `price_cache`.

    A new router is built on every call, so several apps (e.g. in tests) can
    each have their own without registering the route twice.
    """
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        """SSE endpoint: one event with all tracked tickers whenever the cache changes.

            data: {"AAPL": {"ticker": "AAPL", "price": 190.50, ...}, ...}

        A ticker missing from an event is no longer tracked. `{}` is sent when
        nothing is tracked. A `retry` directive makes the browser reconnect.
        """
        return StreamingResponse(
            _generate_events(price_cache, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering if proxied
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
    """Yield SSE frames until the client disconnects.

    The first data frame is always sent on connect; after that, a frame is sent
    only when the cache version changes. A `: ping` comment keeps idle streams
    open (Massive can leave the cache unchanged for 15s or longer).
    """
    yield "retry: 1000\n\n"  # browser reconnects after 1s

    client = request.client.host if getattr(request, "client", None) else "unknown"
    logger.debug("SSE client connected: %s", client)

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
    logger.debug("SSE client disconnected: %s", client)

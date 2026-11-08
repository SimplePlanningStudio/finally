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

    A new router is built on every call so the route is never registered twice.
    """
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        """SSE endpoint for live price updates.

        Whenever the cache changes, sends one event containing every tracked
        ticker (an empty object when nothing is tracked):

            data: {"AAPL": {"ticker": "AAPL", "price": 190.50, ...}, ...}
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
    data = {ticker: update.to_dict() for ticker, update in price_cache.get_all().items()}
    return f"data: {json.dumps(data, separators=(',', ':'))}\n\n"


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
    interval: float = POLL_INTERVAL,
    keepalive: float = KEEPALIVE_SECONDS,
) -> AsyncGenerator[str, None]:
    """Yield SSE frames until the client disconnects.

    Sends an event whenever the cache version changes (the first check always
    sends), and a `: ping` comment after `keepalive` seconds of silence.
    """
    # Tell the client to retry after 1 second if the connection drops
    yield "retry: 1000\n\n"

    last_version = -1
    last_sent = time.monotonic()
    logger.debug("SSE client connected")
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

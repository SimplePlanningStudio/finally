"""Tests for the SSE stream generator and router."""

import json

import pytest
from fastapi import FastAPI

from app.market.cache import PriceCache
from app.market.stream import _generate_events, create_stream_router, format_event


class FakeRequest:
    """Reports connected for `polls` checks, then disconnected."""

    def __init__(self, polls: int) -> None:
        self.remaining = polls

    async def is_disconnected(self) -> bool:
        self.remaining -= 1
        return self.remaining < 0


async def _collect(cache: PriceCache, polls: int, **kwargs) -> list[str]:
    return [f async for f in _generate_events(cache, FakeRequest(polls), interval=0, **kwargs)]


def _payload(frame: str) -> dict:
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    return json.loads(frame.removeprefix("data: "))


@pytest.mark.asyncio
class TestGenerateEvents:
    async def test_first_frame_is_retry_then_data(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        frames = await _collect(cache, 1)
        assert frames[0] == "retry: 1000\n\n"
        payload = _payload(frames[1])
        assert payload["AAPL"]["price"] == 190.0
        assert payload["AAPL"]["open_price"] == 190.0
        assert payload["AAPL"]["session_change_percent"] == 0.0

    async def test_no_duplicate_frame_without_change(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        frames = await _collect(cache, 3, keepalive=999)
        assert sum(f.startswith("data:") for f in frames) == 1

    async def test_empty_cache_sends_empty_object(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        cache.remove("AAPL")
        frames = await _collect(cache, 1)
        assert frames[1] == "data: {}\n\n"

    async def test_keepalive_comment(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        frames = await _collect(cache, 3, keepalive=0)
        assert ": ping\n\n" in frames

    async def test_stops_when_disconnected(self):
        frames = await _collect(PriceCache(), 0)
        assert frames == ["retry: 1000\n\n"]


def test_format_event_contains_all_tickers():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    cache.update("AAPL", 191.0)
    cache.update("MSFT", 420.0)
    payload = _payload(format_event(cache))
    assert set(payload) == {"AAPL", "MSFT"}
    assert payload["AAPL"]["direction"] == "up"
    assert payload["AAPL"]["session_change_percent"] == 0.5263


def test_router_factory_is_fresh_each_call():
    assert len(create_stream_router(PriceCache()).routes) == 1
    assert len(create_stream_router(PriceCache()).routes) == 1


def test_router_mounts_at_api_stream_prices():
    app = FastAPI()
    app.include_router(create_stream_router(PriceCache()))
    paths = {route.path for route in app.routes}
    assert "/api/stream/prices" in paths

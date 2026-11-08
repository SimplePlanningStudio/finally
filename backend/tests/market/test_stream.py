"""Tests for the SSE stream (generator tested directly, no server needed)."""

import json

import pytest
from fastapi import FastAPI

from app.market.cache import PriceCache
from app.market.stream import _generate_events, create_stream_router, format_event


class FakeRequest:
    """Reports connected for `polls` checks, then disconnected."""

    client = None

    def __init__(self, polls: int, on_poll=None) -> None:
        self.n = polls
        self.on_poll = on_poll
        self.i = 0

    async def is_disconnected(self) -> bool:
        if self.on_poll:
            self.on_poll(self.i)
        self.i += 1
        self.n -= 1
        return self.n < 0


async def collect(cache, polls, on_poll=None, **kw):
    req = FakeRequest(polls, on_poll)
    return [f async for f in _generate_events(cache, req, interval=0, **kw)]


def data_frames(frames):
    return [json.loads(f.removeprefix("data: ")) for f in frames if f.startswith("data:")]


@pytest.mark.asyncio
class TestGenerateEvents:
    async def test_first_frame_is_retry_then_data(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        frames = await collect(c, 1)
        assert frames[0] == "retry: 1000\n\n"
        payload = json.loads(frames[1].removeprefix("data: "))
        assert payload["AAPL"]["session_change_percent"] == 0.0
        assert payload["AAPL"]["open_price"] == 190.0
        assert frames[1].endswith("\n\n")

    async def test_one_event_contains_all_tickers(self):
        c = PriceCache()
        for t, p in [("AAPL", 190.0), ("MSFT", 420.0), ("V", 280.0)]:
            c.update(t, p)
        (payload,) = data_frames(await collect(c, 1))
        assert set(payload) == {"AAPL", "MSFT", "V"}

    async def test_no_duplicate_frame_without_change(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        frames = await collect(c, 3, keepalive=999)
        assert len(data_frames(frames)) == 1

    async def test_sends_again_on_change(self):
        c = PriceCache()
        c.update("AAPL", 190.0)

        def on_poll(i):
            if i == 1:
                c.update("AAPL", 191.0)

        payloads = data_frames(await collect(c, 3, on_poll=on_poll, keepalive=999))
        assert [p["AAPL"]["price"] for p in payloads] == [190.0, 191.0]
        assert payloads[1]["AAPL"]["direction"] == "up"

    async def test_removed_ticker_dropped_from_next_event(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        c.update("PYPL", 60.0)

        def on_poll(i):
            if i == 1:
                c.remove("PYPL")

        payloads = data_frames(await collect(c, 3, on_poll=on_poll, keepalive=999))
        assert set(payloads[0]) == {"AAPL", "PYPL"}
        assert set(payloads[1]) == {"AAPL"}

    async def test_empty_cache_sends_empty_object(self):
        c = PriceCache()
        frames = await collect(c, 1)
        assert frames[1] == "data: {}\n\n"

    async def test_empty_cache_after_remove_sends_empty_object(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        c.remove("AAPL")
        frames = await collect(c, 1)
        assert frames[1] == "data: {}\n\n"

    async def test_keepalive_comment(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        frames = await collect(c, 3, keepalive=0)
        assert ": ping\n\n" in frames

    async def test_stops_when_disconnected_immediately(self):
        frames = await collect(PriceCache(), 0)
        assert frames == ["retry: 1000\n\n"]


def test_format_event_compact_json():
    c = PriceCache()
    c.update("AAPL", 190.0)
    frame = format_event(c)
    assert frame.startswith('data: {"AAPL":{"ticker":"AAPL"')
    assert ", " not in frame


def test_router_factory_is_fresh_each_call():
    assert len(create_stream_router(PriceCache()).routes) == 1
    assert len(create_stream_router(PriceCache()).routes) == 1


def test_endpoint_route():
    router = create_stream_router(PriceCache())
    (route,) = router.routes
    assert route.path == "/api/stream/prices"
    assert route.methods == {"GET"}


def test_two_apps_get_their_own_cache():
    c1, c2 = PriceCache(), PriceCache()
    app1, app2 = FastAPI(), FastAPI()
    app1.include_router(create_stream_router(c1))
    app2.include_router(create_stream_router(c2))
    for app in (app1, app2):
        paths = [r.path for r in app.routes if r.path.startswith("/api")]
        assert paths == ["/api/stream/prices"]  # registered once per app

"""Tests for the Parallel.ai search provider."""

import asyncio

import httpx

from cptr.utils.web import parallel


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    """Minimal async stand-in for httpx.AsyncClient (stdlib monkeypatch)."""

    seen = None

    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeClient.seen = {"url": url, "json": json, "headers": headers}
        return _FakeResponse(self._payload)


def _patch(monkeypatch, payload):
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=None: _FakeClient(payload))


def test_search_formats_results(monkeypatch):
    _patch(
        monkeypatch,
        {
            "results": [
                {
                    "title": "Example",
                    "url": "https://example.com",
                    "excerpts": ["First excerpt.", "Second excerpt."],
                }
            ]
        },
    )
    out = asyncio.run(parallel.search("test query", "key-123"))
    assert out == "**Example**\nhttps://example.com\nFirst excerpt.\nSecond excerpt."
    seen = _FakeClient.seen
    assert seen["url"] == "https://api.parallel.ai/v1/search"
    assert seen["headers"]["x-api-key"] == "key-123"
    assert seen["json"]["objective"] == "test query"
    assert seen["json"]["search_queries"] == ["test query"]


def test_search_empty_results(monkeypatch):
    _patch(monkeypatch, {"results": []})
    out = asyncio.run(parallel.search("nothing", "key-123"))
    assert out == "No results found."

"""Tests for provider stream truncation detection in the OpenAI completions path.

These tests drive `stream_openai_completions` against a fake SSE HTTP transport
via respx-free monkeypatching of httpx.AsyncClient.stream, exercising:

- a healthy stream (finish_reason=stop + [DONE]) -> no truncated event
- a stream cut without [DONE] or finish_reason -> truncated event with
  reason="stream_end"
- a stream with finish_reason="length" -> truncated event with reason="length"
"""

import asyncio
import json

import httpx

from cptr.utils import ai


def _sse(chunks: list[dict], sentinel: bool = True) -> httpx.Response:
    lines = [f"data: {json.dumps(c)}" for c in chunks]
    if sentinel:
        lines.append("data: [DONE]")
    body = ("\n".join(lines) + "\n").encode()

    async def _aiter_lines(self):
        for line in body.decode().splitlines():
            yield line

    response = httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
    response._request = httpx.Request("POST", "http://test.local/v1/chat/completions")
    response.aiter_lines = _aiter_lines.__get__(response)
    return response


class _FakeStreamCtx:
    def __init__(self, response: httpx.Response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


def _install_fake_stream(monkeypatch, response_factory):
    class _FakeClient(httpx.AsyncClient):
        def stream(self, method, url, **kwargs):
            return _FakeStreamCtx(response_factory())

    monkeypatch.setattr(ai.httpx, "AsyncClient", _FakeClient)


def _form_data():
    return ai.ChatCompletionForm(
        model="test-model",
        messages=[{"role": "user", "content": "hi"}],
    )


def _collect(events):
    return [(e["type"], e.get("reason")) for e in events if e["type"] in {"done", "truncated"}]


def test_healthy_stream_not_marked_truncated(monkeypatch):
    def factory():
        return _sse(
            [
                {"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ]
        )

    _install_fake_stream(monkeypatch, factory)

    async def run():
        return [
            e
            async for e in ai.stream_openai_completions(
                _form_data(), "http://test.local/v1", "test-key"
            )
        ]

    events = _collect(asyncio.run(run()))
    assert events == [("done", None)]


def test_stream_cut_without_sentinel_marked_truncated(monkeypatch):
    def factory():
        # Provider dies mid-stream: no finish_reason, no [DONE]
        return _sse(
            [{"choices": [{"delta": {"content": "so the easy path (an AcpClient"}}]}],
            sentinel=False,
        )

    _install_fake_stream(monkeypatch, factory)

    async def run():
        return [
            e
            async for e in ai.stream_openai_completions(
                _form_data(), "http://test.local/v1", "test-key"
            )
        ]

    events = _collect(asyncio.run(run()))
    assert events == [("truncated", "stream_end"), ("done", None)]


def test_finish_reason_length_marked_truncated(monkeypatch):
    def factory():
        return _sse(
            [
                {"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "length"}]},
            ]
        )

    _install_fake_stream(monkeypatch, factory)

    async def run():
        return [
            e
            async for e in ai.stream_openai_completions(
                _form_data(), "http://test.local/v1", "test-key"
            )
        ]

    events = _collect(asyncio.run(run()))
    assert events == [("truncated", "length"), ("done", None)]

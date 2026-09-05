"""Tests for WhatsApp bridge delivery fixes.

Covers the bug where `_stream_loop` delivered the final reply via `edit()`
(a no-op on the send-only WhatsApp Cloud API), silently dropping every
normal-length reply:

- send-only adapters get their final reply via send() chunks (all of them)
- edit-capable adapters still use edit-based delivery, with send() fallback
  when the edit fails
- the WhatsApp typing indicator uses the real inbound wamid (not a
  placeholder) and the documented v23+ payload
- inbound webhook dedupe and interactive (button/list) replies
- optional webhook signature verification
"""

import asyncio
import hashlib
import hmac
import json

import httpx
import pytest

from cptr.utils.adapters.whatsapp import WhatsAppAdapter
from cptr.utils.bridge import BotManager, BaseAdapter, chunk_message


# ── Fakes ────────────────────────────────────────────────────


class _RecordingHTTP:
    """Minimal stand-in for httpx.AsyncClient used by the adapter."""

    def __init__(self, status_code: int = 200, response: dict | None = None):
        self.status_code = status_code
        self._response = response or {}
        self.calls: list[dict] = []

    async def post(self, url, json=None, **kwargs):
        self.calls.append({"url": url, "json": json})
        return httpx.Response(
            self.status_code,
            json=self._response or {"messages": [{"id": f"wamid.SENT{len(self.calls)}"}]},
            request=httpx.Request("POST", url),
        )

    async def get(self, url, **kwargs):
        self.calls.append({"url": url, "json": None})
        return httpx.Response(
            200, json={"verified_name": "TestBot"}, request=httpx.Request("GET", url)
        )


class _FakeAdapter(BaseAdapter):
    """Configurable fake adapter for bridge loop tests."""

    platform = "fake"

    def __init__(self, supports_edit: bool = True, edit_fails: bool = False):
        self.supports_edit = supports_edit
        self.edit_fails = edit_fails
        self.sent: list[str] = []
        self.edits: list[str] = []
        self.typing_calls = 0

    async def connect(self):
        return True

    async def disconnect(self):
        pass

    async def send(self, chat_id, text):
        self.sent.append(text)
        return f"m{len(self.sent)}"

    async def edit(self, chat_id, message_id, text):
        if self.edit_fails:
            raise RuntimeError("edit rejected")
        self.edits.append(text)

    async def send_typing(self, chat_id):
        self.typing_calls += 1


def _make_adapter(token: str = "tok|123") -> tuple[WhatsAppAdapter, _RecordingHTTP]:
    adapter = WhatsAppAdapter(token=token, bot_id="b1")
    adapter._http = _RecordingHTTP()  # bypass connect()
    return adapter, adapter._http


# ── Adapter: typing indicator ────────────────────────────────


def test_send_typing_uses_real_inbound_wamid():
    adapter, http = _make_adapter()
    adapter._last_inbound_message_id = "wamid.INBOUND1"

    asyncio.run(adapter.send_typing("15551234567"))

    assert len(http.calls) == 1
    body = http.calls[0]["json"]
    assert body["message_id"] == "wamid.INBOUND1"
    assert body["status"] == "read"
    assert body["typing_indicator"] == {"type": "text"}
    assert body["messaging_product"] == "whatsapp"


def test_send_typing_skips_without_inbound_id():
    """No fake 'placeholder' id — before any inbound message, send nothing."""
    adapter, http = _make_adapter()
    assert adapter._last_inbound_message_id is None

    asyncio.run(adapter.send_typing("15551234567"))

    assert http.calls == []


def test_api_version_supports_typing_indicators():
    from cptr.utils.adapters.whatsapp import API_BASE

    version = float(API_BASE.rstrip("/").split("/v")[-1])
    assert version >= 23, "typing indicators require Graph API >= v23.0"


# ── Adapter: webhook ingestion ───────────────────────────────


def _webhook_payload(msg_id: str, msg_type: str = "text", extra: dict | None = None):
    message = {
        "from": "15551234567",
        "id": msg_id,
        "timestamp": "1",
        "type": msg_type,
        "text": {"body": "hi"},
    }
    message.update(extra or {})
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [message],
                            "contacts": [{"wa_id": "15551234567", "profile": {"name": "K"}}],
                        }
                    }
                ]
            }
        ]
    }


def _run_webhooks(adapter, payloads):
    """Feed payloads through handle_webhook, then drain the queue exactly
    as the adapter's process loop would (deterministically, no timing)."""

    async def _scenario():
        for payload in payloads:
            await adapter.handle_webhook(payload)
        while not adapter._message_queue.empty():
            event = adapter._message_queue.get_nowait()
            if adapter.on_message:
                await adapter.on_message(event)

    asyncio.run(_scenario())


def test_webhook_tracks_inbound_id_and_dedupes_redeliveries():
    adapter, _ = _make_adapter()
    events: list = []

    async def _on_message(event):
        events.append(event)

    adapter.on_message = _on_message

    _run_webhooks(
        adapter,
        [
            _webhook_payload("wamid.A"),
            _webhook_payload("wamid.A"),  # Meta retry
            _webhook_payload("wamid.B"),
        ],
    )

    assert len(events) == 2  # duplicate dropped
    assert adapter._last_inbound_message_id == "wamid.B"


def test_webhook_interactive_button_reply_becomes_text():
    adapter, _ = _make_adapter()
    events: list = []

    async def _on_message(event):
        events.append(event)

    adapter.on_message = _on_message

    payload = _webhook_payload(
        "wamid.C",
        msg_type="interactive",
        extra={
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": "b1", "title": "Run tests"},
            }
        },
    )
    _run_webhooks(adapter, [payload])

    assert len(events) == 1
    assert events[0].text == "Run tests"


def test_webhook_interactive_list_reply_becomes_text():
    adapter, _ = _make_adapter()
    events: list = []

    async def _on_message(event):
        events.append(event)

    adapter.on_message = _on_message

    payload = _webhook_payload(
        "wamid.D",
        msg_type="interactive",
        extra={
            "interactive": {
                "type": "list_reply",
                "list_reply": {"id": "r2", "title": "Deploy"},
            }
        },
    )
    _run_webhooks(adapter, [payload])

    assert events[0].text == "Deploy"


# ── Adapter: send-only contract ──────────────────────────────


def test_whatsapp_adapter_is_send_only():
    adapter, _ = _make_adapter()
    assert adapter.supports_edit is False


def test_edit_logs_warning_and_does_not_send(caplog):
    adapter, http = _make_adapter()

    with caplog.at_level("WARNING", logger="cptr.utils.adapters.whatsapp"):
        asyncio.run(adapter.edit("15551234567", "wamid.X", "should not send"))

    assert http.calls == []
    assert any("cannot be edited" in r.message for r in caplog.records)


def test_send_failure_is_logged_with_status(caplog):
    adapter, _ = _make_adapter()
    adapter._http = _RecordingHTTP(status_code=400, response={"error": {"message": "bad token"}})

    with caplog.at_level("WARNING", logger="cptr.utils.adapters.whatsapp"):
        asyncio.run(adapter.send("15551234567", "hi"))

    assert any("Send failed" in r.message and "400" in r.message for r in caplog.records)


# ── Bridge: final delivery via _stream_loop ──────────────────


class _FakeTask:
    def done(self):
        return True


def _install_bridge_state(monkeypatch, final_content: str):
    """Patch chat_task state + ChatMessage so _stream_loop sees a done task."""
    from cptr.models import ChatMessage
    from cptr.utils import chat_task

    task = _FakeTask()
    monkeypatch.setattr(chat_task, "_tasks", {"msg1": task})
    monkeypatch.setattr(chat_task, "_task_state", {"msg1": {"content": "", "output": []}})

    class _FakeMsg:
        content = final_content

    async def _get_by_id(mid):
        return _FakeMsg()

    monkeypatch.setattr(ChatMessage, "get_by_id", _get_by_id)


def _run_stream_loop(adapter, monkeypatch, final_content="Hello final reply", placeholder=None):
    _install_bridge_state(monkeypatch, final_content)
    bot = {
        "id": "bot1",
        "platform": adapter.platform,
        "user_id": "u",
        "workspace": "w",
        "model_id": "m",
    }
    manager = BotManager.__new__(BotManager)  # skip __init__ (app deps)
    asyncio.run(manager._stream_loop(adapter, "chat1", placeholder, "msg1", bot))


def test_send_only_platform_gets_final_reply_via_send(monkeypatch):
    """THE bug: WhatsApp (send-only) must receive the final reply via send()."""
    adapter = _FakeAdapter(supports_edit=False)

    _run_stream_loop(adapter, monkeypatch, "Hello final reply")

    assert adapter.sent == ["Hello final reply"]
    assert adapter.edits == []


def test_send_only_platform_multichunk_sends_all_chunks(monkeypatch):
    adapter = _FakeAdapter(supports_edit=False)

    long_text = "para one\n\npara two\n\npara three\n\n" + " ".join(["word"] * 900)
    chunks = chunk_message(long_text, 2000)
    assert len(chunks) > 1

    _run_stream_loop(adapter, monkeypatch, long_text)

    assert adapter.sent == chunks
    assert "".join(adapter.sent) == long_text


def test_edit_platform_still_uses_edit(monkeypatch):
    adapter = _FakeAdapter(supports_edit=True)

    _run_stream_loop(adapter, monkeypatch, "Hello final reply", placeholder="ph1")

    assert adapter.edits == ["Hello final reply"]
    assert adapter.sent == []


def test_edit_failure_falls_back_to_send_not_drop(monkeypatch):
    """Edit-capable platform whose edit fails must not lose the reply."""
    adapter = _FakeAdapter(supports_edit=True, edit_fails=True)

    _run_stream_loop(adapter, monkeypatch, "Hello final reply", placeholder="ph1")

    assert adapter.sent == ["Hello final reply"]


def test_send_only_platform_empty_reply_sends_done_notice(monkeypatch):
    adapter = _FakeAdapter(supports_edit=False)

    _run_stream_loop(adapter, monkeypatch, "")

    assert adapter.sent == ["✅ Done (no text output)"]


# ── Webhook signature verification ───────────────────────────


def test_signature_verification_roundtrip():
    from cptr.routers.bridge import _verify_whatsapp_signature

    body = json.dumps({"some": "payload"}).encode()
    secret = "shhh"
    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert _verify_whatsapp_signature(body, good, secret) is True
    assert _verify_whatsapp_signature(body, "sha256=" + "0" * 64, secret) is False
    assert _verify_whatsapp_signature(body, None, secret) is False
    assert _verify_whatsapp_signature(body, "sha256=deadbeef", secret) is False

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
import time

import httpx

from cptr.utils.adapters.whatsapp import WhatsAppAdapter
from cptr.utils.bridge import BaseAdapter, BotManager, chunk_message

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
    import time as _time

    message = {
        "from": "15551234567",
        "id": msg_id,
        # Fresh timestamp, matching a live Meta delivery. (The staleness
        # guard drops webhooks older than ~5 minutes; tests use a distinct
        # old-timestamp payload to verify that behavior explicitly.)
        "timestamp": str(int(_time.time())),
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


# ── Typing refresh on send-only platforms (the dead-branch bug) ──


def test_send_only_platform_sends_typing(monkeypatch):
    """Send-only adapters must get typing indicator calls (was unreachable)."""
    adapter = _FakeAdapter(supports_edit=False)

    _run_stream_loop(adapter, monkeypatch, "Hello final reply")

    assert adapter.typing_calls >= 1, "send-only platform never got a typing indicator"
    assert adapter.sent == ["Hello final reply"]  # reply still delivered once


def test_send_only_typing_refresh_throttled(monkeypatch):
    """Typing refreshes respect TYPING_INTERVAL, not one per 2s poll tick."""
    import cptr.utils.bridge as bridge_mod

    adapter = _FakeAdapter(supports_edit=False)

    # Simulate a long task: not done on the first calls, done on the last.
    calls = {"n": 0}

    class _SlowTask:
        def done(self):
            calls["n"] += 1
            return calls["n"] > 12  # ~12 polls

    from cptr.models import ChatMessage
    from cptr.utils import chat_task

    monkeypatch.setattr(chat_task, "_tasks", {"msg1": _SlowTask()})
    monkeypatch.setattr(chat_task, "_task_state", {"msg1": {"content": "", "output": []}})

    class _FakeMsg:
        content = "final"

    async def _get_by_id(mid):
        return _FakeMsg()

    monkeypatch.setattr(ChatMessage, "get_by_id", _get_by_id)

    monkeypatch.setattr(bridge_mod, "STREAM_EDIT_INTERVAL", 0.0)  # no real sleeping
    bot = {
        "id": "bot1",
        "platform": adapter.platform,
        "user_id": "u",
        "workspace": "w",
        "model_id": "m",
    }
    manager = BotManager.__new__(BotManager)
    asyncio.run(manager._stream_loop(adapter, "chat1", None, "msg1", bot))

    # 13 ticks over ~0s wall time → throttle must cap typing calls far below
    # the tick count (previously the code path fired every tick or never).
    assert adapter.typing_calls <= 2, f"typing fired {adapter.typing_calls}x — throttle broken"


# ── Staleness guard + persisted dedupe ───────────────────────


def test_stale_unseen_message_processed_with_warning(caplog):
    """A stale but never-seen message is PROCESSED (with a warning), not dropped.

    Semantics after the audit fix: dedupe is the primary defense (seen ids),
    so a legitimately new but late-delivered message (user offline, Meta
    queueing) still reaches the agent. Previously it was silently dropped.
    """
    import time as _time

    adapter, _ = _make_adapter()
    events: list = []

    async def _on_message(event):
        events.append(event)

    adapter.on_message = _on_message
    stale = int(_time.time()) - 3600  # 1h old — beyond _max_inbound_age
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "15551234567",
                                    "id": "wamid.STALE1",
                                    "timestamp": str(stale),
                                    "type": "text",
                                    "text": {"body": "late but legit"},
                                }
                            ],
                            "contacts": [{"wa_id": "15551234567", "profile": {"name": "K"}}],
                        }
                    }
                ]
            }
        ]
    }
    _run_webhooks(adapter, [payload])
    assert len(events) == 1, "stale but UNSEEN message must still be processed"
    assert events[0].text == "late but legit"

    # A redelivery of the same stale message is still dropped (dedupe wins)
    _run_webhooks(adapter, [payload])
    assert len(events) == 1, "redelivered stale message must be deduped"


def test_seen_ids_persist_across_reinstants(monkeypatch):
    """Dedup state must survive adapter restart (persisted to data dir)."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr("cptr.utils.adapters.whatsapp.DATA_DIR", Path(tmp))
        seen_path = Path(tmp) / "bridge" / "whatsapp_seen_b1.json"

        adapter1, _ = _make_adapter()
        adapter1._seen_ids_path = seen_path
        fresh = str(int(time.time()))
        payload = _webhook_payload("wamid.PERSIST1")
        payload["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"] = fresh
        _run_webhooks(adapter1, [payload])
        adapter1._save_seen_ids()
        assert seen_path.is_file(), "seen ids were not persisted"

        # "Restart": a new adapter instance loads the persisted ids
        adapter2, _ = _make_adapter()
        adapter2._seen_ids_path = seen_path
        adapter2._load_seen_ids()
        assert "wamid.PERSIST1" in adapter2._seen_message_ids

        # Redelivery of the same id after restart is dropped
        events: list = []

        async def _on_message(event):
            events.append(event)

        adapter2.on_message = _on_message
        _run_webhooks(adapter2, [payload])
        assert events == [], "redelivered message processed after restart"


def test_typing_without_inbound_is_noop():
    """send_typing/mark_read before any inbound message must not POST."""
    adapter, http = _make_adapter()
    adapter._last_inbound_message_id = None
    asyncio.run(adapter.send_typing("chat1"))
    asyncio.run(adapter.mark_read("chat1"))
    assert http.calls == []


def test_mark_read_explicit_message_id():
    """mark_read uses the given wamid (per-message receipts), not just last."""
    adapter, http = _make_adapter()
    adapter._last_inbound_message_id = "wamid.OLD"
    asyncio.run(adapter.mark_read("chat1", "wamid.NEW"))
    assert http.calls[-1]["json"]["message_id"] == "wamid.NEW"
    assert "typing_indicator" not in http.calls[-1]["json"]


# ── Task-watcher (dequeued batch delivery) ───────────────────


def test_task_watcher_registered_and_fires():
    """on_task_started registers, fires per start_task, and unregisters."""
    from cptr.utils import chat_task

    seen: list[tuple[str, str]] = []
    unregister = chat_task.on_task_started(lambda cid, mid: seen.append((cid, mid)))
    try:
        chat_task._notify_task_watchers("chat-abc", "msg-123")
        chat_task._notify_task_watchers("chat-abc", "msg-456")
        assert seen == [("chat-abc", "msg-123"), ("chat-abc", "msg-456")]
    finally:
        unregister()
    chat_task._notify_task_watchers("chat-abc", "msg-789")
    assert seen == [("chat-abc", "msg-123"), ("chat-abc", "msg-456")]


def test_task_watcher_swallows_observer_exceptions():
    """A raising observer must not break start_task or other observers."""
    from cptr.utils import chat_task

    ok_calls: list[str] = []

    def _boom(cid, mid):
        raise RuntimeError("observer exploded")

    unregister = chat_task.on_task_started(_boom)
    try:
        chat_task.on_task_started(lambda cid, mid: ok_calls.append(mid))
        chat_task._notify_task_watchers("chat-abc", "msg-123")  # must not raise
        assert ok_calls == ["msg-123"]
    finally:
        unregister()


def test_start_task_notifies_watchers(monkeypatch):
    """start_task itself fires the watcher (this is the C-fix hook)."""
    import asyncio as _aio

    from cptr.utils import chat_task

    seen: list[tuple[str, str]] = []
    unregister = chat_task.on_task_started(lambda cid, mid: seen.append((cid, mid)))
    try:
        created: dict = {}

        class _FakeTaskObj:
            def done(self):
                return False

        def _fake_create_task(coro):
            created["coro"] = coro
            return _FakeTaskObj()

        async def _never_finish(*args, **kwargs):
            await _aio.sleep(3600)

        monkeypatch.setattr(chat_task.asyncio, "create_task", _fake_create_task)
        monkeypatch.setattr(chat_task, "run_chat_task", _never_finish)

        async def _scenario():
            chat_task.start_task(
                request=None,
                message_id="mid-1",
                chat_id="cid-1",
                user_id="u1",
                workspace="w1",
                connection={"id": "x"},
            )

        _aio.run(_scenario())
        coro = created.get("coro")
        if coro is not None:
            coro.close()  # don't leave the never-ending coroutine pending
        chat_task._tasks.pop("mid-1", None)
        chat_task._task_chat.pop("mid-1", None)
        assert seen == [("cid-1", "mid-1")]
    finally:
        unregister()

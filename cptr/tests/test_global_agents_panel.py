"""Tests for the global running-agents panel backend (GET /api/chats/active).

Covers:
- get_active_tasks() maps chat_id -> running message_id (done tasks excluded)
- list_async_subagents() user_id scoping is fail-closed
- the /active endpoint payload: only the caller's non-internal active chats,
  only starting/running subagents, and JSON-serializable throughout
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import cptr.routers.chat as chat_router
from cptr.models.chats import Chat
from cptr.utils import chat_task as chat_task_mod
from cptr.utils.async_subagents import (
    _reset_for_tests,
    list_async_subagents,
    reserve_async_subagent,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    asyncio.run(_reset_for_tests())
    yield
    asyncio.run(_reset_for_tests())


def _task(done: bool):
    return SimpleNamespace(done=lambda: done)


def _get_active_tasks_with(tasks, task_chat):
    """Run get_active_tasks with fabricated registries (no live tasks)."""
    orig_tasks, orig_chat = chat_task_mod._tasks, chat_task_mod._task_chat
    chat_task_mod._tasks, chat_task_mod._task_chat = tasks, task_chat
    try:
        return chat_task_mod.get_active_tasks()
    finally:
        chat_task_mod._tasks, chat_task_mod._task_chat = orig_tasks, orig_chat


def test_get_active_tasks_excludes_done():
    mapping = _get_active_tasks_with(
        {"m1": _task(False), "m2": _task(True)}, {"m1": "c1", "m2": "c2"}
    )
    assert mapping == {"c1": "m1"}


def test_list_async_subagents_user_scoping_is_fail_closed():
    async def scenario():
        a = await reserve_async_subagent(20, parent_chat_id="chat-a", user_id="u1", task="task a")
        b = await reserve_async_subagent(20, parent_chat_id="chat-b", user_id="u2", task="task b")
        assert a["status"] == "reserved"
        assert b["status"] == "reserved"
        return a["delegation_id"], b["delegation_id"]

    deleg_a, deleg_b = asyncio.run(scenario())

    mine = list_async_subagents(user_id="u1")
    assert [r["delegation_id"] for r in mine] == [deleg_a]
    assert all("request" not in r and "runner_task" not in r for r in mine)
    # Other user's records never leak, even unscoped-by-parent.
    assert deleg_b not in [r["delegation_id"] for r in list_async_subagents(user_id="u1")]
    json.dumps(mine)


def test_list_active_chats_endpoint(monkeypatch):
    async def scenario():
        a = await reserve_async_subagent(20, parent_chat_id="c1", user_id="u1", task="do thing")
        b = await reserve_async_subagent(
            20, parent_chat_id="c9", user_id="u2", task="other user work"
        )
        return a["delegation_id"], b["delegation_id"]

    deleg_mine, deleg_theirs = asyncio.run(scenario())

    monkeypatch.setattr(chat_router, "_get_user", lambda request: "u1")
    monkeypatch.setattr(
        chat_task_mod, "get_active_tasks", lambda: {"c1": "m1", "c2": "m2", "c3": "m3"}
    )

    async def fake_get_by_ids(chat_ids):
        assert set(chat_ids) == {"c1", "c2", "c3"}
        return [
            SimpleNamespace(
                id="c1",
                user_id="u1",
                title="Chat one",
                meta={"workspace": "/ws"},
                updated_at=2000,
            ),
            SimpleNamespace(id="c2", user_id="other", title="Chat two", meta={}, updated_at=3000),
            SimpleNamespace(
                id="c3",
                user_id="u1",
                title="Timer",
                meta={"internal": True},
                updated_at=4000,
            ),
        ]

    monkeypatch.setattr(Chat, "get_by_ids", fake_get_by_ids)

    req = Request({"type": "http", "method": "GET", "path": "/api/chats/active", "headers": []})
    payload = asyncio.run(chat_router.list_active_chats(req))

    # Other user's chat (c2) and internal chat (c3) are excluded.
    assert [c["chat_id"] for c in payload["active_chats"]] == ["c1"]
    chat = payload["active_chats"][0]
    assert chat["workspace"] == "/ws"
    assert chat["title"] == "Chat one"
    assert chat["message_id"] == "m1"

    # Only my starting/running subagents.
    assert [s["delegation_id"] for s in payload["subagents"]] == [deleg_mine]
    assert deleg_theirs not in [s["delegation_id"] for s in payload["subagents"]]

    # The whole payload must be JSON-safe (records hold live runtime objects).
    json.dumps(payload)

"""Tests for the async subagent registry helpers."""

import asyncio

import pytest

from cptr.utils.async_subagents import (
    _reset_for_tests,
    _set_completion_injector_for_tests,
    cancel_async_subagent,
    list_async_subagents,
    reserve_async_subagent,
    start_async_subagent,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    asyncio.run(_reset_for_tests())
    _set_completion_injector_for_tests(None)
    yield
    asyncio.run(_reset_for_tests())
    _set_completion_injector_for_tests(None)


def _no_completion(record):
    async def _inject(_snapshot):
        return None

    _set_completion_injector_for_tests(_inject)


def test_cancel_unknown_delegation_returns_false():
    assert asyncio.run(cancel_async_subagent("deleg_unknown")) is False


def test_list_filters_by_parent_chat_and_hides_task():
    async def scenario():
        reserve_a = await reserve_async_subagent(20, parent_chat_id="chat-a", user_id="u")
        reserve_b = await reserve_async_subagent(20, parent_chat_id="chat-b", user_id="u")
        assert reserve_a["status"] == "reserved"
        assert reserve_b["status"] == "reserved"

        for reserve in (reserve_a, reserve_b):

            async def _runner():
                return "done"

            await start_async_subagent(reserve["delegation_id"], _runner)
            # Let the runner finish and _finalize run
            await asyncio.sleep(0.05)

        only_a = list_async_subagents("chat-a")
        assert [r["delegation_id"] for r in only_a] == [reserve_a["delegation_id"]]
        # Whitelisted fields only: runtime objects (request/connection/task
        # handle) must never leak into the snapshot.
        assert all("request" not in r and "connection" not in r for r in only_a)
        assert only_a[0]["status"] == "completed"

        everything = list_async_subagents()
        assert len(everything) == 2

    asyncio.run(scenario())


def test_cancel_running_subagent_marks_interrupted():
    _no_completion(None)

    async def scenario():
        reserve = await reserve_async_subagent(
            20,
            parent_chat_id="chat-x",
            user_id="u",
            request=None,
            task="t",
            context="",
            workspace="",
            model="m",
        )
        delegation_id = reserve["delegation_id"]

        async def _runner():
            await asyncio.sleep(30)

        await start_async_subagent(delegation_id, _runner)
        await asyncio.sleep(0)  # let the runner task take its first step
        running = list_async_subagents("chat-x")[0]
        assert running["status"] in {"starting", "running"}
        assert await cancel_async_subagent(delegation_id) is True
        await asyncio.sleep(0.05)
        cancelled = list_async_subagents("chat-x")[0]
        assert cancelled["status"] == "interrupted"
        assert cancelled["error"] == "cancelled"

        # Second cancel is a no-op
        assert await cancel_async_subagent(delegation_id) is False

    asyncio.run(scenario())

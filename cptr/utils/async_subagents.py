"""Session-scoped async subagent registry.

Background subagents are deliberately not durable jobs. They run in the
current server process, keep a small in-memory status record, and inject their
final summary back into the parent chat when they finish.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from cptr.models import Chat, ChatMessage
from cptr.socket.main import emit_to_user
from cptr.utils.chat_export import export_chat_to_file
from cptr.utils.config import now_ms

logger = logging.getLogger(__name__)

_records: dict[str, dict[str, Any]] = {}
_lock = asyncio.Lock()
_MAX_RETAINED_COMPLETED = 50
_completion_injector_override: Callable[[dict[str, Any]], Awaitable[None]] | None = None


def _new_delegation_id() -> str:
    return f"deleg_{uuid.uuid4().hex[:8]}"


async def active_count() -> int:
    """Return the number of running background subagents."""
    async with _lock:
        return sum(1 for r in _records.values() if r.get("status") in {"starting", "running"})


async def reserve_async_subagent(max_async: int, **record: Any) -> dict[str, Any]:
    """Reserve capacity for a background subagent before creating its chat."""
    max_async = int(max_async or 20)
    if max_async != -1:
        max_async = max(1, max_async)
    async with _lock:
        running = sum(1 for r in _records.values() if r.get("status") in {"starting", "running"})
        if max_async != -1 and running >= max_async:
            return {
                "status": "rejected",
                "error": (
                    f"Async subagent capacity reached ({max_async} running). "
                    "Wait for one to finish or increase subagents.max_async."
                ),
            }

        delegation_id = _new_delegation_id()
        now = time.time()
        _records[delegation_id] = {
            **record,
            "delegation_id": delegation_id,
            "status": "starting",
            "dispatched_at": now,
            "completed_at": None,
            "task": None,
            "error": None,
            "summary": None,
        }
        return {"status": "reserved", "delegation_id": delegation_id}


async def attach_subagent_chat(
    delegation_id: str,
    *,
    subagent_chat_id: str,
    subagent_message_id: str,
) -> None:
    async with _lock:
        record = _records.get(delegation_id)
        if record:
            record["subagent_chat_id"] = subagent_chat_id
            record["subagent_message_id"] = subagent_message_id
            record["status"] = "running"


async def start_async_subagent(
    delegation_id: str,
    runner: Callable[[], Awaitable[str]],
) -> None:
    """Start a reserved subagent runner and return immediately."""

    async def _run() -> None:
        status = "completed"
        summary = ""
        error = None
        try:
            summary = await runner()
        except asyncio.CancelledError:
            status = "interrupted"
            error = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 - completion must still be recorded
            logger.exception("Async subagent %s failed", delegation_id)
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
        finally:
            await _finalize(delegation_id, status=status, summary=summary, error=error)

    task = asyncio.create_task(_run())
    async with _lock:
        record = _records.get(delegation_id)
        if record:
            record["task"] = task


async def fail_reserved_subagent(delegation_id: str, error: str) -> None:
    await _finalize(delegation_id, status="error", summary="", error=error)


async def cancel_all_async_subagents(reason: str = "shutdown") -> int:
    """Cancel all running background subagents."""
    async with _lock:
        tasks = [
            r.get("task")
            for r in _records.values()
            if r.get("status") in {"starting", "running"} and r.get("task")
        ]
    count = 0
    for task in tasks:
        if task and not task.done():
            task.cancel()
            count += 1
    if count:
        logger.info("Cancelled %d async subagent(s) (%s)", count, reason)
    return count


def list_async_subagents() -> list[dict[str, Any]]:
    """Return a serializable snapshot of records."""
    snapshot = []
    for record in _records.values():
        snapshot.append({k: v for k, v in record.items() if k != "task"})
    return snapshot


async def _finalize(
    delegation_id: str,
    *,
    status: str,
    summary: str,
    error: str | None,
) -> None:
    async with _lock:
        record = _records.get(delegation_id)
        if not record:
            return
        record["status"] = status
        record["summary"] = summary
        record["error"] = error
        record["completed_at"] = time.time()
        snapshot = {k: v for k, v in record.items() if k != "task"}
        _prune_completed_locked()

    injector = _completion_injector_override or _inject_completion
    try:
        await injector(snapshot)
    except Exception:
        logger.exception("Failed to inject async subagent completion %s", delegation_id)


def _prune_completed_locked() -> None:
    completed = [
        (rid, r) for rid, r in _records.items() if r.get("status") not in {"starting", "running"}
    ]
    if len(completed) <= _MAX_RETAINED_COMPLETED:
        return
    completed.sort(key=lambda kv: kv[1].get("completed_at") or kv[1].get("dispatched_at") or 0)
    for rid, _ in completed[: len(completed) - _MAX_RETAINED_COMPLETED]:
        _records.pop(rid, None)


async def _inject_completion(record: dict[str, Any]) -> None:
    parent_chat_id = record.get("parent_chat_id")
    user_id = record.get("user_id")
    if not parent_chat_id or not user_id:
        return

    chat = await Chat.get_by_id(parent_chat_id)
    if not chat:
        return

    model_id = _parent_model_id(chat, record)
    content = _format_completion(record)

    from cptr.utils.chat_task import get_pending_input_lock, start_task

    assistant_msg = None
    direct_timer_completion = False
    async with get_pending_input_lock(parent_chat_id):
        all_msgs = await ChatMessage.get_all_by_chat(parent_chat_id)
        active = any(m.role == "assistant" and not m.done for m in all_msgs)
        done_assistants = [m for m in all_msgs if m.role == "assistant" and m.done]
        parent_id = done_assistants[-1].id if done_assistants else record.get("parent_message_id")

        meta = {
            "internal": True,
            "type": "timer" if record.get("timer_chat_id") else "subagent",
            "delegation_id": record.get("delegation_id"),
            "subagent_chat_id": record.get("subagent_chat_id"),
        }
        if active:
            meta["status"] = "pending"

        if record.get("timer_chat_id") and not active:
            direct_timer_completion = True
            meta["timer_completion"] = True
            assistant_msg = await ChatMessage.create(
                chat_id=parent_chat_id,
                role="assistant",
                content=record.get("summary") or "Timer completed.",
                parent_id=parent_id,
                model=model_id,
                done=True,
                meta=meta,
                created_at=now_ms(),
            )
            await Chat.update_current_message(parent_chat_id, assistant_msg.id, now_ms())
        else:
            user_msg = await ChatMessage.create(
                chat_id=parent_chat_id,
                role="user",
                content=content,
                parent_id=parent_id,
                model=model_id,
                meta=meta,
                created_at=now_ms(),
            )

            if not active:
                assistant_msg = await ChatMessage.create(
                    chat_id=parent_chat_id,
                    role="assistant",
                    content="",
                    parent_id=user_msg.id,
                    model=model_id,
                    done=False,
                    created_at=now_ms(),
                )
                await Chat.update_current_message(parent_chat_id, assistant_msg.id, now_ms())

    request = record["request"]
    await export_chat_to_file(request, parent_chat_id)

    if direct_timer_completion:
        await emit_to_user(
            user_id,
            {
                "chat_id": parent_chat_id,
                "message_id": assistant_msg.id,
                "pending_inputs_processed": True,
            },
        )
        return

    if not assistant_msg:
        await emit_to_user(
            user_id,
            {
                "chat_id": parent_chat_id,
                "message_id": user_msg.id,
                "pending_inputs_processed": True,
            },
        )
        return

    await emit_to_user(
        user_id,
        {
            "chat_id": parent_chat_id,
            "message_id": assistant_msg.id,
            "pending_inputs_processed": True,
        },
    )

    start_task(
        request,
        message_id=assistant_msg.id,
        chat_id=parent_chat_id,
        user_id=user_id,
        connection=record["connection"],
        workspace=record["workspace"],
        model=record["model"],
    )


def _parent_model_id(chat: Chat, record: dict[str, Any]) -> str:
    meta = chat.meta or {}
    return str(meta.get("last_model") or record.get("model_id") or record.get("model") or "")


def _format_completion(record: dict[str, Any]) -> str:
    status = record.get("status") or "completed"
    summary = record.get("summary") or ""
    error = record.get("error")
    dispatched_at = record.get("dispatched_at")
    completed_at = record.get("completed_at") or time.time()
    duration = ""
    if isinstance(dispatched_at, (int, float)):
        duration = f"{completed_at - dispatched_at:.1f}s"

    lines = [
        f"[ASYNC SUBAGENT COMPLETE - {record.get('delegation_id', 'unknown')}]",
        (
            "A background subagent you dispatched earlier has finished. "
            "The original task source is included so you can decide whether "
            "to use the result or continue without it."
        ),
        "",
        f"Original task: {record.get('task', '')}",
    ]
    if record.get("context"):
        lines.append(f"Context provided: {record['context']}")
    if record.get("subagent_chat_id"):
        lines.append(f"Subagent chat: {record['subagent_chat_id']}")
    if duration:
        lines.append(f"Status: {status}   Duration: {duration}")
    else:
        lines.append(f"Status: {status}")
    lines.append("--- RESULT ---")
    if status == "completed":
        lines.append(summary or "Subagent completed without a final summary.")
    elif status == "interrupted":
        lines.append("The subagent was interrupted before completing.")
        if summary:
            lines.extend(["Partial output:", summary])
    else:
        detail = f" {error}" if error else ""
        lines.append(f"The subagent did not complete successfully.{detail}")
        if summary:
            lines.extend(["Partial output:", summary])
    return "\n".join(lines)


def _set_completion_injector_for_tests(
    injector: Callable[[dict[str, Any]], Awaitable[None]] | None,
) -> None:
    global _completion_injector_override
    _completion_injector_override = injector


async def _reset_for_tests() -> None:
    await cancel_all_async_subagents(reason="test reset")
    async with _lock:
        _records.clear()
    _set_completion_injector_for_tests(None)

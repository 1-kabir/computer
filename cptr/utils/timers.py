"""Durable one-shot timers backed by dormant internal child chats."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime

from cptr.env import TIMER_POLL_INTERVAL

logger = logging.getLogger(__name__)

_RELATIVE_TIME = re.compile(
    r"^(?:\+|in\s+)?(\d+)\s*(s|sec(?:onds?)?|m|min(?:utes?)?|h|hours?|d|days?)$"
)
_RFC3339_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
_TIME_UNITS_NS = {
    "s": 1_000_000_000,
    "m": 60 * 1_000_000_000,
    "h": 60 * 60 * 1_000_000_000,
    "d": 24 * 60 * 60 * 1_000_000_000,
}
_manager_lock = asyncio.Lock()


def parse_timer_at(value: str) -> int:
    """Normalize a relative offset or timezone-aware RFC 3339 timestamp."""
    raw = value.strip()
    now = time.time_ns()
    relative = _RELATIVE_TIME.fullmatch(raw.lower())
    if relative:
        count = int(relative.group(1))
        if count <= 0:
            raise ValueError("at must be in the future.")
        return now + count * _TIME_UNITS_NS[relative.group(2)[0]]

    if not _RFC3339_TIME.fullmatch(raw):
        raise ValueError(
            "at must be a relative time such as 10s or in 10 seconds, "
            "or an RFC 3339 timestamp with a timezone."
        )
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "at must be a relative time such as 10s or in 10 seconds, "
            "or an RFC 3339 timestamp with a timezone."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("absolute at values must include an explicit timezone.")

    due_at = int(parsed.timestamp() * 1_000_000_000)
    if due_at <= now:
        raise ValueError("at must be in the future.")
    return due_at


async def cancel_timers_for_event(event) -> None:
    """Cancel matching dormant children after a committed parent-chat event."""
    subject = event.subject or {}
    if subject.get("type") != "chat" or not subject.get("id"):
        return

    from cptr.models import Chat
    from cptr.utils.config import now_ms

    async with _manager_lock:
        timers = await Chat.get_pending_timers(str(subject["id"]))
        for timer in timers:
            meta = dict(timer.meta or {})
            if event.event not in (meta.get("cancel_on") or []):
                continue
            meta.update(
                {
                    "status": "cancelled",
                    "timer_cancelled_at": time.time_ns(),
                    "timer_cancelled_by": event.event,
                }
            )
            await Chat.update_meta(timer.id, meta, now_ms())


async def _set_timer_status(chat_id: str, status: str, **fields) -> None:
    from cptr.models import Chat
    from cptr.utils.config import now_ms

    chat = await Chat.get_by_id(chat_id)
    if not chat:
        return
    meta = dict(chat.meta or {})
    meta["status"] = status
    meta.update(fields)
    await Chat.update_meta(chat_id, meta, now_ms())


async def _launch_timer(timer, app) -> None:
    from cptr.models import Chat, ChatMessage
    from cptr.socket.main import emit_to_user
    from cptr.utils.chat_export import export_chat_to_file
    from cptr.utils.chat_task import get_pending_input_lock, start_task
    from cptr.utils.config import now_ms
    from cptr.utils.identity import internal_request_for_user
    from cptr.utils.model_targets import resolve_model_target

    async with _manager_lock:
        timer = await Chat.get_by_id(timer.id)
        if not timer:
            return
        meta = dict(timer.meta or {})
        status = meta.get("status") or meta.get("timer_status")
        if status != "pending" or int(meta.get("timer_at") or 0) > time.time_ns():
            return
        request = await internal_request_for_user(app, timer.user_id)

        parent = await Chat.get_by_id(meta.get("parent_chat_id", ""))
        if not parent:
            await _set_timer_status(timer.id, "error", timer_error="parent chat no longer exists")
            return

        task_message = await ChatMessage.get_by_id(timer.current_message_id)
        if not task_message:
            await _set_timer_status(timer.id, "error", timer_error="timer task message is missing")
            return

        async with get_pending_input_lock(parent.id):
            parent_messages = await ChatMessage.get_all_by_chat(parent.id)
            active = any(
                message.role == "assistant" and not message.done for message in parent_messages
            )
            if active:
                return

            try:
                target = await resolve_model_target(meta["timer_model_id"], app.state)
            except Exception as exc:  # model configuration can change while a timer waits
                await _set_timer_status(timer.id, "error", timer_error=f"model unavailable: {exc}")
                return

            done_assistants = [
                message
                for message in parent_messages
                if message.role == "assistant" and message.done
            ]
            parent_id = (
                done_assistants[-1].id if done_assistants else meta.get("timer_parent_message_id")
            )
            prompt_msg = await ChatMessage.create(
                chat_id=parent.id,
                role="user",
                content=task_message.content,
                parent_id=parent_id,
                model=target.full_model_id,
                meta={"internal": True, "type": "timer"},
                created_at=now_ms(),
            )
            assistant_msg = await ChatMessage.create(
                chat_id=parent.id,
                role="assistant",
                content="",
                parent_id=prompt_msg.id,
                model=target.full_model_id,
                done=False,
                created_at=now_ms(),
            )
            await Chat.update_current_message(parent.id, assistant_msg.id, now_ms())

            meta.update(
                {
                    "status": "completed",
                    "timer_completed_at": time.time_ns(),
                }
            )
            await Chat.update_meta(timer.id, meta, now_ms())

        await export_chat_to_file(request, timer.id)
        await export_chat_to_file(request, parent.id)

        await emit_to_user(
            timer.user_id,
            {
                "chat_id": parent.id,
                "message_id": assistant_msg.id,
                "pending_inputs_processed": True,
            },
        )
        start_task(
            request,
            message_id=assistant_msg.id,
            chat_id=parent.id,
            user_id=timer.user_id,
            workspace=meta.get("workspace", ""),
            target=target,
        )


async def timer_worker_loop(app) -> None:
    """Poll durable pending timers and wake their parent chats."""
    from cptr.models import Chat

    logger.info("Timer worker started (poll interval: %ds)", TIMER_POLL_INTERVAL)
    while True:
        try:
            due = await Chat.get_due_timers(time.time_ns())
            for timer in due:
                await _launch_timer(timer, app)
        except Exception:
            logger.exception("Timer worker error")
        await asyncio.sleep(TIMER_POLL_INTERVAL)


async def recover_timers() -> None:
    """Do not replay work that was already launched before a process restart."""
    from cptr.models import Chat, ChatMessage
    from cptr.utils.config import now_ms

    for timer in await Chat.get_timers("pending"):
        current = await ChatMessage.get_by_id(timer.current_message_id)
        if current and current.role == "assistant":
            await ChatMessage.delete(current.id)
            await Chat.update_current_message(timer.id, current.parent_id, now_ms())

    for timer in await Chat.get_timers("running"):
        await _set_timer_status(
            timer.id,
            "error",
            timer_completed_at=time.time_ns(),
            timer_error="interrupted by restart",
        )

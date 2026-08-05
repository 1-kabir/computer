"""Automation utilities: RRULE helpers, scheduler worker, execution.

The scheduler_worker_loop polls the DB for due automations and executes them
by creating a real chat and calling start_task() — the same agentic loop
that runs for interactive chats.

Environment:
    AUTOMATION_POLL_INTERVAL  – seconds between polls (default: 10)
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from dateutil.rrule import rrulestr

logger = logging.getLogger(__name__)


####################
# RRULE Helpers
####################


def _parse_rule(s: str):
    """Parse RRULE with clock-aligned DTSTART for sub-daily frequencies.

    MINUTELY/HOURLY rules use a fixed epoch DTSTART (2000-01-01 00:00)
    so intervals snap to clock boundaries (e.g. every 5min = :00, :05, :10).
    """
    raw = s.replace("RRULE:", "")
    parts = dict(p.split("=", 1) for p in raw.split(";") if "=" in p)
    freq = parts.get("FREQ", "")

    if freq in ("MINUTELY", "HOURLY"):
        epoch = datetime(2000, 1, 1, 0, 0, 0)
        return rrulestr(s, dtstart=epoch, ignoretz=True)
    return rrulestr(s, ignoretz=True)


def validate_rrule(s: str) -> None:
    """Raise ValueError if the RRULE is malformed or exhausted."""
    try:
        rule = _parse_rule(s)
    except Exception as e:
        raise ValueError(f"Invalid RRULE: {e}")
    now = datetime.now()
    if rule.after(now) is None:
        raise ValueError("RRULE has no future occurrences")


def next_run_ns(s: str) -> Optional[int]:
    """Next occurrence as epoch nanoseconds."""
    now = datetime.now()
    dt = _parse_rule(s).after(now)
    if dt is None:
        return None
    return int(dt.timestamp() * 1_000_000_000)


def next_n_runs_ns(s: str, n: int = 5) -> list[int]:
    """Compute next N occurrences for UI preview."""
    rule = _parse_rule(s)
    result = []
    dt = datetime.now()
    for _ in range(n):
        dt = rule.after(dt)
        if not dt:
            break
        result.append(int(dt.timestamp() * 1_000_000_000))
    return result


####################
# Scheduler Worker
####################


async def scheduler_worker_loop(app) -> None:
    """Background scheduler for automation execution.

    Runs on startup, polls every AUTOMATION_POLL_INTERVAL seconds.
    Claims due automations and dispatches them as asyncio tasks.
    """
    from cptr.env import AUTOMATION_POLL_INTERVAL

    logger.info("Automation scheduler started (poll interval: %ds)", AUTOMATION_POLL_INTERVAL)

    while True:
        try:
            from cptr.models.automations import Automation

            batch = await Automation.claim_due(int(time.time_ns()), limit=10)
            if batch:
                logger.info("Claimed %d due automation(s)", len(batch))
            for automation in batch:
                asyncio.create_task(execute_automation(app, automation))
        except Exception:
            logger.exception("Scheduler worker error")

        await asyncio.sleep(AUTOMATION_POLL_INTERVAL + random.uniform(0, 2))


####################
# Execute
####################


async def execute_automation(app, automation, webhook_payload: str | None = None) -> None:
    """Execute an automation by creating a chat and calling start_task().

    Creates a real chat + messages, then uses the same agentic loop
    as interactive chats, giving automations full tool-calling capabilities.

    If webhook_payload is provided, {{webhook_payload}} in the prompt is
    replaced with the payload content.
    """
    from cptr.models import Chat, ChatMessage
    from cptr.models.automations import AutomationRun
    from cptr.utils.config import now_ms
    from cptr.utils.identity import internal_request_for_user
    from cptr.socket.main import emit_to_user

    try:
        workspace = automation.workspace
        request = await internal_request_for_user(app, automation.user_id)
        model_id = automation.model_id
        prompt = automation.prompt

        if webhook_payload:
            prompt = prompt.replace("{{webhook_payload}}", webhook_payload)

        # Create the chat
        chat = await Chat.create(
            user_id=automation.user_id,
            title=automation.name,
            meta={
                "workspace": workspace,
                "automation_id": automation.id,
                "params": {"tool_approval_mode": "full"},
            },
            created_at=now_ms(),
        )

        # Create user message
        user_msg = await ChatMessage.create(
            chat_id=chat.id,
            role="user",
            content=prompt,
            created_at=now_ms(),
        )

        # Create assistant placeholder
        assistant_msg = await ChatMessage.create(
            chat_id=chat.id,
            role="assistant",
            content="",
            parent_id=user_msg.id,
            model=model_id,
            done=False,
            created_at=now_ms(),
        )

        await Chat.update_current_message(chat.id, assistant_msg.id, now_ms())

        marker = Path(workspace) / ".cptr" / "chats" / f"{chat.id}.json"

        from cptr.utils.runtime import Runtime

        await Runtime.write_file(request, str(marker), "{}")

        from cptr.utils.model_targets import resolve_model_target

        target = await resolve_model_target(model_id)

        # Start the agentic loop (same as interactive chat)
        from cptr.utils.chat_task import start_task

        start_task(
            request,
            message_id=assistant_msg.id,
            chat_id=chat.id,
            user_id=automation.user_id,
            workspace=workspace,
            target=target,
        )

        # Notify frontend (standard chat event so sidebar updates)
        await emit_to_user(
            automation.user_id,
            {
                "chat_id": chat.id,
                "title": automation.name,
            },
        )

        # Record successful run
        await AutomationRun.create(
            automation_id=automation.id,
            status="success",
            chat_id=chat.id,
            created_at=int(time.time_ns()),
        )

        logger.info(
            "Automation '%s' (%s) executed → chat %s",
            automation.name,
            automation.id[:8],
            chat.id[:8],
        )

    except Exception as e:
        logger.exception("Automation %s failed", automation.id)
        try:
            await AutomationRun.create(
                automation_id=automation.id,
                status="error",
                error=str(e)[:4000],
                created_at=int(time.time_ns()),
            )
        except Exception:
            logger.exception("Failed to record automation run error")

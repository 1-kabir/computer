"""Gemini ACP adapter."""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from typing import Any, AsyncIterator

from cptr.utils.agents.acp import (
    AcpClient,
    acp_event_stream,
    acp_text_from_update,
    acp_tool_from_update,
)
from cptr.utils.agents.attachments import PreparedAgentAttachments
from cptr.utils.agents.events import (
    AgentDone,
    AgentError,
    AgentEvent,
    AgentTextDelta,
    AgentToolUpdate,
)
from cptr.utils.agents.prompts import turn_prompt_text


def _auto_approve(chat_params: dict[str, Any]) -> bool:
    if chat_params.get("tool_approval_mode") == "full":
        return True
    return bool(chat_params.get("auto_approve_tools"))


async def run_gemini_agent(
    *,
    profile: dict[str, Any],
    model: str,
    workspace: str,
    messages: list[dict[str, Any]],
    system_prompt: str,
    chat_params: dict[str, Any],
    resume_state: dict[str, Any] | None,
    attachments: PreparedAgentAttachments,
) -> AsyncIterator[AgentEvent]:
    env = os.environ.copy()
    if profile.get("home"):
        env["HOME"] = os.path.expanduser(str(profile["home"]))

    session_id = None
    if resume_state and isinstance(resume_state.get("session_id"), str):
        session_id = resume_state["session_id"]

    client = AcpClient(
        command=str(profile["command"]),
        args=["--acp"],
        cwd=workspace,
        env=env,
        auth_method_id="oauth-personal",
        resume_session_id=session_id,
        auto_approve_permissions=_auto_approve(chat_params),
    )
    try:
        await client.start()
        if model != "default":
            await client.set_model(model)

        prompt = turn_prompt_text(messages, system_prompt, resumed=bool(session_id))

        images = [
            {"data": image.base64, "mimeType": image.mime_type} for image in attachments.images
        ]
        prompt_task = asyncio.create_task(client.prompt(prompt, images=images))
        try:
            async for event in acp_event_stream(client):
                params = event.get("params") if isinstance(event.get("params"), dict) else {}
                text = acp_text_from_update(params)
                if text:
                    yield AgentTextDelta(text)
                tool = acp_tool_from_update(params)
                if tool:
                    yield AgentToolUpdate(**tool)
                if prompt_task.done():
                    try:
                        next_event = await asyncio.wait_for(client.events.get(), timeout=0.25)
                    except asyncio.TimeoutError:
                        break
                    next_params = (
                        next_event.get("params")
                        if isinstance(next_event.get("params"), dict)
                        else {}
                    )
                    next_text = acp_text_from_update(next_params)
                    if next_text:
                        yield AgentTextDelta(next_text)
                    next_tool = acp_tool_from_update(next_params)
                    if next_tool:
                        yield AgentToolUpdate(**next_tool)
            await prompt_task
        finally:
            if not prompt_task.done():
                prompt_task.cancel()
                with suppress(asyncio.CancelledError):
                    await prompt_task

        yield AgentDone(
            resume_state={
                "profile_id": profile["id"],
                "session_id": client.session_id,
                "workspace": workspace,
                "model": model,
            }
        )
    except asyncio.CancelledError:
        await client.cancel()
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in chat.
        yield AgentError(str(exc))
    finally:
        await client.close()

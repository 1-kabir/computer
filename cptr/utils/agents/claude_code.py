"""Claude Code SDK adapter."""

from __future__ import annotations

import asyncio
import os
import shlex
from typing import Any, AsyncIterator

from cptr.env import CLAUDE_CODE_MAX_BUFFER_SIZE
from cptr.utils.agents.attachments import PreparedAgentAttachments
from cptr.utils.agents.events import (
    AgentDone,
    AgentError,
    AgentEvent,
    AgentReasoningDelta,
    AgentTextDelta,
    AgentToolUpdate,
)
from cptr.utils.agents.prompts import latest_user_text


def _claude_query_input(
    prompt: str, attachments: PreparedAgentAttachments
) -> str | AsyncIterator[dict[str, Any]]:
    if not attachments.images:
        return prompt
    content: list[dict[str, Any]] = []
    if prompt.strip():
        content.append({"type": "text", "text": prompt})
    for image in attachments.images:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.mime_type,
                    "data": image.base64,
                },
            }
        )

    async def messages() -> AsyncIterator[dict[str, Any]]:
        yield {
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
        }

    return messages()


def _chat_approval_mode(chat_params: dict[str, Any]) -> str:
    if "tool_approval_mode" in chat_params:
        return str(chat_params.get("tool_approval_mode") or "auto")
    if "auto_approve_tools" in chat_params:
        return "full" if chat_params.get("auto_approve_tools") else "auto"
    return "auto"


def _permission_mode(chat_approval_mode: str) -> str:
    if chat_approval_mode == "full":
        return "bypassPermissions"
    if chat_approval_mode == "auto":
        return "acceptEdits"
    return "default"


def _tool_update_from_claude_start(
    event: dict[str, Any],
) -> tuple[int | None, AgentToolUpdate | None]:
    block = event.get("content_block")
    if not isinstance(block, dict):
        return None, None
    if block.get("type") not in {"tool_use", "server_tool_use", "mcp_tool_use"}:
        return None, None
    call_id = block.get("id")
    if not isinstance(call_id, str) or not call_id.strip():
        return None, None
    index = event.get("index")
    name = str(block.get("name") or block.get("type") or "Claude action").strip()
    raw_input = block.get("input")
    arguments = raw_input if isinstance(raw_input, dict) else {}
    return (
        index if isinstance(index, int) else None,
        AgentToolUpdate(
            call_id=call_id.strip(),
            name="agent_tool",
            status="in_progress",
            arguments={"title": name, **arguments},
        ),
    )


async def run_claude_code_agent(
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
    try:
        import claude_agent_sdk as sdk
    except ImportError:
        yield AgentError("Claude Code support requires the claude-agent-sdk Python package")
        return

    prompt = latest_user_text(messages)
    env = os.environ.copy()
    if profile.get("home"):
        env["HOME"] = os.path.expanduser(str(profile["home"]))

    permission_mode = _permission_mode(_chat_approval_mode(chat_params))
    launch_args = str(profile.get("launch_args") or "").strip()
    extra_args = shlex.split(launch_args) if launch_args else []

    try:
        session_id = None
        if resume_state:
            value = resume_state.get("session_id")
            session_id = value if isinstance(value, str) and value else None

        options_kwargs: dict[str, Any] = {
            "system_prompt": system_prompt or None,
            "permission_mode": permission_mode,
            "env": env,
            "include_partial_messages": True,
            "max_buffer_size": CLAUDE_CODE_MAX_BUFFER_SIZE,
        }
        if session_id:
            options_kwargs["resume"] = session_id
        if workspace:
            options_kwargs["cwd"] = workspace
        if extra_args:
            options_kwargs["extra_args"] = {arg: None for arg in extra_args}

        options = sdk.ClaudeAgentOptions(**options_kwargs)
        options.cli_path = str(profile["command"])
        if model != "default":
            options.model = model

        client = sdk.ClaudeSDKClient(options)
        await client.connect()

        try:
            query_input = _claude_query_input(prompt, attachments)
            query = (
                client.query(query_input, session_id=session_id)
                if session_id
                else client.query(query_input)
            )
            await asyncio.wait_for(query, timeout=20)
            stream = client.receive_response()
            usage: dict[str, Any] | None = None
            observed_session_id = session_id
            tool_calls: dict[int, AgentToolUpdate] = {}
            received_text_delta = False
            received_thinking_delta = False

            async for message in stream:
                event = getattr(message, "event", None)
                if isinstance(event, dict):
                    event_type = event.get("type")
                    if event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if isinstance(delta, dict):
                            if delta.get("type") == "text_delta" and isinstance(
                                delta.get("text"), str
                            ):
                                text = delta["text"]
                                if text:
                                    received_text_delta = True
                                    yield AgentTextDelta(text)
                            elif delta.get("type") == "thinking_delta" and isinstance(
                                delta.get("thinking"), str
                            ):
                                thinking = delta["thinking"]
                                yield AgentReasoningDelta(thinking)
                                if thinking:
                                    received_thinking_delta = True
                    elif event_type == "content_block_start":
                        index, tool = _tool_update_from_claude_start(event)
                        if tool:
                            if index is not None:
                                tool_calls[index] = tool
                            yield tool
                    elif event_type == "content_block_stop":
                        index = event.get("index")
                        tool = tool_calls.get(index) if isinstance(index, int) else None
                        if tool:
                            yield AgentToolUpdate(
                                call_id=tool.call_id,
                                name=tool.name,
                                status="completed",
                                arguments=tool.arguments,
                                output="",
                            )
                    continue

                class_name = message.__class__.__name__
                if class_name == "AssistantMessage":
                    for block in getattr(message, "content", []) or []:
                        if block.__class__.__name__ == "TextBlock":
                            if received_text_delta:
                                continue
                            text = getattr(block, "text", "")
                            if text:
                                yield AgentTextDelta(text)
                        elif block.__class__.__name__ == "ThinkingBlock":
                            if received_thinking_delta:
                                continue
                            text = getattr(block, "thinking", "")
                            if text:
                                yield AgentReasoningDelta(text)
                elif class_name == "ResultMessage":
                    observed_session_id = (
                        getattr(message, "session_id", None) or observed_session_id
                    )
                    raw_usage = getattr(message, "usage", None)
                    if isinstance(raw_usage, dict):
                        usage = {
                            **raw_usage,
                            "total_tokens": (raw_usage.get("input_tokens") or 0)
                            + (raw_usage.get("output_tokens") or 0),
                        }
                    break

            yield AgentDone(
                usage=usage,
                resume_state={
                    "profile_id": profile["id"],
                    "session_id": observed_session_id,
                    "workspace": workspace,
                    "model": model,
                },
            )
        finally:
            await client.disconnect()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in the chat.
        yield AgentError(str(exc))

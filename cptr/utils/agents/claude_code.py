"""Claude Code SDK adapter."""

from __future__ import annotations

import asyncio
import json
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
from cptr.utils.agents.prompts import session_turn_prompt_text
from cptr.utils.identity import env_for


_claude_clients: dict[str, tuple[Any, tuple[Any, ...]]] = {}


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


async def _disconnect_cached_claude_client(session_id: str) -> None:
    cached = _claude_clients.pop(session_id, None)
    if cached:
        await cached[0].disconnect()


async def _clear_claude_client_cache() -> None:
    cached_clients = list(_claude_clients.values())
    _claude_clients.clear()
    for client, _ in cached_clients:
        await client.disconnect()


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
            arguments={**arguments, "title": name},
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
    identity=None,
) -> AsyncIterator[AgentEvent]:
    try:
        import claude_agent_sdk as sdk
    except ImportError:
        yield AgentError("Claude Code support requires the claude-agent-sdk Python package")
        return

    env = env_for(identity, workspace) if identity and identity.is_pam else os.environ.copy()
    home = os.path.expanduser(str(profile["home"])) if profile.get("home") else None
    if home:
        env["HOME"] = home

    permission_mode = _permission_mode(_chat_approval_mode(chat_params))
    launch_args = str(profile.get("launch_args") or "").strip()
    extra_args = tuple(shlex.split(launch_args)) if launch_args else ()
    command = str(profile["command"])
    cache_key = (workspace, command, home, extra_args, model, permission_mode)

    session_id = None
    client = None
    try:
        if resume_state:
            value = resume_state.get("session_id")
            session_id = value if isinstance(value, str) and value else None
        prompt = session_turn_prompt_text(messages, resumed=bool(session_id))

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
        options.cli_path = command
        if model != "default":
            options.model = model

        cached = None
        if session_id:
            cached = _claude_clients.get(session_id)
            if cached and cached[1] != cache_key:
                await _disconnect_cached_claude_client(session_id)
                cached = None

        if cached:
            client = cached[0]
        else:
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
            tool_input_json: dict[int, str] = {}
            received_tool_call_ids: set[str] = set()
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
                            elif delta.get("type") == "input_json_delta" and isinstance(
                                delta.get("partial_json"), str
                            ):
                                index = event.get("index")
                                if isinstance(index, int):
                                    tool_input_json[index] = (
                                        tool_input_json.get(index, "") + delta["partial_json"]
                                    )
                    elif event_type == "content_block_start":
                        index, tool = _tool_update_from_claude_start(event)
                        if tool:
                            received_tool_call_ids.add(tool.call_id)
                            if index is not None:
                                tool_calls[index] = tool
                            yield tool
                    elif event_type == "content_block_stop":
                        index = event.get("index")
                        tool = tool_calls.get(index) if isinstance(index, int) else None
                        if tool:
                            arguments = dict(tool.arguments or {})
                            title = str(arguments.pop("title", None) or "Claude action")
                            input_json = (
                                tool_input_json.get(index) if isinstance(index, int) else None
                            )
                            if input_json and input_json.strip():
                                try:
                                    parsed = json.loads(input_json)
                                    if isinstance(parsed, dict):
                                        arguments = parsed
                                except json.JSONDecodeError:
                                    pass
                            yield AgentToolUpdate(
                                call_id=tool.call_id,
                                name=tool.name,
                                status="completed",
                                arguments={**arguments, "title": title},
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
                        elif block.__class__.__name__ == "ToolUseBlock":
                            call_id = getattr(block, "id", None)
                            if (
                                isinstance(call_id, str)
                                and call_id.strip()
                                and call_id not in received_tool_call_ids
                            ):
                                title = str(
                                    getattr(block, "name", None)
                                    or getattr(block, "type", None)
                                    or "Claude action"
                                ).strip()
                                raw_input = getattr(block, "input", None)
                                arguments = raw_input if isinstance(raw_input, dict) else {}
                                received_tool_call_ids.add(call_id)
                                yield AgentToolUpdate(
                                    call_id=call_id.strip(),
                                    name="agent_tool",
                                    status="completed",
                                    arguments={**arguments, "title": title},
                                    output="",
                                )
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

            if observed_session_id:
                old_client = _claude_clients.pop(session_id, (None,))[0] if session_id else None
                if old_client is not None and old_client is not client:
                    await old_client.disconnect()
                _claude_clients[observed_session_id] = (client, cache_key)
                session_id = observed_session_id
            else:
                await client.disconnect()

            yield AgentDone(
                usage=usage,
                resume_state={
                    "profile_id": profile["id"],
                    "session_id": observed_session_id,
                    "workspace": workspace,
                    "model": model,
                },
            )
        except Exception:
            if session_id:
                await _disconnect_cached_claude_client(session_id)
            else:
                await client.disconnect()
            raise
    except asyncio.CancelledError:
        if session_id:
            await _disconnect_cached_claude_client(session_id)
        elif client is not None:
            await client.disconnect()
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in the chat.
        yield AgentError(str(exc))

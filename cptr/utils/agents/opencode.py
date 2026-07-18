"""OpenCode server adapter."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import socket
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from cptr.utils.agents.attachments import PreparedAgentAttachments
from cptr.utils.agents.events import (
    AgentDone,
    AgentError,
    AgentEvent,
    AgentTextDelta,
    AgentToolUpdate,
)
from cptr.utils.agents.prompts import turn_prompt_text


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _server_url_from_stdout(proc: asyncio.subprocess.Process, port: int) -> str:
    assert proc.stdout is not None
    fallback = f"http://127.0.0.1:{port}"
    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=1)
        if not line:
            break
        match = re.search(r"(https?://[^\s]+)", line.decode(errors="replace"))
        if match:
            return match.group(1)
    return fallback


@asynccontextmanager
async def _opencode_server(profile: dict[str, Any], workspace: str):
    server_url = str(profile.get("server_url") or "").strip()
    if server_url:
        yield server_url, None
        return

    env = os.environ.copy()
    if profile.get("home"):
        env["HOME"] = os.path.expanduser(str(profile["home"]))
    port = _free_port()
    proc = await asyncio.create_subprocess_exec(
        str(profile["command"]),
        "serve",
        "--hostname=127.0.0.1",
        f"--port={port}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workspace or os.getcwd(),
        env={**env, "OPENCODE_CONFIG_CONTENT": "{}"},
    )
    stderr_task = asyncio.create_task(_drain_stderr(proc))
    try:
        yield await _server_url_from_stdout(proc, port), proc
    finally:
        stderr_task.cancel()
        with suppress(asyncio.CancelledError):
            await stderr_task
        if proc.returncode is None:
            proc.terminate()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=2)
            if proc.returncode is None:
                proc.kill()
                await proc.wait()


async def _drain_stderr(proc: asyncio.subprocess.Process) -> None:
    assert proc.stderr is not None
    while await proc.stderr.readline():
        pass


def _headers(profile: dict[str, Any]) -> dict[str, str]:
    password = str(profile.get("server_password") or "").strip()
    if not password:
        return {}
    token = base64.b64encode(f"opencode:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def _request(
    client: httpx.AsyncClient,
    method: str,
    paths: list[str],
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for path in paths:
        try:
            response = await client.request(method, f"/{path}", headers=headers, json=json_body)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001 - try alternate generated route names.
            last_error = exc
    if last_error:
        raise last_error
    return {}


def _session_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _parse_model(model: str) -> dict[str, str]:
    provider_id, _, model_id = model.partition("/")
    if not provider_id or not model_id:
        raise RuntimeError("OpenCode models must use provider/model format")
    return {"providerID": provider_id, "modelID": model_id}


def _opencode_parts(prompt: str, attachments: PreparedAgentAttachments) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if prompt.strip():
        parts.append({"type": "text", "text": prompt})
    for item in [*attachments.images, *attachments.files]:
        parts.append(
            {
                "type": "file",
                "mime": item.mime_type,
                "filename": item.name,
                "url": Path(item.path).resolve().as_uri(),
            }
        )
    return parts


def _resume_session_id(resume_state: dict[str, Any] | None) -> str | None:
    if not resume_state:
        return None
    value = resume_state.get("session_id")
    return value if isinstance(value, str) and value.strip() else None


async def _create_opencode_session(
    client: httpx.AsyncClient,
    headers: dict[str, str],
) -> str:
    session_payload = await _request(
        client,
        "POST",
        ["session.create", "session/create", "session"],
        headers=headers,
        json_body={"title": "cptr", "permission": []},
    )
    session = _session_data(session_payload)
    session_id = session.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("OpenCode did not return a session id")
    return session_id


async def _start_opencode_prompt(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    session_id: str,
    parsed_model: dict[str, str],
    parts: list[dict[str, Any]],
) -> None:
    await _request(
        client,
        "POST",
        ["session.promptAsync", "session/promptAsync", "session/prompt"],
        headers=headers,
        json_body={
            "sessionID": session_id,
            "model": parsed_model,
            "parts": parts,
        },
    )


def _text_from_event(event: dict[str, Any], emitted: dict[str, str]) -> str | None:
    event_type = event.get("type")
    props = event.get("properties") if isinstance(event.get("properties"), dict) else {}
    if event_type == "message.part.delta":
        delta = props.get("delta")
        return delta if isinstance(delta, str) and delta else None
    if event_type == "message.part.updated":
        part = props.get("part") if isinstance(props.get("part"), dict) else {}
        if part.get("type") not in ("text", "reasoning"):
            return None
        part_id = part.get("id")
        text = part.get("text")
        if not isinstance(part_id, str) or not isinstance(text, str):
            return None
        previous = emitted.get(part_id, "")
        if text.startswith(previous):
            delta = text[len(previous) :]
        else:
            delta = text
        emitted[part_id] = text
        return delta or None
    return None


def _tool_from_event(event: dict[str, Any]) -> AgentToolUpdate | None:
    if event.get("type") != "message.part.updated":
        return None
    props = event.get("properties") if isinstance(event.get("properties"), dict) else {}
    part = props.get("part") if isinstance(props.get("part"), dict) else {}
    if part.get("type") != "tool":
        return None
    call_id = part.get("callID") or part.get("id")
    if not isinstance(call_id, str) or not call_id.strip():
        return None
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    tool = str(part.get("tool") or state.get("title") or "Agent tool").strip()
    status = _opencode_tool_status(state.get("status"))
    output = _opencode_tool_output(state)
    return AgentToolUpdate(
        call_id=call_id.strip(),
        name="agent_tool",
        status=status,
        arguments={"title": tool, **({"state": state} if state else {})},
        output=output,
    )


def _opencode_tool_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "completed":
        return "completed"
    if normalized == "error":
        return "failed"
    if normalized == "pending":
        return "pending"
    return "in_progress"


def _opencode_tool_output(state: dict[str, Any]) -> str | None:
    for key in ("output", "result", "message", "error"):
        value = state.get(key)
        if value is None:
            continue
        return value if isinstance(value, str) else json.dumps(value, indent=2)
    return None


async def run_opencode_agent(
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
    del chat_params
    try:
        async with _opencode_server(profile, workspace) as (server_url, _proc):
            headers = _headers(profile)
            async with httpx.AsyncClient(base_url=server_url, timeout=None) as client:
                session_id = _resume_session_id(resume_state)
                resumed = bool(session_id)
                if session_id is None:
                    session_id = await _create_opencode_session(client, headers)

                parsed_model = _parse_model(model)
                while True:
                    emitted: dict[str, str] = {}
                    event_queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
                    event_task = asyncio.create_task(
                        _collect_opencode_events(client, headers, session_id, emitted, event_queue)
                    )

                    prompt = turn_prompt_text(messages, system_prompt, resumed=resumed)
                    parts = _opencode_parts(prompt, attachments)
                    try:
                        try:
                            await _start_opencode_prompt(
                                client, headers, session_id, parsed_model, parts
                            )
                        except Exception:
                            if not resumed:
                                raise
                            event_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await event_task
                            session_id = await _create_opencode_session(client, headers)
                            resumed = False
                            continue

                        while True:
                            item = await event_queue.get()
                            if item is None:
                                break
                            yield item
                    except asyncio.CancelledError:
                        with suppress(Exception):
                            await _request(
                                client,
                                "POST",
                                ["session.abort", "session/abort"],
                                headers=headers,
                                json_body={"sessionID": session_id},
                            )
                        raise
                    finally:
                        event_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await event_task
                    break

                yield AgentDone(
                    resume_state={
                        "profile_id": profile["id"],
                        "session_id": session_id,
                        "workspace": workspace,
                        "model": model,
                    }
                )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in chat.
        yield AgentError(str(exc))


async def _collect_opencode_events(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    session_id: str,
    emitted: dict[str, str],
    queue: asyncio.Queue[AgentEvent | None],
) -> None:
    try:
        for path in ("event.subscribe", "event/subscribe", "event"):
            try:
                async with client.stream("GET", f"/{path}", headers=headers) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        with suppress(Exception):
                            event = json.loads(line)
                            if not isinstance(event, dict):
                                continue
                            props = (
                                event.get("properties")
                                if isinstance(event.get("properties"), dict)
                                else {}
                            )
                            if props.get("sessionID") != session_id:
                                continue
                            text = _text_from_event(event, emitted)
                            if text:
                                await queue.put(AgentTextDelta(text))
                            tool = _tool_from_event(event)
                            if tool:
                                await queue.put(tool)
                            status = (
                                props.get("status") if isinstance(props.get("status"), dict) else {}
                            )
                            if (
                                event.get("type") == "session.status"
                                and status.get("type") == "idle"
                            ):
                                await queue.put(None)
                                return
            except Exception:
                continue
    except Exception:
        await queue.put(None)
    await queue.put(None)

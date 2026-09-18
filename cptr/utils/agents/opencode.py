"""OpenCode server adapter."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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
from cptr.utils.identity import env_for, preexec_for

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_OPENCODE_ACTIVITY = object()
_OPENCODE_STREAM_FAILED = object()


def _auth_username(profile: dict[str, Any]) -> str:
    """Basic-auth username for an OpenCode-family server.

    Kilo's server defaults KILO_SERVER_USERNAME to "kilo" (an explicit
    kilocode_change from upstream), so authenticating as "opencode" always
    401s against a Kilo server.
    """
    return "kilo" if str(profile.get("agent") or "") == "kilo" else "opencode"


def _config_env(profile: dict[str, Any], env: dict[str, str]) -> dict[str, str]:
    """Isolation config env for an OpenCode-family server spawn.

    Kilo (an OpenCode fork) reads KILO_CONFIG_CONTENT and no longer falls
    back to OpenCode config locations, so a Kilo profile needs its config
    variable set too. Setting both is harmless for real OpenCode.
    """
    if str(profile.get("agent") or "") == "kilo":
        return {**env, "OPENCODE_CONFIG_CONTENT": "{}", "KILO_CONFIG_CONTENT": "{}"}
    return {**env, "OPENCODE_CONFIG_CONTENT": "{}"}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def opencode_server_url_candidates(server_url: str) -> list[str]:
    server_url = server_url.strip()
    if not server_url:
        return []
    parsed = urlsplit(server_url)
    in_container = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    if not in_container or parsed.hostname not in _LOOPBACK_HOSTS:
        return [server_url]
    docker_url = urlunsplit(
        (
            parsed.scheme,
            f"host.docker.internal{':' + str(parsed.port) if parsed.port else ''}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )
    return [server_url] if docker_url == server_url else [server_url, docker_url]


async def _server_url_from_stdout(proc: asyncio.subprocess.Process, port: int) -> str:
    assert proc.stdout is not None
    fallback = f"http://127.0.0.1:{port}"
    # Cold starts of the Node-based CLI can stall stdout for several seconds
    # (first-run model fetches, GC). Keep reading until the deadline instead
    # of letting one slow line fail the turn.
    deadline = asyncio.get_running_loop().time() + 15
    while asyncio.get_running_loop().time() < deadline:
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=1)
        except asyncio.TimeoutError:
            continue
        if not line:
            break
        match = re.search(r"(https?://[^\s]+)", line.decode(errors="replace"))
        if match:
            return match.group(1)
    return fallback


async def _wait_until_listening(server_url: str, headers: dict[str, str]) -> None:
    """Poll until the spawned server accepts TCP connections.

    Without this, the fallback URL is returned before the server is listening
    and the first real request fails with ConnectError. Any HTTP response
    (even 401/404) proves the socket is live.
    """
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(2, connect=1)) as client:
                await client.get(server_url, headers=headers)
                return
        except (httpx.ConnectError, httpx.ConnectTimeout):
            await asyncio.sleep(0.3)
        except httpx.HTTPStatusError:
            return


@asynccontextmanager
async def _opencode_server(profile: dict[str, Any], workspace: str, identity=None):
    """Yield (server_url, spawn_password, stderr_tail).

    spawn_password is None when using an admin-provided external server_url;
    for a spawned server it is a fresh per-spawn credential so the server is
    never unsecured (loopback is shared between local users). stderr_tail is
    a live list of the server's last stderr lines for error reporting.
    """
    server_url = str(profile.get("server_url") or "").strip()
    if server_url:
        yield server_url, None, []
        return

    env = (
        env_for(identity, workspace or os.getcwd())
        if identity and identity.is_pam
        else os.environ.copy()
    )
    if profile.get("home"):
        env["HOME"] = _expand_home(str(profile["home"]), identity)
    # Secure the spawned server with a fresh credential. Setting these env
    # vars also overrides anything inherited from the server process, which
    # would otherwise make the spawned server REQUIRE auth we never send.
    spawn_password = secrets.token_urlsafe(24)
    env["OPENCODE_SERVER_PASSWORD"] = spawn_password
    env["KILO_SERVER_PASSWORD"] = spawn_password
    port = _free_port()
    proc = await asyncio.create_subprocess_exec(
        str(profile["command"]),
        "serve",
        "--hostname=127.0.0.1",
        f"--port={port}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workspace or os.getcwd(),
        env=_config_env(profile, env),
        preexec_fn=preexec_for(identity) if identity and identity.is_pam else None,
    )
    stderr_tail: list[str] = []
    stderr_task = asyncio.create_task(_drain_stderr(proc, stderr_tail))
    try:
        url = await _server_url_from_stdout(proc, port)
        headers = _headers(profile, spawn_password)
        await _wait_until_listening(url, headers)
        yield url, spawn_password, stderr_tail
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


async def _drain_stderr(proc: asyncio.subprocess.Process, tail: list[str]) -> None:
    assert proc.stderr is not None
    while True:
        try:
            line = await proc.stderr.readline()
        except ValueError:
            # Line exceeded the stream buffer limit; skip it but keep draining
            # so the child's stderr pipe never fills and blocks the server.
            continue
        if not line:
            break
        text = line.decode(errors="replace").strip()
        if text:
            tail.append(text[:2000])
            del tail[:-20]


def _expand_home(home: str, identity=None) -> str:
    """Resolve a profile home path against the execution identity.

    os.path.expanduser always uses the server process's HOME, so a '~'-based
    profile home would silently point a PAM-dropped-privilege agent at the
    server user's home (and its credentials).
    """
    from pathlib import Path

    if identity is not None and identity.is_pam:
        from cptr.utils.identity import expand_user_path

        return str(expand_user_path(home, identity))
    return str(Path(os.path.expanduser(home)))


def _headers(profile: dict[str, Any], spawn_password: str | None = None) -> dict[str, str]:
    password = spawn_password or str(profile.get("server_password") or "").strip()
    if not password:
        return {}
    token = base64.b64encode(f"{_auth_username(profile)}:{password}".encode()).decode()
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
            if not response.content:
                return {}
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
        [
            f"session/{session_id}/prompt_async",
            f"session/{session_id}/message",
            "session.promptAsync",
            "session/promptAsync",
            "session/prompt",
        ],
        headers=headers,
        json_body={
            "sessionID": session_id,
            "model": parsed_model,
            "parts": parts,
        },
    )


def _role_update_from_event(event: dict[str, Any]) -> tuple[str, str] | None:
    if event.get("type") != "message.updated":
        return None
    props = event.get("properties") if isinstance(event.get("properties"), dict) else {}
    info = props.get("info") if isinstance(props.get("info"), dict) else {}
    message_id = info.get("id")
    role = info.get("role")
    if isinstance(message_id, str) and isinstance(role, str):
        return message_id, role
    return None


def _text_from_event(
    event: dict[str, Any],
    emitted: dict[str, str],
    message_roles: dict[str, str],
) -> str | None:
    event_type = event.get("type")
    props = event.get("properties") if isinstance(event.get("properties"), dict) else {}
    if event_type == "message.part.delta":
        message_id = props.get("messageID")
        if isinstance(message_id, str) and message_roles.get(message_id) == "user":
            return None
        delta = props.get("delta")
        part_id = props.get("partID")
        if isinstance(part_id, str) and isinstance(delta, str):
            emitted[part_id] = f"{emitted.get(part_id, '')}{delta}"
        return delta if isinstance(delta, str) and delta else None
    if event_type == "message.part.updated":
        part = props.get("part") if isinstance(props.get("part"), dict) else {}
        if part.get("type") not in ("text", "reasoning"):
            return None
        message_id = part.get("messageID")
        if isinstance(message_id, str) and message_roles.get(message_id) == "user":
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
    identity=None,
) -> AsyncIterator[AgentEvent]:
    del chat_params
    stderr_tail: list[str] = []
    try:
        async with _opencode_server(profile, workspace, identity) as (
            server_url,
            spawn_password,
            server_stderr_tail,
        ):
            stderr_tail = server_stderr_tail
            headers = _headers(profile, spawn_password)
            urls = opencode_server_url_candidates(server_url)
            last_connect_error: Exception | None = None
            for index, candidate_url in enumerate(urls):
                try:
                    async with httpx.AsyncClient(
                        base_url=candidate_url,
                        timeout=httpx.Timeout(None, connect=5),
                    ) as client:
                        session_id = _resume_session_id(resume_state)
                        resumed = bool(session_id)
                        if session_id is None:
                            session_id = await _create_opencode_session(client, headers)

                        parsed_model = _parse_model(model)
                        while True:
                            emitted: dict[str, str] = {}
                            event_queue: asyncio.Queue[AgentEvent | object | None] = asyncio.Queue()
                            event_task = asyncio.create_task(
                                _collect_opencode_events(
                                    client, headers, session_id, emitted, event_queue
                                )
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

                                last_activity = asyncio.get_running_loop().time()
                                saw_activity = False
                                while True:
                                    try:
                                        item = await asyncio.wait_for(event_queue.get(), timeout=1)
                                    except asyncio.TimeoutError:
                                        with suppress(Exception):
                                            payload = await asyncio.wait_for(
                                                _request(
                                                    client,
                                                    "GET",
                                                    ["session/status", "session.status"],
                                                    headers=headers,
                                                ),
                                                timeout=5,
                                            )
                                            status = _session_data(payload).get(session_id)
                                            if (
                                                isinstance(status, dict)
                                                and status.get("type") == "idle"
                                                and saw_activity
                                            ):
                                                # Only trust idle once the turn has
                                                # shown activity: right after
                                                # prompt_async the session may not
                                                # be marked busy yet, and a
                                                # missing/empty status map must not
                                                # end the turn as a successful
                                                # empty run.
                                                break
                                        if asyncio.get_running_loop().time() - last_activity >= 600:
                                            raise RuntimeError(
                                                "OpenCode stopped sending events for 10 minutes."
                                            )
                                        continue
                                    if item is _OPENCODE_ACTIVITY:
                                        last_activity = asyncio.get_running_loop().time()
                                        saw_activity = True
                                        continue
                                    if item is _OPENCODE_STREAM_FAILED:
                                        # SSE stream died without a completion signal:
                                        # distinguish "turn finished while we were not
                                        # looking" from a mid-turn server death.
                                        with suppress(Exception):
                                            payload = await asyncio.wait_for(
                                                _request(
                                                    client,
                                                    "GET",
                                                    ["session/status", "session.status"],
                                                    headers=headers,
                                                ),
                                                timeout=5,
                                            )
                                            status = _session_data(payload).get(session_id)
                                            if (
                                                isinstance(status, dict)
                                                and status.get("type") == "idle"
                                            ):
                                                break
                                        raise RuntimeError(
                                            "OpenCode event stream disconnected before the "
                                            "turn completed."
                                        )
                                    if item is None:
                                        break
                                    last_activity = asyncio.get_running_loop().time()
                                    saw_activity = True
                                    yield item
                            except asyncio.CancelledError:
                                with suppress(Exception):
                                    await _request(
                                        client,
                                        "POST",
                                        [
                                            f"session/{session_id}/abort",
                                            "session.abort",
                                            "session/abort",
                                        ],
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
                    return
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    last_connect_error = exc
                    if index < len(urls) - 1:
                        continue
                    if len(urls) > 1:
                        port = urlsplit(server_url).port or 4096
                        raise RuntimeError(
                            "Unable to connect to OpenCode from Docker. If OpenCode is running "
                            "on the host, start it with "
                            f"`opencode serve --hostname 0.0.0.0 --port {port}` and set the "
                            f"OpenCode Server URL to `http://host.docker.internal:{port}`. "
                            "On Linux Docker, add "
                            "`--add-host=host.docker.internal:host-gateway` if that hostname "
                            "is unavailable."
                        ) from exc
                    raise
            if last_connect_error:
                raise last_connect_error
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in chat.
        message = str(exc)
        if stderr_tail:
            message = f"{message} (server stderr: {' | '.join(stderr_tail[-3:])})"
        yield AgentError(message)


async def _collect_opencode_events(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    session_id: str,
    emitted: dict[str, str],
    queue: asyncio.Queue[AgentEvent | object | None],
) -> None:
    message_roles: dict[str, str] = {}
    stream_failed = True
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
                            await queue.put(_OPENCODE_ACTIVITY)
                            role_update = _role_update_from_event(event)
                            if role_update:
                                message_roles[role_update[0]] = role_update[1]
                            text = _text_from_event(event, emitted, message_roles)
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
                                stream_failed = False
                                await queue.put(None)
                                return
            except Exception:  # noqa: BLE001, S112 - try alternate route names.
                continue
    except Exception:  # noqa: BLE001, S110 - collector task; failure is signaled via queue.
        pass
    # Stream ended without a completion signal: report failure distinctly so
    # the consumer can check the session instead of assuming success.
    if stream_failed:
        await queue.put(_OPENCODE_STREAM_FAILED)
    else:
        await queue.put(None)

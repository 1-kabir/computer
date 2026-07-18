"""Codex app-server adapter."""

from __future__ import annotations

import asyncio
import codecs
import json
import os
import re
from collections import deque
from contextlib import suppress
from typing import Any, AsyncIterator

from cptr.utils.agents.attachments import PreparedAgentAttachments
from cptr.utils.agents.events import (
    AgentDone,
    AgentError,
    AgentEvent,
    AgentReasoningDelta,
    AgentTextDelta,
    AgentToolOutputDelta,
    AgentToolUpdate,
)
from cptr.utils.agents.prompts import latest_user_text

CODEX_STDOUT_CHUNK_SIZE = 64 * 1024
CODEX_MAX_WIRE_MESSAGE_CHARS = 16 * 1024 * 1024


class CodexAppServer:
    def __init__(self, command: str, cwd: str, env: dict[str, str]) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.proc: asyncio.subprocess.Process | None = None
        self.reader_task: asyncio.Task | None = None
        self.stderr_task: asyncio.Task | None = None
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.stderr_tail: deque[str] = deque(maxlen=20)
        self.approval_mode = "auto"
        self.next_id = 1

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            self.command,
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd or os.getcwd(),
            env=self.env,
        )
        self.reader_task = asyncio.create_task(self._reader_loop())
        self.stderr_task = asyncio.create_task(self._stderr_loop())
        await self.request(
            "initialize",
            {
                "clientInfo": {"name": "cptr", "version": "0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        await self.notify("initialized")

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            if self.proc.returncode is None:
                self.proc.kill()
                await self.proc.wait()
        for task in (self.reader_task, self.stderr_task):
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        for future in self.pending.values():
            if not future.done():
                future.cancel()
        self.pending.clear()

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self.pending[request_id] = future
        await self._send({"id": request_id, "method": method, "params": params})
        response = await future
        if response.get("error"):
            raise RuntimeError(str(response["error"]))
        return response

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"method": method}
        if params is not None:
            payload["params"] = params
        await self._send(payload)

    async def _send(self, payload: dict[str, Any]) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(payload) + "\n").encode())
        await self.proc.stdin.drain()

    async def respond(self, request_id: str | int, result: dict[str, Any]) -> None:
        await self._send({"id": request_id, "result": result})

    async def respond_error(self, request_id: str | int, code: int, message: str) -> None:
        await self._send({"id": request_id, "error": {"code": code, "message": message}})

    async def _reader_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        reader_error: str | None = None
        buffer = ""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                chunk = await self.proc.stdout.read(CODEX_STDOUT_CHUNK_SIZE)
                if not chunk:
                    break
                buffer += decoder.decode(chunk)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    await self._handle_stdout_line(line.rstrip("\r"))
                if len(buffer) > CODEX_MAX_WIRE_MESSAGE_CHARS:
                    raise RuntimeError("Codex app-server emitted an oversized message")
            buffer += decoder.decode(b"", final=True)
            if buffer.strip():
                await self._handle_stdout_line(buffer.rstrip("\r"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced in the chat.
            reader_error = str(exc)
        finally:
            if reader_error:
                message = f"Codex app-server transport error: {reader_error}"
                self._fail_pending(message)
                await self.events.put({"method": "transport/error", "params": {"message": message}})
            else:
                message = self._exit_message()
                self._fail_pending(message)
                await self.events.put({"method": "process/exited", "params": {"message": message}})

    async def _handle_stdout_line(self, line: str) -> None:
        if not line.strip():
            return
        if len(line) > CODEX_MAX_WIRE_MESSAGE_CHARS:
            raise RuntimeError("Codex app-server emitted an oversized message")
        message = json.loads(line)
        if not isinstance(message, dict):
            raise RuntimeError("Codex app-server emitted an invalid message")
        await self._route_message(message)

    async def _route_message(self, message: dict[str, Any]) -> None:
        if "id" in message and "method" in message:
            await self._handle_server_request(message)
            return
        if (
            "id" in message
            and "method" not in message
            and ("result" in message or "error" in message)
        ):
            try:
                request_id = int(message["id"])
            except (TypeError, ValueError):
                return
            future = self.pending.pop(request_id, None)
            if future and not future.done():
                future.set_result(message)
            return
        if "method" in message:
            await self.events.put(message)

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        request_id = message["id"]
        method = str(message.get("method") or "")
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            decision = "decline" if self.approval_mode == "ask" else "accept"
            await self.respond(request_id, {"decision": decision})
            return
        if method in {"applyPatchApproval", "execCommandApproval"}:
            decision = "denied" if self.approval_mode == "ask" else "approved"
            await self.respond(request_id, {"decision": decision})
            return
        await self.respond_error(request_id, -32601, f"Method not found: {method}")

    async def _stderr_loop(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while line := await self.proc.stderr.readline():
            text = line.decode(errors="replace").strip()
            if text:
                self.stderr_tail.append(text[:2000])

    def _fail_pending(self, message: str) -> None:
        for future in self.pending.values():
            if not future.done():
                future.set_exception(RuntimeError(message))
        self.pending.clear()

    def _exit_message(self) -> str:
        detail = "\n".join(self.stderr_tail).strip()
        if detail:
            return f"Codex app-server exited: {detail}"
        if self.proc and self.proc.returncode is not None:
            return f"Codex app-server exited with code {self.proc.returncode}."
        return "Codex app-server exited."


def _approval_policy(value: str) -> str:
    return {"ask": "on-request", "auto": "on-failure", "full": "never"}.get(value, "on-failure")


def _chat_approval_mode(chat_params: dict[str, Any]) -> str:
    if "tool_approval_mode" in chat_params:
        return str(chat_params.get("tool_approval_mode") or "auto")
    if "auto_approve_tools" in chat_params:
        return "full" if chat_params.get("auto_approve_tools") else "auto"
    return "auto"


def _sandbox_for_approval(value: str) -> str:
    if value == "ask":
        return "read-only"
    return "workspace-write"


def _codex_turn_options(chat_params: dict[str, Any]) -> dict[str, str]:
    options: dict[str, str] = {}
    effort = chat_params.get("reasoningEffort") or chat_params.get("reasoning_effort")
    if isinstance(effort, str) and effort.strip():
        options["effort"] = effort.strip()
    service_tier = chat_params.get("serviceTier") or chat_params.get("service_tier")
    if isinstance(service_tier, str) and service_tier.strip():
        options["serviceTier"] = service_tier.strip()
    return options


def _resume_thread_id(resume_state: dict[str, Any] | None) -> str | None:
    if not resume_state:
        return None
    value = resume_state.get("thread_id")
    return value if isinstance(value, str) and value.strip() else None


async def _open_thread(
    client: CodexAppServer,
    thread_params: dict[str, Any],
    resume_state: dict[str, Any] | None,
) -> str | None:
    thread_id = _resume_thread_id(resume_state)
    if thread_id:
        try:
            response = await client.request(
                "thread/resume", {"threadId": thread_id, **thread_params}
            )
        except RuntimeError:
            response = await client.request("thread/start", thread_params)
    else:
        response = await client.request("thread/start", thread_params)

    thread = response.get("result", {}).get("thread", {})
    value = thread.get("id") if isinstance(thread, dict) else None
    return value if isinstance(value, str) and value else None


def _tool_from_item_event(method: str, params: dict[str, Any]) -> AgentToolUpdate | None:
    if method not in {"item/started", "item/completed"}:
        return None
    item = params.get("item") if isinstance(params.get("item"), dict) else {}
    item_type = str(item.get("type") or "").strip()
    normalized_type = re.sub(r"[^a-z0-9]+", " ", item_type.lower()).strip()
    if not _is_tool_item_type(normalized_type):
        return None
    call_id = params.get("itemId") or item.get("id") or item.get("callId")
    if not isinstance(call_id, str) or not call_id.strip():
        return None
    title = str(item.get("title") or item.get("name") or item_type or "Codex action").strip()
    detail = _item_detail(item)
    return AgentToolUpdate(
        call_id=call_id.strip(),
        name="run_command" if "command" in normalized_type and detail else "agent_tool",
        status="completed" if method == "item/completed" else "in_progress",
        arguments={"command": detail}
        if "command" in normalized_type and detail
        else {"title": title},
        output=None,
    )


def _is_tool_item_type(normalized_type: str) -> bool:
    if not normalized_type:
        return False
    blocked = ("user", "agent message", "assistant", "reasoning", "thought", "plan", "todo")
    if any(value in normalized_type for value in blocked):
        return False
    allowed = (
        "command",
        "file change",
        "patch",
        "edit",
        "mcp",
        "dynamic tool",
        "collab",
        "web search",
        "image",
        "error",
    )
    return any(value in normalized_type for value in allowed)


def _item_detail(item: dict[str, Any]) -> str | None:
    for key in ("command", "title", "summary", "text", "path", "prompt"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _tool_output_from_event(method: str, params: dict[str, Any]) -> AgentToolOutputDelta | None:
    if method not in {"item/commandExecution/outputDelta", "item/fileChange/outputDelta"}:
        return None
    call_id = params.get("itemId")
    delta = params.get("delta")
    if not isinstance(call_id, str) or not call_id.strip():
        return None
    if not isinstance(delta, str) or not delta:
        return None
    return AgentToolOutputDelta(
        call_id=call_id.strip(),
        delta=delta,
        stream_kind="command_output"
        if method == "item/commandExecution/outputDelta"
        else "file_change_output",
    )


async def run_codex_agent(
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
        env["CODEX_HOME"] = os.path.expanduser(str(profile["home"]))

    client = CodexAppServer(str(profile["command"]), workspace, env)
    thread_id: str | None = None
    turn_id: str | None = None
    try:
        approval_mode = _chat_approval_mode(chat_params)
        client.approval_mode = approval_mode
        await client.start()
        thread_params: dict[str, Any] = {
            "cwd": workspace,
            "approvalPolicy": _approval_policy(approval_mode),
            "sandbox": _sandbox_for_approval(approval_mode),
        }
        if model != "default":
            thread_params["model"] = model
        if system_prompt:
            thread_params["developerInstructions"] = system_prompt

        thread_id = await _open_thread(client, thread_params, resume_state)
        if not thread_id:
            yield AgentError("Codex did not return a thread id")
            return

        prompt = latest_user_text(messages)
        turn_input: list[dict[str, Any]] = []
        if prompt:
            turn_input.append({"type": "text", "text": prompt})
        for image in attachments.images:
            turn_input.append(
                {
                    "type": "image",
                    "url": f"data:{image.mime_type};base64,{image.base64}",
                }
            )
        turn_response = await client.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": turn_input,
                **_codex_turn_options(chat_params),
            },
        )
        turn = turn_response.get("result", {}).get("turn", {})
        turn_id = turn.get("id") if isinstance(turn, dict) else None

        while True:
            event = await client.events.get()
            method = event.get("method")
            params = event.get("params") if isinstance(event.get("params"), dict) else {}
            event_turn_id = params.get("turnId")
            if event_turn_id and turn_id and event_turn_id != turn_id:
                continue
            if method == "item/agentMessage/delta":
                delta = params.get("delta")
                if isinstance(delta, str) and delta:
                    yield AgentTextDelta(delta)
            elif method in ("item/reasoning/textDelta", "item/reasoning/summaryTextDelta"):
                delta = params.get("delta")
                if isinstance(delta, str) and delta:
                    yield AgentReasoningDelta(delta)
            else:
                tool_output = _tool_output_from_event(str(method or ""), params)
                if tool_output:
                    yield tool_output
                else:
                    tool = _tool_from_item_event(str(method or ""), params)
                    if tool:
                        yield tool
            if method == "turn/completed":
                yield AgentDone(
                    resume_state={
                        "profile_id": profile["id"],
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "workspace": workspace,
                        "model": model,
                    }
                )
                return
            elif method == "turn/failed":
                detail = params.get("message") or params.get("error") or "Codex turn failed"
                yield AgentError(str(detail))
                return
            elif method == "error":
                yield AgentError(str(params or "Codex app-server error"))
                return
            elif method == "process/exited":
                yield AgentError(str(params.get("message") or "Codex app-server exited."))
                return
            elif method == "transport/error":
                yield AgentError(str(params.get("message") or "Codex app-server transport error"))
                return
    except asyncio.CancelledError:
        if thread_id and turn_id:
            with suppress(Exception):
                await client.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in the chat.
        yield AgentError(str(exc))
    finally:
        await client.close()

"""Pi coding-agent adapter using Pi's public JSONL RPC mode."""

from __future__ import annotations

import asyncio
import codecs
import collections
import json
import os
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
from cptr.utils.identity import env_for, preexec_for


def _content_text(value: Any) -> str:
    if not isinstance(value, dict) or not isinstance(value.get("content"), list):
        return ""
    return "\n".join(
        item["text"]
        for item in value["content"]
        if isinstance(item, dict)
        and item.get("type") == "text"
        and isinstance(item.get("text"), str)
    )


def _tool_name(name: str) -> str:
    return "run_command" if name == "bash" else "agent_tool"


def _message_text(messages: list[dict[str, Any]], system_prompt: str, resumed: bool) -> str:
    source = messages
    if resumed:
        source = [
            next((message for message in reversed(messages) if message.get("role") == "user"), {})
        ]

    parts = []
    if system_prompt and not resumed:
        parts.append(f"[system]\n{system_prompt}")
    for message in source:
        content = message.get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                str(block.get("text") or "") for block in content if isinstance(block, dict)
            )
        if content:
            parts.append(f"[{message.get('role', 'user')}]\n{content}")
    return "\n\n".join(parts)


def translate_event(event: dict[str, Any]) -> AgentEvent | None:
    event_type = event.get("type")
    if event_type == "message_update":
        delta = event.get("assistantMessageEvent")
        if not isinstance(delta, dict):
            return None
        text = delta.get("delta")
        if not isinstance(text, str) or not text:
            return None
        if delta.get("type") == "thinking_delta":
            return AgentReasoningDelta(text)
        if delta.get("type") == "text_delta":
            return AgentTextDelta(text)
    elif event_type == "tool_execution_start":
        call_id = event.get("toolCallId")
        if not isinstance(call_id, str) or not call_id:
            return None
        raw_name = str(event.get("toolName") or "agent_tool")
        args = event.get("args") if isinstance(event.get("args"), dict) else {}
        arguments = {"command": args.get("command")} if raw_name == "bash" else {"input": args}
        return AgentToolUpdate(call_id, "in_progress", _tool_name(raw_name), arguments)
    elif event_type == "tool_execution_end":
        call_id = event.get("toolCallId")
        if not isinstance(call_id, str) or not call_id:
            return None
        return AgentToolUpdate(
            call_id,
            "failed" if event.get("isError") else "completed",
            _tool_name(str(event.get("toolName") or "agent_tool")),
            output=_content_text(event.get("result")),
        )
    return None


def tool_output_delta(event: dict[str, Any], sent: dict[str, str]) -> AgentToolOutputDelta | None:
    """Convert Pi's accumulated partial result into cptr's append/replace event."""
    call_id = event.get("toolCallId")
    if not isinstance(call_id, str) or not call_id:
        return None
    full = _content_text(event.get("partialResult"))
    previous = sent.get(call_id, "")
    replace = not full.startswith(previous)
    delta = full if replace else full[len(previous) :]
    sent[call_id] = full
    if not delta and not replace:
        return None
    return AgentToolOutputDelta(
        call_id,
        delta,
        "command_output"
        if _tool_name(str(event.get("toolName") or "")) == "run_command"
        else "tool_output",
        replace,
    )


class PiRpcClient:
    """Small stdio client for ``pi --mode rpc``."""

    def __init__(
        self, command: str, cwd: str, env: dict[str, str], model: str = "", preexec_fn=None
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.model = model
        self.preexec_fn = preexec_fn
        self.proc: asyncio.subprocess.Process | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.stderr_task: asyncio.Task[None] | None = None
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=20)
        self._next_id = 0

    async def start(self) -> None:
        args = ["--mode", "rpc"]
        if self.model and self.model != "default":
            args.extend(["--model", self.model])
        self.proc = await asyncio.create_subprocess_exec(
            self.command,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd or os.getcwd(),
            env=self.env,
            preexec_fn=self.preexec_fn,
        )
        self.reader_task = asyncio.create_task(self._read_stdout())
        self.stderr_task = asyncio.create_task(self._read_stderr())

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            if self.proc.returncode is None:
                self.proc.kill()
                await self.proc.wait()
        for task in (self.reader_task, self.stderr_task):
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._fail_pending("Pi RPC client closed")

    async def request(self, command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        request_id = str(self._next_id)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        await self._send({"type": command, "id": request_id, **(params or {})})
        response = await asyncio.wait_for(future, timeout=30)
        if not response.get("success"):
            raise RuntimeError(str(response.get("error") or f"Pi {command} failed"))
        return response

    async def cancel_extension_dialog(self, event: dict[str, Any]) -> bool:
        request_id = event.get("id")
        if event.get("method") not in {"select", "confirm", "input", "editor"} or not isinstance(
            request_id, str
        ):
            return False
        await self._send({"type": "extension_ui_response", "id": request_id, "cancelled": True})
        return True

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("Pi RPC process is not running")
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
        await self.proc.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        buffer = ""
        error = None
        try:
            while chunk := await self.proc.stdout.read(8192):
                buffer += decoder.decode(chunk)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.rstrip("\r").strip():
                        await self._route(json.loads(line.rstrip("\r")))
            buffer += decoder.decode(b"", final=True)
            if buffer.strip():
                raise RuntimeError("Pi RPC ended mid-record")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - report malformed RPC output to the chat.
            error = str(exc)
        finally:
            message = f"Pi RPC transport error: {error}" if error else "Pi RPC process exited"
            self._fail_pending(message)
            await self.events.put({"type": "process_exited", "message": message})

    async def _route(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if message.get("type") == "response" and isinstance(request_id, str):
            future = self.pending.pop(request_id, None)
            if future and not future.done():
                future.set_result(message)
            return
        await self.events.put(message)

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        while True:
            try:
                line = await self.proc.stderr.readline()
            except ValueError as exc:
                self._append_stderr(f"stderr line too long: {exc}", truncated=True)
                continue
            if not line:
                break
            self._append_stderr(line.decode(errors="replace"))

    def _append_stderr(self, text: str, *, truncated: bool = False) -> None:
        text = text.strip()
        if text:
            suffix = " [truncated]" if truncated else ""
            self.stderr_tail.append(f"{text[:2000]}{suffix}")

    def _fail_pending(self, message: str) -> None:
        for future in self.pending.values():
            if not future.done():
                future.set_exception(RuntimeError(message))
        self.pending.clear()

    def error_detail(self, message: str) -> str:
        return (
            f"{message}: {' | '.join(list(self.stderr_tail)[-3:])}" if self.stderr_tail else message
        )


async def run_pi_agent(
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
    env = env_for(identity, workspace) if identity and identity.is_pam else os.environ.copy()
    if profile.get("home"):
        env["HOME"] = os.path.expanduser(str(profile["home"]))
    client = PiRpcClient(
        str(profile.get("command") or "pi"),
        workspace,
        env,
        model,
        preexec_for(identity) if identity and identity.is_pam else None,
    )
    session_file = (resume_state or {}).get("session_file")
    resumed = isinstance(session_file, str) and bool(session_file)

    try:
        await client.start()
        if resumed:
            await client.request("switch_session", {"sessionPath": session_file})
        images = [
            {"type": "image", "data": image.base64, "mimeType": image.mime_type}
            for image in attachments.images
        ]
        await client.request(
            "prompt",
            {
                "message": _message_text(messages, system_prompt, resumed),
                **({"images": images} if images else {}),
            },
        )

        sent: dict[str, str] = {}
        while True:
            event = await asyncio.wait_for(client.events.get(), timeout=120)
            if event.get("type") == "process_exited":
                yield AgentError(client.error_detail(str(event.get("message") or "Pi exited")))
                return
            if event.get("type") == "extension_ui_request" and await client.cancel_extension_dialog(
                event
            ):
                yield AgentError(
                    "Pi extension requested interactive UI, which Computer does not support."
                )
                return
            if event.get("type") == "tool_execution_update":
                output = tool_output_delta(event, sent)
                if output:
                    yield output
                continue
            translated = translate_event(event)
            if translated:
                yield translated
            if event.get("type") == "agent_settled":
                stats = await client.request("get_session_stats")
                state = await client.request("get_state")
                state_data = state.get("data") if isinstance(state.get("data"), dict) else {}
                stat_data = stats.get("data") if isinstance(stats.get("data"), dict) else {}
                tokens = (
                    stat_data.get("tokens") if isinstance(stat_data.get("tokens"), dict) else None
                )
                session = state_data.get("sessionFile")
                yield AgentDone(
                    usage=tokens,
                    resume_state={"session_file": session}
                    if isinstance(session, str) and session
                    else None,
                )
                return
            if event.get("type") == "extension_error":
                yield AgentError(f"Pi extension error: {event.get('error') or 'unknown error'}")
                return
    except asyncio.CancelledError:
        with suppress(Exception):
            await asyncio.wait_for(client.request("abort"), timeout=2)
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in chat.
        yield AgentError(client.error_detail(str(exc)))
    finally:
        await client.close()

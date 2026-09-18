"""Command Code CLI adapter (`cmd` headless JSON mode).

Protocol (https://commandcode.ai/docs, verified against CLI 1.56.0 output):
- `cmd -p <prompt> --output-format json [--model M] [--effort L] [--resume ID]
  [--yolo] [--skip-onboarding]` emits `{"type": "event", "event": {...}}`
  frames and exactly one terminal `{"type": "result", ...}` line.
- Text streams via `text_delta` events; tool activity via
  `tool_running`/`tool_completed` events carrying `toolCallId`, `toolName`,
  `description`, and (on completion) `output`.
- `result.subtype`: `success | error | max_turns`. `result.finalText` holds
  the accumulated answer; `result.usage` uses camelCase keys
  (`inputTokens`, `outputTokens`, `cacheReadTokens`, `cacheWriteTokens`).
- Resume: `--resume <sessionId>` from `result.sessionId`.
- Exit codes: 0 ok; 3 auth; 4 permission; 5 rate-limit; 8 max-turns;
  10 credits; 130 interrupted; 1 general.
- Unknown event types are ignored (forward-compatible by the CLI's contract).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

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

_EXIT_CODE_MESSAGES = {
    3: "authentication failed (run `cmd login`)",
    4: "permission denied",
    5: "rate limited",
    8: "max turns reached",
    10: "insufficient credits",
    130: "interrupted",
}


def _auto_approve(chat_params: dict[str, Any]) -> bool:
    if chat_params.get("tool_approval_mode") == "full":
        return True
    return bool(chat_params.get("auto_approve_tools"))


def _usage_from(result: dict[str, Any]) -> dict[str, Any] | None:
    raw = result.get("usage")
    if not isinstance(raw, dict):
        return None
    # CLI emits camelCase; normalize to snake_case for the shared usage shape.
    key_map = {
        "inputTokens": "input_tokens",
        "outputTokens": "output_tokens",
        "cacheReadTokens": "cache_read_tokens",
        "cacheWriteTokens": "cache_write_tokens",
    }
    usage = {key_map.get(k, k): v for k, v in raw.items() if isinstance(v, (int, float))}
    if "total_tokens" not in usage:
        usage["total_tokens"] = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
    return usage or None


def _tool_update_from_event(
    event: dict[str, Any], session_id: str | None
) -> AgentToolUpdate | None:
    event_type = event.get("type")
    if event_type not in ("tool_running", "tool_completed"):
        return None
    tool_name = str(event.get("toolName") or "Command Code action").strip()
    call_id = str(event.get("toolCallId") or "").strip()
    if not call_id:
        call_id = f"{session_id or 'cmd'}:{tool_name}"
    arguments: dict[str, Any] = {"title": tool_name}
    description = event.get("description")
    if isinstance(description, str) and description:
        arguments["description"] = description
    raw_params = event.get("input")
    if isinstance(raw_params, dict):
        arguments.update(raw_params)
    output = event.get("output")
    return AgentToolUpdate(
        call_id=call_id,
        name="agent_tool",
        status="completed" if event_type == "tool_completed" else "in_progress",
        arguments=arguments,
        output=output if isinstance(output, str) else "",
    )


async def run_command_code_agent(
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

    session_id = None
    if resume_state and isinstance(resume_state.get("session_id"), str):
        session_id = resume_state["session_id"]

    # Command Code headless input is prompt text only; there is no documented
    # image-block protocol, so images are omitted (documented limitation).
    if attachments.images:
        pass  # intentionally omitted; noted in the issue doc

    prompt = turn_prompt_text(messages, system_prompt, resumed=bool(session_id))
    if not prompt.strip():
        yield AgentError("Command Code turn produced an empty prompt")
        return

    args: list[str] = [
        "-p",
        prompt,
        "--output-format",
        "json",
        "--skip-onboarding",
    ]
    if model and model != "default":
        args.extend(["--model", model])
    if session_id:
        args.extend(["--resume", session_id])
    if _auto_approve(chat_params):
        args.append("--yolo")

    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            str(profile["command"]),
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace or os.getcwd(),
            env=env,
            preexec_fn=preexec_for(identity) if identity and identity.is_pam else None,
        )

        usage: dict[str, Any] | None = None
        result_session_id: str | None = None
        result_subtype: str | None = None
        result_error: str | None = None
        final_text: str | None = None
        stderr_tail: list[str] = []

        async def _drain_stderr() -> None:
            assert proc is not None and proc.stderr is not None
            try:
                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        return
                    text = line.decode(errors="replace").strip()
                    if text:
                        stderr_tail.append(text)
                        del stderr_tail[:-20]
            except Exception:  # noqa: BLE001 - stderr drain is best-effort
                return

        stderr_task = asyncio.create_task(_drain_stderr())
        assert proc.stdout is not None
        try:
            async for line in proc.stdout:
                text = line.decode(errors="replace").strip()
                if not text:
                    continue
                try:
                    frame = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(frame, dict):
                    continue
                if frame.get("type") == "event":
                    event = frame.get("event")
                    if not isinstance(event, dict):
                        continue
                    tool = _tool_update_from_event(event, session_id)
                    if tool:
                        yield tool
                        continue
                    delta = event.get("delta")
                    if event.get("type") == "text_delta" and isinstance(delta, str) and delta:
                        yield AgentTextDelta(delta)
                    # Unknown event types are ignored (forward-compatible).
                elif frame.get("type") == "result":
                    usage = _usage_from(frame)
                    result_session_id = (
                        frame.get("sessionId") if isinstance(frame.get("sessionId"), str) else None
                    )
                    result_subtype = frame.get("subtype")
                    final_text = (
                        frame.get("finalText") if isinstance(frame.get("finalText"), str) else None
                    )
                    raw_error = frame.get("error")
                    if isinstance(raw_error, str):
                        result_error = raw_error
                    elif isinstance(raw_error, dict):
                        result_error = str(raw_error.get("message") or raw_error)
                    break
        finally:
            stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await stderr_task

        if proc.returncode is None:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=10)
        if proc.returncode is None:
            proc.terminate()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=2)

        if result_subtype == "success":
            if final_text:
                yield AgentTextDelta(final_text)
            yield AgentDone(
                usage=usage,
                resume_state={
                    "profile_id": profile["id"],
                    "session_id": result_session_id or session_id,
                    "workspace": workspace,
                    "model": model,
                },
            )
        elif result_subtype is not None:
            detail = result_error or " ".join(stderr_tail[-3:]) or result_subtype
            yield AgentError(f"Command Code run ended with {result_subtype}: {detail}")
        else:
            # No result line: process died before producing one.
            exit_note = _EXIT_CODE_MESSAGES.get(proc.returncode, f"exit code {proc.returncode}")
            tail = " ".join(stderr_tail[-3:]) or exit_note
            yield AgentError(f"Command Code CLI exited without a result: {tail}")
    except asyncio.CancelledError:
        if proc is not None and proc.returncode is None:
            proc.terminate()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=2)
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in chat.
        yield AgentError(str(exc))

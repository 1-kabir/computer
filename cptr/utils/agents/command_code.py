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
import codecs
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


async def _terminate_proc(proc: asyncio.subprocess.Process) -> None:
    """Terminate, then SIGKILL-escalate, then reap the child."""
    if proc.returncode is not None:
        return
    proc.terminate()
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(proc.wait(), timeout=2)
    if proc.returncode is None:
        proc.kill()
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=2)


def _usage_from(result: dict[str, Any]) -> dict[str, Any] | None:
    raw = result.get("usage")
    if not isinstance(raw, dict):
        return None
    # CLI emits camelCase; normalize to snake_case for the shared usage shape.
    # Unknown keys are dropped so junk/future fields cannot pollute downstream
    # usage aggregation.
    key_map = {
        "inputTokens": "input_tokens",
        "outputTokens": "output_tokens",
        "cacheReadTokens": "cache_read_tokens",
        "cacheWriteTokens": "cache_write_tokens",
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "total_tokens": "total_tokens",
    }
    usage = {
        k: v
        for k, v in ((key_map.get(raw_k), v) for raw_k, v in raw.items())
        if k is not None and isinstance(v, (int, float))
    }
    if "total_tokens" not in usage:
        usage["total_tokens"] = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
    return usage or None


def _tool_update_from_event(
    event: dict[str, Any], session_id: str | None, call_counter: dict[str, int]
) -> AgentToolUpdate | None:
    event_type = event.get("type")
    if event_type not in ("tool_running", "tool_completed"):
        return None
    tool_name = str(event.get("toolName") or "Command Code action").strip()
    call_id = str(event.get("toolCallId") or "").strip()
    if not call_id:
        # Without a CLI-provided id, same-named tool calls would collapse into
        # one merged UI item; disambiguate with a per-name counter.
        seq = call_counter.get(tool_name, 0)
        call_counter[tool_name] = seq + 1
        call_id = f"{session_id or 'cmd'}:{tool_name}:{seq}"
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
        from pathlib import Path

        if identity and identity.is_pam:
            from cptr.utils.identity import expand_user_path

            env["HOME"] = str(expand_user_path(str(profile["home"]), identity))
        else:
            env["HOME"] = str(Path(os.path.expanduser(str(profile["home"]))))

    session_id = None
    if resume_state and isinstance(resume_state.get("session_id"), str):
        # cmd sessions are cwd-scoped: resuming an id captured in a different
        # workspace either fails or silently starts fresh, so ignore it.
        stored_workspace = resume_state.get("workspace")
        if stored_workspace is None or stored_workspace == workspace:
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
    # The prompt is the value of -p in its own argv element, so it cannot
    # execute as a flag — but a prompt starting with '-' can still confuse
    # getopt-style parsers into "expected one argument" failures.
    if prompt.startswith("-"):
        prompt = " " + prompt

    proc: asyncio.subprocess.Process | None = None
    try:
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
        except OSError as exc:
            # E2BIG: the prompt is a single argv element and Linux caps one
            # argument at ~128KB (MAX_ARG_STRLEN).
            if len(prompt) > 100_000:
                yield AgentError(
                    "Command Code turn failed: prompt too large for the CLI's command "
                    f"line ({len(prompt)} characters). Shorten the conversation and retry."
                )
                return
            yield AgentError(f"Command Code CLI failed to start: {exc}")
            return

        usage: dict[str, Any] | None = None
        result_session_id: str | None = None
        result_subtype: str | None = None
        result_error: str | None = None
        final_text: str | None = None
        stderr_tail: list[str] = []
        call_counter: dict[str, int] = {}

        async def _drain_stderr() -> None:
            assert proc is not None and proc.stderr is not None
            while True:
                try:
                    line = await proc.stderr.readline()
                except ValueError:
                    # Line exceeded the stream buffer limit; skip it but keep
                    # draining so the child's stderr pipe never fills.
                    continue
                if not line:
                    return
                text = line.decode(errors="replace").strip()
                if text:
                    stderr_tail.append(text[:2000])
                    del stderr_tail[:-20]

        stderr_task = asyncio.create_task(_drain_stderr())
        assert proc.stdout is not None
        emitted_any_text = False
        try:
            # Chunked read with an incremental decoder: tool events can embed
            # output exceeding asyncio's 64KB readline limit (a bare
            # readline() would raise ValueError and kill the turn).
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            buffer = ""
            eof = False
            while not eof:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    eof = True
                buffer += decoder.decode(chunk, final=eof)
                while True:
                    newline = buffer.find("\n")
                    if newline < 0:
                        if eof and buffer:
                            text, buffer = buffer, ""
                        else:
                            break
                    else:
                        text, buffer = buffer[:newline], buffer[newline + 1 :]
                    text = text.strip()
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
                        tool = _tool_update_from_event(event, session_id, call_counter)
                        if tool:
                            yield tool
                            continue
                        delta = event.get("delta")
                        if event.get("type") == "text_delta" and isinstance(delta, str) and delta:
                            emitted_any_text = True
                            yield AgentTextDelta(delta)
                        # Unknown event types are ignored (forward-compatible).
                    elif frame.get("type") == "result":
                        usage = _usage_from(frame)
                        result_session_id = (
                            frame.get("sessionId")
                            if isinstance(frame.get("sessionId"), str)
                            else None
                        )
                        result_subtype = frame.get("subtype")
                        final_text = (
                            frame.get("finalText")
                            if isinstance(frame.get("finalText"), str)
                            else None
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
        await _terminate_proc(proc)

        if result_subtype == "success":
            if final_text and not emitted_any_text:
                # The CLI streams text via deltas AND repeats the full answer
                # in result.finalText; emitting both double-renders in the UI.
                # Only fall back to finalText when nothing was streamed.
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
        elif result_subtype == "max_turns":
            # The turn hit the turn cap: keep the partial answer AND the
            # session (persisted via resume_state) so the next turn can
            # continue instead of silently discarding the work.
            if final_text and not emitted_any_text:
                yield AgentTextDelta(final_text)
            yield AgentTextDelta(
                "\n\n_(turn limit reached — ask to continue to pick up where this left off)_"
            )
            yield AgentDone(
                usage=usage,
                resume_state={
                    "profile_id": profile["id"],
                    "session_id": result_session_id or session_id,
                    "workspace": workspace,
                    "model": model,
                    "truncated": "max_turns",
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
        if proc is not None:
            await _terminate_proc(proc)
        raise
    except GeneratorExit:
        # Consumer abandoned the generator (client disconnect, GC). Without
        # this the child would be orphaned indefinitely.
        if proc is not None:
            await _terminate_proc(proc)
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in chat.
        if proc is not None:
            await _terminate_proc(proc)
        yield AgentError(str(exc))

"""Google Antigravity CLI adapter (`agy` headless stream-json mode).

Protocol (https://antigravity.google/docs/cli/headless):
- `agy -p <prompt> --output-format stream-json` emits one `init` event,
  any number of `step_update` events, and exactly one terminal `result`.
- `step_update.step_type`: `user_input`, `agent_response` (streamed
  `text_delta`), `tool` (details in `tool_info`), `checkpoint`, plus
  subagent steps carrying `subagent_info`.
- `result.status`: SUCCESS | ERROR | CANCELED | INTERRUPTED | INVALID |
  WAITING | RUNNING. `result.usage` carries token counts for the run.
- Resume: `--conversation <id>`; unknown models fail loudly (non-zero exit).
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

# Antigravity's default headless timeout is 5m — too short for agentic tool
# workloads (multi-step builds, test runs). 30m keeps long turns alive.
_PRINT_TIMEOUT = "30m"

_TERMINAL_STATUS_HINTS = {
    "WAITING": "agent is paused awaiting permission (headless mode cannot approve it)",
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
    usage = {k: v for k, v in raw.items() if isinstance(v, (int, float))}
    if "total_tokens" not in usage:
        usage["total_tokens"] = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
    return usage or None


def _tool_update_from_step(conversation_id: str, step: dict[str, Any]) -> AgentToolUpdate | None:
    info = step.get("tool_info")
    if not isinstance(info, dict):
        return None
    tool_name = str(info.get("name") or step.get("tool_name") or "Antigravity action").strip()
    call_id = f"{conversation_id}:{step.get('step_index', 'x')}:{tool_name}"
    raw_params = info.get("parameters")
    arguments = raw_params if isinstance(raw_params, dict) else {}
    if isinstance(info.get("error"), dict):
        arguments = {**arguments, "error": info["error"]}
    output = info.get("output")
    return AgentToolUpdate(
        call_id=call_id,
        name="agent_tool",
        status="completed" if step.get("state") == "DONE" else "in_progress",
        arguments={**arguments, "title": tool_name},
        output=output if isinstance(output, str) else "",
    )


async def run_antigravity_agent(
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

    conversation_id = None
    if resume_state and isinstance(resume_state.get("session_id"), str):
        conversation_id = resume_state["session_id"]

    # Antigravity headless content blocks are text-only; images cannot be
    # uploaded via this protocol (documented limitation).
    if attachments.images:
        pass  # intentionally omitted; noted in the issue doc

    prompt = turn_prompt_text(messages, system_prompt, resumed=bool(conversation_id))
    if not prompt.strip():
        yield AgentError("Antigravity turn produced an empty prompt")
        return

    args: list[str] = [
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--print-timeout",
        _PRINT_TIMEOUT,
    ]
    if model and model != "default":
        args.extend(["--model", model])
    if conversation_id:
        args.extend(["--conversation", conversation_id])
    if _auto_approve(chat_params):
        args.append("--dangerously-skip-permissions")

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
                    "Antigravity turn failed: prompt too large for the CLI's command "
                    f"line ({len(prompt)} characters). Shorten the conversation and retry."
                )
                return
            yield AgentError(f"Antigravity CLI failed to start: {exc}")
            return

        usage: dict[str, Any] | None = None
        result_conversation_id: str | None = None
        final_status: str | None = None
        final_error: str | None = None
        stderr_tail: list[str] = []

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
        try:
            # Chunked read with an incremental decoder: Antigravity's NDJSON
            # tool events embed full tool output, which can exceed asyncio's
            # 64KB readline limit (a bare readline() would raise ValueError
            # and kill the turn).
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
                        event = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    kind = event.get("event")
                    if kind == "step_update":
                        step = event.get("step_update")
                        if not isinstance(step, dict):
                            continue
                        if step.get("step_type") == "agent_response":
                            delta = step.get("text_delta")
                            if isinstance(delta, str) and delta:
                                yield AgentTextDelta(delta)
                        elif step.get("step_type") == "tool":
                            conv = (
                                step.get("conversation_id")
                                or result_conversation_id
                                or conversation_id
                                or "agy"
                            )
                            tool = _tool_update_from_step(str(conv), step)
                            if tool:
                                yield tool
                    elif kind == "init":
                        init = event.get("init")
                        if isinstance(init, dict) and event.get("conversation_id"):
                            result_conversation_id = event["conversation_id"]
                    elif kind == "result":
                        result = event.get("result")
                        if isinstance(result, dict):
                            usage = _usage_from(result)
                            result_conversation_id = (
                                result.get("conversation_id") or result_conversation_id
                            )
                            final_status = result.get("status")
                            final_error = result.get("error")
                        break
        finally:
            stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await stderr_task

        if proc.returncode is None:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=10)
        await _terminate_proc(proc)

        if final_status == "SUCCESS":
            yield AgentDone(
                usage=usage,
                resume_state={
                    "profile_id": profile["id"],
                    "session_id": result_conversation_id or conversation_id,
                    "workspace": workspace,
                    "model": model,
                },
            )
        elif final_status is not None:
            detail = final_error or " ".join(stderr_tail[-3:]) or final_status
            hint = _TERMINAL_STATUS_HINTS.get(str(final_status))
            suffix = f" ({hint})" if hint else ""
            yield AgentError(f"Antigravity run ended with status {final_status}{suffix}: {detail}")
        else:
            # No result event: process died before producing one.
            tail = " ".join(stderr_tail[-3:]) or f"exit code {proc.returncode}"
            yield AgentError(f"Antigravity CLI exited without a result: {tail}")
    except asyncio.CancelledError:
        if proc is not None:
            await _terminate_proc(proc)
        raise
    except GeneratorExit:
        # Consumer abandoned the generator (client disconnect, GC). Without
        # this the child would be orphaned until its own timeout expires.
        if proc is not None:
            await _terminate_proc(proc)
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in chat.
        if proc is not None:
            await _terminate_proc(proc)
        yield AgentError(str(exc))

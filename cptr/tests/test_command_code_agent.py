"""Tests for the Command Code CLI agent profile integration.

Covers profile schema/validation, NDJSON frame parsing helpers (the core of
the driver), and dispatch wiring. The subprocess itself is exercised via
live smoke testing, not unit tests.

Safety: CPTR_DATA_DIR guard — never touch the live data dir.
"""

import json
import os

import pytest

_LIVE_PREFIX = os.path.expanduser("~/.local/share/cptr")
if os.path.abspath(os.environ.get("CPTR_DATA_DIR", _LIVE_PREFIX)) == _LIVE_PREFIX:
    pytest.exit("refusing: CPTR_DATA_DIR points at the live data dir", returncode=1)

from cptr.utils.agents.command_code import (
    _tool_update_from_event,
    _usage_from,
)
from cptr.utils.agents.models import normalize_agent_profile


def test_command_code_profile_normalizes():
    profile = normalize_agent_profile({"id": "command-code", "agent": "command_code"})
    assert profile["command"] == "cmd"
    assert profile["name"] == "Command Code"
    assert profile["mode"] == "auto"


def test_command_code_rejects_unknown_agent():
    with pytest.raises(Exception, match="agent must be"):
        normalize_agent_profile({"id": "x", "agent": "bogus"})


def test_usage_from_result_normalizes_camel_case():
    usage = _usage_from(
        {
            "usage": {
                "inputTokens": 22328,
                "outputTokens": 20,
                "cacheReadTokens": 9984,
                "cacheWriteTokens": 0,
            }
        }
    )
    assert usage == {
        "input_tokens": 22328,
        "output_tokens": 20,
        "cache_read_tokens": 9984,
        "cache_write_tokens": 0,
        "total_tokens": 22348,
    }


def test_usage_from_result_keeps_snake_case_passthrough():
    usage = _usage_from({"usage": {"input_tokens": 10, "output_tokens": 5}})
    assert usage == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def test_usage_from_non_dict_is_none():
    assert _usage_from({}) is None
    assert _usage_from({"usage": "x"}) is None


def test_tool_update_from_running_event():
    event = {
        "type": "tool_running",
        "toolCallId": "call-1",
        "toolName": "run_command",
        "description": "Listing files",
        "input": {"command": "ls"},
    }
    tool = _tool_update_from_event(event, "sess-1", {})
    assert tool is not None
    assert tool.status == "in_progress"
    assert tool.call_id == "call-1"
    assert tool.arguments["title"] == "run_command"
    assert tool.arguments["command"] == "ls"


def test_tool_update_from_completed_event_has_output():
    event = {
        "type": "tool_completed",
        "toolCallId": "call-2",
        "toolName": "read_file",
        "output": "file contents",
    }
    tool = _tool_update_from_event(event, None, {})
    assert tool is not None
    assert tool.status == "completed"
    assert tool.output == "file contents"


def test_tool_update_without_call_id_falls_back_with_counter():
    event = {"type": "tool_running", "toolName": "write_file"}
    counter: dict[str, int] = {}
    tool = _tool_update_from_event(event, "sess-9", counter)
    assert tool is not None
    assert tool.call_id == "sess-9:write_file:0"
    # A second id-less call to the same tool gets a distinct id instead of
    # collapsing into the same UI item.
    tool2 = _tool_update_from_event(dict(event), "sess-9", counter)
    assert tool2 is not None
    assert tool2.call_id == "sess-9:write_file:1"


def test_tool_update_ignores_non_tool_events():
    assert _tool_update_from_event({"type": "text_delta", "delta": "hi"}, None, {}) is None
    assert _tool_update_from_event({"type": "unknown_future_event"}, None, {}) is None


def test_stream_frame_shapes_round_trip():
    """The parser's input contract: real frames from CLI 1.56.0 output."""
    event_line = json.dumps(
        {
            "type": "event",
            "event": {
                "type": "text_delta",
                "delta": "mango",
            },
        }
    )
    result_line = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "sessionId": "6859d72e-fb43-41ec-9d01-9c5b0c78b3d1",
            "finalText": "mango",
            "stopReason": "end_turn",
            "turnCount": 1,
            "usage": {"inputTokens": 22328, "outputTokens": 20, "cacheReadTokens": 9984},
            "durationMs": 3210,
        }
    )
    event = json.loads(event_line)
    assert event["type"] == "event" and event["event"]["type"] == "text_delta"
    result = json.loads(result_line)
    assert result["type"] == "result" and result["subtype"] == "success"
    assert result["sessionId"] and result["finalText"] == "mango"


def test_terminate_proc_escalates_to_kill_and_reaps():
    """Subprocess-hygiene regression: a child ignoring SIGTERM is SIGKILLed
    and reaped, not left running."""
    import asyncio

    from cptr.utils.agents.command_code import _terminate_proc

    async def _run():
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            "trap '' TERM; sleep 30",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert proc.returncode is None
        await _terminate_proc(proc)
        return proc.returncode

    returncode = asyncio.run(_run())
    assert returncode is not None and returncode != 0


def test_terminate_proc_is_noop_on_exited_process():
    import asyncio

    from cptr.utils.agents.command_code import _terminate_proc

    async def _run():
        proc = await asyncio.create_subprocess_exec(
            "true",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        await _terminate_proc(proc)
        return proc.returncode

    assert asyncio.run(_run()) == 0


def test_dispatch_includes_command_code():
    """The runners map in chat_task must route the command_code agent type."""
    import inspect

    from cptr.utils import chat_task

    source = inspect.getsource(chat_task.run_chat_task)
    assert '"command_code": run_command_code_agent' in source

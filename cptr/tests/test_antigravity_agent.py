"""Tests for the Antigravity CLI agent profile integration.

Covers profile schema/validation, NDJSON stream parsing (the core of the
driver), and dispatch wiring. The subprocess itself is exercised via
live smoke testing, not unit tests.

Safety: CPTR_DATA_DIR guard — never touch the live data dir.
"""

import json
import os

import pytest

_LIVE_PREFIX = os.path.expanduser("~/.local/share/cptr")
if os.path.abspath(os.environ.get("CPTR_DATA_DIR", _LIVE_PREFIX)) == _LIVE_PREFIX:
    pytest.exit("refusing: CPTR_DATA_DIR points at the live data dir", returncode=1)

from cptr.utils.agents.antigravity import (  # noqa: E402
    _tool_update_from_step,
    _usage_from,
)
from cptr.utils.agents.models import normalize_agent_profile  # noqa: E402


def test_antigravity_profile_normalizes():
    profile = normalize_agent_profile({"id": "antigravity", "agent": "antigravity"})
    assert profile["command"] == "agy"
    assert profile["name"] == "Antigravity"
    assert profile["mode"] == "auto"


def test_antigravity_rejects_unknown_agent():
    with pytest.raises(Exception, match="agent must be"):
        normalize_agent_profile({"id": "x", "agent": "bogus"})


def test_usage_from_result_adds_total():
    usage = _usage_from({"usage": {"input_tokens": 10, "output_tokens": 5}})
    assert usage == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def test_usage_from_result_keeps_provider_total():
    usage = _usage_from(
        {"usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 99}}
    )
    assert usage["total_tokens"] == 99


def test_usage_from_non_dict_is_none():
    assert _usage_from({}) is None
    assert _usage_from({"usage": "x"}) is None
    # An all-zero usage dict is still a dict — passed through (never None).
    zero = _usage_from({"usage": {"input_tokens": 0, "output_tokens": 0}})
    assert zero is not None and zero["total_tokens"] == 0


def test_tool_update_from_step_done():
    step = {
        "step_index": 4,
        "state": "DONE",
        "step_type": "tool",
        "tool_name": "run_command",
        "tool_info": {
            "name": "run_command",
            "parameters": {"CommandLine": "echo hi"},
            "output": "hi\n",
        },
    }
    tool = _tool_update_from_step("conv-1", step)
    assert tool is not None
    assert tool.status == "completed"
    assert tool.call_id == "conv-1:4:run_command"
    assert tool.arguments["title"] == "run_command"
    assert tool.output == "hi\n"


def test_tool_update_from_step_error_attaches_error():
    step = {
        "step_index": 2,
        "state": "DONE",
        "tool_info": {"name": "write_file", "error": {"type": "denied", "message": "nope"}},
    }
    tool = _tool_update_from_step("conv-2", step)
    assert tool is not None
    assert tool.arguments["error"] == {"type": "denied", "message": "nope"}


def test_tool_update_from_non_tool_step_is_none():
    assert _tool_update_from_step("c", {"step_type": "agent_response"}) is None
    assert _tool_update_from_step("c", {"step_type": "tool", "tool_info": "junk"}) is None


def test_stream_event_shapes_round_trip():
    """The parser's input contract: real samples from Antigravity's docs."""
    init_line = json.dumps(
        {
            "event": "init",
            "conversation_id": "abc",
            "init": {"cwd": "/w", "tools": ["run_command"], "permission_mode": "request-review"},
        }
    )
    delta_line = json.dumps(
        {
            "event": "step_update",
            "step_update": {
                "step_index": 3,
                "state": "ACTIVE",
                "step_type": "agent_response",
                "text_delta": "Hel",
            },
        }
    )
    result_line = json.dumps(
        {
            "event": "result",
            "result": {
                "conversation_id": "abc",
                "status": "SUCCESS",
                "response": "Hello",
                "duration_seconds": 1.5,
                "num_turns": 1,
                "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            },
        }
    )
    for line in (init_line, delta_line, result_line):
        event = json.loads(line)
        assert event["event"] in {"init", "step_update", "result"}


def test_dispatch_includes_antigravity():
    """The runners map in chat_task must route the antigravity agent type."""
    import inspect

    from cptr.utils import chat_task

    source = inspect.getsource(chat_task.run_chat_task)
    assert '"antigravity": run_antigravity_agent' in source

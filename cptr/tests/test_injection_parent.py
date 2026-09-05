"""Tests for injection parent resolution in async subagent completions.

The completion notice must attach to the chat's current branch leaf
(chat.current_message_id), never to an arbitrary "newest done assistant"
that can be a sibling of an in-flight task — that is what sidelined live
conversations when the pointer moved onto the injected branch.
"""

from types import SimpleNamespace

from cptr.utils.async_subagents import _resolve_injection_parent_id


def _msg(id: str, role: str = "assistant", done: bool = True):
    return SimpleNamespace(id=id, role=role, done=done)


def test_prefers_current_message_leaf_over_newest_done_assistant():
    chat = SimpleNamespace(current_message_id="in-flight-leaf")
    msgs = [
        _msg("old-done"),
        _msg("in-flight-leaf", role="assistant", done=False),
    ]
    record = {"parent_message_id": "dispatch-origin"}
    assert _resolve_injection_parent_id(chat, msgs, record) == "in-flight-leaf"


def test_leaf_child_of_user_message_is_used():
    # Simulates: user sent a message while a task was active; the leaf is the
    # user message (or its assistant), and the notice must join that branch.
    chat = SimpleNamespace(current_message_id="user-leaf")
    msgs = [
        _msg("old-done"),
        _msg("user-leaf", role="user", done=True),
    ]
    assert _resolve_injection_parent_id(chat, msgs, {}) == "user-leaf"


def test_falls_back_to_newest_done_assistant_when_leaf_missing():
    chat = SimpleNamespace(current_message_id="deleted-leaf")
    msgs = [
        _msg("done-1"),
        _msg("done-2"),
    ]
    assert _resolve_injection_parent_id(chat, msgs, {}) == "done-2"


def test_falls_back_to_dispatch_parent_when_no_history():
    chat = SimpleNamespace(current_message_id=None)
    record = {"parent_message_id": "origin"}
    assert _resolve_injection_parent_id(chat, [], record) == "origin"


def test_empty_string_leaf_treated_as_missing():
    chat = SimpleNamespace(current_message_id="")
    msgs = [_msg("done-1")]
    assert _resolve_injection_parent_id(chat, msgs, {"parent_message_id": None}) == "done-1"


def test_returns_none_without_any_anchor():
    chat = SimpleNamespace(current_message_id="gone")
    assert _resolve_injection_parent_id(chat, [], {}) is None

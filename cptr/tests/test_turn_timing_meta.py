"""Tests for turn-timing meta stamps in the chat task lifecycle.

Regression tests for the turn-timing accuracy bug: the frontend computed
turn duration from created_at deltas, but assistant rows are created as
placeholders at task start (~same instant as the user's send) and completion
never touched created_at — so durations showed ~0s (queue wait) or, on
regeneration, the time since the ORIGINAL prompt.

The fix stamps meta.timing = {started_at, completed_at} at the task
lifecycle; the terminal-save stamp logic is _apply_timing_stamp (pure,
tested here directly).

Safety: aborts if CPTR_DATA_DIR looks like the live instance data dir.
"""

import os

import pytest

_LIVE_PREFIX = os.path.expanduser("~/.local/share/cptr")
if os.path.abspath(os.environ.get("CPTR_DATA_DIR", _LIVE_PREFIX)) == _LIVE_PREFIX:
    pytest.exit("refusing: CPTR_DATA_DIR points at the live data dir", returncode=1)

from cptr.utils.chat_task import _apply_timing_stamp  # noqa: E402


def _stamp(message_meta: dict, kwargs: dict, completed_at: int = 7_000):
    """Run _apply_timing_stamp the way _save_message does."""

    def mark():
        message_meta["timing"]["completed_at"] = completed_at

    return _apply_timing_stamp(message_meta, {**kwargs, "_timing_mark": mark})


def test_terminal_save_stamps_completed_at():
    message_meta = {"timing": {"started_at": 1_000}}
    out = _stamp(message_meta, {"done": True})
    assert out is not None
    assert out["timing"]["completed_at"] == 7_000
    # The delta the frontend will compute:
    assert (out["timing"]["completed_at"] - out["timing"]["started_at"]) / 1000 == 6.0


def test_stamp_is_idempotent_across_multiple_done_saves():
    message_meta = {"timing": {"started_at": 1_000}}
    _stamp(message_meta, {"done": True}, completed_at=7_000)
    out2 = _stamp(message_meta, {"done": True}, completed_at=9_999)
    assert out2["timing"]["completed_at"] == 7_000  # original stamp kept


def test_merge_preserves_explicit_error_meta():
    """Error path passes meta={**message_meta, 'error': ...}; the merge must
    keep the error key AND the timing stamp."""
    message_meta = {"timing": {"started_at": 1_000}}
    out = _stamp(message_meta, {"done": True, "meta": {**message_meta, "error": "boom"}})
    assert out["error"] == "boom"
    assert out["timing"]["completed_at"] == 7_000


def test_explicit_meta_keys_win_over_message_meta():
    message_meta = {"timing": {"started_at": 1_000}, "truncated": {"reason": "x"}}
    out = _stamp(message_meta, {"done": True, "meta": {"truncated": {"reason": "y"}}})
    assert out["truncated"]["reason"] == "y"
    assert out["timing"]["completed_at"] == 7_000


def test_no_started_at_no_stamp():
    """Tasks without a start stamp (legacy rows) pass meta through untouched."""
    message_meta: dict = {}
    kwargs = {"done": True, "content": "x"}
    out = _apply_timing_stamp(message_meta, dict(kwargs))
    assert out is None
    assert "meta" not in kwargs


def test_non_terminal_saves_never_stamp():
    message_meta = {"timing": {"started_at": 1_000}}
    assert _stamp(message_meta, {"done": False}) is None
    assert _stamp(message_meta, {"content": "streaming"}) is None
    assert "completed_at" not in message_meta["timing"]


def test_malformed_timing_meta_is_ignored():
    """meta.timing present but not a dict (hand-edited config etc.) is safe."""
    message_meta = {"timing": "garbage"}
    assert _stamp(message_meta, {"done": True}) is None

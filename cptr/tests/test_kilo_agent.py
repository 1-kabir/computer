"""Tests for the Kilo CLI agent profile integration.

Kilo is an OpenCode fork: the driver is the existing OpenCode adapter with a
Kilo-specific config environment. These tests cover the profile schema, the
config-env branch (the only protocol divergence), and dispatch wiring.

Safety: CPTR_DATA_DIR guard — never touch the live data dir.
"""

import os

import pytest

_LIVE_PREFIX = os.path.expanduser("~/.local/share/cptr")
if os.path.abspath(os.environ.get("CPTR_DATA_DIR", _LIVE_PREFIX)) == _LIVE_PREFIX:
    pytest.exit("refusing: CPTR_DATA_DIR points at the live data dir", returncode=1)

from cptr.utils.agents.detection import _opencode_config_env  # noqa: E402
from cptr.utils.agents.models import normalize_agent_profile  # noqa: E402
from cptr.utils.agents.opencode import _config_env  # noqa: E402


def test_kilo_profile_normalizes():
    profile = normalize_agent_profile({"id": "kilo", "agent": "kilo"})
    assert profile["command"] == "kilo"
    assert profile["name"] == "Kilo"


def test_kilo_profile_accepts_server_fields():
    """server_url/server_password are valid for the opencode family."""
    profile = normalize_agent_profile(
        {
            "id": "kilo",
            "agent": "kilo",
            "server_url": "http://127.0.0.1:4096",
            "server_password": "pw",
        }
    )
    assert profile["server_url"] == "http://127.0.0.1:4096"
    assert profile["server_password"] == "pw"


def test_driver_config_env_sets_kilo_variable_for_kilo_profiles():
    env = _config_env({"agent": "kilo"}, {})
    assert env["OPENCODE_CONFIG_CONTENT"] == "{}"
    assert env["KILO_CONFIG_CONTENT"] == "{}"


def test_driver_config_env_unchanged_for_opencode_profiles():
    env = _config_env({"agent": "opencode"}, {})
    assert env["OPENCODE_CONFIG_CONTENT"] == "{}"
    assert "KILO_CONFIG_CONTENT" not in env


def test_detection_config_env_sets_kilo_variable_for_kilo_profiles():
    env = _opencode_config_env("kilo", {"agent": "kilo"}, {})
    assert env["KILO_CONFIG_CONTENT"] == "{}"
    env_open = _opencode_config_env("opencode", {"agent": "opencode"}, {})
    assert "KILO_CONFIG_CONTENT" not in env_open


def test_dispatch_includes_kilo():
    import inspect

    from cptr.utils import chat_task

    source = inspect.getsource(chat_task.run_chat_task)
    assert '"kilo": run_opencode_agent' in source


def test_frontend_union_includes_kilo():
    from pathlib import Path

    admin_ts = Path(__file__).resolve().parents[2] / "cptr/frontend/src/lib/apis/admin.ts"
    assert "'kilo'" in admin_ts.read_text()

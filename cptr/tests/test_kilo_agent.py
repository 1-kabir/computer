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

from cptr.utils.agents.detection import _opencode_auth_headers, _opencode_config_env
from cptr.utils.agents.models import normalize_agent_profile
from cptr.utils.agents.opencode import _auth_username, _config_env, _headers


def test_kilo_profile_normalizes():
    profile = normalize_agent_profile({"id": "kilo", "agent": "kilo"})
    assert profile["command"] == "kilo"
    assert profile["name"] == "Kilo"


def test_kilo_auth_username_is_kilo_not_opencode():
    """H1 regression: Kilo's server defaults KILO_SERVER_USERNAME to 'kilo'
    (explicit kilocode_change from upstream); authenticating as 'opencode'
    always 401s."""
    assert _auth_username({"agent": "kilo"}) == "kilo"
    assert _auth_username({"agent": "opencode"}) == "opencode"


def test_kilo_basic_auth_header_uses_spawn_password_over_profile_password():
    """M1 regression: a spawned server must auth with the fresh per-spawn
    credential, and the header must use the Kilo username."""
    import base64

    headers = _headers({"agent": "kilo", "server_password": "profile-pw"}, "spawn-pw")
    decoded = base64.b64decode(headers["Authorization"].removeprefix("Basic ")).decode()
    assert decoded == "kilo:spawn-pw"


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


def test_detection_auth_headers_use_kilo_username_for_kilo():
    headers = _opencode_auth_headers({"agent": "kilo"}, "pw")
    assert "Basic" in headers["Authorization"]
    import base64

    decoded = base64.b64decode(headers["Authorization"].removeprefix("Basic ")).decode()
    assert decoded.startswith("kilo:")


def test_dispatch_includes_kilo():
    import inspect

    from cptr.utils import chat_task

    source = inspect.getsource(chat_task.run_chat_task)
    assert '"kilo": run_opencode_agent' in source


def test_frontend_union_includes_kilo():
    from pathlib import Path

    admin_ts = Path(__file__).resolve().parents[2] / "cptr/frontend/src/lib/apis/admin.ts"
    assert "'kilo'" in admin_ts.read_text()

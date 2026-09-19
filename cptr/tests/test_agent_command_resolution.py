"""Tests for agent command resolution outside the service PATH.

The cptr service runs with a fixed systemd PATH that excludes common
per-user install locations (~/.local/bin, nvm/fnm global bins). Bare
command names for CLIs installed there must still resolve.
"""

import asyncio
import os
import stat

import pytest

from cptr.utils.agents.detection import (
    AgentDetection,
    _effective_spawn_command,
    _npm_global_bin_dirs,
    _resolve_command,
    get_agent_status,
    get_available_agent_model_entries,
)


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """An empty dir standing in for a per-user bin dir + isolated HOME."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return bin_dir


def _make_executable(path):
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class TestResolveCommandFallback:
    def test_bare_name_in_extra_bin_dir(self, tmp_path, monkeypatch, fake_bin):
        _make_executable(fake_bin / "agy")
        monkeypatch.setattr("cptr.utils.agents.detection._EXTRA_BIN_DIRS", (str(fake_bin),))
        monkeypatch.setattr("cptr.utils.agents.detection._npm_global_bin_dirs", list)
        assert _resolve_command("agy") == str(fake_bin / "agy")

    def test_bare_name_in_npm_global_bin(self, tmp_path, monkeypatch, fake_bin):
        nvm_bin = tmp_path / "nvm" / "v99" / "bin"
        nvm_bin.mkdir(parents=True)
        _make_executable(nvm_bin / "cmd")
        monkeypatch.setattr("cptr.utils.agents.detection._EXTRA_BIN_DIRS", (str(fake_bin),))
        monkeypatch.setattr(
            "cptr.utils.agents.detection._npm_global_bin_dirs",
            lambda: [str(nvm_bin)],
        )
        assert _resolve_command("cmd") == str(nvm_bin / "cmd")

    def test_path_still_wins_over_fallbacks(self, tmp_path, monkeypatch, fake_bin):
        path_dir = tmp_path / "onpath"
        path_dir.mkdir()
        _make_executable(path_dir / "tool")
        _make_executable(fake_bin / "tool")
        monkeypatch.setenv("PATH", str(path_dir))
        monkeypatch.setattr("cptr.utils.agents.detection._EXTRA_BIN_DIRS", (str(fake_bin),))
        monkeypatch.setattr("cptr.utils.agents.detection._npm_global_bin_dirs", list)
        assert _resolve_command("tool") == str(path_dir / "tool")

    def test_non_executable_file_is_skipped(self, tmp_path, monkeypatch, fake_bin):
        (fake_bin / "tool").write_text("not executable")
        monkeypatch.setattr("cptr.utils.agents.detection._EXTRA_BIN_DIRS", (str(fake_bin),))
        monkeypatch.setattr("cptr.utils.agents.detection._npm_global_bin_dirs", list)
        assert _resolve_command("tool") is None

    def test_unknown_command_still_none(self, monkeypatch, fake_bin):
        monkeypatch.setattr("cptr.utils.agents.detection._EXTRA_BIN_DIRS", (str(fake_bin),))
        monkeypatch.setattr("cptr.utils.agents.detection._npm_global_bin_dirs", list)
        monkeypatch.setenv("PATH", str(fake_bin))
        assert _resolve_command("definitely-not-a-real-cli-xyz") is None

    def test_absolute_path_unchanged(self, tmp_path):
        exe = _make_executable(tmp_path / "absolute-cli")
        assert _resolve_command(str(exe)) == str(exe)

    def test_absolute_path_missing_returns_none(self, tmp_path):
        assert _resolve_command(str(tmp_path / "nope" / "missing-cli")) is None

    def test_empty_command_returns_none(self):
        assert _resolve_command("") is None
        assert _resolve_command("   ") is None


class TestNpmGlobalBinDirs:
    def test_discovers_nvm_newest_first(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".nvm" / "versions" / "node" / "v18.0.0" / "bin").mkdir(parents=True)
        (home / ".nvm" / "versions" / "node" / "v26.7.0" / "bin").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        dirs = _npm_global_bin_dirs()
        assert dirs[0].endswith("v26.7.0/bin")
        assert dirs[1].endswith("v18.0.0/bin")

    def test_discovers_volta(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".volta" / "bin").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        dirs = _npm_global_bin_dirs()
        assert any(d.endswith(".volta/bin") for d in dirs)

    def test_discovers_fnm(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".fnm" / "node-versions" / "v22.0.0" / "installation" / "bin").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        dirs = _npm_global_bin_dirs()
        assert any("node-versions/v22.0.0/installation/bin" in d for d in dirs)

    def test_no_managers_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
        (tmp_path / "empty-home").mkdir()
        assert _npm_global_bin_dirs() == []


class TestAvailableAgentModelEntries:
    """Ready profiles must always be selectable in the model picker.

    Adapters that resolve models at run time (antigravity, command_code)
    probe none during detection; without a fallback entry their profiles
    were ready in admin yet absent from the picker.
    """

    @staticmethod
    def _patch_status(monkeypatch, profiles):
        async def fake_status(app_state=None, refresh=False):
            return {"profiles": profiles}

        monkeypatch.setattr("cptr.utils.agents.detection.get_agent_status", fake_status)

    def test_modelless_ready_profile_gets_default_entry(self, monkeypatch):
        self._patch_status(
            monkeypatch,
            [
                {
                    "id": "antigravity",
                    "agent": "antigravity",
                    "name": "Antigravity",
                    "available": True,
                    "config": {
                        "id": "antigravity",
                        "agent": "antigravity",
                        "models": [],
                    },
                }
            ],
        )
        entries = asyncio.run(get_available_agent_model_entries())
        assert len(entries) == 1
        assert entries[0]["id"] == "agent:antigravity/default"

    def test_configured_models_listed_without_default(self, monkeypatch):
        self._patch_status(
            monkeypatch,
            [
                {
                    "id": "cmd",
                    "agent": "command_code",
                    "name": "Command Code",
                    "available": True,
                    "config": {
                        "id": "cmd",
                        "agent": "command_code",
                        "models": ["model-x", "model-y"],
                    },
                }
            ],
        )
        entries = asyncio.run(get_available_agent_model_entries())
        assert [e["id"] for e in entries] == [
            "agent:cmd/model-x",
            "agent:cmd/model-y",
        ]

    def test_unavailable_profile_skipped(self, monkeypatch):
        self._patch_status(
            monkeypatch,
            [
                {
                    "id": "broken",
                    "agent": "antigravity",
                    "name": "Broken",
                    "available": False,
                    "config": {"id": "broken", "agent": "antigravity", "models": []},
                }
            ],
        )
        assert asyncio.run(get_available_agent_model_entries()) == []

    def test_probed_models_precede_fallback(self, monkeypatch):
        """Effective profile already merges probed + configured models; the
        fallback only fires when that merged list is genuinely empty."""
        self._patch_status(
            monkeypatch,
            [
                {
                    "id": "kilo",
                    "agent": "kilo",
                    "name": "Kilo",
                    "available": True,
                    "config": {
                        "id": "kilo",
                        "agent": "kilo",
                        "models": ["probed-model"],
                    },
                }
            ],
        )
        entries = asyncio.run(get_available_agent_model_entries())
        assert [e["id"] for e in entries] == ["agent:kilo/probed-model"]


class TestEffectiveSpawnCommand:
    """Regression: the resolved absolute command must be persisted into the
    effective profile. Adapters spawn profile["command"] verbatim against
    the fixed service PATH; if only detection resolves it, spawn fails with
    FileNotFoundError ([Errno 2]) while admin shows the profile as ready."""

    def test_resolved_path_persisted_when_it_differs_from_raw(self):
        detected = AgentDetection("ready", "/home/u/.nvm/versions/node/v9/bin/cmd")
        assert (
            _effective_spawn_command({"command": "cmd", "agent": "command_code"}, detected)
            == "/home/u/.nvm/versions/node/v9/bin/cmd"
        )

    def test_raw_command_kept_when_detection_found_nothing_new(self):
        # Absolute-path profiles resolve to themselves: keep as-is.
        detected = AgentDetection("ready", "/opt/bin/agy")
        assert (
            _effective_spawn_command({"command": "/opt/bin/agy", "agent": "antigravity"}, detected)
            == "/opt/bin/agy"
        )

    def test_raw_command_kept_when_detection_failed(self):
        detected = AgentDetection("not_found", None, None, "Command not found")
        assert (
            _effective_spawn_command({"command": "missing-cli", "agent": "grok"}, detected)
            == "missing-cli"
        )

    def test_claude_desktop_fallback_applied_when_detection_is_none(self, monkeypatch, tmp_path):
        desktop = tmp_path / "claude"
        desktop.write_text("#!/bin/sh\n")
        desktop.chmod(0o755)
        monkeypatch.setattr(
            "cptr.utils.agents.detection._find_claude_desktop_command",
            lambda: str(desktop),
        )
        detected = AgentDetection("not_found", None, None, "Command not found")
        assert _effective_spawn_command(
            {"command": "claude", "agent": "claude_code"}, detected
        ) == str(desktop)


class TestGetAgentStatusPersistsResolvedCommand:
    """End-to-end through get_agent_status: the effective profile handed to
    adapters must carry the resolved absolute path."""

    def _patch_profiles(self, monkeypatch, profiles):
        async def fake_raw_profiles():
            return profiles

        monkeypatch.setattr("cptr.utils.agents.detection.get_raw_agent_profiles", fake_raw_profiles)

    def test_effective_profile_command_is_absolute(self, monkeypatch, tmp_path):
        nvm_bin = tmp_path / "nvm" / "v9" / "bin"
        nvm_bin.mkdir(parents=True)
        exe = nvm_bin / "fakecli"
        exe.write_text("#!/bin/sh\necho fakecli version 1.0\n")
        exe.chmod(0o755)
        self._patch_profiles(
            monkeypatch,
            [
                {
                    "id": "fakecli",
                    "agent": "antigravity",
                    "name": "FakeCLI",
                    "mode": "auto",
                    "command": "fakecli",
                    "home": None,
                    "models": [],
                    "default_model": "",
                }
            ],
        )
        monkeypatch.setattr("cptr.utils.agents.detection._EXTRA_BIN_DIRS", (str(nvm_bin),))
        monkeypatch.setattr("cptr.utils.agents.detection._npm_global_bin_dirs", list)
        status = asyncio.run(get_agent_status(refresh=True))
        entry = next(p for p in status["profiles"] if p["id"] == "fakecli")
        assert entry["available"] is True
        # This is the assertion that failed in production: the effective
        # profile must NOT carry the bare name that spawn cannot resolve.
        assert entry["config"]["command"] == str(exe)
        assert os.path.isabs(entry["config"]["command"])

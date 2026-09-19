"""Tests for agent command resolution outside the service PATH.

The cptr service runs with a fixed systemd PATH that excludes common
per-user install locations (~/.local/bin, nvm/fnm global bins). Bare
command names for CLIs installed there must still resolve.
"""

import stat

import pytest

from cptr.utils.agents.detection import (
    _npm_global_bin_dirs,
    _resolve_command,
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

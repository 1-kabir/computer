"""Tests for config.toml serialization round-trips.

Regression tests for the config-corruption bug: save_config() wrote raw
newlines inside TOML basic strings (invalid TOML), which detonated on the
next parse as TOMLDecodeError and took the whole UI down. Also covers the
corrupt-file backup path in load_config().

Safety: these tests monkeypatch cptr.utils.config.CONFIG_FILE / DATA_DIR to
a tmp directory — they never touch the real config.toml or data dir.
"""

import logging

import pytest

from cptr.utils import config as config_mod


@pytest.fixture(autouse=True)
def _isolated_config_paths(tmp_path, monkeypatch):
    """Point the config module at a tmp config.toml (never the real one)."""
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(config_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "_config_cache", None)
    yield


def _reset_cache():
    config_mod._config_cache = None


def test_roundtrip_multiline_system_prompt_tomllib():
    """The actual regression: a multi-line gateway.system_prompt must survive
    save → parse (tomllib path) unchanged."""
    config = {
        "gateway": {
            "system_prompt": (
                "You are Computer (cptr).\n\nYou must never restart yourself.\n"
                'WHO AM I: "I am GLM-5.3-Flash."\n\tTabbed line, CRLF ends here.\r'
            ),
            "port": 8000,
        }
    }
    config_mod.save_config(config)
    _reset_cache()
    loaded = config_mod.load_config()

    # tomllib must be able to parse the file at all (3.11+); on 3.10 the
    # fallback parser handles it — either way the value must round-trip.
    assert loaded["gateway"]["system_prompt"] == config["gateway"]["system_prompt"]
    assert loaded["gateway"]["port"] == 8000


def test_roundtrip_all_control_chars_and_quotes():
    """Backslashes, quotes, newlines, tabs, CR — the full escaping matrix."""
    tricky = 'back\\slash "quoted" \nnewline\ttab\rcarriage'
    config_mod.save_config({"sec": {"key": tricky}})
    _reset_cache()
    loaded = config_mod.load_config()
    assert loaded["sec"]["key"] == tricky


def test_roundtrip_fallback_parser_matches():
    """The Python<3.11 fallback parser must unescape what save_config writes."""
    text = 'plain string\nwith "quotes" and\\backslash\n\ttab'
    config_mod.save_config({"sec": {"prompt": text}})
    raw = config_mod.CONFIG_FILE.read_text()
    # The written file must be single-line per value (valid basic string).
    for line in raw.splitlines():
        if line.strip().startswith('"prompt"'):
            assert line.count('"') >= 2  # still one logical line
    parsed = config_mod._parse_simple_toml(raw)
    assert parsed["sec"]["prompt"] == text


def test_roundtrip_dotted_keys_and_bools_unchanged():
    config = {
        "auth": {"signup_enabled": True},
        "gateway": {"system_prompt": "line one\nline two"},
    }
    config_mod.save_config(config)
    _reset_cache()
    loaded = config_mod.load_config()
    assert loaded["auth"]["signup_enabled"] is True
    assert loaded["gateway"]["system_prompt"] == "line one\nline two"


def test_corrupt_file_backed_up_and_defaults(tmp_path, caplog):
    """A corrupt config.toml (the exact incident shape: raw newline in a
    basic string) must be backed up, logged loudly, and not crash load."""
    corrupt = config_mod.CONFIG_FILE
    corrupt.write_text('"gateway.system_prompt" = "You are Computer (cptr)...\nYou are OI..."')
    _reset_cache()
    with caplog.at_level(logging.ERROR, logger="cptr.utils.config"):
        loaded = config_mod.load_config()

    assert loaded == {}
    backups = list(config_mod.CONFIG_FILE.parent.glob("config.toml.bak-*"))
    assert len(backups) == 1
    assert b"You are Computer" in backups[0].read_bytes()
    assert any("backed up" in r.message for r in caplog.records)


def test_valid_file_not_backed_up(tmp_path):
    """Sanity: a healthy file loads normally and produces no backups."""
    config_mod.save_config({"server": {"host": "127.0.0.1"}})
    _reset_cache()
    loaded = config_mod.load_config()
    assert loaded["server"]["host"] == "127.0.0.1"
    assert not list(config_mod.CONFIG_FILE.parent.glob("config.toml.bak-*"))


def test_sync_config_to_toml_preserves_multiline_prompt():
    """End-to-end through the sync path the UI actually uses."""
    config_mod.save_config({"server": {"port": 8000}})
    _reset_cache()
    config_mod.sync_config_to_toml({"gateway.system_prompt": "para one\n\npara two"})
    _reset_cache()
    loaded = config_mod.load_config()
    assert loaded["app_config"]["gateway.system_prompt"] == "para one\n\npara two"
    assert loaded["server"]["port"] == 8000

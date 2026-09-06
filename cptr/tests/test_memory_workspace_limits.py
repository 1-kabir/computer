"""Tests for per-workspace memory char limit overrides."""

import asyncio
import os
import tempfile
from pathlib import Path

# Isolate data dir before any cptr import (env is read at import time).
_tmp_data_dir = tempfile.mkdtemp(prefix="cptr-test-memory-")
if "CPTR_DATA_DIR" not in os.environ:
    os.environ["CPTR_DATA_DIR"] = _tmp_data_dir
elif not os.environ["CPTR_DATA_DIR"].startswith(tempfile.gettempdir()):
    # Inherited a real CPTR_DATA_DIR (e.g. from the cptr service environment):
    # these tests create/drop tables; fail loudly rather than touch live data.
    raise SystemExit(
        f"refusing to run: CPTR_DATA_DIR={os.environ['CPTR_DATA_DIR']!r} was inherited "
        "from the environment and does not point at a temp dir. Run with an isolated "
        "data dir, not the live one."
    )

import pytest

from cptr.models.base import Base
from cptr.models.config import Config
from cptr.utils import memory as memory_mod
from cptr.utils.db import get_engine

WORKSPACE_A = str(Path(tempfile.mkdtemp(prefix="cptr-ws-a-")).resolve())
WORKSPACE_B = str(Path(tempfile.mkdtemp(prefix="cptr-ws-b-")).resolve())


@pytest.fixture(autouse=True)
def _config_table():
    async def _setup():
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[Config.__table__])
        await _clear_memory_config()

    asyncio.run(_setup())
    yield
    asyncio.run(_clear_memory_config())


async def _clear_memory_config():
    """Reset all memory.* config keys so tests stay isolated."""
    from sqlalchemy import delete

    from cptr.utils.db import get_db

    async with await get_db() as db:
        await db.execute(delete(Config).where(Config.key.like("memory.%")))
        await db.commit()


def test_global_default_without_override():
    async def scenario():
        settings = await memory_mod.get_memory_settings(WORKSPACE_A)
        assert settings["workspace_char_limit"] == 3000
        assert settings["user_char_limit"] == 2000

    asyncio.run(scenario())


def test_override_applies_to_matching_workspace_only():
    async def scenario():
        await memory_mod.save_memory_settings({"workspace_char_limit": 8000}, workspace=WORKSPACE_A)

        a = await memory_mod.get_memory_settings(WORKSPACE_A)
        b = await memory_mod.get_memory_settings(WORKSPACE_B)
        none_ws = await memory_mod.get_memory_settings("")

        assert a["workspace_char_limit"] == 8000
        assert b["workspace_char_limit"] == 3000
        assert none_ws["workspace_char_limit"] == 3000
        # Global value untouched by the workspace-scoped save.
        assert await Config.get("memory.workspace_char_limit") is None

    asyncio.run(scenario())


def test_override_below_floor_is_clamped():
    async def scenario():
        await memory_mod.save_memory_settings({"workspace_char_limit": 10}, workspace=WORKSPACE_A)
        settings = await memory_mod.get_memory_settings(WORKSPACE_A)
        assert settings["workspace_char_limit"] == 250

    asyncio.run(scenario())


def test_invalid_override_value_is_ignored():
    async def scenario():
        key = memory_mod.normalize_workspace_path(WORKSPACE_A)
        await Config.upsert({memory_mod.WORKSPACE_LIMIT_OVERRIDE_KEY: {key: "not-a-number"}})
        settings = await memory_mod.get_memory_settings(WORKSPACE_A)
        assert settings["workspace_char_limit"] == 3000

        await Config.upsert({memory_mod.WORKSPACE_LIMIT_OVERRIDE_KEY: {key: None}})
        settings = await memory_mod.get_memory_settings(WORKSPACE_A)
        assert settings["workspace_char_limit"] == 3000

    asyncio.run(scenario())


def test_clearing_override_restores_global():
    async def scenario():
        await memory_mod.save_memory_settings({"workspace_char_limit": 8000}, workspace=WORKSPACE_A)
        await memory_mod.save_memory_settings({"workspace_char_limit": None}, workspace=WORKSPACE_A)
        settings = await memory_mod.get_memory_settings(WORKSPACE_A)
        assert settings["workspace_char_limit"] == 3000
        assert await Config.get(memory_mod.WORKSPACE_LIMIT_OVERRIDE_KEY) is None

    asyncio.run(scenario())


def test_workspace_save_does_not_leak_into_other_keys():
    async def scenario():
        await memory_mod.save_memory_settings(
            {"workspace_char_limit": 8000, "user_char_limit": 4000}, workspace=WORKSPACE_A
        )
        assert await Config.get("memory.user_char_limit") == 4000
        user_settings = await memory_mod.get_memory_settings(WORKSPACE_A)
        assert user_settings["user_char_limit"] == 4000
        assert user_settings["workspace_char_limit"] == 8000

    asyncio.run(scenario())


def test_resolve_memory_file_uses_override():
    async def scenario():
        await memory_mod.save_memory_settings({"workspace_char_limit": 8000}, workspace=WORKSPACE_A)
        ws_file = await memory_mod.resolve_memory_file("user1", WORKSPACE_A, "workspace")
        user_file = await memory_mod.resolve_memory_file("user1", WORKSPACE_A, "user")
        assert ws_file.character_limit == 8000
        assert user_file.character_limit == 2000

    asyncio.run(scenario())


def test_apply_memory_batch_respects_override_limit():
    entry_big = "x" * 3100  # exceeds the 3000 global default, fits the 8000 override
    asyncio.run(
        memory_mod.save_memory_settings({"workspace_char_limit": 8000}, workspace=WORKSPACE_A)
    )
    ws_file = asyncio.run(memory_mod.resolve_memory_file("user1", WORKSPACE_A, "workspace"))

    ok, message, _entries, usage = memory_mod.apply_memory_batch(
        [], [{"action": "add", "content": entry_big}], ws_file.character_limit
    )
    assert ok
    assert usage == "3100/8000"

    # Same batch against the global default limit must be refused.
    global_file = asyncio.run(memory_mod.resolve_memory_file("user1", WORKSPACE_B, "workspace"))
    ok, message, _, usage = memory_mod.apply_memory_batch(
        [], [{"action": "add", "content": entry_big}], global_file.character_limit
    )
    assert not ok
    assert "3100" in message
    assert "3000" in message

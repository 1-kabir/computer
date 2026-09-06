"""Regression tests: renaming a chat must never make it count as unread.

The unread predicate is `updated_at > coalesce(last_read_at, 0)`.
update_title() used to bump updated_at=now_ms, flipping every renamed chat
to unread and lighting up workspace badges with no actual new messages.
"""

import asyncio
import os
import tempfile

# Isolate data dir before any cptr import (env is read at import time).
_tmp_data_dir = tempfile.mkdtemp(prefix="cptr-test-rename-")
if "CPTR_DATA_DIR" not in os.environ:
    os.environ["CPTR_DATA_DIR"] = _tmp_data_dir
elif not os.environ["CPTR_DATA_DIR"].startswith(tempfile.gettempdir()):
    # Inherited a real CPTR_DATA_DIR (e.g. from the cptr service environment):
    # drop_all in these tests would destroy live data. Fail loudly instead.
    raise SystemExit(
        f"refusing to run: CPTR_DATA_DIR={os.environ['CPTR_DATA_DIR']!r} was inherited "
        "from the environment and does not point at a temp dir. These tests DROP "
        "tables; run with an isolated data dir, not the live one."
    )

import pytest

from cptr.models.base import Base
from cptr.models.chats import Chat
from cptr.models.users import UserStates
from cptr.utils.db import get_db, get_engine

USER = "test-user"
WS = "/tmp/ws-rename-test"

CHAT_META = {"workspace": WS}


@pytest.fixture(autouse=True)
def _tables():
    asyncio.run(_create())
    yield
    asyncio.run(_drop())


async def _create():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[Chat.__table__, UserStates.__table__])


async def _drop():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=[Chat.__table__, UserStates.__table__])


async def _create_chat(chat_id: str, title: str, *, last_read_at=None):
    session = await get_db()
    async with session as db:
        db.add(
            Chat(
                id=chat_id,
                user_id=USER,
                title=title,
                meta=CHAT_META,
                updated_at=1_000,
                last_read_at=last_read_at,
                created_at=1_000,
            )
        )
        await db.commit()


async def _is_unread(chat_id: str) -> bool:
    from sqlalchemy import select

    session = await get_db()
    async with session as db:
        row = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one()
        return row.updated_at > (row.last_read_at or 0)


def test_rename_read_chat_stays_read():
    """Renaming an already-read chat must not flip it to unread."""

    async def scenario():
        await _create_chat("chat-read", "Old title", last_read_at=2_000)
        from sqlalchemy import select

        ok = await Chat.update_title("chat-read", "New title", 5_000)
        assert ok is True
        assert await _is_unread("chat-read") is False
        # Title did change:
        session = await get_db()
        async with session as db:
            row = (await db.execute(select(Chat).where(Chat.id == "chat-read"))).scalar_one()
            assert row.title == "New title"
            assert row.updated_at == 1_000  # untouched

    asyncio.run(scenario())


def test_rename_never_read_chat_gets_read_stamp():
    """A never-read chat (last_read_at NULL) gets title + read stamp, not unread."""

    async def scenario():
        await _create_chat("chat-null", "Old title", last_read_at=None)
        await Chat.update_title("chat-null", "New title", 5_000)
        assert await _is_unread("chat-null") is False

    asyncio.run(scenario())

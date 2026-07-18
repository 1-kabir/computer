"""Chat and ChatMessage models with data-access class methods."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Text,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import JSON

from cptr.models.base import Base
from cptr.utils.db import get_db


def _uuid() -> str:
    return str(uuid.uuid4())


def is_internal_chat(meta: dict | None) -> bool:
    """Whether a chat is harness-owned and should stay out of normal chat surfaces."""
    meta = meta or {}
    return bool(meta.get("internal") is True or meta.get("subagent") is True)


def internal_status(meta: dict | None) -> str | None:
    """Canonical internal lifecycle status, with legacy timer fallback."""
    meta = meta or {}
    return meta.get("status") or meta.get("timer_status")


def is_subagent_result_message(meta: dict | None) -> bool:
    """Whether a parent-chat message carries a subagent result."""
    meta = meta or {}
    return bool(
        (meta.get("internal") is True and meta.get("type") == "subagent")
        or meta.get("async_subagent_result")
        or meta.get("async_subagent_pending")
    )


def is_pending_subagent_result_message(meta: dict | None) -> bool:
    """Whether a subagent result is queued behind active parent work."""
    meta = meta or {}
    return bool(
        (
            meta.get("internal") is True
            and meta.get("type") == "subagent"
            and meta.get("status") == "pending"
        )
        or meta.get("async_subagent_pending")
    )


class Chat(Base):
    """A chat conversation. Workspace association lives in the filesystem."""

    __tablename__ = "chats"

    id = Column(Text, primary_key=True, default=_uuid)
    user_id = Column(Text, ForeignKey("users.id"), nullable=False)
    title = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    current_message_id = Column(Text, nullable=True)
    meta = Column(JSON, nullable=True)  # {model_id, connection_id, pinned, archived, ...}
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    last_read_at = Column(BigInteger, nullable=True)

    # ── Class methods ────────────────────────────────────────

    @staticmethod
    async def get_by_id(chat_id: str) -> Chat | None:
        async with await get_db() as db:
            result = await db.execute(select(Chat).where(Chat.id == chat_id))
            return result.scalar_one_or_none()

    @staticmethod
    async def get_by_ids(chat_ids: list[str]) -> list[Chat]:
        """Batch-fetch chats by a list of IDs."""
        if not chat_ids:
            return []
        async with await get_db() as db:
            result = await db.execute(select(Chat).where(Chat.id.in_(chat_ids)))
            return list(result.scalars().all())

    @staticmethod
    async def get_due_timers(now_ns: int, limit: int = 10) -> list[Chat]:
        """Return pending internal timer chats whose due time has arrived."""
        async with await get_db() as db:
            result = await db.execute(select(Chat).where(Chat.meta.is_not(None)))
            timers = [
                chat
                for chat in result.scalars().all()
                if (chat.meta or {}).get("internal") is True
                and (chat.meta or {}).get("type") == "timer"
                and internal_status(chat.meta) == "pending"
                and int((chat.meta or {}).get("timer_at") or 0) <= now_ns
            ]
            timers.sort(key=lambda chat: int((chat.meta or {}).get("timer_at") or 0))
            return timers[:limit]

    @staticmethod
    async def get_pending_timers(parent_chat_id: str) -> list[Chat]:
        """Return pending timer children for a parent chat."""
        async with await get_db() as db:
            result = await db.execute(select(Chat).where(Chat.meta.is_not(None)))
            return [
                chat
                for chat in result.scalars().all()
                if (chat.meta or {}).get("internal") is True
                and (chat.meta or {}).get("type") == "timer"
                and (chat.meta or {}).get("parent_chat_id") == parent_chat_id
                and internal_status(chat.meta) == "pending"
            ]

    @staticmethod
    async def get_timers(status: str | None = None) -> list[Chat]:
        """Return internal timer chats, optionally in one lifecycle state."""
        async with await get_db() as db:
            result = await db.execute(select(Chat).where(Chat.meta.is_not(None)))
            return [
                chat
                for chat in result.scalars().all()
                if (chat.meta or {}).get("internal") is True
                and (chat.meta or {}).get("type") == "timer"
                and (status is None or internal_status(chat.meta) == status)
            ]

    @staticmethod
    async def get_internal_descendants(parent_chat_id: str) -> list[Chat]:
        """Return every internal child below a chat, including nested timers."""
        async with await get_db() as db:
            result = await db.execute(select(Chat).where(Chat.meta.is_not(None)))
            candidates = list(result.scalars().all())
        children: list[Chat] = []
        parent_ids = {parent_chat_id}
        while parent_ids:
            found = [
                chat
                for chat in candidates
                if is_internal_chat(chat.meta)
                and (chat.meta or {}).get("parent_chat_id") in parent_ids
            ]
            if not found:
                break
            children.extend(found)
            parent_ids = {chat.id for chat in found}
            candidates = [chat for chat in candidates if chat not in found]
        return children

    @staticmethod
    async def create(
        user_id: str,
        title: str,
        meta: dict | None = None,
        created_at: int = 0,
    ) -> Chat:
        async with await get_db() as db:
            chat = Chat(
                user_id=user_id,
                title=title,
                meta=meta,
                created_at=created_at,
                updated_at=created_at,
                last_read_at=created_at,
            )
            db.add(chat)
            await db.commit()
            await db.refresh(chat)
            return chat

    @staticmethod
    async def update_title(chat_id: str, title: str, updated_at: int = 0) -> bool:
        async with await get_db() as db:
            result = await db.execute(
                update(Chat).where(Chat.id == chat_id).values(title=title, updated_at=updated_at)
            )
            await db.commit()
            return result.rowcount > 0

    @staticmethod
    async def update_summary(chat_id: str, summary: str, updated_at: int = 0) -> bool:
        async with await get_db() as db:
            result = await db.execute(
                update(Chat)
                .where(Chat.id == chat_id)
                .values(summary=summary, updated_at=updated_at)
            )
            await db.commit()
            return result.rowcount > 0

    @staticmethod
    async def update_meta(chat_id: str, meta: dict, updated_at: int = 0) -> bool:
        async with await get_db() as db:
            result = await db.execute(
                update(Chat).where(Chat.id == chat_id).values(meta=meta, updated_at=updated_at)
            )
            await db.commit()
            return result.rowcount > 0

    @staticmethod
    async def update_current_message(
        chat_id: str, message_id: str | None, updated_at: int = 0
    ) -> bool:
        async with await get_db() as db:
            result = await db.execute(
                update(Chat)
                .where(Chat.id == chat_id)
                .values(current_message_id=message_id, updated_at=updated_at)
            )
            await db.commit()
            return result.rowcount > 0

    @staticmethod
    async def touch(chat_id: str, updated_at: int) -> bool:
        """Record a completed background update without changing chat content."""
        async with await get_db() as db:
            result = await db.execute(
                update(Chat).where(Chat.id == chat_id).values(updated_at=updated_at)
            )
            await db.commit()
            return result.rowcount > 0

    @staticmethod
    async def unread_counts_by_workspace(
        user_id: str, workspace_paths: list[str], active_chat_ids: set[str]
    ) -> dict[str, int]:
        """Derive unread counts from chats; do not persist a second read-state."""
        paths = list(dict.fromkeys(path for path in workspace_paths if path))
        if not paths:
            return {}

        workspace = Chat.meta["workspace"].as_string()
        statement = (
            select(workspace, func.count(Chat.id))
            .where(
                Chat.user_id == user_id,
                workspace.in_(paths),
                Chat.meta["internal"].as_boolean().is_not(True),
                Chat.meta["subagent"].as_boolean().is_not(True),
                Chat.updated_at > func.coalesce(Chat.last_read_at, 0),
            )
            .group_by(workspace)
        )
        if active_chat_ids:
            statement = statement.where(Chat.id.not_in(active_chat_ids))

        async with await get_db() as db:
            result = await db.execute(statement)
            return {workspace: count for workspace, count in result.all() if workspace}

    @staticmethod
    async def update_last_read_at(chat_id: str, user_id: str, last_read_at: int) -> bool:
        """Mark a chat read without changing its activity timestamp."""
        async with await get_db() as db:
            result = await db.execute(
                update(Chat)
                .where(Chat.id == chat_id, Chat.user_id == user_id)
                .values(last_read_at=last_read_at)
            )
            await db.commit()
            return result.rowcount > 0

    @staticmethod
    async def delete(chat_id: str) -> bool:
        async with await get_db() as db:
            result = await db.execute(select(Chat).where(Chat.meta.is_not(None)))
            candidates = list(result.scalars().all())
            delete_ids = {chat_id}
            frontier = {chat_id}
            while frontier:
                frontier = {
                    chat.id
                    for chat in candidates
                    if is_internal_chat(chat.meta)
                    and (chat.meta or {}).get("parent_chat_id") in frontier
                }
                delete_ids.update(frontier)
            # Messages cascade via FK, but be explicit
            await db.execute(delete(ChatMessage).where(ChatMessage.chat_id.in_(delete_ids)))
            result = await db.execute(delete(Chat).where(Chat.id.in_(delete_ids)))
            await db.commit()
            return result.rowcount > 0

    @staticmethod
    async def search_by_text(
        user_id: str,
        query: str,
        limit: int = 10,
        offset: int = 0,
        workspace: str | None = None,
        include_subagents: bool = False,
    ) -> list[dict]:
        """Search chats by id, title, summary, and message content.

        Ranking favors exact/prefix id and title matches, then summary matches,
        then message content. This intentionally avoids schema changes or FTS
        dependencies; it is a better-ranked LIKE search over the current tables.
        """
        needle = (query or "").strip()
        if not needle or limit <= 0:
            return []
        terms = _search_terms(needle)

        async with await get_db() as db:
            pattern = f"%{needle}%"
            term_patterns = [f"%{term}%" for term in terms]
            metadata_clauses = [
                Chat.id.ilike(pattern),
                Chat.title.ilike(pattern),
                Chat.summary.ilike(pattern),
            ]
            message_clauses = [ChatMessage.content.ilike(pattern)]
            for term_pattern in term_patterns:
                metadata_clauses.extend(
                    (
                        Chat.id.ilike(term_pattern),
                        Chat.title.ilike(term_pattern),
                        Chat.summary.ilike(term_pattern),
                    )
                )
                message_clauses.append(ChatMessage.content.ilike(term_pattern))

            chat_rows = await db.execute(
                select(Chat).where(Chat.user_id == user_id).where(or_(*metadata_clauses))
            )
            chats = list(chat_rows.scalars().all())

            message_rows = await db.execute(
                select(Chat, ChatMessage)
                .join(ChatMessage, ChatMessage.chat_id == Chat.id)
                .where(Chat.user_id == user_id)
                .where(or_(*message_clauses))
                .order_by(Chat.updated_at.desc(), ChatMessage.created_at.desc())
                .limit(max(200, (limit + offset) * 50))
            )
            message_matches = list(message_rows.all())

        results: dict[str, dict] = {}

        def allowed(chat: Chat) -> bool:
            meta = chat.meta or {}
            if not include_subagents and is_internal_chat(meta):
                return False
            if workspace and meta.get("workspace") != workspace:
                return False
            return True

        def add_candidate(
            chat: Chat,
            match_type: str,
            rank: int,
            snippet_source: str | None = None,
            matched_message: ChatMessage | None = None,
        ) -> None:
            if not allowed(chat):
                return
            payload = {
                "id": chat.id,
                "title": chat.title,
                "summary": chat.summary,
                "meta": chat.meta,
                "updated_at": chat.updated_at,
                "created_at": chat.created_at,
                "match_type": match_type,
                "snippet": _extract_snippet(snippet_source, needle, terms)
                if snippet_source
                else None,
                "matched_message_id": matched_message.id if matched_message else None,
                "matched_role": matched_message.role if matched_message else None,
                "_rank": rank,
            }
            existing = results.get(chat.id)
            if not existing or (rank, -chat.updated_at) < (
                existing["_rank"],
                -existing["updated_at"],
            ):
                results[chat.id] = payload

        for chat in chats:
            rank, match_type, snippet_source = _rank_chat_match(chat, needle, terms)
            add_candidate(chat, match_type, rank, snippet_source)

        for chat, message in message_matches:
            phrase_or_terms_rank = 60 if needle.casefold() in message.content.casefold() else 70
            coverage = _term_coverage(message.content, terms)
            add_candidate(
                chat,
                "message",
                phrase_or_terms_rank - min(coverage, 5),
                message.content,
                matched_message=message,
            )

        ordered = sorted(
            results.values(), key=lambda r: (r["_rank"], -r["updated_at"], r["title"].lower())
        )
        page = ordered[offset : offset + limit]
        for row in page:
            row.pop("_rank", None)
        return page


_SEARCH_STOPWORDS = {
    "a",
    "am",
    "an",
    "and",
    "are",
    "do",
    "does",
    "how",
    "i",
    "is",
    "me",
    "my",
    "of",
    "or",
    "the",
    "to",
    "was",
    "what",
    "when",
}


def _search_terms(query: str) -> list[str]:
    """Return useful term fallbacks for non-phrase recall queries."""
    terms: list[str] = []
    for token in re.findall(r"[\w'-]+", query.casefold()):
        token = token.strip("-'")
        if len(token) < 2 or token in _SEARCH_STOPWORDS:
            continue
        if token not in terms:
            terms.append(token)
    return terms[:8]


def _term_coverage(content: str | None, terms: list[str]) -> int:
    if not content or not terms:
        return 0
    folded = content.casefold()
    return sum(1 for term in terms if term in folded)


def _rank_chat_match(chat: Chat, query: str, terms: list[str]) -> tuple[int, str, str | None]:
    """Return rank, match_type, and optional snippet source for chat metadata."""
    q = query.casefold()
    values = {
        "id": chat.id or "",
        "title": chat.title or "",
        "summary": chat.summary or "",
    }
    folded = {key: value.casefold() for key, value in values.items()}

    if folded["id"] == q:
        return (0, "id", f"Chat ID: {values['id']}")
    if folded["title"] == q:
        return (1, "title", None)
    if folded["id"].startswith(q):
        return (2, "id", f"Chat ID: {values['id']}")
    if folded["title"].startswith(q):
        return (3, "title", None)
    if q in folded["id"]:
        return (10, "id", f"Chat ID: {values['id']}")
    if q in folded["title"]:
        return (11, "title", None)
    if q in folded["summary"]:
        return (40, "summary", values["summary"])

    best: tuple[int, str, str | None] | None = None
    for field, value in folded.items():
        coverage = _term_coverage(value, terms)
        if coverage <= 0:
            continue
        if field == "id":
            candidate = (20 - min(coverage, 5), "id", f"Chat ID: {values['id']}")
        elif field == "title":
            candidate = (30 - min(coverage, 5), "title", None)
        else:
            candidate = (50 - min(coverage, 5), "summary", values["summary"])
        if best is None or candidate[0] < best[0]:
            best = candidate
    return best or (90, "summary", values["summary"])


def _extract_snippet(
    content: str | None, query: str, terms: list[str] | None = None, context_chars: int = 96
) -> str | None:
    """Extract a short snippet around the first match in message content."""
    if not content:
        return None
    query = (query or "").strip()
    if not query:
        return content[: context_chars * 2] + ("..." if len(content) > context_chars * 2 else "")

    lower = content.casefold()
    idx = lower.find(query.casefold())
    if idx == -1:
        for token in terms or query.split():
            idx = lower.find(token.casefold())
            if idx != -1:
                query = token
                break
    if idx == -1:
        return content[: context_chars * 2] + ("..." if len(content) > context_chars * 2 else "")
    start = max(0, idx - context_chars)
    end = min(len(content), idx + len(query) + context_chars)
    snippet = content[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."
    return snippet


class ChatMessage(Base):
    """A single message in a chat conversation."""

    __tablename__ = "chat_messages"

    id = Column(Text, primary_key=True, default=_uuid)
    chat_id = Column(Text, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id = Column(Text, nullable=True)
    role = Column(Text, nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)  # Flattened text content
    model = Column(Text, nullable=True)  # Model used (assistant only)
    done = Column(Boolean, default=True)  # false = streaming or pending approval
    output = Column(JSON, nullable=True)  # Responses API output items
    usage = Column(JSON, nullable=True)  # {input_tokens, output_tokens, ...}
    meta = Column(JSON, nullable=True)  # {files, followups, error, ...}
    chat_summary = Column(
        Text, nullable=True
    )  # Compaction summary (covers all ancestors before this msg)
    created_at = Column(BigInteger, nullable=False)

    # ── Class methods ────────────────────────────────────────

    @staticmethod
    async def get_by_id(message_id: str) -> ChatMessage | None:
        async with await get_db() as db:
            result = await db.execute(select(ChatMessage).where(ChatMessage.id == message_id))
            return result.scalar_one_or_none()

    @staticmethod
    async def get_all_by_chat(chat_id: str) -> list[ChatMessage]:
        """Get all messages for a chat, ordered by creation time."""
        async with await get_db() as db:
            result = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.chat_id == chat_id)
                .order_by(ChatMessage.created_at)
            )
            return list(result.scalars().all())

    @staticmethod
    async def get_children(parent_id: str) -> list[ChatMessage]:
        """Get child messages (for branching conversations)."""
        async with await get_db() as db:
            result = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.parent_id == parent_id)
                .order_by(ChatMessage.created_at)
            )
            return list(result.scalars().all())

    @staticmethod
    async def create(
        chat_id: str,
        role: str,
        content: str,
        parent_id: str | None = None,
        model: object = None,
        done: bool = True,
        output: dict | list | None = None,
        usage: dict | None = None,
        meta: dict | None = None,
        created_at: int = 0,
    ) -> ChatMessage:
        if isinstance(model, (list, tuple)):
            model = next((item.strip() for item in model if isinstance(item, str) and item.strip()), None)
        elif model is not None and not isinstance(model, str):
            model = str(model)

        async with await get_db() as db:
            msg = ChatMessage(
                chat_id=chat_id,
                parent_id=parent_id,
                role=role,
                content=content,
                model=model,
                done=done,
                output=output,
                usage=usage,
                meta=meta,
                created_at=created_at,
            )
            db.add(msg)
            await db.commit()
            await db.refresh(msg)
            return msg

    @staticmethod
    async def update(message_id: str, **kwargs) -> bool:
        """Update arbitrary fields on a message."""
        if not kwargs:
            return False
        async with await get_db() as db:
            result = await db.execute(
                update(ChatMessage).where(ChatMessage.id == message_id).values(**kwargs)
            )
            await db.commit()
            return result.rowcount > 0

    @staticmethod
    async def delete(message_id: str) -> bool:
        async with await get_db() as db:
            result = await db.execute(delete(ChatMessage).where(ChatMessage.id == message_id))
            await db.commit()
            return result.rowcount > 0

"""Messaging bridge: adapter base class, BotManager lifecycle, and message routing.

Integrates external messaging platforms (Telegram, Discord, etc.) as alternative
frontends to cptr.  Each platform adapter connects via its native protocol (long-
polling, WebSocket) and translates inbound messages into cptr chat tasks using the
same agentic loop as the web UI.

Bot configs are stored in the Config key-value store (same as connections).
Thread mappings (external chat → cptr chat) are stored in chat.meta.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)
_current_bot_manager: "BotManager | None" = None

# How often to edit the streaming message (seconds).
STREAM_EDIT_INTERVAL = 2.0

# Typing indicator refresh interval (seconds).
TYPING_INTERVAL = 5.0


# ── Adapter interface ────────────────────────────────────────


@dataclass
class Attachment:
    """A file attached to a message."""

    type: str  # "image", "audio", "document"
    filename: str  # Original filename or generated one
    data: bytes  # Raw file content
    mime_type: str  # e.g. "image/jpeg", "audio/ogg"


@dataclass
class MessageEvent:
    """Normalized inbound message from any platform."""

    platform: str  # "telegram", "discord", etc.
    chat_id: str  # Platform's chat/channel ID
    sender_id: str  # Platform's user ID
    sender_name: str  # Display name
    text: str  # Message content
    attachments: list[Attachment] = field(default_factory=list)


class BaseAdapter(ABC):
    """Abstract base for messaging platform adapters.

    Subclasses implement the required methods.  The adapter does NOT
    interpret message content — it only shuttles text between the platform
    and the bridge core.
    """

    platform: str = "unknown"

    # Set by BotManager when the adapter is started.
    on_message: Optional[Callable[[MessageEvent], Awaitable[None]]] = None

    @abstractmethod
    async def connect(self) -> bool:
        """Start listening for messages.  Return True on success."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Stop listening and release resources."""
        ...

    @abstractmethod
    async def send(self, chat_id: str, text: str) -> str | None:
        """Send a text message.  Returns the platform message ID (for editing)."""
        ...

    @abstractmethod
    async def edit(self, chat_id: str, message_id: str, text: str) -> None:
        """Edit a previously sent message."""
        ...

    @abstractmethod
    async def send_typing(self, chat_id: str) -> None:
        """Show a typing indicator in the chat."""
        ...

    async def send_photo(
        self, chat_id: str, data: bytes, filename: str, caption: str = ""
    ) -> str | None:
        """Send a photo. Default: falls back to send() with caption only."""
        if caption:
            return await self.send(chat_id, caption)
        return None

    async def send_document(
        self, chat_id: str, data: bytes, filename: str, caption: str = ""
    ) -> str | None:
        """Send a file. Default: falls back to send() with caption only."""
        if caption:
            return await self.send(chat_id, caption)
        return None


# ── Message chunking ─────────────────────────────────────────


def chunk_message(text: str, max_len: int = 4096) -> list[str]:
    """Split a long message at paragraph / sentence boundaries."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        cut = remaining.rfind("\n\n", 0, max_len)
        if cut > max_len // 4:
            chunks.append(remaining[:cut])
            remaining = remaining[cut + 2 :]
            continue

        cut = remaining.rfind("\n", 0, max_len)
        if cut > max_len // 4:
            chunks.append(remaining[:cut])
            remaining = remaining[cut + 1 :]
            continue

        for sep in (". ", "! ", "? "):
            cut = remaining.rfind(sep, 0, max_len)
            if cut > max_len // 4:
                chunks.append(remaining[: cut + 1])
                remaining = remaining[cut + 2 :]
                break
        else:
            chunks.append(remaining[:max_len])
            remaining = remaining[max_len:]

    return [c for c in chunks if c.strip()]


# ── Config-backed bot storage ────────────────────────────────


async def get_bot_configs() -> list[dict]:
    """Load all bot configs from the Config store."""
    from cptr.models import Config

    bots = await Config.get("bots")
    return bots if isinstance(bots, list) else []


async def save_bot_configs(bots: list[dict]) -> None:
    """Save all bot configs to the Config store."""
    from cptr.models import Config

    await Config.upsert({"bots": bots})


async def get_bot_by_id(bot_id: str) -> dict | None:
    """Find a single bot config by ID."""
    for bot in await get_bot_configs():
        if bot.get("id") == bot_id:
            return bot
    return None


async def upsert_bot(bot: dict) -> None:
    """Insert or update a single bot config."""
    bots = await get_bot_configs()
    for i, b in enumerate(bots):
        if b.get("id") == bot.get("id"):
            bots[i] = bot
            await save_bot_configs(bots)
            return
    bots.append(bot)
    await save_bot_configs(bots)


async def delete_bot_config(bot_id: str) -> bool:
    """Remove a bot config by ID."""
    bots = await get_bot_configs()
    filtered = [b for b in bots if b.get("id") != bot_id]
    if len(filtered) == len(bots):
        return False
    await save_bot_configs(filtered)
    return True


def get_current_bot_manager() -> "BotManager | None":
    return _current_bot_manager


# ── Thread mapping via chat.meta ─────────────────────────────


async def find_chat_for_thread(bot_id: str, external_thread_id: str) -> str | None:
    """Find the most recent cptr chat_id for a platform thread.

    Scans chats with matching bridge metadata and returns the newest one,
    so /new (which creates a new chat with the same thread ID) takes effect.
    """
    from cptr.models import Chat
    from cptr.utils.db import get_db
    from sqlalchemy import select

    best_id = None
    best_ts = -1

    async with await get_db() as db:
        result = await db.execute(select(Chat).where(Chat.user_id.isnot(None)))
        for chat in result.scalars():
            meta = chat.meta or {}
            if (
                meta.get("bridge_bot_id") == bot_id
                and meta.get("bridge_external_thread_id") == external_thread_id
            ):
                ts = getattr(chat, "created_at", 0) or 0
                if ts > best_ts:
                    best_ts = ts
                    best_id = chat.id
    return best_id


# ── BotManager ───────────────────────────────────────────────


class BotManager:
    """Manages messaging bot lifecycles.

    Singleton attached to ``app.state.bot_manager``.  Reads bot configs
    from the Config store on startup, creates adapters, and runs them as
    asyncio tasks.
    """

    def __init__(self, app=None) -> None:
        global _current_bot_manager
        self.app = app
        self._adapters: dict[str, BaseAdapter] = {}  # bot_id → adapter
        self._tasks: dict[str, asyncio.Task] = {}  # bot_id → polling task
        self._stream_tasks: dict[str, asyncio.Task] = {}  # key → streaming task
        _current_bot_manager = self

    # ── Lifecycle ──────────────────────────────────────────

    async def start_all(self) -> None:
        """Load all active bots from Config and start them."""
        bots = await get_bot_configs()
        started = 0
        for bot in bots:
            if not bot.get("is_active", True):
                continue
            try:
                await self.start_bot(bot)
                started += 1
            except Exception:
                logger.exception("Failed to start bot %s", bot.get("name", "?"))
        if started:
            logger.info("Bot manager started %d bot(s)", started)

    async def start_bot(self, bot: dict) -> None:
        """Start a single bot adapter."""
        bot_id = bot["id"]
        if bot_id in self._tasks:
            logger.warning("Bot %s already running", bot_id[:8])
            return

        adapter = self._create_adapter(bot)
        if adapter is None:
            logger.error("Unknown platform '%s' for bot %s", bot.get("platform"), bot_id[:8])
            return

        adapter.on_message = lambda event, _bot=bot: self._handle_message(event, _bot)
        self._adapters[bot_id] = adapter

        async def _run() -> None:
            try:
                ok = await adapter.connect()
                if not ok:
                    logger.error("Bot %s failed to connect", bot.get("name", "?"))
                    return
                logger.info("Bot %s connected on %s", bot.get("name"), bot.get("platform"))
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Bot %s crashed", bot_id[:8])
            finally:
                try:
                    await adapter.disconnect()
                except Exception:
                    logger.debug("Adapter disconnect error", exc_info=True)

        self._tasks[bot_id] = asyncio.create_task(_run())

    async def stop_bot(self, bot_id: str) -> None:
        task = self._tasks.pop(bot_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self._adapters.pop(bot_id, None)
        logger.info("Bot %s stopped", bot_id[:8])

    async def stop_all(self) -> None:
        for bot_id in list(self._tasks.keys()):
            await self.stop_bot(bot_id)

    def is_running(self, bot_id: str) -> bool:
        task = self._tasks.get(bot_id)
        return task is not None and not task.done()

    def get_status(self) -> dict[str, bool]:
        return {bid: not t.done() for bid, t in self._tasks.items()}

    async def send_notification(
        self,
        bot_id: str,
        destination_chat_id: str,
        text: str,
    ) -> str | None:
        adapter = self._adapters.get(bot_id)
        if not adapter or not self.is_running(bot_id):
            raise RuntimeError("bot is not running")
        return await adapter.send(destination_chat_id, text)

    # ── Adapter factory ────────────────────────────────────

    def _create_adapter(self, bot: dict) -> BaseAdapter | None:
        from cptr.utils.config import _get_jwt_secret
        from cptr.utils.crypto import decrypt_key

        token = decrypt_key(bot["token"], _get_jwt_secret())

        if bot["platform"] == "telegram":
            from cptr.utils.adapters.telegram import TelegramAdapter

            return TelegramAdapter(token=token)

        if bot["platform"] == "discord":
            from cptr.utils.adapters.discord import DiscordAdapter

            return DiscordAdapter(token=token)

        if bot["platform"] == "slack":
            from cptr.utils.adapters.slack import SlackAdapter

            return SlackAdapter(token=token)

        if bot["platform"] == "whatsapp":
            from cptr.utils.adapters.whatsapp import WhatsAppAdapter

            return WhatsAppAdapter(token=token, bot_id=bot["id"])

        if bot["platform"] == "signal":
            from cptr.utils.adapters.signal import SignalAdapter

            return SignalAdapter(token=token)

        return None

    # ── Message handling ───────────────────────────────────

    async def _handle_message(self, event: MessageEvent, bot: dict) -> None:
        """Core message handler: auth → commands → thread map → create chat → start_task → stream."""
        # 1. Auth check
        allowed = bot.get("allowed_senders") or []
        if allowed and event.sender_id not in allowed:
            logger.debug(
                "Ignoring unauthorized sender %s on bot %s", event.sender_id, bot["id"][:8]
            )
            return

        adapter = self._adapters.get(bot["id"])
        text = (event.text or "").strip()

        # Normalize: strip Discord bot mention prefix (<@123456>)
        # and Telegram command suffix (/cmd@BotName → /cmd)
        import re

        clean = re.sub(r"<@!?\d+>\s*", "", text).strip()
        cmd = clean.split("@")[0].lower() if clean.startswith("/") else ""

        # 2. Slash commands
        if cmd in ("/new", "/reset", "/start"):
            await self._create_chat(event, bot)
            if adapter:
                try:
                    await adapter.send(event.chat_id, "✨ New conversation started.")
                except Exception:
                    pass
            return

        if cmd == "/stop":
            chat_id = await find_chat_for_thread(bot["id"], event.chat_id)
            cancelled = False
            if chat_id:
                from cptr.utils.chat_task import _tasks, _task_chat, cancel_task

                for mid, cid in list(_task_chat.items()):
                    if cid == chat_id and mid in _tasks and not _tasks[mid].done():
                        await cancel_task(mid)
                        cancelled = True
                        break
            if adapter:
                try:
                    msg = "⏹️ Stopped." if cancelled else "Nothing running."
                    await adapter.send(event.chat_id, msg)
                except Exception:
                    pass
            return

        if cmd == "/retry":
            from cptr.models import ChatMessage

            chat_id = await find_chat_for_thread(bot["id"], event.chat_id)
            if not chat_id:
                if adapter:
                    try:
                        await adapter.send(
                            event.chat_id, "No active conversation. Send a message first."
                        )
                    except Exception:
                        pass
                return
            # Check if a task is already running
            from cptr.utils.chat_task import get_active_chat_ids

            if chat_id in get_active_chat_ids():
                if adapter:
                    try:
                        await adapter.send(
                            event.chat_id, "⏳ A task is already running. Use /stop first."
                        )
                    except Exception:
                        pass
                return
            # Find the last user message
            msgs = await ChatMessage.get_all_by_chat(chat_id)
            last_user = None
            for m in reversed(msgs):
                if m.role == "user":
                    last_user = m
                    break
            if not last_user:
                if adapter:
                    try:
                        await adapter.send(event.chat_id, "Nothing to retry.")
                    except Exception:
                        pass
                return
            # Re-dispatch with the same text as a new branch
            event.text = last_user.content
            await self._dispatch_task(chat_id, event, bot, adapter)
            return

        if cmd == "/model":
            parts = clean.split(None, 1)
            new_model = parts[1].strip() if len(parts) > 1 else ""
            if not new_model:
                if adapter:
                    try:
                        await adapter.send(event.chat_id, f"Current model: `{bot['model_id']}`")
                    except Exception:
                        pass
                return
            # Update bot config
            from cptr.models import Config

            bots_raw = await Config.get("bots") or []
            for b in bots_raw:
                if b.get("id") == bot["id"]:
                    b["model_id"] = new_model
                    break
            await Config.upsert({"bots": bots_raw})
            bot["model_id"] = new_model
            if adapter:
                try:
                    await adapter.send(event.chat_id, f"✅ Model switched to: `{new_model}`")
                except Exception:
                    pass
            return

        if cmd == "/help":
            help_text = (
                "/new — Start a new conversation\n"
                "/stop — Stop the running agent\n"
                "/retry — Retry the last message\n"
                "/model [id] — Show or switch model\n"
                "/workspace <name> — Switch workspace (starts new chat)\n"
                "/workspaces — List available workspaces\n"
                "/help — Show this message"
            )
            if adapter:
                try:
                    await adapter.send(event.chat_id, help_text)
                except Exception:
                    pass
            return

        if cmd == "/workspaces":
            from cptr.models import Workspace

            workspaces = await Workspace.get_by_user(bot["user_id"])
            if not workspaces:
                if adapter:
                    await adapter.send(event.chat_id, "No workspaces found.")
                return
            current = bot.get("workspace", "")
            lines = []
            for ws in workspaces:
                marker = " ←" if ws.path == current else ""
                lines.append(f"• {ws.name}{marker}")
            if adapter:
                try:
                    await adapter.send(event.chat_id, "\n".join(lines))
                except Exception:
                    pass
            return

        if cmd == "/workspace":
            # /workspace <name> — switch workspace
            parts = clean.split(None, 1)
            ws_name = parts[1].strip() if len(parts) > 1 else ""
            if not ws_name:
                if adapter:
                    await adapter.send(event.chat_id, "Usage: /workspace <name>")
                return

            from cptr.models import Workspace

            workspaces = await Workspace.get_by_user(bot["user_id"])
            match = None
            ws_lower = ws_name.lower()
            for ws in workspaces:
                if ws.name.lower() == ws_lower or ws.path.lower().endswith("/" + ws_lower):
                    match = ws
                    break
            # Fuzzy: partial match
            if not match:
                for ws in workspaces:
                    if ws_lower in ws.name.lower():
                        match = ws
                        break

            if not match:
                if adapter:
                    await adapter.send(
                        event.chat_id, f"Workspace '{ws_name}' not found. Use /workspaces to list."
                    )
                return

            # Update bot config with new workspace
            from cptr.models import Config

            bots_raw = await Config.get("bots") or []
            for b in bots_raw:
                if b.get("id") == bot["id"]:
                    b["workspace"] = match.path
                    break
            await Config.upsert({"bots": bots_raw})
            bot["workspace"] = match.path

            # Start a new chat in the new workspace
            await self._create_chat(event, bot)
            if adapter:
                try:
                    await adapter.send(
                        event.chat_id, f"✨ Switched to {match.name}\nNew conversation started."
                    )
                except Exception:
                    pass
            return

        if not clean and not event.attachments:
            return

        # 3. Thread mapping — find or create a cptr chat
        chat_id = await find_chat_for_thread(bot["id"], event.chat_id)

        if not chat_id:
            chat_id = await self._create_chat(event, bot)

        # Use the cleaned text (bot mention stripped) for the actual message
        event.text = clean

        # 4. Dispatch task with streaming
        await self._dispatch_task(chat_id, event, bot, adapter)

    async def _create_chat(self, event: MessageEvent, bot: dict) -> str:
        from cptr.models import Chat
        from cptr.utils.config import now_ms

        title = f"{event.sender_name} ({bot['platform']})"
        chat = await Chat.create(
            user_id=bot["user_id"],
            title=title[:100],
            meta={
                "workspace": bot["workspace"],
                "bridge_bot_id": bot["id"],
                "bridge_platform": bot["platform"],
                "bridge_external_thread_id": event.chat_id,
                "params": {"tool_approval_mode": "full"},
            },
            created_at=now_ms(),
        )

        marker = Path(bot["workspace"]) / ".cptr" / "chats" / f"{chat.id}.json"
        from cptr.utils.identity import internal_request_for_user
        from cptr.utils.runtime import Runtime

        request = await internal_request_for_user(self.app, bot["user_id"])
        await Runtime.write_file(request, str(marker), "{}")

        return chat.id

    async def _process_attachments(
        self,
        attachments: list[Attachment],
        adapter: BaseAdapter | None,
        platform_chat_id: str,
    ) -> tuple[list[dict], str]:
        """Process inbound attachments: save to storage, transcribe voice.

        Returns (file_entries_for_meta, text_to_prepend).
        file_entries match the web UI format so _load_message_history handles
        them automatically (base64 for images, file:// refs for documents).
        """
        from cptr.utils.storage import get_storage

        file_entries: list[dict] = []
        text_parts: list[str] = []

        for att in attachments:
            if att.type == "audio":
                # Voice message → transcribe via Whisper
                transcript = await self._transcribe_voice(att, adapter, platform_chat_id)
                if transcript:
                    text_parts.append(transcript)
                continue

            # Images and documents → save to blob storage
            file_id = str(uuid.uuid4())
            try:
                await get_storage().put(file_id, att.data)
            except Exception:
                logger.exception("[bridge] Failed to save attachment %s", att.filename)
                continue

            entry: dict = {
                "id": file_id,
                "name": att.filename,
                "content_type": att.mime_type,
            }
            if att.type == "image":
                entry["type"] = "image"
            else:
                entry["type"] = "file"

            file_entries.append(entry)
            logger.info(
                "[bridge] Saved attachment %s (%s, %d bytes)", att.filename, att.type, len(att.data)
            )

        return file_entries, "\n".join(text_parts)

    async def _transcribe_voice(
        self,
        att: Attachment,
        adapter: BaseAdapter | None,
        platform_chat_id: str,
    ) -> str:
        """Transcribe a voice attachment using the configured STT API.

        Returns the transcript text, or empty string on failure/not configured.
        """
        from cptr.models import Config
        from cptr.utils.config import _get_jwt_secret
        from cptr.utils.crypto import decrypt_key

        api_key_encrypted = await Config.get("audio.stt_api_key")
        if not api_key_encrypted:
            # STT not configured — warn the user
            if adapter:
                try:
                    await adapter.send(
                        platform_chat_id,
                        "⚠️ Voice messages require speech-to-text to be configured in Settings → Audio.",
                    )
                except Exception:
                    pass
            return ""

        api_key = decrypt_key(api_key_encrypted, _get_jwt_secret())
        base_url = (await Config.get("audio.stt_base_url")) or "https://api.openai.com/v1"
        model = (await Config.get("audio.stt_model")) or "whisper-1"

        try:
            from cptr.routers.audio import _transcribe_chunk

            transcript = await _transcribe_chunk(
                data=att.data,
                filename=att.filename,
                content_type=att.mime_type,
                base_url=base_url,
                api_key=api_key,
                model=model,
            )
            if transcript:
                logger.info("[bridge] Transcribed voice message: %d chars", len(transcript))
            return transcript
        except Exception:
            logger.exception("[bridge] Voice transcription failed")
            if adapter:
                try:
                    await adapter.send(platform_chat_id, "⚠️ Failed to transcribe voice message.")
                except Exception:
                    pass
            return ""

    async def _dispatch_task(
        self,
        chat_id: str,
        event: MessageEvent,
        bot: dict,
        adapter: BaseAdapter | None,
    ) -> None:
        from cptr.models import Chat, ChatMessage
        from cptr.utils.chat_task import start_task, _task_chat
        from cptr.utils.config import now_ms

        # ── Process attachments ──
        user_meta: dict | None = None
        if event.attachments:
            file_entries, text_prepend = await self._process_attachments(
                event.attachments,
                adapter,
                event.chat_id,
            )
            if file_entries:
                user_meta = {"files": file_entries}
            if text_prepend:
                event.text = f"{text_prepend}\n{event.text}".strip() if event.text else text_prepend

        # Get current chat state for parent_id threading
        chat = await Chat.get_by_id(chat_id)
        parent_id = chat.current_message_id if chat else None

        # Don't dispatch if a task is already running for this chat
        active_chat_ids = set(_task_chat.values())
        if chat_id in active_chat_ids:
            await ChatMessage.create(
                chat_id=chat_id,
                role="user",
                content=event.text,
                parent_id=parent_id,
                meta={"queued": True, **(user_meta or {})},
                created_at=now_ms(),
            )
            logger.info("[bridge] Queued message for busy chat %s", chat_id[:8])
            return

        # Create user message — parent_id chains to previous assistant reply
        user_msg = await ChatMessage.create(
            chat_id=chat_id,
            role="user",
            content=event.text,
            parent_id=parent_id,
            meta=user_meta,
            created_at=now_ms(),
        )

        # Resolve model target
        try:
            from cptr.utils.model_targets import resolve_model_target

            target = await resolve_model_target(bot["model_id"])
        except Exception:
            logger.exception("[bridge] Failed to resolve model %s", bot["model_id"])
            if adapter:
                await adapter.send(event.chat_id, "⚠️ Model connection error. Check settings.")
            return

        # Create assistant placeholder
        assistant_msg = await ChatMessage.create(
            chat_id=chat_id,
            role="assistant",
            content="",
            parent_id=user_msg.id,
            model=bot["model_id"],
            done=False,
            created_at=now_ms(),
        )
        await Chat.update_current_message(chat_id, assistant_msg.id, now_ms())

        from cptr.utils.chat_export import export_chat_to_file
        from cptr.utils.identity import internal_request_for_user

        request = await internal_request_for_user(self.app, bot["user_id"])
        await export_chat_to_file(request, chat_id)

        # Send initial "thinking" message for non-draft platforms (Discord)
        # Telegram uses sendMessageDraft which handles this natively
        platform_msg_id = None
        if adapter and bot["platform"] != "telegram":
            try:
                platform_msg_id = await adapter.send(event.chat_id, "⏳ Thinking...")
            except Exception:
                logger.debug("[bridge] Failed to send initial message", exc_info=True)

        # Start the streaming edit loop
        stream_key = f"{bot['id']}:{event.chat_id}"
        self._stream_tasks[stream_key] = asyncio.create_task(
            self._stream_loop(
                adapter,
                event.chat_id,
                platform_msg_id,
                assistant_msg.id,
                bot,
            )
        )

        # Start the agentic loop
        start_task(
            request,
            message_id=assistant_msg.id,
            chat_id=chat_id,
            user_id=bot["user_id"],
            workspace=bot["workspace"],
            target=target,
        )
        logger.info("[bridge] Started task for bot %s, chat %s", bot["id"][:8], chat_id[:8])

    async def _stream_loop(
        self,
        adapter: BaseAdapter | None,
        platform_chat_id: str,
        platform_msg_id: str | None,
        task_message_id: str,
        bot: dict,
    ) -> None:
        """Stream task output to the platform in real time.

        Telegram: uses sendMessageDraft for smooth native streaming, then
        sendMessage to persist the final response (drafts are ephemeral).

        Discord: uses edit-based streaming (PATCH /messages).
        """
        from cptr.utils.chat_task import _task_state, _tasks

        if not adapter:
            return

        max_len = 32_768 if bot["platform"] == "telegram" else 2000
        last_sent = ""
        is_telegram = bot["platform"] == "telegram"

        # Telegram: use draft-based streaming
        draft_id: int | None = None
        use_draft = is_telegram
        if use_draft:
            try:
                from cptr.utils.adapters.telegram import TelegramAdapter

                if isinstance(adapter, TelegramAdapter):
                    # Start with empty draft → shows "Thinking..." placeholder
                    draft_id = await adapter.send_draft(platform_chat_id, "", None)
                else:
                    use_draft = False
            except Exception:
                use_draft = False
                logger.debug("[bridge] Draft not supported, falling back to edit", exc_info=True)

        # Fallback: if draft failed or non-Telegram, ensure we have a message to edit
        if not use_draft and not platform_msg_id:
            try:
                platform_msg_id = await adapter.send(platform_chat_id, "⏳ Thinking...")
            except Exception:
                logger.debug("[bridge] Failed to send fallback message", exc_info=True)

        try:
            while True:
                task = _tasks.get(task_message_id)
                task_done = task is None or task.done()

                state = _task_state.get(task_message_id)
                content = (state.get("content", "") if state else "").strip()
                output_items = state.get("output", []) if state else []

                # Build display: tool progress + text content
                tool_lines = _render_tool_progress(output_items)
                display_parts = []

                if tool_lines:
                    display_parts.append(tool_lines)
                if content:
                    display_parts.append(content)

                display = "\n\n".join(display_parts).strip()

                if display and display != last_sent:
                    display = display[:max_len]

                    if use_draft:
                        try:
                            draft_id = await adapter.send_draft(platform_chat_id, display, draft_id)
                            last_sent = display
                        except Exception:
                            use_draft = False

                    if not use_draft and platform_msg_id:
                        try:
                            await adapter.edit(platform_chat_id, platform_msg_id, display)
                            last_sent = display
                        except Exception:
                            pass

                elif not display and not last_sent:
                    if not use_draft:
                        try:
                            await adapter.send_typing(platform_chat_id)
                        except Exception:
                            pass

                if task_done:
                    break

                await asyncio.sleep(STREAM_EDIT_INTERVAL)

            # ── Task complete — send final response ──

            from cptr.models import ChatMessage

            msg = await ChatMessage.get_by_id(task_message_id)
            final_content = (msg.content if msg else content).strip()

            if not final_content:
                if use_draft:
                    await adapter.send(platform_chat_id, "✅ Done (no text output)")
                elif platform_msg_id:
                    await adapter.edit(
                        platform_chat_id, platform_msg_id, "✅ Done (no text output)"
                    )
                return

            final_display = final_content

            if use_draft:
                # Draft is ephemeral — must send a real message to persist
                chunks = chunk_message(final_display, max_len)
                for chunk in chunks:
                    try:
                        await adapter.send(platform_chat_id, chunk)
                    except Exception:
                        logger.exception("[bridge] Failed to send final chunk")
            else:
                # Edit the placeholder, then send overflow.
                if len(final_display) <= max_len and platform_msg_id:
                    try:
                        await adapter.edit(platform_chat_id, platform_msg_id, final_display)
                        return
                    except Exception:
                        logger.debug("[bridge] Final edit failed", exc_info=True)
                        return

                chunks = chunk_message(final_display, max_len)
                if platform_msg_id and chunks:
                    try:
                        await adapter.edit(platform_chat_id, platform_msg_id, chunks[0])
                        chunks = chunks[1:]
                    except Exception:
                        logger.debug("[bridge] Final chunk edit failed", exc_info=True)
                        return
                for chunk in chunks:
                    try:
                        await adapter.send(platform_chat_id, chunk)
                    except Exception:
                        logger.exception("[bridge] Failed to send reply chunk")
                    break

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[bridge] Stream loop error")


# ── Tool call rendering ──────────────────────────────────────

# Map tool names to compact emoji + labels
_TOOL_ICONS = {
    "bash": "💻",
    "shell": "💻",
    "run_command": "💻",
    "read_file": "📄",
    "write_file": "✏️",
    "edit_file": "✏️",
    "create_file": "✏️",
    "replace_file": "✏️",
    "search": "🔍",
    "grep": "🔍",
    "find_files": "🔍",
    "web_search": "🌐",
    "browser": "🌐",
    "create_artifact": "📎",
}


def _render_tool_progress(output_items: list[dict]) -> str:
    """Render tool calls as compact status lines for streaming display.

    Example output:
        💻 `ls -la src/`
        📄 `main.py` (reading)
        ✏️ `config.ts` (editing)
    """
    lines = []
    for item in output_items:
        item_type = item.get("type", "")
        if item_type != "function_call":
            continue

        name = item.get("name", "")
        status = item.get("status", "")
        args = item.get("arguments", {})
        icon = _TOOL_ICONS.get(name, "🔧")

        # Extract the most relevant argument for display
        label = _tool_label(name, args)
        status_text = "" if status == "completed" else " _(running)_"

        lines.append(f"{icon} `{label}`{status_text}")

    return "\n".join(lines[-5:])  # Show last 5 tool calls max


def _render_tool_summary(output_items: list[dict]) -> str:
    """Render a compact summary of all tools used (for final message).

    Example: "🔧 Used 3 tools: bash, read_file, write_file"
    """
    tool_names = []
    for item in output_items:
        if item.get("type") == "function_call":
            name = item.get("name", "unknown")
            if name not in tool_names:
                tool_names.append(name)

    if not tool_names:
        return ""

    count = sum(1 for i in output_items if i.get("type") == "function_call")
    names = ", ".join(tool_names[:5])
    if len(tool_names) > 5:
        names += f" +{len(tool_names) - 5} more"

    return f"🔧 _{count} tool call{'s' if count != 1 else ''}: {names}_"


def _tool_label(name: str, args: dict) -> str:
    """Extract a short, meaningful label from tool arguments."""
    if isinstance(args, str):
        return name

    # Command execution
    if name in ("bash", "shell", "run_command"):
        cmd = args.get("command", args.get("cmd", ""))
        if isinstance(cmd, str):
            return cmd[:60] if cmd else name
        return name

    # File operations
    for key in ("path", "file", "file_path", "filename", "target"):
        val = args.get(key, "")
        if val and isinstance(val, str):
            # Just show the basename
            parts = val.rsplit("/", 1)
            return parts[-1] if parts else val

    # Search
    if name in ("search", "grep", "find_files"):
        query = args.get("query", args.get("pattern", ""))
        if isinstance(query, str):
            return query[:40] if query else name

    return name

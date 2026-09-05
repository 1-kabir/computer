"""WhatsApp adapter — zero SDK dependencies.

Uses Meta's WhatsApp Cloud API via httpx:
- Webhook endpoint for inbound messages (registered via FastAPI)
- REST POST to /messages for sending
- No native streaming; uses edit-via-context approach

Token format: ``access_token|phone_number_id`` (pipe-separated).

Requires a publicly accessible URL for webhook verification.
Configure the webhook URL in Meta's WhatsApp dashboard as:
    https://your-domain/api/webhooks/whatsapp/{bot_id}
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Optional

import httpx

from cptr.utils.bridge import Attachment, BaseAdapter, MessageEvent, chunk_message

logger = logging.getLogger(__name__)

# v23.0 is the first GA version with typing indicators; docs examples use
# v23–v25.  Bump deliberately when adopting newer Cloud API features.
API_BASE = "https://graph.facebook.com/v23.0"
MAX_MESSAGE_LEN = 4096


def _parse_token(token: str) -> tuple[str, str]:
    """Parse 'access_token|phone_number_id' format."""
    if "|" in token:
        access_token, phone_number_id = token.split("|", 1)
        return access_token.strip(), phone_number_id.strip()
    raise ValueError("WhatsApp token must be 'access_token|phone_number_id' (pipe-separated)")


class WhatsAppAdapter(BaseAdapter):
    """WhatsApp bot via Cloud API + webhook inbound."""

    platform = "whatsapp"
    # Cloud API messages cannot be edited — the bridge delivers replies via
    # send() instead of edit-based streaming.
    supports_edit = False

    def __init__(self, token: str, bot_id: str = "") -> None:
        super().__init__()
        self._access_token, self._phone_number_id = _parse_token(token)
        self._bot_id = bot_id
        self._http: Optional[httpx.AsyncClient] = None
        self._running = False
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._process_task: Optional[asyncio.Task] = None
        # Last inbound message id from the webhook — required for the
        # read-receipt / typing-indicator endpoint.
        self._last_inbound_message_id: Optional[str] = None
        # Recent inbound ids for redelivery dedupe (Meta retries webhooks).
        self._seen_message_ids: deque[str] = deque(maxlen=256)

    # ── Lifecycle ──────────────────────────────────────────

    async def connect(self) -> bool:
        self._http = httpx.AsyncClient(
            timeout=15,
            headers={"Authorization": f"Bearer {self._access_token}"},
        )

        # Verify token by fetching phone number info
        try:
            resp = await self._http.get(f"{API_BASE}/{self._phone_number_id}")
            data = resp.json()
            if "error" in data:
                raise ValueError(data["error"].get("message", "Invalid token"))
            display_name = data.get("verified_name") or data.get("display_phone_number", "?")
            logger.info("WhatsApp bot connected: %s (%s)", display_name, self._phone_number_id)
        except Exception:
            logger.exception("WhatsApp token verification failed")
            await self._http.aclose()
            self._http = None
            return False

        self._running = True
        self._process_task = asyncio.create_task(self._process_loop())
        return True

    async def disconnect(self) -> None:
        self._running = False
        if self._process_task and not self._process_task.done():
            self._process_task.cancel()
            try:
                await self._process_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── Webhook ingestion ──────────────────────────────────

    async def handle_webhook(self, payload: dict) -> None:
        """Called by the webhook route to push inbound messages."""
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for message in value.get("messages", []):
                    msg_id = message.get("id", "")
                    if msg_id:
                        if msg_id in self._seen_message_ids:
                            logger.debug("[whatsapp] Dropping redelivered message %s", msg_id)
                            continue
                        self._seen_message_ids.append(msg_id)
                    self._last_inbound_message_id = msg_id or None
                    msg_type = message.get("type", "")

                    text = ""
                    attachments: list[Attachment] = []

                    if msg_type == "text":
                        text = message.get("text", {}).get("body", "").strip()
                    elif msg_type == "interactive":
                        # Button / list replies arrive as interactive payloads;
                        # surface the selected option title as plain text.
                        interactive = message.get("interactive", {})
                        reply = (
                            interactive.get("button_reply") or interactive.get("list_reply") or {}
                        )
                        text = reply.get("title", "").strip()
                    elif msg_type == "image":
                        image = message.get("image", {})
                        text = image.get("caption", "").strip()
                        media_data = await self._download_media(image.get("id", ""))
                        if media_data:
                            attachments.append(
                                Attachment(
                                    type="image",
                                    filename="photo.jpg",
                                    data=media_data,
                                    mime_type=image.get("mime_type", "image/jpeg"),
                                )
                            )
                    elif msg_type == "audio":
                        audio = message.get("audio", {})
                        media_data = await self._download_media(audio.get("id", ""))
                        if media_data:
                            attachments.append(
                                Attachment(
                                    type="audio",
                                    filename="voice.ogg",
                                    data=media_data,
                                    mime_type=audio.get("mime_type", "audio/ogg"),
                                )
                            )
                    elif msg_type == "document":
                        doc = message.get("document", {})
                        text = doc.get("caption", "").strip()
                        media_data = await self._download_media(doc.get("id", ""))
                        if media_data:
                            fname = doc.get("filename", "document")
                            attachments.append(
                                Attachment(
                                    type="document",
                                    filename=fname,
                                    data=media_data,
                                    mime_type=doc.get("mime_type", "application/octet-stream"),
                                )
                            )
                    elif msg_type == "video":
                        video = message.get("video", {})
                        text = video.get("caption", "").strip()
                        media_data = await self._download_media(video.get("id", ""))
                        if media_data:
                            attachments.append(
                                Attachment(
                                    type="document",
                                    filename="video.mp4",
                                    data=media_data,
                                    mime_type=video.get("mime_type", "video/mp4"),
                                )
                            )
                    else:
                        continue

                    if not text and not attachments:
                        continue

                    sender = message.get("from", "")

                    # Resolve sender name from contacts
                    contacts = value.get("contacts", [])
                    sender_name = "User"
                    for c in contacts:
                        if c.get("wa_id") == sender:
                            profile = c.get("profile", {})
                            sender_name = profile.get("name", sender)
                            break

                    event = MessageEvent(
                        platform="whatsapp",
                        chat_id=sender,  # WhatsApp uses phone number as chat ID
                        sender_id=sender,
                        sender_name=sender_name,
                        text=text,
                        attachments=attachments,
                    )
                    await self._message_queue.put(event)

    async def _process_loop(self) -> None:
        """Process inbound messages from the webhook queue."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._message_queue.get(), timeout=5.0)
                if self.on_message:
                    await self.on_message(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Error processing WhatsApp message")

    # ── Sending ────────────────────────────────────────────

    async def send(self, chat_id: str, text: str) -> str | None:
        if not self._http:
            return None
        chunks = chunk_message(text, MAX_MESSAGE_LEN)
        msg_id = None
        for chunk in chunks:
            resp = await self._http.post(
                f"{API_BASE}/{self._phone_number_id}/messages",
                json={
                    "messaging_product": "whatsapp",
                    "to": chat_id,
                    "type": "text",
                    "text": {"body": chunk},
                },
            )
            data = resp.json()
            if resp.status_code != 200:
                logger.warning(
                    "[whatsapp] Send failed (HTTP %d): %s",
                    resp.status_code,
                    data.get("error", {}).get("message", resp.text[:200]),
                )
            if msg_id is None:
                messages = data.get("messages", [])
                if messages:
                    msg_id = messages[0].get("id")
        return msg_id

    async def edit(self, chat_id: str, message_id: str, text: str) -> None:
        """WhatsApp Cloud API is send-only: sent messages cannot be edited.

        The bridge must not call this on WhatsApp (see supports_edit = False);
        log loudly if it ever does so the dropped reply is diagnosable.
        """
        logger.warning(
            "[whatsapp] edit() called but WhatsApp messages cannot be edited — "
            "bridge should deliver via send() (chat %s)",
            chat_id,
        )

    async def send_typing(self, chat_id: str) -> None:
        """Mark the last inbound message read and show a typing indicator.

        Cloud API syntax (Graph >= v23.0): POST /{phone_number_id}/messages
        with {status: "read", message_id: <inbound wamid>,
        typing_indicator: {type: "text"}}.  The indicator auto-dismisses
        after 25 seconds or when the business replies, so refreshing it
        periodically is the intended usage.  Requires a real inbound message
        id — silently skip before any inbound message was seen.
        """
        if not self._http or not self._last_inbound_message_id:
            return
        try:
            await self._http.post(
                f"{API_BASE}/{self._phone_number_id}/messages",
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": self._last_inbound_message_id,
                    "typing_indicator": {"type": "text"},
                },
            )
        except Exception:
            pass

    # ── Media ──────────────────────────────────────────────

    async def _download_media(self, media_id: str) -> bytes | None:
        """Download media from WhatsApp Cloud API.

        Two-step: GET /{media_id} for URL, then GET the URL for the bytes.
        """
        if not self._http or not media_id:
            return None
        try:
            # Step 1: Get the media URL
            resp = await self._http.get(f"{API_BASE}/{media_id}")
            data = resp.json()
            url = data.get("url")
            if not url:
                return None
            # Step 2: Download the actual file (requires auth header)
            resp2 = await self._http.get(url)
            if resp2.status_code == 200:
                return resp2.content
            logger.warning("[whatsapp] Media download failed: HTTP %d", resp2.status_code)
        except Exception:
            logger.exception("[whatsapp] Failed to download media %s", media_id)
        return None

    async def send_photo(
        self, chat_id: str, data: bytes, filename: str, caption: str = ""
    ) -> str | None:
        """Send a photo via WhatsApp media upload."""
        media_id = await self._upload_media(data, filename, "image/jpeg")
        if not media_id:
            return await self.send(chat_id, caption) if caption else None
        return await self._send_media_message(chat_id, "image", media_id, caption)

    async def send_document(
        self, chat_id: str, data: bytes, filename: str, caption: str = ""
    ) -> str | None:
        """Send a document via WhatsApp media upload."""
        media_id = await self._upload_media(data, filename, "application/octet-stream")
        if not media_id:
            return await self.send(chat_id, caption) if caption else None
        return await self._send_media_message(chat_id, "document", media_id, caption, filename)

    async def _upload_media(self, data: bytes, filename: str, mime_type: str) -> str | None:
        """Upload media to WhatsApp Cloud API, return media_id."""
        if not self._http:
            return None
        try:
            resp = await self._http.post(
                f"{API_BASE}/{self._phone_number_id}/media",
                files={"file": (filename, data, mime_type)},
                data={"messaging_product": "whatsapp", "type": mime_type},
            )
            result = resp.json()
            return result.get("id")
        except Exception:
            logger.exception("[whatsapp] Failed to upload media")
        return None

    async def _send_media_message(
        self,
        chat_id: str,
        media_type: str,
        media_id: str,
        caption: str = "",
        filename: str = "",
    ) -> str | None:
        """Send a media message using an uploaded media_id."""
        if not self._http:
            return None
        media_obj: dict = {"id": media_id}
        if caption:
            media_obj["caption"] = caption[:1024]
        if filename and media_type == "document":
            media_obj["filename"] = filename
        try:
            resp = await self._http.post(
                f"{API_BASE}/{self._phone_number_id}/messages",
                json={
                    "messaging_product": "whatsapp",
                    "to": chat_id,
                    "type": media_type,
                    media_type: media_obj,
                },
            )
            data = resp.json()
            messages = data.get("messages", [])
            return messages[0].get("id") if messages else None
        except Exception:
            logger.exception("[whatsapp] Failed to send %s message", media_type)
        return None


async def verify_token(token: str) -> dict:
    """Verify WhatsApp Cloud API credentials."""
    access_token, phone_number_id = _parse_token(token)

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{API_BASE}/{phone_number_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        data = resp.json()
        if "error" in data:
            raise ValueError(data["error"].get("message", "Invalid credentials"))

        return {
            "username": data.get("verified_name") or data.get("display_phone_number"),
            "id": phone_number_id,
        }

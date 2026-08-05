"""Admin API for messaging bots — CRUD + start/stop + token verification.

Bot configs are stored in the Config key-value store (not a separate table).
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from cptr.routers.admin import require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/bots", tags=["bots"])


# ── Request schemas ──────────────────────────────────────────


class BotCreate(BaseModel):
    platform: str
    name: str
    workspace: str
    model_id: str
    token: str
    allowed_senders: list[str] | None = None
    is_active: bool = True


class BotUpdate(BaseModel):
    name: str | None = None
    workspace: str | None = None
    model_id: str | None = None
    token: str | None = None
    allowed_senders: list[str] | None = None
    is_active: bool | None = None


class TokenVerify(BaseModel):
    platform: str
    token: str


# ── Helpers ──────────────────────────────────────────────────


def _mask_token(token: str) -> str:
    if not token or len(token) < 8:
        return "****"
    return f"{token[:4]}...{token[-4:]}"


def _serialize_bot(bot: dict, running: bool = False) -> dict:
    """Serialize a bot config for API response (token masked)."""
    from cptr.utils.config import _get_jwt_secret
    from cptr.utils.crypto import decrypt_key

    try:
        plain_token = decrypt_key(bot.get("token", ""), _get_jwt_secret())
        masked = _mask_token(plain_token)
    except Exception:
        masked = "****"

    return {
        "id": bot.get("id"),
        "platform": bot.get("platform"),
        "name": bot.get("name"),
        "workspace": bot.get("workspace"),
        "model_id": bot.get("model_id"),
        "token_masked": masked,
        "allowed_senders": bot.get("allowed_senders") or [],
        "is_active": bot.get("is_active", True),
        "is_running": running,
        "created_at": bot.get("created_at"),
        "updated_at": bot.get("updated_at"),
    }


# ── Endpoints ────────────────────────────────────────────────


@router.get("")
async def list_bots(request: Request):
    auth = require_admin(request)
    from cptr.utils.bridge import get_bot_configs

    bots = await get_bot_configs()
    # Filter to bots owned by this user
    user_bots = [b for b in bots if b.get("user_id") == auth.user_id]

    bot_manager = getattr(request.app.state, "bot_manager", None)
    status = bot_manager.get_status() if bot_manager else {}

    return [_serialize_bot(b, running=status.get(b["id"], False)) for b in user_bots]


@router.post("")
async def create_bot(request: Request, body: BotCreate):
    auth = require_admin(request)
    from cptr.utils.bridge import upsert_bot
    from cptr.utils.config import _get_jwt_secret
    from cptr.utils.crypto import encrypt_key

    if body.platform not in ("telegram", "discord", "slack", "whatsapp", "signal"):
        raise HTTPException(400, f"Unsupported platform: {body.platform}")

    now = int(time.time_ns())
    bot = {
        "id": str(uuid.uuid4()),
        "user_id": auth.user_id,
        "platform": body.platform,
        "name": body.name,
        "workspace": body.workspace,
        "model_id": body.model_id,
        "token": encrypt_key(body.token, _get_jwt_secret()),
        "allowed_senders": body.allowed_senders,
        "is_active": body.is_active,
        "created_at": now,
        "updated_at": now,
    }
    await upsert_bot(bot)

    # Auto-start if active
    if bot["is_active"]:
        bot_manager = getattr(request.app.state, "bot_manager", None)
        if bot_manager:
            try:
                await bot_manager.start_bot(bot)
            except Exception:
                logger.exception("Failed to auto-start bot %s", bot["id"][:8])

    return _serialize_bot(bot)


@router.put("/{bot_id}")
async def update_bot(request: Request, bot_id: str, body: BotUpdate):
    require_admin(request)
    from cptr.utils.bridge import get_bot_by_id, upsert_bot
    from cptr.utils.config import _get_jwt_secret
    from cptr.utils.crypto import encrypt_key

    bot = await get_bot_by_id(bot_id)
    if not bot:
        raise HTTPException(404, "Bot not found")

    if body.name is not None:
        bot["name"] = body.name
    if body.workspace is not None:
        bot["workspace"] = body.workspace
    if body.model_id is not None:
        bot["model_id"] = body.model_id
    if body.token is not None:
        bot["token"] = encrypt_key(body.token, _get_jwt_secret())
    if body.allowed_senders is not None:
        bot["allowed_senders"] = body.allowed_senders
    if body.is_active is not None:
        bot["is_active"] = body.is_active
    bot["updated_at"] = int(time.time_ns())

    await upsert_bot(bot)

    # Restart if running (config changed)
    bot_manager = getattr(request.app.state, "bot_manager", None)
    if bot_manager and bot_manager.is_running(bot_id):
        await bot_manager.stop_bot(bot_id)
        if bot.get("is_active", True):
            await bot_manager.start_bot(bot)

    running = bot_manager.is_running(bot_id) if bot_manager else False
    return _serialize_bot(bot, running=running)


@router.delete("/{bot_id}")
async def delete_bot(request: Request, bot_id: str):
    require_admin(request)
    from cptr.utils.bridge import delete_bot_config

    bot_manager = getattr(request.app.state, "bot_manager", None)
    if bot_manager and bot_manager.is_running(bot_id):
        await bot_manager.stop_bot(bot_id)

    ok = await delete_bot_config(bot_id)
    if not ok:
        raise HTTPException(404, "Bot not found")

    return {"ok": True}


@router.post("/{bot_id}/start")
async def start_bot(request: Request, bot_id: str):
    require_admin(request)
    from cptr.utils.bridge import get_bot_by_id

    bot = await get_bot_by_id(bot_id)
    if not bot:
        raise HTTPException(404, "Bot not found")

    bot_manager = getattr(request.app.state, "bot_manager", None)
    if not bot_manager:
        raise HTTPException(500, "Bot manager not initialized")
    if bot_manager.is_running(bot_id):
        raise HTTPException(400, "Bot is already running")

    await bot_manager.start_bot(bot)
    return {"ok": True, "is_running": True}


@router.post("/{bot_id}/stop")
async def stop_bot(request: Request, bot_id: str):
    require_admin(request)

    bot_manager = getattr(request.app.state, "bot_manager", None)
    if not bot_manager:
        raise HTTPException(500, "Bot manager not initialized")

    await bot_manager.stop_bot(bot_id)
    return {"ok": True, "is_running": False}


@router.post("/verify")
async def verify_token(request: Request, body: TokenVerify):
    require_admin(request)

    try:
        if body.platform == "telegram":
            from cptr.utils.adapters.telegram import verify_token as tg_verify

            info = await tg_verify(body.token)
            return {"ok": True, "info": {"username": info.get("username"), "id": info.get("id")}}

        elif body.platform == "discord":
            from cptr.utils.adapters.discord import verify_token as dc_verify

            info = await dc_verify(body.token)
            return {"ok": True, "info": {"username": info.get("username"), "id": info.get("id")}}

        elif body.platform == "slack":
            from cptr.utils.adapters.slack import verify_token as slack_verify

            info = await slack_verify(body.token)
            return {"ok": True, "info": {"username": info.get("username"), "id": info.get("id")}}

        elif body.platform == "whatsapp":
            from cptr.utils.adapters.whatsapp import verify_token as wa_verify

            info = await wa_verify(body.token)
            return {"ok": True, "info": {"username": info.get("username"), "id": info.get("id")}}

        elif body.platform == "signal":
            from cptr.utils.adapters.signal import verify_token as sig_verify

            info = await sig_verify(body.token)
            return {"ok": True, "info": {"username": info.get("username"), "id": info.get("id")}}

        else:
            raise HTTPException(400, f"Unsupported platform: {body.platform}")

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── WhatsApp Webhook ─────────────────────────────────────────


webhook_router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@webhook_router.get("/whatsapp/{bot_id}")
async def whatsapp_webhook_verify(request: Request, bot_id: str):
    """Meta webhook verification challenge (GET)."""
    mode = request.query_params.get("hub.mode")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and challenge:
        # Accept any verify_token for now — user sets it in Meta dashboard
        from starlette.responses import PlainTextResponse

        return PlainTextResponse(challenge)
    raise HTTPException(403, "Verification failed")


@webhook_router.post("/whatsapp/{bot_id}")
async def whatsapp_webhook_inbound(request: Request, bot_id: str):
    """Receive inbound WhatsApp messages via webhook."""
    payload = await request.json()

    bot_manager = getattr(request.app.state, "bot_manager", None)
    if not bot_manager:
        raise HTTPException(503, "Bot manager not initialized")

    adapter = bot_manager._adapters.get(bot_id)
    if adapter:
        from cptr.utils.adapters.whatsapp import WhatsAppAdapter

        if isinstance(adapter, WhatsAppAdapter):
            await adapter.handle_webhook(payload)

    return {"status": "ok"}

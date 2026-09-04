"""Socket.IO server: creates the sio instance and registers all handlers.

Usage in app.py:
    from cptr.socket.main import sio_app
    # mount sio_app as the root ASGI app
"""

from __future__ import annotations

import socketio

from cptr.models import Chat
from cptr.utils.config import check_access, now_ms
from cptr.env import CORS_ALLOWED_ORIGINS

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=CORS_ALLOWED_ORIGINS)

# user_id → set of connected sids
_user_sids: dict[str, set[str]] = {}
# (user_id, chat_id) → visible panel instances
_chat_view_sids: dict[tuple[str, str], set[tuple[str, str]]] = {}


@sio.on("connect")
async def on_connect(sid, environ, auth):
    token = auth.get("token") if auth else None
    if not token:
        # Try cookie fallback
        cookie_header = environ.get("HTTP_COOKIE", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("cptr_session="):
                token = part.split("=", 1)[1]
                break

    client_host = environ.get("REMOTE_ADDR", "127.0.0.1")
    user = check_access(client_host=client_host, jwt_token=token)
    if not user or not user.user_id:
        raise ConnectionRefusedError("unauthorized")

    await sio.save_session(sid, {"user_id": user.user_id})
    _user_sids.setdefault(user.user_id, set()).add(sid)


@sio.on("disconnect")
async def on_disconnect(sid):
    session = await sio.get_session(sid)
    uid = session.get("user_id")
    if uid and uid in _user_sids:
        _user_sids[uid].discard(sid)
        if not _user_sids[uid]:
            del _user_sids[uid]
    for key, views in list(_chat_view_sids.items()):
        views_copy = {view for view in views if view[0] != sid}
        if views_copy:
            _chat_view_sids[key] = views_copy
        else:
            del _chat_view_sids[key]


@sio.on("chat:view")
async def on_chat_view(sid, data):
    """Track which chat panels are currently visible for timeout pausing."""
    session = await sio.get_session(sid)
    user_id = session.get("user_id")
    chat_id = data.get("chat_id") if isinstance(data, dict) else None
    view_id = data.get("view_id") if isinstance(data, dict) else None
    if not user_id or not isinstance(chat_id, str) or not isinstance(view_id, str):
        return

    views = _chat_view_sids.setdefault((user_id, chat_id), set())
    view = (sid, view_id)
    if data.get("visible"):
        views.add(view)
    else:
        views.discard(view)
        if not views:
            del _chat_view_sids[(user_id, chat_id)]


@sio.on("chat:read")
async def on_chat_read(sid, data):
    """Persist a user's explicit read acknowledgement for a chat."""
    session = await sio.get_session(sid)
    user_id = session.get("user_id")
    chat_id = data.get("chat_id") if isinstance(data, dict) else None
    if not user_id or not isinstance(chat_id, str):
        return

    last_read_at = now_ms()
    if await Chat.update_last_read_at(chat_id, user_id, last_read_at):
        from cptr.events import EVENTS, publish_event

        await publish_event(
            EVENTS.CHAT_READ,
            actor={"id": user_id},
            subject_id=chat_id,
            subject_type="chat",
            source="socket",
        )
        chat = await Chat.get_by_id(chat_id)
        workspace = (chat.meta or {}).get("workspace", "") if chat else ""
        from cptr.utils.chat_task import get_active_chat_ids

        from cptr.models.users import UserStates

        mute_bridge = await UserStates.get_flag(user_id, "bridge_notifications_muted")
        unread_counts = await Chat.unread_counts_by_workspace(
            user_id,
            [workspace],
            get_active_chat_ids(),
            exclude_bridge=mute_bridge,
        )
        await emit_to_user(
            user_id,
            {
                "chat_id": chat_id,
                "workspace": workspace,
                "last_read_at": last_read_at,
                "workspace_unread_count": unread_counts.get(workspace, 0),
            },
        )


async def emit_to_user(user_id: str, data: dict):
    """Send events:chat to all of a user's connected tabs/windows."""
    for sid in list(_user_sids.get(user_id, set())):
        await sio.emit("events:chat", data, to=sid)


def is_chat_visible(user_id: str, chat_id: str) -> bool:
    return bool(_chat_view_sids.get((user_id, chat_id)))


def is_user_active(user_id: str) -> bool:
    """Return True when the user has at least one connected app session."""
    return bool(_user_sids.get(user_id))


def get_asgi_app(other_app):
    """Wrap a FastAPI/Starlette app with the Socket.IO ASGI layer."""
    return socketio.ASGIApp(sio, other_asgi_app=other_app)

"""Gateway: expose cptr workspaces as OpenAI-compatible models.

GET  /v1/models           - list workspaces in OpenAI model-list format
POST /v1/chat/completions - run the agentic loop on a workspace, stream SSE

Any OpenAI-compatible client (Open WebUI, curl, Python SDK) can connect
to cptr and use each workspace as a "model" that can read files, edit code,
run commands, and use skills.

Session mapping: if the caller sends X-Chat-Id or X-OpenWebUI-Chat-Id,
the same cptr chat is reused across turns. Otherwise each request creates
a fresh chat.

Auth: Bearer token validated against hashed keys in the Config store.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from cptr.models import Auth, Chat, ChatMessage, Config
from cptr.models.workspaces import Workspace
from cptr.utils.agents.prompts import message_text
from cptr.utils.config import AuthResult, now_ms
from cptr.utils.runtime import Runtime, FileError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["gateway"])

# Headers that clients can send with the chat ID.
CHAT_ID_HEADER = "X-Chat-Id"
OWUI_CHAT_ID_HEADER = "X-OpenWebUI-Chat-Id"
OWUI_MESSAGE_ID_HEADER = "X-OpenWebUI-Message-Id"
OWUI_TASK_HEADER = "X-OpenWebUI-Task"


# ── API key management ───────────────────────────────────────


async def _get_api_keys() -> list[dict]:
    """Load API keys from Config store."""
    keys = await Config.get("api_keys")
    return keys if isinstance(keys, list) else []


async def _save_api_keys(keys: list[dict]) -> None:
    await Config.upsert({"api_keys": keys})


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _format_tool_call(item: dict) -> str | None:
    """Render a tool call as compact markdown for OpenAI-compatible clients."""
    if item.get("type") != "function_call" or item.get("status") != "in_progress":
        return None

    arguments = item.get("arguments")
    title = arguments.get("title") if isinstance(arguments, dict) else None
    name = str(title or item.get("name") or "tool").strip() or "tool"
    return f"\n\n`{name}`\n\n"


async def _authenticate(request: Request) -> str:
    """Validate Bearer token from Authorization header.  Returns user_id."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")

    token = auth[7:].strip()
    if not token:
        raise HTTPException(401, "Empty bearer token")

    token_hash = _hash_key(token)
    keys = await _get_api_keys()
    for key in keys:
        if key.get("key_hash") == token_hash:
            user_id = key.get("user_id")
            if not user_id:
                raise HTTPException(500, "API key has no user_id")
            auth_row = await Auth.get_by_user_id(user_id)
            request.state.auth = AuthResult(
                user_id=user_id,
                username=auth_row.username if auth_row else None,
            )
            return user_id

    raise HTTPException(401, "Invalid API key")


# ── GET /v1/models ───────────────────────────────────────────


@router.get("/models")
async def list_models(request: Request):
    """List workspaces as OpenAI-format models."""
    user_id = await _authenticate(request)
    workspaces = await Workspace.get_by_user(user_id)

    # Disambiguate basenames
    name_counts: dict[str, int] = {}
    for ws in workspaces:
        name = Path(ws.path).name
        name_counts[name] = name_counts.get(name, 0) + 1

    seen: dict[str, int] = {}
    models = []
    for ws in workspaces:
        basename = Path(ws.path).name
        if name_counts[basename] > 1:
            seen[basename] = seen.get(basename, 0) + 1
            model_id = f"cptr/{basename}-{seen[basename]}"
        else:
            model_id = f"cptr/{basename}"

        models.append(
            {
                "id": model_id,
                "object": "model",
                "created": ws.created_at or int(time.time()),
                "owned_by": "cptr",
                "name": f"{ws.name} - {ws.path}",
                # Extra metadata for cptr
                "cptr_workspace": ws.path,
            }
        )

    return {"object": "list", "data": models}


# ── POST /v1/chat/completions ────────────────────────────────


class ChatCompletionMessage(BaseModel):
    role: str
    content: str = ""


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[dict]
    stream: bool = False
    # Other OpenAI params are accepted but ignored
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    # OWUI metadata for message tree mapping
    id: str | None = None  # OWUI assistant message ID
    parent_id: str | None = None  # OWUI user message's parentId
    user_message: dict | None = None  # OWUI user message object

    model_config = {"extra": "allow"}  # Accept any additional fields


@router.post("/chat/completions")
async def create_chat_completion(request: Request, body: ChatCompletionRequest):
    """Run the cptr agentic loop and stream results as OpenAI SSE."""
    user_id = await _authenticate(request)

    # 1. Resolve model → workspace path
    workspace = await _resolve_workspace(user_id, body.model)

    # 2. Resolve the selected model target for this workspace.
    target, model_id = await _resolve_model(request, workspace, request.app.state)

    # Intercept OWUI utility tasks (follow-ups, title gen, tags gen).
    # These should go directly to the LLM, not through the agentic loop.
    utility_result = await _intercept_task(request, body, workspace, model_id)
    if utility_result is not None:
        return utility_result

    # 3. Session mapping: find or create a cptr chat
    client_chat_id = request.headers.get(CHAT_ID_HEADER) or request.headers.get(OWUI_CHAT_ID_HEADER)
    chat_id = await _ensure_chat(
        request, user_id, workspace, client_chat_id, body.messages, model_id
    )

    # 4. Resolve message tree — map OWUI message IDs to cptr message IDs
    #    Supports: normal messages, regeneration (sibling), edit (fork)
    user_msg, assistant_msg = await _resolve_messages(
        request=request,
        chat_id=chat_id,
        model_id=model_id,
        messages=body.messages,
    )

    await Chat.update_current_message(chat_id, assistant_msg.id, now_ms())

    # Export JSON so cptr sidebar sees it immediately
    from cptr.utils.chat_export import export_chat_to_file

    await export_chat_to_file(request, chat_id)

    # 5. Create output queue and start the agentic loop
    output_queue: asyncio.Queue = asyncio.Queue()

    from cptr.utils.chat_task import start_task

    start_task(
        request,
        message_id=assistant_msg.id,
        chat_id=chat_id,
        user_id=user_id,
        workspace=workspace,
        output_queue=output_queue,
        target=target,
    )

    # 6. Stream SSE response
    completion_id = f"chatcmpl-{assistant_msg.id[:24]}"
    created = int(time.time())

    if body.stream:
        return StreamingResponse(
            _stream(output_queue, completion_id, created, body.model),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        # Non-streaming: collect all text, return as single response
        full_text = await _collect(output_queue)
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": body.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": full_text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }


# ── Message tree resolution ──────────────────────────────────


async def _resolve_messages(
    request: Request,
    chat_id: str,
    model_id: str,
    messages: list[dict],
) -> tuple:
    """Create user + assistant messages with proper tree structure.

    Uses OWUI headers (set via custom header templates) to map message IDs
    and create proper branches for regeneration and edits.

    Headers used:
      X-OpenWebUI-Message-Id        → OWUI assistant message ID
      X-OpenWebUI-User-Message-Id   → OWUI user message ID
      X-OpenWebUI-User-Message-Parent-Id → OWUI user message's parentId

    Returns (user_msg, assistant_msg).
    """
    chat = await Chat.get_by_id(chat_id)
    meta = (chat.meta or {}) if chat else {}
    msg_map: dict = dict(meta.get("owui_msg_map", {}))

    # Extract OWUI message IDs from headers
    # Distinguish "header not set" (no OWUI config) from "header set to empty" (root-level msg)
    owui_asst_id = (request.headers.get("X-OpenWebUI-Message-Id") or "").strip() or None
    owui_user_id = (request.headers.get("X-OpenWebUI-User-Message-Id") or "").strip() or None
    _raw_parent = request.headers.get("X-OpenWebUI-User-Message-Parent-Id")
    has_parent_header = _raw_parent is not None
    owui_user_parent_id = (_raw_parent or "").strip() or None

    # Extract user content
    user_content = ""
    if messages:
        last_user = next(
            (m for m in reversed(messages) if m.get("role") == "user"),
            None,
        )
        user_content = message_text(last_user).strip() if last_user else ""

    # Determine parent for the user message
    cptr_parent_id = chat.current_message_id if chat else None

    # First time this OWUI chat reaches cptr, copy the request's existing
    # transcript into the parent chain so the native agent can cold-start with it.
    if chat and not chat.current_message_id and messages:
        last_user_index = None
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "user":
                last_user_index = index
                break
        if last_user_index:
            created_at = now_ms()
            for offset, message in enumerate(messages[:last_user_index]):
                role = message.get("role")
                if role not in ("user", "assistant"):
                    continue
                content = message_text(message).strip()
                if not content:
                    continue
                imported = await ChatMessage.create(
                    chat_id=chat_id,
                    role=role,
                    content=content,
                    parent_id=cptr_parent_id,
                    done=True,
                    created_at=created_at + offset,
                )
                cptr_parent_id = imported.id
                owui_id = str(message.get("id") or "").strip()
                if owui_id:
                    msg_map[owui_id] = imported.id

    if owui_user_id and owui_user_id in msg_map:
        # User message already exists in cptr (regeneration case)
        existing_user_msg = await ChatMessage.get_by_id(msg_map[owui_user_id])
        if existing_user_msg:
            # Skip creating user msg — just create a sibling assistant
            assistant_msg = await ChatMessage.create(
                chat_id=chat_id,
                role="assistant",
                content="",
                parent_id=existing_user_msg.id,
                model=model_id,
                done=False,
                created_at=now_ms(),
            )
            # Update map
            if owui_asst_id:
                msg_map[owui_asst_id] = assistant_msg.id
                meta["owui_msg_map"] = msg_map
                await Chat.update_meta(chat_id, meta, now_ms())

            return existing_user_msg, assistant_msg

    # Resolve parent for edited/new user messages
    if owui_user_parent_id and owui_user_parent_id in msg_map:
        # Edit/fork case: parent is a known OWUI message → resolve to cptr ID
        cptr_parent_id = msg_map[owui_user_parent_id]
    elif has_parent_header and not owui_user_parent_id:
        # Header present but empty → editing root-level message (first msg has no parent)
        # Create as root sibling, not child of current_message_id
        cptr_parent_id = None

    # Create user message
    user_msg = await ChatMessage.create(
        chat_id=chat_id,
        role="user",
        content=user_content,
        parent_id=cptr_parent_id,
        created_at=now_ms(),
    )

    # Create assistant message
    assistant_msg = await ChatMessage.create(
        chat_id=chat_id,
        role="assistant",
        content="",
        parent_id=user_msg.id,
        model=model_id,
        done=False,
        created_at=now_ms(),
    )

    # Update the message ID map
    if owui_user_id:
        msg_map[owui_user_id] = user_msg.id
    if owui_asst_id:
        msg_map[owui_asst_id] = assistant_msg.id

    if msg_map:
        meta["owui_msg_map"] = msg_map
        await Chat.update_meta(chat_id, meta, now_ms())

    return user_msg, assistant_msg


# ── SSE generator ────────────────────────────────────────────


async def _stream(
    queue: asyncio.Queue,
    completion_id: str,
    created: int,
    model: str,
):
    """Translate queue events → OpenAI SSE chunks."""

    def _chunk(delta: dict, finish_reason: str | None = None) -> str:
        data = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }
        return f"data: {json.dumps(data)}\n\n"

    # Initial chunk with role
    yield _chunk({"role": "assistant", "content": ""})

    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=300)
        except asyncio.TimeoutError:
            # Safety timeout, end the stream
            yield _chunk({}, "stop")
            yield "data: [DONE]\n\n"
            return

        if event is None:
            # Sentinel: stream complete
            yield _chunk({}, "stop")
            yield "data: [DONE]\n\n"
            return

        event_type = event.get("type")

        if event_type == "delta":
            content = event.get("content", "")
            if content:
                yield _chunk({"content": content})

        elif event_type == "output":
            content = _format_tool_call(event.get("item") or {})
            if content:
                yield _chunk({"content": content})

        elif event_type == "done":
            finish = event.get("finish_reason", "stop")
            yield _chunk({}, finish)
            yield "data: [DONE]\n\n"
            return

        elif event_type == "error":
            # Stream the error as content, then stop
            error_msg = event.get("message", "Internal error")
            yield _chunk({"content": f"\n\n> **Error:** {error_msg}"})
            yield _chunk({}, "stop")
            yield "data: [DONE]\n\n"
            return

        # Other output types are persisted in cptr's DB and visible in its sidebar.


async def _collect(queue: asyncio.Queue) -> str:
    """Collect all text from the queue for non-streaming mode."""
    parts = []
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=300)
        except asyncio.TimeoutError:
            break
        if event is None:
            break
        if event.get("type") == "delta":
            parts.append(event.get("content", ""))
        elif event.get("type") == "output":
            content = _format_tool_call(event.get("item") or {})
            if content:
                parts.append(content)
        elif event.get("type") in ("done", "error"):
            if event.get("type") == "error":
                parts.append(f"\n\n> **Error:** {event.get('message', '')}")
            break
    return "".join(parts)


# ── Utility task interception ────────────────────────────────

# Patterns that identify OWUI background task prompts.
# These should be proxied directly to the LLM, not through the agentic loop.
_UTILITY_PATTERNS = [
    "follow_ups",  # follow-up generation
    "follow-up questions",
    "Generate a concise",  # title generation
    "generate a brief 2-3 word",
    "Generate 1-3 broad tags",  # tags generation
    "tags_generation",
]

async def _intercept_task(
    request: Request,
    body: ChatCompletionRequest,
    workspace: str,
    model_id: str,
) -> dict | None:
    """Detect OWUI utility tasks and proxy them directly to the LLM.

    Detection priority:
      1. X-OpenWebUI-Task header (set via custom header template {{TASK}})
      2. Message content pattern matching (fallback)

    Returns a response dict if this is a utility task, or None if it should
    go through the normal agentic loop.
    """
    # 1. Header-based detection (reliable, requires OWUI config)
    task_header = request.headers.get(OWUI_TASK_HEADER, "").strip()
    if task_header:
        logger.info("[gateway] Detected utility task via header: %s", task_header)
        utility_target = await _resolve_utility_model(request, workspace, request.app.state)
        return await _proxy_to_llm(body, utility_target.connection, utility_target.runtime_model)

    # 2. Message content pattern matching (fallback)
    if not body.messages:
        return None

    last_msg = body.messages[-1]
    content = last_msg.get("content", "") if isinstance(last_msg, dict) else ""
    if not isinstance(content, str):
        return None

    is_utility = any(pattern in content for pattern in _UTILITY_PATTERNS)
    if not is_utility:
        return None

    logger.info("[gateway] Detected utility task via content matching")
    utility_target = await _resolve_utility_model(request, workspace, request.app.state)
    return await _proxy_to_llm(body, utility_target.connection, utility_target.runtime_model)


async def _proxy_to_llm(
    body: ChatCompletionRequest,
    connection: dict,
    runtime_model: str,
) -> dict:
    """Proxy a utility task directly to the underlying LLM."""
    from cptr.utils.ai import chat_completion
    from cptr.utils.config import _get_jwt_secret
    from cptr.utils.crypto import decrypt_key

    provider = connection["provider"]
    api_key = decrypt_key(connection.get("api_key", ""), _get_jwt_secret())
    base_url = connection.get("base_url") or None

    try:
        result = await chat_completion(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=runtime_model,
            messages=body.messages,
            max_tokens=200,
        )
    except Exception as e:
        logger.warning("[gateway] Utility task LLM call failed: %r", e)
        result = ""

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result or ""},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


async def _resolve_workspace(user_id: str, model_id: str) -> str:
    """Resolve 'cptr/basename' → workspace filesystem path."""
    if not model_id.startswith("cptr/"):
        raise HTTPException(400, f"Invalid model ID: {model_id}")

    target = model_id[5:]  # strip "cptr/"
    workspaces = await Workspace.get_by_user(user_id)

    # Exact basename match
    for ws in workspaces:
        if Path(ws.path).name == target:
            return ws.path

    # Disambiguated match (e.g., "my-project-2")
    name_counts: dict[str, int] = {}
    for ws in workspaces:
        name = Path(ws.path).name
        name_counts[name] = name_counts.get(name, 0) + 1

    seen: dict[str, int] = {}
    for ws in workspaces:
        basename = Path(ws.path).name
        if name_counts[basename] > 1:
            seen[basename] = seen.get(basename, 0) + 1
            if f"{basename}-{seen[basename]}" == target:
                return ws.path

    raise HTTPException(404, f"Workspace not found for model: {model_id}")


# ── Model connection resolution ──────────────────────────────


async def _resolve_model(request: Request, workspace: str, app_state=None):
    """Find a model target to use for the agentic loop.

    Priority:
      1. Gateway model selected in Settings > Gateway
      2. Workspace-specific model override (.cptr/model)
      3. Default model from Settings > Models (chat.default_model)
      4. First enabled connection's first model (with auto-discovery)

    Returns (target, full_model_id).
    """
    from cptr.utils.model_targets import first_api_model_target, resolve_model_target

    gateway_model = await Config.get("gateway.model")
    if isinstance(gateway_model, str) and gateway_model.strip():
        try:
            target = await resolve_model_target(gateway_model.strip(), app_state)
            return target, gateway_model.strip()
        except Exception:
            logger.warning(
                "[openai-compat] Gateway model '%s' not found, falling back",
                gateway_model,
            )

    # Check for workspace-specific model override
    model_override = None
    try:
        model = await Runtime.read_file(request, str(Path(workspace) / ".cptr" / "model"))
        model_override = str(model.get("content") or "").strip()
    except FileError:
        model_override = None

    if model_override:
        try:
            target = await resolve_model_target(model_override, app_state)
            return target, model_override
        except Exception:
            logger.warning(
                "[openai-compat] Workspace model override '%s' not found, falling back",
                model_override,
            )

    # Try the default model (set in Settings > Models)
    default_model = await Config.get("chat.default_model")
    if isinstance(default_model, str) and default_model.strip():
        try:
            target = await resolve_model_target(default_model.strip(), app_state)
            return target, default_model.strip()
        except Exception:
            logger.warning(
                "[openai-compat] Default model '%s' not found, falling back",
                default_model,
            )

    target = await first_api_model_target(app_state)
    return target, target.full_model_id


async def _resolve_utility_model(request: Request, workspace: str, app_state=None):
    """Resolve an API model for gateway utility tasks."""
    from cptr.utils.model_targets import (
        ApiModelTarget,
        first_api_model_target,
        resolve_model_target,
    )

    candidates: list[str] = []
    task_header = request.headers.get(OWUI_TASK_HEADER, "").strip()
    if task_header:
        from cptr.utils.utility_models import configured_utility_model

        if preferred_model := await configured_utility_model(task_header):
            candidates.append(preferred_model)

    gateway_model = await Config.get("gateway.model")
    if isinstance(gateway_model, str) and gateway_model.strip():
        candidates.append(gateway_model.strip())

    try:
        model = await Runtime.read_file(request, str(Path(workspace) / ".cptr" / "model"))
        override = str(model.get("content") or "").strip()
        if override:
            candidates.append(override)
    except FileError:
        pass

    default_model = await Config.get("chat.default_model")
    if isinstance(default_model, str) and default_model.strip():
        candidates.append(default_model.strip())

    for model_id in dict.fromkeys(candidates):
        try:
            target = await resolve_model_target(model_id, app_state)
            if isinstance(target, ApiModelTarget):
                return target
        except Exception:
            logger.warning("[openai-compat] Utility model '%s' not available", model_id)

    return await first_api_model_target(app_state)


# ── Session mapping ──────────────────────────────────────────


async def _ensure_chat(
    request: Request,
    user_id: str,
    workspace: str,
    client_chat_id: str | None,
    messages: list[dict],
    model_id: str,
) -> str:
    """Find an existing cptr chat for this client conversation, or create one."""

    if client_chat_id:
        # Search for a chat with this client chat ID in metadata
        from cptr.utils.db import get_db
        from sqlalchemy import select

        async with await get_db() as db:
            result = await db.execute(select(Chat).where(Chat.user_id == user_id))
            for chat in result.scalars():
                meta = chat.meta or {}
                if (
                    meta.get("client_chat_id") == client_chat_id
                    or meta.get("owui_chat_id") == client_chat_id
                ):
                    normalized = dict(meta)
                    normalized["workspace"] = workspace
                    params = (
                        normalized.get("params")
                        if isinstance(normalized.get("params"), dict)
                        else {}
                    )
                    normalized["params"] = {**params, "tool_approval_mode": "full"}
                    normalized["client_chat_id"] = client_chat_id
                    normalized["owui_chat_id"] = client_chat_id
                    if normalized != meta:
                        await Chat.update_meta(chat.id, normalized, now_ms())
                    return chat.id

    # Create a new chat
    title = "Open WebUI Chat"
    if messages:
        first_user = next(
            (message_text(m)[:50] for m in messages if m.get("role") == "user"),
            None,
        )
        if first_user:
            title = first_user.strip() or title

    meta = {
        "workspace": workspace,
        "params": {"tool_approval_mode": "full"},
    }
    if client_chat_id:
        meta["client_chat_id"] = client_chat_id
        meta["owui_chat_id"] = client_chat_id

    chat = await Chat.create(
        user_id=user_id,
        title=title[:100],
        meta=meta,
        created_at=now_ms(),
    )

    # Ensure .cptr/chats/ dir exists and create marker file
    await Runtime.write_file(
        request, str(Path(workspace) / ".cptr" / "chats" / f"{chat.id}.json"), "{}"
    )

    return chat.id


# ── API key admin endpoint ───────────────────────────────────


class CreateApiKeyRequest(BaseModel):
    name: str = "default"


@router.post("/keys")
async def create_api_key(request: Request, body: CreateApiKeyRequest):
    """Create a new API key (requires cookie auth, admin only)."""
    from cptr.utils.config import check_access

    client_host = request.client.host if request.client else "127.0.0.1"
    jwt_token = request.cookies.get("cptr_session")
    auth = check_access(client_host=client_host, jwt_token=jwt_token)
    if not auth or not auth.user_id:
        raise HTTPException(401, "Admin authentication required")

    raw = f"sk-cptr-{secrets.token_urlsafe(32)}"
    entry = {
        "id": str(uuid.uuid4()),
        "key_hash": _hash_key(raw),
        "user_id": auth.user_id,
        "name": body.name,
        "created_at": int(time.time()),
    }
    keys = await _get_api_keys()
    keys.append(entry)
    await _save_api_keys(keys)

    return {"key": raw, "id": entry["id"], "name": body.name}


@router.get("/keys")
async def list_api_keys(request: Request):
    """List API keys (masked). Requires cookie auth."""
    from cptr.utils.config import check_access

    client_host = request.client.host if request.client else "127.0.0.1"
    jwt_token = request.cookies.get("cptr_session")
    auth = check_access(client_host=client_host, jwt_token=jwt_token)
    if not auth or not auth.user_id:
        raise HTTPException(401, "Admin authentication required")

    keys = await _get_api_keys()
    return [
        {
            "id": k.get("id"),
            "name": k.get("name", ""),
            "created_at": k.get("created_at"),
        }
        for k in keys
    ]


@router.delete("/keys/{key_id}")
async def delete_api_key(request: Request, key_id: str):
    """Delete an API key. Requires cookie auth."""
    from cptr.utils.config import check_access

    client_host = request.client.host if request.client else "127.0.0.1"
    jwt_token = request.cookies.get("cptr_session")
    auth = check_access(client_host=client_host, jwt_token=jwt_token)
    if not auth or not auth.user_id:
        raise HTTPException(401, "Admin authentication required")

    keys = await _get_api_keys()
    filtered = [k for k in keys if k.get("id") != key_id]
    if len(filtered) == len(keys):
        raise HTTPException(404, "Key not found")
    await _save_api_keys(filtered)
    return {"ok": True}

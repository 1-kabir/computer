"""Chat router: CRUD for chats + model aggregation."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from cptr.models import Chat, ChatMessage, Config, is_internal_chat
from cptr.utils.config import check_access, now_ms, _get_jwt_secret
from cptr.utils.crypto import decrypt_key
from cptr.utils.db import get_db
from cptr.utils.chat_export import chat_directory
from cptr.utils.runtime import Runtime, FileError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chats", tags=["chats"])

COOKIE_NAME = "cptr_session"


def _get_user(request: Request) -> str:
    """Extract user_id from cookie, raise 401 if not authenticated."""
    token = request.cookies.get(COOKIE_NAME)
    client_host = request.client.host if request.client else "127.0.0.1"
    auth = check_access(client_host=client_host, jwt_token=token)
    if not auth or not auth.user_id:
        raise HTTPException(401, "authentication required")
    return auth.user_id


# ── List chats for a workspace or Home ──────────────────────


@router.get("")
async def list_chats(
    request: Request,
    workspace: str | None = Query(None, description="Workspace root path; omit for Home chats"),
    limit: int = Query(50, ge=1, le=200, description="Max chats to return"),
    offset: int = Query(0, ge=0, description="Number of chats to skip"),
    sort_by: str = Query("updated_at", description="Sort field: 'title' or 'updated_at'"),
    sort_dir: str = Query("desc", description="Sort direction: 'asc' or 'desc'"),
):
    """List chats by scanning the workspace or global chat directory.

    Returns chat metadata with relative folder paths for sidebar display.
    Supports pagination via limit/offset and sorting via sort_by/sort_dir.
    """
    user_id = _get_user(request)

    chats_dir = chat_directory(workspace)

    if not chats_dir.exists():
        return {"chats": [], "total": 0, "has_more": False}

    # Scan filesystem for chat files.
    def _scan_chat_files() -> list[dict]:
        chat_files = []
        for json_file in chats_dir.rglob("*.json"):
            chat_id = json_file.stem
            rel_folder = str(json_file.parent.relative_to(chats_dir))
            if rel_folder == ".":
                rel_folder = ""
            chat_files.append(
                {
                    "chat_id": chat_id,
                    "folder": rel_folder,
                    "path": json_file,
                }
            )
        return chat_files

    chat_files = await asyncio.to_thread(_scan_chat_files)

    if not chat_files:
        return {"chats": [], "total": 0, "has_more": False}

    # Batch-fetch from DB
    db_chats = await Chat.get_by_ids([chat_file["chat_id"] for chat_file in chat_files])
    chat_by_id = {chat.id: chat for chat in db_chats}

    # Detect which chats have a running task (in-memory check, no DB hit)
    from cptr.utils.chat_task import get_active_chat_ids

    active_ids = get_active_chat_ids()

    # Build response: only include chats owned by this user
    visible_chats = []
    for chat_file in chat_files:
        chat = chat_by_id.get(chat_file["chat_id"])
        if not chat or chat.user_id != user_id:
            continue

        chat_meta = chat.meta if isinstance(chat.meta, dict) else {}
        if is_internal_chat(chat_meta):
            continue
        if chat.meta is not None and not isinstance(chat.meta, dict):
            log.warning(
                "chat.list.bad_meta workspace=%r chat_id=%s title=%r meta_type=%s",
                workspace,
                chat.id,
                chat.title,
                type(chat.meta).__name__,
            )

        chat_file_updated_at = None
        listing_updated_at = chat.updated_at or chat.created_at or 0
        if not chat.updated_at:
            try:
                chat_file_data = json.loads(chat_file["path"].read_text())
                chat_file_updated_at = chat_file_data.get("updated_at")
                if isinstance(chat_file_updated_at, int):
                    listing_updated_at = max(listing_updated_at, chat_file_updated_at)
            except Exception as exc:
                log.warning(
                    "chat.list.file_read_error workspace=%r chat_id=%s path=%s error=%s: %s",
                    workspace,
                    chat.id,
                    chat_file["path"],
                    type(exc).__name__,
                    exc,
                )
            log.warning(
                "chat.list.timestamp_anomaly workspace=%r chat_id=%s title=%r db_updated_at=%r chat_file_updated_at=%r created_at=%r listing_updated_at=%r",
                workspace,
                chat.id,
                chat.title,
                chat.updated_at,
                chat_file_updated_at,
                chat.created_at,
                listing_updated_at,
            )

        stored_tasks = chat_meta.get("tasks")
        if stored_tasks is not None:
            malformed_task_count = 0
            unfinished_task_count = 0
            task_status_counts: dict[str, int] = {}
            if isinstance(stored_tasks, list):
                for task in stored_tasks:
                    if not isinstance(task, dict):
                        malformed_task_count += 1
                        continue
                    status = str(task.get("status", "pending"))
                    task_status_counts[status] = task_status_counts.get(status, 0) + 1
                    if status in {"pending", "in_progress"}:
                        unfinished_task_count += 1
                    if not task.get("id") or not task.get("content"):
                        malformed_task_count += 1
            else:
                malformed_task_count += 1
            if unfinished_task_count or malformed_task_count:
                log.warning(
                    "chat.list.task_state_anomaly workspace=%r chat_id=%s title=%r db_updated_at=%r chat_file_updated_at=%r current_message_id=%r task_type=%s task_count=%s unfinished=%d malformed=%d statuses=%r",
                    workspace,
                    chat.id,
                    chat.title,
                    chat.updated_at,
                    chat_file_updated_at,
                    chat.current_message_id,
                    type(stored_tasks).__name__,
                    len(stored_tasks) if isinstance(stored_tasks, list) else "n/a",
                    unfinished_task_count,
                    malformed_task_count,
                    task_status_counts,
                )

        chat_list_meta = dict(chat_meta)
        chat_list_meta.pop("tasks", None)
        visible_chats.append(
            {
                "id": chat.id,
                "title": chat.title,
                "summary": chat.summary,
                "folder": chat_file["folder"],
                "meta": chat_list_meta,
                "current_message_id": chat.current_message_id,
                "created_at": chat.created_at,
                "updated_at": listing_updated_at,
                "last_read_at": chat.last_read_at,
                "is_active": chat.id in active_ids,
            }
        )

    # Sort by requested field
    sort_field = sort_by if sort_by in ("title", "updated_at") else "updated_at"
    reverse = sort_dir != "asc"
    if sort_field == "updated_at":
        visible_chats.sort(
            key=lambda c: (
                not (
                    not c.get("is_active")
                    and (c["updated_at"] if isinstance(c["updated_at"], int) else 0) > 0
                    and (
                        c["last_read_at"] is None
                        or (c["updated_at"] if isinstance(c["updated_at"], int) else 0)
                        > c["last_read_at"]
                    )
                ),
                -(c["updated_at"] if isinstance(c["updated_at"], int) else 0)
                if reverse
                else c["updated_at"]
                if isinstance(c["updated_at"], int)
                else 0,
            ),
        )
    else:
        visible_chats.sort(key=lambda c: str(c["title"] or ""), reverse=reverse)
    total_chats = len(visible_chats)
    chat_page = visible_chats[offset : offset + limit]
    log.info(
        "chat.list workspace=%r chat_file_count=%d db_chat_count=%d visible_count=%d limit=%d offset=%d sort=%s/%s page_count=%d has_more=%s",
        workspace,
        len(chat_files),
        len(db_chats),
        total_chats,
        limit,
        offset,
        sort_field,
        sort_dir,
        len(chat_page),
        offset + limit < total_chats,
    )
    return {
        "chats": chat_page,
        "total": total_chats,
        "has_more": offset + limit < total_chats,
    }


# ── Models aggregation ──────────────────────────────────────
# NOTE: Must be declared before /{chat_id} to avoid 'models' being treated as a chat_id.


async def _get_connections() -> list[dict]:
    return await Config.get("chat.connections") or []


# ── Model cache (app.state) ─────────────────────────────────


async def _get_connection_models(conn: dict, app_state) -> list[str]:
    """Get models for a connection: from stored data, cache, or auto-discover."""
    stored = conn.get("data", {}).get("models")
    if stored:
        return stored

    conn_id = conn.get("id", "")
    cache = getattr(app_state, "MODELS", None)
    if cache is None:
        cache = {}
        app_state.MODELS = cache

    if conn_id in cache:
        return cache[conn_id]

    models = await _fetch_provider_models(conn)
    if models is not None:
        cache[conn_id] = models
        return models
    return cache.get(conn_id, [])


async def warm_model_cache(app_state) -> None:
    """Pre-fetch chat model sources before the application accepts requests."""
    connections = [c for c in await _get_connections() if c.get("enabled", True)]
    tasks = [_get_connection_models(conn, app_state) for conn in connections]

    from cptr.utils.agents.detection import get_available_agent_model_entries

    tasks.append(get_available_agent_model_entries(app_state))
    await asyncio.gather(*tasks)


def invalidate_model_cache(app_state):
    """Clear cached models. Call after connection create/update/delete."""
    app_state.MODELS = {}


@router.get("/models")
async def get_models(request: Request):
    """Aggregate available models across all connections.

    If a connection has data.models set, use those.
    Otherwise, call the provider's /models endpoint to discover available models.
    """
    _get_user(request)
    connections = [c for c in await _get_connections() if c.get("enabled", True)]
    models = []

    for conn in connections:
        model_ids = await _get_connection_models(conn, request.app.state)

        prefix = (conn.get("prefix_id") or "").strip()

        for model_id in model_ids or []:
            prefixed_id = f"{prefix}/{model_id}" if prefix else model_id
            models.append(
                {
                    "id": prefixed_id,
                    "name": model_id,
                    "provider": conn.get("provider", ""),
                    "connection_id": conn["id"],
                }
            )

    from cptr.utils.agents.detection import get_available_agent_model_entries

    models.extend(await get_available_agent_model_entries(request.app.state))

    default_model = await Config.get("chat.default_model")

    # Filter out inactive models
    chat_models_config = await Config.get("chat.models") or {}
    inactive = {k for k, v in chat_models_config.items() if v.get("is_active") is False}
    if inactive:
        models = [m for m in models if m["id"] not in inactive]

    return {"models": models, "default": default_model}


@router.get("/usage")
async def get_usage(request: Request, days: int | None = Query(None, ge=7, le=732)):
    """Aggregate personal chat usage for the settings Usage tab."""
    user_id = _get_user(request)

    async with await get_db() as db:
        chat_result = await db.execute(select(Chat).where(Chat.user_id == user_id))
        chats = [
            chat
            for chat in chat_result.scalars().all()
            if not is_internal_chat(chat.meta if isinstance(chat.meta, dict) else None)
        ]

        messages = []
        if chats:
            message_result = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.chat_id.in_([chat.id for chat in chats]))
                .order_by(ChatMessage.chat_id, ChatMessage.created_at)
            )
            messages = list(message_result.scalars().all())

    chat_ids_by_day: dict[str, set[str]] = defaultdict(set)
    day_stats: dict[str, dict] = defaultdict(lambda: {"tokens": 0, "messages": 0, "models": {}})
    model_stats: dict[str, dict] = defaultdict(lambda: {"messages": 0, "total_tokens": 0})
    tool_counts: dict[str, int] = defaultdict(int)
    models_used: set[str] = set()
    rows = []
    last_message_at_by_chat: dict[str, int] = {}
    active_seconds_by_chat: dict[str, int] = defaultdict(int)
    user_messages = 0
    assistant_messages = 0
    lifetime_tokens = 0

    for message in messages:
        created_at = int(message.created_at or 0)
        created_seconds = created_at / (
            1_000_000_000
            if created_at > 10_000_000_000_000
            else 1000
            if created_at > 10_000_000_000
            else 1
        )
        created_seconds = int(created_seconds)
        day_date = datetime.fromtimestamp(created_seconds, tz=timezone.utc).date()
        model_id = message.model or None
        usage = message.usage if isinstance(message.usage, dict) else {}
        try:
            tokens = max(
                0,
                int(
                    usage.get("total_tokens")
                    or (usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
                    + (usage.get("output_tokens") or usage.get("completion_tokens") or 0)
                    or 0
                ),
            )
        except (TypeError, ValueError):
            tokens = 0
        if tokens:
            lifetime_tokens += tokens

        if message.role == "assistant" and model_id:
            models_used.add(model_id)

        last_message_at = last_message_at_by_chat.get(message.chat_id)
        if last_message_at is not None:
            delta = created_seconds - last_message_at
            if 0 < delta <= 30 * 60:
                active_seconds_by_chat[message.chat_id] += delta
        last_message_at_by_chat[message.chat_id] = created_seconds
        rows.append((message, day_date, tokens))

    today = datetime.now(timezone.utc).date()
    days = days or 730
    start = today - timedelta(days=days - 1)
    period_start = int(
        datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp()
    )
    period_end = int(datetime.combine(today, datetime.max.time(), tzinfo=timezone.utc).timestamp())

    for message, day_date, tokens in rows:
        if day_date < start or day_date > today:
            continue

        day = day_date.isoformat()
        day_stats[day]["messages"] += 1
        chat_ids_by_day[day].add(message.chat_id)

        if message.role == "user":
            user_messages += 1
        elif message.role == "assistant":
            assistant_messages += 1

        model_id = message.model or None
        if tokens:
            day_stats[day]["tokens"] += tokens

        if message.role == "assistant" and model_id:
            day_models = day_stats[day]["models"]
            day_models[model_id] = day_models.get(model_id, 0) + 1
            model_stats[model_id]["messages"] += 1
            model_stats[model_id]["total_tokens"] += tokens

        for value in (message.output, message.meta):
            stack = [value]
            while stack:
                item = stack.pop()
                if isinstance(item, list):
                    stack.extend(item)
                    continue
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "")
                if "tool" in item_type or item_type in {"function_call", "function_call_output"}:
                    for name in (item.get("name"), item.get("tool_name")):
                        if isinstance(name, str) and name.strip() and len(name.strip()) <= 128:
                            tool_counts[name.strip()] += 1
                    if isinstance(item.get("function"), dict):
                        name = item["function"].get("name")
                        if isinstance(name, str) and name.strip() and len(name.strip()) <= 128:
                            tool_counts[name.strip()] += 1
                for key in ("tool_calls", "tools", "output", "meta"):
                    if key in item:
                        stack.append(item[key])

    heatmap = []
    cumulative_tokens = 0
    cumulative_messages = 0
    cumulative_chats = 0
    cumulative_models: dict[str, int] = {}
    weekly: dict[str, dict] = defaultdict(
        lambda: {"tokens": 0, "messages": 0, "chats": 0, "models": {}}
    )
    cumulative = []

    for offset in range((today - start).days + 1):
        current = start + timedelta(days=offset)
        key = current.isoformat()
        stats = day_stats.get(key, {"tokens": 0, "messages": 0, "models": {}})
        chats_for_day = chat_ids_by_day.get(key, set())
        entry = {
            "date": key,
            "tokens": stats["tokens"],
            "messages": stats["messages"],
            "chats": len(chats_for_day),
            "models": stats["models"],
        }
        heatmap.append(entry)

        week_key = (current - timedelta(days=current.weekday())).isoformat()
        weekly_entry = weekly[week_key]
        weekly_entry["tokens"] += stats["tokens"]
        weekly_entry["messages"] += stats["messages"]
        weekly_entry["chats"] += len(chats_for_day)
        for model_id, count in stats["models"].items():
            weekly_entry["models"][model_id] = weekly_entry["models"].get(model_id, 0) + count

        cumulative_tokens += stats["tokens"]
        cumulative_messages += stats["messages"]
        cumulative_chats += len(chats_for_day)
        for model_id, count in stats["models"].items():
            cumulative_models[model_id] = cumulative_models.get(model_id, 0) + count
        cumulative.append(
            {
                "date": key,
                "tokens": cumulative_tokens,
                "messages": cumulative_messages,
                "chats": cumulative_chats,
                "models": dict(cumulative_models),
            }
        )

    active_days = [day for day, stats in day_stats.items() if stats["messages"] > 0]
    active_dates = {date.fromisoformat(day) for day in active_days}
    current_streak = 0
    cursor = today
    while cursor in active_dates:
        current_streak += 1
        cursor -= timedelta(days=1)

    longest_streak = 0
    run = 0
    previous = None
    for active_date in sorted(active_dates):
        run = run + 1 if previous and active_date == previous + timedelta(days=1) else 1
        previous = active_date
        longest_streak = max(longest_streak, run)

    total_chats = len(chats)
    longest_chat_seconds = max(active_seconds_by_chat.values(), default=0)

    weekly_heatmap = [
        {
            "date": week,
            "tokens": stats["tokens"],
            "messages": stats["messages"],
            "chats": stats["chats"],
            "models": stats["models"],
        }
        for week, stats in sorted(weekly.items())
    ]
    total_messages = user_messages + assistant_messages

    return {
        "totals": {
            "lifetime_tokens": lifetime_tokens,
            "peak_daily_tokens": max((entry["tokens"] for entry in heatmap), default=0),
            "longest_chat_seconds": longest_chat_seconds,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "models_used": len(models_used),
            "user_messages": user_messages,
            "assistant_messages": assistant_messages,
            "messages": total_messages,
            "total_chats": total_chats,
        },
        "insights": {
            "average_tokens_per_chat": round(lifetime_tokens / total_chats, 1)
            if total_chats
            else 0,
            "average_messages_per_active_day": round(total_messages / len(active_days), 1)
            if active_days
            else 0,
            "user_message_share": round((user_messages / total_messages) * 100, 1)
            if total_messages
            else 0,
            "assistant_message_share": round((assistant_messages / total_messages) * 100, 1)
            if total_messages
            else 0,
        },
        "heatmap": heatmap,
        "weekly_heatmap": weekly_heatmap,
        "cumulative_heatmap": cumulative,
        "top_models": [
            {"model_id": model_id, **stats}
            for model_id, stats in sorted(
                model_stats.items(), key=lambda item: item[1]["messages"], reverse=True
            )[:5]
        ],
        "top_tools": [
            {"name": name, "count": count}
            for name, count in sorted(tool_counts.items(), key=lambda item: item[1], reverse=True)
        ][:5],
        "period": {
            "start_date": period_start,
            "end_date": period_end,
            "days": (today - start).days + 1,
        },
    }


async def _fetch_provider_models(conn: dict) -> list[str] | None:
    """Discover models from a provider's /models endpoint."""
    import httpx

    from cptr.utils.ai import _openrouter_headers

    try:
        secret = _get_jwt_secret()
        api_key = decrypt_key(conn.get("api_key", ""), secret) if conn.get("api_key") else None
        provider = conn.get("provider", "")
        base_url = conn.get("base_url")

        if provider == "anthropic":
            url = (base_url or "https://api.anthropic.com/v1") + "/models"
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    url,
                    headers={
                        "x-api-key": api_key or "",
                        "anthropic-version": "2023-06-01",
                        **_openrouter_headers(url),
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    models = [m["id"] for m in data.get("data", [])]
                    log.info("Auto-discovered %d models from %s", len(models), url)
                    return models
                else:
                    log.warning(
                        "Model auto-discovery failed for %s: HTTP %d",
                        url,
                        r.status_code,
                    )

        elif provider == "openai":
            url = (base_url or "https://api.openai.com/v1") + "/models"
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key or ''}",
                        **_openrouter_headers(url),
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    models = [m["id"] for m in data.get("data", [])]
                    log.info("Auto-discovered %d models from %s", len(models), url)
                    return models
                else:
                    log.warning(
                        "Model auto-discovery failed for %s: HTTP %d",
                        url,
                        r.status_code,
                    )

        else:
            log.warning("Unknown provider '%s', skipping model auto-discovery", provider)

    except Exception:
        log.exception("Model auto-discovery error for connection %s", conn.get("id", "?"))

    return None


# ── Get a chat with all messages ────────────────────────────


@router.get("/{chat_id}")
async def get_chat(
    chat_id: str,
    request: Request,
    model_id: str | None = Query(None, description="Model used for system prompt context"),
):
    """Get chat metadata + all messages."""
    user_id = _get_user(request)
    chat = await Chat.get_by_id(chat_id)
    if not chat or chat.user_id != user_id:
        raise HTTPException(404, "chat not found")

    messages = await ChatMessage.get_all_by_chat(chat_id)
    context_usage = await _get_chat_context_usage(request, chat, model_id)
    from cptr.utils.chat_task import get_active_chat_ids
    from cptr.utils.tools import _normalize_tasks

    tasks = _normalize_tasks((chat.meta or {}).get("tasks"))
    return {
        "chat": {
            "id": chat.id,
            "title": chat.title,
            "summary": chat.summary,
            "meta": chat.meta,
            "current_message_id": chat.current_message_id,
            "created_at": chat.created_at,
            "updated_at": chat.updated_at,
            "last_read_at": chat.last_read_at,
            "is_active": chat_id in get_active_chat_ids(),
        },
        "messages": [_message_dict(m) for m in messages],
        "tasks": tasks,
        "context_usage": context_usage,
    }


def _message_dict(m) -> dict:
    """Serialize a ChatMessage, overlaying live state if task is running."""
    from cptr.utils.chat_task import get_live_state

    d = {
        "id": m.id,
        "parent_id": m.parent_id,
        "role": m.role,
        "content": m.content,
        "model": m.model,
        "done": m.done,
        "output": m.output,
        "usage": m.usage,
        "meta": m.meta,
        "created_at": m.created_at,
    }
    live = get_live_state(m.id)
    if live:
        d["content"] = live["content"]
        d["output"] = live["output"]
        d["done"] = False
    return d


async def _get_context_leaf_message_id(chat) -> str | None:
    if chat.current_message_id:
        return chat.current_message_id
    messages = await ChatMessage.get_all_by_chat(chat.id)
    return messages[-1].id if messages else None


async def _get_chat_context_usage(
    request: Request, chat, model_id: str | None = None
) -> dict | None:
    message_id = await _get_context_leaf_message_id(chat)
    if not message_id:
        return None

    from cptr.utils.chat_task import _load_message_history, _load_system_prompt
    from cptr.utils.context import (
        build_context_usage,
        estimate_context_usage,
        estimate_messages_tokens,
        load_compact_token_threshold,
        usage_context_tokens,
    )

    messages, existing_summary = await _load_message_history(chat.id, message_id)
    workspace = (chat.meta or {}).get("workspace", "")
    model = model_id or await _infer_chat_model(chat.id)
    compact_token_threshold = await load_compact_token_threshold(model)
    system = await _load_system_prompt(request, workspace, model or "", user_id=chat.user_id)
    if existing_summary:
        system += f"\n\n[CONVERSATION SUMMARY]\n{existing_summary}"

    usage_checkpoint = await _get_latest_usage_checkpoint(chat.id, message_id)
    if usage_checkpoint:
        trailing_messages, usage = usage_checkpoint
        tokens = usage_context_tokens(usage)
        if tokens > 0:
            if trailing_messages:
                tokens += estimate_messages_tokens(
                    [{"role": m.role, "content": m.content or ""} for m in trailing_messages]
                )
            return build_context_usage(tokens, threshold=compact_token_threshold)

    return estimate_context_usage(messages, system, threshold=compact_token_threshold)


async def _infer_chat_model(chat_id: str) -> str:
    messages = await ChatMessage.get_all_by_chat(chat_id)
    for message in reversed(messages):
        if message.model:
            return message.model
    return await Config.get("chat.default_model") or ""


async def _get_latest_usage_checkpoint(
    chat_id: str, message_id: str
) -> tuple[list[ChatMessage], dict] | None:
    from cptr.utils.context import normalize_usage, usage_context_tokens

    all_msgs = await ChatMessage.get_all_by_chat(chat_id)
    msg_map = {m.id: m for m in all_msgs}
    chain = []
    cur = msg_map.get(message_id)
    while cur:
        chain.append(cur)
        cur = msg_map.get(cur.parent_id) if cur.parent_id else None
    chain.reverse()

    for index in range(len(chain) - 1, -1, -1):
        message = chain[index]
        if message.chat_summary:
            return None
        usage = normalize_usage(message.usage)
        if message.role == "assistant" and usage_context_tokens(usage):
            return chain[index + 1 :], usage
    return None


# ── Delete a chat ───────────────────────────────────────────


class UpdateChatRequest(BaseModel):
    title: str


class UpdateChatSettingsRequest(BaseModel):
    model_id: str
    params: dict = {}


@router.patch("/{chat_id}")
async def update_chat(request: Request, chat_id: str, body: UpdateChatRequest):
    """Rename a chat."""
    user_id = _get_user(request)
    chat = await Chat.get_by_id(chat_id)
    if not chat or chat.user_id != user_id:
        raise HTTPException(404, "chat not found")

    title = body.title.strip()
    if not title:
        raise HTTPException(422, "title cannot be empty")

    updated_at = now_ms()
    await Chat.update_title(chat_id, title, updated_at)
    workspace = (chat.meta or {}).get("workspace", "")
    from cptr.utils.chat_task import get_active_chat_ids
    from cptr.socket.main import emit_to_user

    unread_counts = await Chat.unread_counts_by_workspace(
        user_id, [workspace], get_active_chat_ids()
    )
    await emit_to_user(
        user_id,
        {
            "chat_id": chat_id,
            "title": title,
            "workspace": workspace,
            "updated_at": updated_at,
            "workspace_unread_count": unread_counts.get(workspace, 0),
        },
    )
    return {"ok": True, "title": title}


@router.patch("/{chat_id}/settings")
async def update_chat_settings(request: Request, chat_id: str, body: UpdateChatSettingsRequest):
    """Save composer settings for one chat."""
    user_id = _get_user(request)
    chat = await Chat.get_by_id(chat_id)
    if not chat or chat.user_id != user_id:
        raise HTTPException(404, "chat not found")

    meta = dict(chat.meta or {})
    meta["last_model"] = body.model_id
    meta["params"] = body.params
    await Chat.update_meta(chat_id, meta, now_ms())
    params = body.params if isinstance(body.params, dict) else {}
    approval_mode = params.get("tool_approval_mode")
    if approval_mode not in {"ask", "auto", "full"}:
        approval_mode = (
            "full"
            if "tool_approval_mode" not in params and params.get("auto_approve_tools")
            else "auto"
        )
    if approval_mode in {"auto", "full"} and chat.current_message_id:
        msg = await ChatMessage.get_by_id(chat.current_message_id)
        output = (msg.output or []) if msg else []
        pending_tool_approval = any(
            item.get("type") == "function_call"
            and item.get("status") == "pending"
            and item.get("name") != "ask_user"
            for item in output
        )
        if msg and msg.role == "assistant" and not msg.done and pending_tool_approval:
            from cptr.utils.chat_task import is_running, start_task

            if msg.model and not is_running(msg.id):
                try:
                    from cptr.utils.model_targets import resolve_model_target

                    target = await resolve_model_target(msg.model, request.app.state)
                    start_task(
                        request,
                        message_id=msg.id,
                        chat_id=chat.id,
                        user_id=chat.user_id,
                        workspace=meta.get("workspace", ""),
                        target=target,
                    )
                except Exception:
                    log.debug("[chat-settings] pending tool continuation failed", exc_info=True)
    return {"ok": True}


@router.delete("/{chat_id}")
async def delete_chat(request: Request, chat_id: str):
    """Delete a chat and all its messages."""
    user_id = _get_user(request)
    chat = await Chat.get_by_id(chat_id)
    if not chat or chat.user_id != user_id:
        raise HTTPException(404, "chat not found")

    # Remove the workspace chat file and durable internal children.
    workspace = chat.meta.get("workspace") if chat.meta else None
    chat_file = chat_directory(workspace) / f"{chat_id}.json"
    if workspace:
        try:
            await Runtime.delete_item(request, str(chat_file))
        except FileError:
            pass
    else:
        await asyncio.to_thread(chat_file.unlink, True)  # missing_ok=True
    for child in await Chat.get_internal_descendants(chat_id):
        child_workspace = (child.meta or {}).get("workspace")
        child_file = chat_directory(child_workspace) / f"{child.id}.json"
        if child_workspace:
            try:
                await Runtime.delete_item(request, str(child_file))
            except FileError:
                pass
        else:
            await asyncio.to_thread(child_file.unlink, True)

    await Chat.delete(chat_id)
    from cptr.socket.main import emit_to_user
    from cptr.utils.chat_task import get_active_chat_ids

    unread_counts = await Chat.unread_counts_by_workspace(
        user_id, [workspace or ""], get_active_chat_ids()
    )
    await emit_to_user(
        user_id,
        {
            "chat_id": chat_id,
            "workspace": workspace or "",
            "workspace_unread_count": unread_counts.get(workspace or "", 0),
        },
    )
    return {"ok": True}


class ForkChatRequest(BaseModel):
    message_id: Optional[str] = None


@router.post("/{chat_id}/fork")
async def fork_chat(request: Request, chat_id: str, body: ForkChatRequest | None = None):
    """Clone a completed chat branch through the selected message."""
    user_id = _get_user(request)
    chat = await Chat.get_by_id(chat_id)
    if not chat or chat.user_id != user_id:
        raise HTTPException(404, "chat not found")
    if await _chat_has_active_generation(chat_id):
        raise HTTPException(409, "wait for the current response to finish before forking")

    messages = await ChatMessage.get_all_by_chat(chat_id)
    if not messages:
        raise HTTPException(400, "chat has no messages to fork")
    message_by_id = {message.id: message for message in messages}
    source_message_id = (
        (body.message_id if body else None) or chat.current_message_id or messages[-1].id
    )
    source_message = message_by_id.get(source_message_id)
    if not source_message:
        raise HTTPException(404, "message not found")

    branch: list[ChatMessage] = []
    seen: set[str] = set()
    current: ChatMessage | None = source_message
    while current:
        if current.id in seen:
            raise HTTPException(400, "message branch contains a cycle")
        seen.add(current.id)
        branch.append(current)
        current = message_by_id.get(current.parent_id) if current.parent_id else None
    branch.reverse()

    meta = deepcopy(chat.meta or {})
    meta["forked_from"] = chat.id
    meta["forked_from_message_id"] = source_message.id
    now = now_ms()
    fork = await Chat.create(
        user_id=user_id,
        title=f"{chat.title} (fork)",
        meta=meta,
        created_at=now,
    )
    if chat.summary:
        await Chat.update_summary(fork.id, chat.summary, now)

    id_map: dict[str, str] = {}
    for message in branch:
        copied = await ChatMessage.create(
            chat_id=fork.id,
            role=message.role,
            content=message.content,
            parent_id=id_map.get(message.parent_id) if message.parent_id else None,
            model=message.model,
            done=message.done,
            output=deepcopy(message.output),
            usage=deepcopy(message.usage),
            meta=deepcopy(message.meta),
            created_at=message.created_at,
        )
        id_map[message.id] = copied.id
        if message.chat_summary:
            await ChatMessage.update(copied.id, chat_summary=message.chat_summary)

    await Chat.update_current_message(fork.id, id_map[source_message.id], now)

    from cptr.utils.chat_export import export_chat_to_file

    await export_chat_to_file(request, fork.id)
    return {"ok": True, "chat_id": fork.id}


# ── Send a message ──────────────────────────────────────────


class SendMessageRequest(BaseModel):
    content: str = ""
    model_id: str
    workspace: Optional[str] = None
    chat_id: Optional[str] = None
    parent_id: Optional[str] = None
    regeneration_prompt: Optional[str] = None
    files: List[dict] = []
    params: dict = {}


class CompactRequest(BaseModel):
    model_id: Optional[str] = None


@router.post("")
async def send_message(request: Request, body: SendMessageRequest):
    """Send a message. Omit chat_id to create a new chat.
    Returns: { chat_id, message_id, queued? }
    """
    user_id = _get_user(request)

    from cptr.utils.model_targets import resolve_model_target

    target = await resolve_model_target(body.model_id, request.app.state)

    # Create or fetch chat
    if body.chat_id:
        chat = await Chat.get_by_id(body.chat_id)
        if not chat or chat.user_id != user_id:
            raise HTTPException(404, "chat not found")
        workspace = (chat.meta or {}).get("workspace") or None
        # Sync params into chat meta
        if chat.meta is None:
            chat.meta = {}
        if chat.meta.get("params") != body.params or chat.meta.get("last_model") != body.model_id:
            chat.meta["params"] = body.params
            chat.meta["last_model"] = body.model_id
            await Chat.update_meta(chat.id, chat.meta)
    else:
        workspace = body.workspace or None
        title = body.content[:50].strip() or "New Chat"
        meta = {
            "params": body.params,
            "last_model": body.model_id,
        }
        if workspace:
            meta["workspace"] = workspace
        chat = await Chat.create(
            user_id=user_id,
            title=title,
            meta=meta,
            created_at=now_ms(),
        )
        if workspace:
            await Runtime.write_file(
                request,
                str(chat_directory(workspace) / f"{chat.id}.json"),
                "{}",
            )
        else:
            chats_dir = chat_directory(workspace)
            await asyncio.to_thread(lambda: chats_dir.mkdir(parents=True, exist_ok=True))

    from cptr.utils.chat_task import (
        get_pending_input_lock,
        process_pending_chat_inputs,
        start_task,
    )

    queued_msg = None
    user_msg = None
    assistant_msg = None
    async with get_pending_input_lock(chat.id):
        # Queue only follow-ups attached to their own unfinished assistant branch.
        # Other branches in the same chat are independent and can run concurrently.
        parent_msg = await ChatMessage.get_by_id(body.parent_id) if body.parent_id else None
        if body.chat_id and _should_queue_for_parent(parent_msg):
            queued_meta = {"queued": True}
            if body.files:
                queued_meta["files"] = body.files
            queued_msg = await ChatMessage.create(
                chat_id=chat.id,
                role="user",
                content=body.content,
                parent_id=body.parent_id,
                model=body.model_id,
                meta=queued_meta,
                created_at=now_ms(),
            )
        else:
            if parent_msg and parent_msg.role == "user":
                # Regeneration: create assistant as sibling of existing response.
                assistant_parent = body.parent_id
            else:
                user_meta = {"files": body.files} if body.files else None
                user_msg = await ChatMessage.create(
                    chat_id=chat.id,
                    role="user",
                    content=body.content,
                    parent_id=body.parent_id,
                    meta=user_meta,
                    created_at=now_ms(),
                )
                assistant_parent = user_msg.id

            assistant_msg = await ChatMessage.create(
                chat_id=chat.id,
                role="assistant",
                content="",
                parent_id=assistant_parent,
                model=body.model_id,
                done=False,
                created_at=now_ms(),
            )

            # Update chat pointer to new leaf while still holding the input lock.
            await Chat.update_current_message(chat.id, assistant_msg.id, now_ms())

    submitted_message = queued_msg or user_msg
    if submitted_message:
        from cptr.events import EVENTS, publish_event

        await publish_event(
            EVENTS.CHAT_USER_MESSAGE,
            actor={"id": user_id},
            subject_id=chat.id,
            subject_type="chat",
            source="chat",
        )

    if queued_msg:
        await process_pending_chat_inputs(request, chat.id, user_id, workspace or "")
        return {"chat_id": chat.id, "message_id": queued_msg.id, "queued": True}

    if not assistant_msg:
        raise HTTPException(500, "failed to create assistant message")

    # Export JSON immediately so list_chats discovers it right away
    from cptr.utils.chat_export import export_chat_to_file

    await export_chat_to_file(request, chat.id)

    start_task(
        request,
        message_id=assistant_msg.id,
        chat_id=chat.id,
        user_id=user_id,
        workspace=workspace or "",
        regeneration_prompt=body.regeneration_prompt,
        target=target,
    )

    # Return the created messages so the frontend can append them
    # directly — no separate GET needed, no client-side construction.
    resp: dict = {
        "chat_id": chat.id,
        "message_id": assistant_msg.id,
        "assistant_message": _message_dict(assistant_msg),
    }
    if parent_msg is None or parent_msg.role != "user":
        resp["user_message"] = _message_dict(user_msg)
    return resp


# ── Manual context compaction ───────────────────────────────


@router.post("/{chat_id}/compact")
async def compact_chat(request: Request, chat_id: str, body: CompactRequest):
    """Summarize older active-branch messages and store a compaction checkpoint."""
    user_id = _get_user(request)
    chat = await Chat.get_by_id(chat_id)
    if not chat or chat.user_id != user_id:
        raise HTTPException(404, "chat not found")
    if await _chat_has_active_generation(chat_id):
        raise HTTPException(409, "wait for the current response to finish before compacting")
    model_id = body.model_id or await _infer_chat_model(chat_id)
    if not model_id:
        raise HTTPException(400, "choose a model before compacting")

    message_id = await _get_context_leaf_message_id(chat)
    if not message_id:
        return {"ok": True, "compacted": False, "reason": "empty", "context_usage": None}
    current_msg = await ChatMessage.get_by_id(message_id)
    if not current_msg or not current_msg.parent_id:
        usage = await _get_chat_context_usage(request, chat, model_id)
        return {"ok": True, "compacted": False, "reason": "too_short", "context_usage": usage}

    from cptr.utils.model_targets import (
        ApiModelTarget,
        first_api_model_target,
        resolve_model_target,
    )

    target = await resolve_model_target(model_id, request.app.state)
    if not isinstance(target, ApiModelTarget):
        target = await first_api_model_target(request.app.state)

    from cptr.utils.chat_task import _load_message_history, _summary_checkpoint_message_id
    from cptr.utils.summarize import summarize_messages

    messages, existing_summary = await _load_message_history(chat_id, current_msg.parent_id)
    compacted_messages = messages[:-1]
    keep_zone = messages[-1:]
    if not compacted_messages or not keep_zone:
        usage = await _get_chat_context_usage(request, chat, model_id)
        return {"ok": True, "compacted": False, "reason": "too_short", "context_usage": usage}

    summary = await summarize_messages(
        compacted_messages,
        existing_summary,
        target.connection,
        target.runtime_model,
    )
    checkpoint_message_id = _summary_checkpoint_message_id(keep_zone, message_id)
    await ChatMessage.update(checkpoint_message_id, chat_summary=summary)

    from cptr.utils.chat_export import export_chat_to_file

    await export_chat_to_file(request, chat_id)
    usage = await _get_chat_context_usage(request, chat, model_id)
    return {
        "ok": True,
        "compacted": True,
        "dropped_messages": len(messages),
        "kept_messages": 1,
        "summary_chars": len(summary),
        "context_usage": usage,
    }


# ── Resolve a pending tool call ─────────────────────────────


class ResolveRequest(BaseModel):
    call_id: str
    action: Literal["approve", "reject", "answer"] = "approve"
    answers: dict[str, str] | None = None
    timed_out: bool = False


class AskUserNotPendingError(ValueError):
    pass


async def resolve_ask_user(
    app,
    chat_id: str,
    message_id: str,
    call_id: str,
    answers: dict[str, str] | None,
    timed_out: bool,
):
    chat = await Chat.get_by_id(chat_id)
    msg = await ChatMessage.get_by_id(message_id)
    if not chat or not msg or msg.chat_id != chat_id:
        raise AskUserNotPendingError("ask_user request is no longer pending")
    output = list(msg.output or [])
    call = next(
        (
            item
            for item in output
            if item.get("type") == "function_call"
            and item.get("name") == "ask_user"
            and item.get("call_id") == call_id
            and item.get("status") == "pending"
        ),
        None,
    )
    if not call:
        raise AskUserNotPendingError("ask_user request is no longer pending")
    if timed_out and now_ms() < int(call.get("expires_at") or 0):
        raise AskUserNotPendingError("ask_user request has not timed out")

    from cptr.utils.chat_task import ask_user_answers, start_task
    from cptr.utils.model_targets import resolve_model_target
    from cptr.socket.main import emit_to_user

    result = ask_user_answers(call.get("arguments") or {}, None if timed_out else answers)
    if timed_out:
        result["timed_out"] = True
    call["status"] = "completed"
    call["timed_out"] = timed_out
    output.append(
        {"type": "function_call_output", "call_id": call_id, "output": json.dumps(result)}
    )
    await ChatMessage.update(message_id, output=output, done=False)
    await emit_to_user(chat.user_id, {"chat_id": chat_id, "message_id": message_id, "output": call})
    await emit_to_user(
        chat.user_id, {"chat_id": chat_id, "message_id": message_id, "output": output[-1]}
    )
    target = await resolve_model_target(msg.model or "", getattr(app, "state", None))
    task_request = None
    if app is not None:
        try:
            from cptr.utils.identity import internal_request_for_user

            task_request = await internal_request_for_user(app, chat.user_id)
        except Exception:
            log.debug("[ask_user] internal request creation failed", exc_info=True)
    start_task(
        task_request,
        message_id=message_id,
        chat_id=chat_id,
        user_id=chat.user_id,
        workspace=(chat.meta or {}).get("workspace", ""),
        target=target,
    )


async def resolve_pending_tool_call(
    request: Request, chat_id: str, message_id: str, body: ResolveRequest
):
    """Resolve a pending function_call, then continue the assistant message."""
    user_id = _get_user(request)
    chat = await Chat.get_by_id(chat_id)
    if not chat or chat.user_id != user_id:
        raise HTTPException(404, "chat not found")

    msg = await ChatMessage.get_by_id(message_id)
    if not msg or msg.chat_id != chat_id:
        raise HTTPException(404, "message not found")

    # Find pending tool call
    output = msg.output or []
    call = None
    for item in output:
        if (
            item.get("type") == "function_call"
            and item.get("call_id") == body.call_id
            and item.get("status") == "pending"
        ):
            call = item
            break

    if not call:
        raise HTTPException(400, "no pending tool call with that call_id")

    if body.action == "answer":
        if call.get("name") != "ask_user":
            raise HTTPException(400, "tool call does not accept answers")
        if body.answers is None and not body.timed_out:
            raise HTTPException(400, "answers are required for ask_user")
        try:
            await resolve_ask_user(
                request.app,
                chat_id,
                message_id,
                body.call_id,
                body.answers,
                body.timed_out,
            )
        except AskUserNotPendingError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True}

    if body.action == "approve" and call.get("name") == "ask_user":
        raise HTTPException(400, "ask_user requires answer or reject")

    model_id = msg.model or ""
    workspace = chat.meta.get("workspace", "") if chat.meta else ""
    from cptr.socket.main import emit_to_user

    if body.action == "approve":
        call["approved"] = True
        call["status"] = "queued"
        await ChatMessage.update(message_id, output=output, done=False)

        await emit_to_user(user_id, {"chat_id": chat_id, "message_id": message_id, "output": call})
    else:
        call["status"] = "rejected"
        result_item = {
            "type": "function_call_output",
            "call_id": body.call_id,
            "output": "Error: tool call rejected by user.",
        }
        output.append(result_item)
        await ChatMessage.update(message_id, output=output, done=False)
        await emit_to_user(user_id, {"chat_id": chat_id, "message_id": message_id, "output": call})
        await emit_to_user(
            user_id,
            {"chat_id": chat_id, "message_id": message_id, "output": result_item},
        )

    # Resolve model target and continue the saved tool-call queue.
    from cptr.utils.model_targets import resolve_model_target

    target = await resolve_model_target(model_id, request.app.state)

    from cptr.utils.chat_task import start_task

    start_task(
        request,
        message_id=message_id,
        chat_id=chat_id,
        user_id=user_id,
        workspace=workspace,
        target=target,
    )

    return {"ok": True}


@router.post("/{chat_id}/messages/{message_id}/resolve")
async def resolve_tool(request: Request, chat_id: str, message_id: str, body: ResolveRequest):
    return await resolve_pending_tool_call(request, chat_id, message_id, body)


# ── Cancel a running task ───────────────────────────────────


@router.post("/{chat_id}/messages/{message_id}/cancel")
async def cancel_task_endpoint(request: Request, chat_id: str, message_id: str):
    """Cancel running task, preserve partial output."""
    user_id = _get_user(request)
    chat = await Chat.get_by_id(chat_id)
    if not chat or chat.user_id != user_id:
        raise HTTPException(404, "chat not found")

    from cptr.utils.chat_task import cancel_task

    found = await cancel_task(message_id)

    if not found:
        # Task already exited (e.g., waiting for tool approval) but message
        # may still be marked done=False, force-finalize it.
        msg = await ChatMessage.get_by_id(message_id)
        if msg and not msg.done:
            output = msg.output or []
            for item in output:
                if item.get("type") == "function_call" and item.get("status") == "pending":
                    item["status"] = "rejected"
            await ChatMessage.update(message_id, output=output, done=True)

    # Process pending inputs since this chat may now be idle.
    from cptr.utils.chat_task import process_pending_chat_inputs

    workspace = chat.meta.get("workspace", "") if chat.meta else ""
    await process_pending_chat_inputs(request, chat_id, user_id, workspace)

    return {"ok": True}


# ── Update current branch pointer ──────────────────────────


class UpdateCurrentRequest(BaseModel):
    message_id: str


@router.post("/{chat_id}/current")
async def update_current(request: Request, chat_id: str, body: UpdateCurrentRequest):
    """Set current_message_id (the active branch leaf)."""
    user_id = _get_user(request)
    chat = await Chat.get_by_id(chat_id)
    if not chat or chat.user_id != user_id:
        raise HTTPException(404, "chat not found")
    await Chat.update_current_message(chat_id, body.message_id, now_ms())
    return {"ok": True}


# ── Update message content / output ────────────────────────


class UpdateMessageRequest(BaseModel):
    content: Optional[str] = None
    output: Optional[list] = None


@router.patch("/{chat_id}/messages/{message_id}")
async def update_message(
    chat_id: str, message_id: str, body: UpdateMessageRequest, request: Request
):
    """Edit a message's content or output in-place."""
    user_id = _get_user(request)
    chat = await Chat.get_by_id(chat_id)
    if not chat or chat.user_id != user_id:
        raise HTTPException(404, "chat not found")

    updates = {}
    if body.content is not None:
        updates["content"] = body.content
    if body.output is not None:
        updates["output"] = body.output
    if updates:
        await ChatMessage.update(message_id, **updates)
    return {"ok": True}


# ── Create message directly (no task) ──────────────────────


class CreateMessageRequest(BaseModel):
    parent_id: Optional[str] = None
    role: str
    content: str
    output: Optional[list] = None


@router.post("/{chat_id}/messages")
async def create_message_endpoint(request: Request, chat_id: str, body: CreateMessageRequest):
    """Create a message without starting a task (for Save As Copy)."""
    user_id = _get_user(request)
    chat = await Chat.get_by_id(chat_id)
    if not chat or chat.user_id != user_id:
        raise HTTPException(404, "chat not found")

    msg = await ChatMessage.create(
        chat_id=chat_id,
        role=body.role,
        content=body.content,
        parent_id=body.parent_id,
        output=body.output,
        done=True,
        created_at=now_ms(),
    )
    await Chat.update_current_message(chat_id, msg.id, now_ms())
    return {"ok": True, "message_id": msg.id}


async def _chat_has_active_generation(chat_id: str) -> bool:
    """Check if any assistant message in this chat has done=False."""
    messages = await ChatMessage.get_all_by_chat(chat_id)
    return any(m.role == "assistant" and not m.done for m in messages)


def _should_queue_for_parent(parent_msg: ChatMessage | None) -> bool:
    return bool(parent_msg and parent_msg.role == "assistant" and not parent_msg.done)


# ── Queue management ───────────────────────────────────────


@router.post("/{chat_id}/queue/{message_id}/send")
async def queue_send_now(request: Request, chat_id: str, message_id: str):
    """Dequeue one message and send it immediately on its stored branch."""
    user_id = _get_user(request)
    chat = await Chat.get_by_id(chat_id)
    if not chat or chat.user_id != user_id:
        raise HTTPException(404, "chat not found")

    msg = await ChatMessage.get_by_id(message_id)
    if not msg or msg.chat_id != chat_id:
        raise HTTPException(404, "message not found")
    if not (msg.meta and msg.meta.get("queued")):
        raise HTTPException(400, "message is not queued")

    from cptr.utils.chat_task import cancel_task, get_pending_input_lock, start_task

    async with get_pending_input_lock(chat_id):
        all_msgs = await ChatMessage.get_all_by_chat(chat_id)
        parent_msg = next((m for m in all_msgs if m.id == msg.parent_id), None)
        if parent_msg and parent_msg.role == "assistant" and not parent_msg.done:
            await cancel_task(parent_msg.id)
            await ChatMessage.update(parent_msg.id, done=True)
            parent_msg.done = True

        # Clear queued flag on this message
        meta = dict(msg.meta or {})
        meta.pop("queued", None)
        await ChatMessage.update(message_id, meta=meta or None)

        # Resolve model target and start task
        model_id = msg.model or (chat.meta or {}).get("last_model", "")
        if not model_id:
            model_id = next((m.model for m in reversed(all_msgs) if m.model), "")
        from cptr.utils.model_targets import resolve_model_target

        target = await resolve_model_target(model_id, request.app.state)
        workspace = (chat.meta or {}).get("workspace", "")
        meta_for_model = dict(chat.meta or {})
        if meta_for_model.get("last_model") != model_id:
            meta_for_model["last_model"] = model_id
            await Chat.update_meta(chat.id, meta_for_model, now_ms())

        assistant_msg = await ChatMessage.create(
            chat_id=chat_id,
            role="assistant",
            content="",
            parent_id=message_id,
            model=model_id,
            done=False,
            created_at=now_ms(),
        )
        await Chat.update_current_message(chat_id, assistant_msg.id, now_ms())

    start_task(
        request,
        message_id=assistant_msg.id,
        chat_id=chat_id,
        user_id=user_id,
        workspace=workspace,
        target=target,
    )
    return {"ok": True, "chat_id": chat_id, "message_id": assistant_msg.id}


@router.delete("/{chat_id}/queue/{message_id}")
async def queue_delete(request: Request, chat_id: str, message_id: str):
    """Remove a queued message."""
    user_id = _get_user(request)
    chat = await Chat.get_by_id(chat_id)
    if not chat or chat.user_id != user_id:
        raise HTTPException(404, "chat not found")

    msg = await ChatMessage.get_by_id(message_id)
    if not msg or msg.chat_id != chat_id:
        raise HTTPException(404, "message not found")
    if not (msg.meta and msg.meta.get("queued")):
        raise HTTPException(400, "message is not queued")

    await ChatMessage.delete(message_id)
    return {"ok": True}


async def _resolve_connection(model_id: str, app_state=None) -> tuple[dict, str]:
    """Find connection for model.
    'openrouter/gpt-4o' → connection with prefix_id='openrouter', runtime model='gpt-4o'
    'claude-sonnet-4-20250514' → scan all connections for match
    Raises 400 if not found.
    """
    connections = [c for c in await _get_connections() if c.get("enabled", True)]
    if not connections:
        raise HTTPException(400, "no connections configured")

    # Try prefix match first
    if "/" in model_id:
        prefix, runtime_model = model_id.split("/", 1)
        for conn in connections:
            if (conn.get("prefix_id") or "").strip() == prefix:
                return conn, runtime_model

    # Scan all connections using cache
    for conn in connections:
        if app_state:
            model_ids = await _get_connection_models(conn, app_state)
        else:
            model_ids = conn.get("data", {}).get("models") or await _fetch_provider_models(conn)
        prefix = (conn.get("prefix_id") or "").strip()
        for mid in model_ids:
            prefixed = f"{prefix}/{mid}" if prefix else mid
            if prefixed == model_id or mid == model_id:
                return conn, mid

    raise HTTPException(400, f"no connection found for model: {model_id}")

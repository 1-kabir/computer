"""Unified search: chats + files in one request."""

from __future__ import annotations

import asyncio
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from cptr.models import Chat, is_internal_chat
from cptr.utils.runtime import Runtime
from cptr.utils.runtime import FileError

router = APIRouter(prefix="/api/search", tags=["search"])

COOKIE_NAME = "cptr_session"


def _get_user(request: Request) -> str:
    """Extract user_id from cookie, raise 401 if not authenticated."""
    from cptr.utils.config import check_access

    token = request.cookies.get(COOKIE_NAME)
    client_host = request.client.host if request.client else "127.0.0.1"
    auth = check_access(client_host=client_host, jwt_token=token)
    if not auth or not auth.user_id:
        raise HTTPException(401, "authentication required")
    return auth.user_id


@router.get("")
async def unified_search(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query"),
    workspaces: List[str] = Query(default=[], description="Workspace paths to search files in"),
    workspace: Optional[str] = Query(None, description="Scope to one workspace path"),
    chat_limit: int = Query(10, ge=0, le=50),
    file_limit: int = Query(10, ge=0, le=50),
):
    """Search chats by title/content + files by name across workspaces.

    Returns: { chats: [...], files: [...] }
    """
    user_id = _get_user(request)

    # Determine which workspaces to search for files
    ws_paths = [workspace] if workspace else workspaces

    # Run chat search and file search concurrently
    chat_task = Chat.search_by_text(user_id, q, chat_limit, workspace=workspace)

    async def _search_files() -> list[dict]:
        if not ws_paths or file_limit <= 0:
            return []

        all_results: list[dict] = []
        per_ws_limit = max(3, file_limit // max(len(ws_paths), 1))

        for ws in ws_paths:
            try:
                results = await Runtime.search_files(request, q, ws, per_ws_limit)
            except FileError:
                continue
            for r in results["results"]:
                all_results.append(
                    {
                        "path": r["path"],
                        "name": r["name"],
                        "type": r["type"],
                        "workspace": ws,
                    }
                )

        # Deduplicate by path and cap at file_limit
        seen = set()
        deduped = []
        for f in all_results:
            if f["path"] not in seen:
                seen.add(f["path"])
                deduped.append(f)
        return deduped[:file_limit]

    chat_results, file_results = await asyncio.gather(chat_task, _search_files())

    # Build chat response (strip meta, add workspace)
    chats_out = []
    for c in chat_results:
        chats_out.append(
            {
                "id": c["id"],
                "title": c["title"],
                "summary": c.get("summary"),
                "workspace": (c.get("meta") or {}).get("workspace", ""),
                "updated_at": c["updated_at"],
                "created_at": c["created_at"],
                "match_type": c.get("match_type", "title"),
                "snippet": c.get("snippet"),
                "matched_message_id": c.get("matched_message_id"),
                "matched_role": c.get("matched_role"),
            }
        )

    return {"chats": chats_out, "files": file_results}


@router.get("/recent")
async def recent_chats(
    request: Request,
    limit: int = Query(9, ge=1, le=20),
    workspace: Optional[str] = Query(None, description="Scope to one workspace path"),
):
    """Return most recent chats, optionally scoped to one workspace."""
    user_id = _get_user(request)

    from sqlalchemy import select
    from cptr.utils.db import get_db

    async with await get_db() as db:
        result = await db.execute(
            select(Chat).where(Chat.user_id == user_id).order_by(Chat.updated_at.desc())
        )
        rows = list(result.scalars().all())
    rows = [
        c
        for c in rows
        if not is_internal_chat(c.meta)
        and (workspace is None or (c.meta or {}).get("workspace") == workspace)
    ][:limit]

    return {
        "chats": [
            {
                "id": c.id,
                "title": c.title,
                "summary": c.summary,
                "workspace": (c.meta or {}).get("workspace", ""),
                "updated_at": c.updated_at,
                "created_at": c.created_at,
                "match_type": "recent",
                "snippet": None,
            }
            for c in rows
        ]
    }

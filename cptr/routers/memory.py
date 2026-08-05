"""Managed memory API."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from cptr.utils.config import AuthResult, check_access
from cptr.utils.memory import (
    list_memory_files_state,
    remember,
    read_memory_file_state,
    read_memory_state,
    review_memory_vault,
    save_memory_settings,
    search_memory_state,
)

router = APIRouter(prefix="/api/memory", tags=["memory"])
COOKIE_NAME = "cptr_session"


def _get_auth(request: Request) -> AuthResult:
    token = request.cookies.get(COOKIE_NAME)
    client_host = request.client.host if request.client else "127.0.0.1"
    auth = check_access(client_host=client_host, jwt_token=token)
    if not auth or not auth.user_id:
        raise HTTPException(401, "authentication required")
    return auth


def _get_user(request: Request) -> str:
    return _get_auth(request).user_id or ""


def _require_admin(request: Request) -> AuthResult:
    auth = _get_auth(request)
    if auth.role != "admin":
        raise HTTPException(403, "admin required")
    return auth


class MemorySettingsRequest(BaseModel):
    settings: dict[str, Any]


class MemoryUpdateRequest(BaseModel):
    scope: Literal["user", "workspace"]
    operations: list[dict[str, Any]]
    workspace: str = ""


class MemorySearchRequest(BaseModel):
    query: str = ""
    scope: Literal["user", "workspace", "both"] = "both"
    workspace: str = ""
    path: str | None = None
    memory_id: str | None = None
    expand_links: bool = False
    include_trash: bool = False
    limit: int = 8


class MemoryReviewRequest(BaseModel):
    workspace: str = ""


@router.get("")
async def get_memory(request: Request, workspace: str = Query("")):
    user_id = _get_user(request)
    return await read_memory_state(request, user_id, workspace)


@router.put("/config")
async def put_memory_settings(request: Request, body: MemorySettingsRequest):
    _require_admin(request)
    return {"settings": await save_memory_settings(body.settings)}


@router.post("/update")
async def update_memory(request: Request, body: MemoryUpdateRequest):
    user_id = _get_user(request)
    return await remember(
        request,
        user_id=user_id,
        workspace=body.workspace,
        scope=body.scope,
        operations=body.operations,
    )


@router.post("/search")
async def search_memory(request: Request, body: MemorySearchRequest):
    user_id = _get_user(request)
    return await search_memory_state(
        user_id=user_id,
        workspace=body.workspace,
        query=body.query,
        scope=body.scope,
        path=body.path,
        memory_id=body.memory_id,
        expand_links=body.expand_links,
        include_trash=body.include_trash,
        limit=body.limit,
    )


@router.get("/files")
async def list_memory_files(
    request: Request,
    workspace: str = Query(""),
    scope: Literal["user", "workspace", "both"] = Query("both"),
    q: str = Query(""),
    limit: int = Query(100),
    include_trash: bool = Query(False),
):
    user_id = _get_user(request)
    return await list_memory_files_state(
        user_id=user_id,
        workspace=workspace,
        scope=scope,
        query=q,
        limit=limit,
        include_trash=include_trash,
    )


@router.get("/file")
async def get_memory_file(
    request: Request,
    workspace: str = Query(""),
    scope: Literal["user", "workspace"] = Query("user"),
    path: str = Query(...),
):
    user_id = _get_user(request)
    try:
        return await read_memory_file_state(
            request,
            user_id=user_id,
            workspace=workspace,
            scope=scope,
            path=path,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/review")
async def review_memory(request: Request, body: MemoryReviewRequest):
    user_id = _get_user(request)
    return await review_memory_vault(request, user_id, body.workspace)

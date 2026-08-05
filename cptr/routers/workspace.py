"""Endpoints for browsing directories and managing files within the workspace.

Security: these endpoints accept arbitrary absolute paths with no sandboxing.
Authenticated users get full filesystem access. This is intentional for the
single-user model. Not safe for shared or public instances. See README.md.
"""

from __future__ import annotations

import io
import mimetypes
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File as FastAPIFile, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from cptr.utils.identity import IdentityUnavailable, expand_user_path, identity_for_request
from cptr.utils.runtime import Runtime
from cptr.utils.runtime import MATCH_PAGE_SIZE, FileError

router = APIRouter(prefix="/api/workspace/files", tags=["workspace-files"])


class FileEntry(BaseModel):
    name: str
    type: str
    size: Optional[int] = None
    modified: Optional[str] = None


class DirectoryListing(BaseModel):
    path: str
    entries: list[FileEntry]


class FileContent(BaseModel):
    path: str
    name: str
    size: int
    binary: bool
    content: Optional[str] = None
    language: Optional[str] = None


class ContentMatch(BaseModel):
    line: int
    column: int
    text: str


class FileMatch(BaseModel):
    path: str
    relative_path: str
    name: str
    type: str
    name_match: bool
    content_matches: list[ContentMatch]


class FileMatches(BaseModel):
    results: list[FileMatch]
    next_offset: int | None = None


class SearchResult(BaseModel):
    path: str
    name: str
    type: str


class SearchResponse(BaseModel):
    results: list[SearchResult]


class WriteFileRequest(BaseModel):
    path: str
    content: str


class CreateRequest(BaseModel):
    path: str
    type: str = "file"


class MoveRequest(BaseModel):
    source: str
    destination: str


class DeleteRequest(BaseModel):
    path: str


class ArchiveRequest(BaseModel):
    paths: list[str]


async def send_file(request: Request, path: str, *, download: bool = False):
    try:
        identity = await identity_for_request(request)
    except IdentityUnavailable as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    if not identity.is_pam or identity.uid == os.geteuid():
        target = expand_user_path(path, identity).resolve()
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {path}")
        if not target.is_file():
            raise HTTPException(status_code=400, detail=f"Not a file: {path}")
        media_type, _ = mimetypes.guess_type(str(target))
        return FileResponse(
            str(target),
            filename=target.name if download else None,
            media_type="application/octet-stream" if download else media_type or "application/octet-stream",
        )

    try:
        result = await Runtime.stream_file(request, path)
    except FileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    headers = {"Content-Length": str(result["size"])}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{result["name"]}"'
    return StreamingResponse(
        result["body"],
        media_type="application/octet-stream" if download else result.get("media_type") or "application/octet-stream",
        headers=headers,
    )


@router.get("", response_model=DirectoryListing)
async def list_directory(
    request: Request,
    path: str = Query(..., description="Absolute path to list"),
):
    try:
        return DirectoryListing(**await Runtime.list_directory(request, path))
    except FileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/read", response_model=FileContent)
async def read_file(
    request: Request,
    path: str = Query(..., description="Absolute path to file"),
):
    try:
        return FileContent(**await Runtime.read_file(request, path))
    except FileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/write")
async def write_file(request: Request, req: WriteFileRequest):
    try:
        return await Runtime.write_file(request, req.path, req.content)
    except FileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/matches", response_model=FileMatches)
async def file_matches(
    request: Request,
    query: str = Query(..., description="Literal text to match"),
    path: str = Query(..., description="Root path to search"),
    show_hidden: bool = Query(False, description="Include dotfiles and dot-directories"),
    offset: int = Query(0, ge=0, description="Result offset"),
    limit: int = Query(MATCH_PAGE_SIZE, ge=1, le=MATCH_PAGE_SIZE, description="Page size"),
):
    if not query.strip():
        raise HTTPException(status_code=400, detail="Query must not be blank")
    try:
        result = await Runtime.file_matches(request, query, path, show_hidden, offset, limit)
        return FileMatches(**result)
    except FileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/search", response_model=SearchResponse)
async def search_files(
    request: Request,
    query: str = Query(..., description="Search term"),
    path: str = Query(..., description="Root path to search"),
    limit: int = Query(20, description="Max results"),
):
    try:
        return SearchResponse(**await Runtime.search_files(request, query, path, limit))
    except FileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/create")
async def create_item(request: Request, req: CreateRequest):
    try:
        return await Runtime.create_item(request, req.path, req.type)
    except FileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/move")
async def move_item(request: Request, req: MoveRequest):
    try:
        return await Runtime.move_item(request, req.source, req.destination)
    except FileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/delete")
async def delete_item(request: Request, req: DeleteRequest):
    try:
        return await Runtime.delete_item(request, req.path)
    except FileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = FastAPIFile(...),
    directory: str = Form(...),
):
    try:
        return await Runtime.upload_file(
            request,
            directory,
            file.filename or "file",
            await file.read(),
        )
    except FileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/view")
async def view_file(
    request: Request,
    path: str = Query(..., description="Absolute path to file"),
):
    return await send_file(request, path)


@router.get("/download")
async def download_file(
    request: Request,
    path: str = Query(..., description="Absolute path to file"),
):
    return await send_file(request, path, download=True)


@router.post("/archive")
async def archive_files(request: Request, req: ArchiveRequest):
    try:
        result = await Runtime.archive_files(request, req.paths)
    except FileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return StreamingResponse(
        io.BytesIO(result["data"]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{result["name"]}"'},
    )


@router.get("/serve/{file_path:path}")
async def serve_static(request: Request, file_path: str):
    path = str(Path(file_path if len(file_path) >= 2 and file_path[1] == ":" else "/" + file_path))
    return await send_file(request, path)

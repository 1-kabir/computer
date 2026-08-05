"""OpenAI-compatible image generation and editing routes."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from cptr.utils.config import check_access
from cptr.utils.images import MAX_IMAGES_PER_REQUEST, edit_images, generate_images

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/images", tags=["images"])

COOKIE_NAME = "cptr_session"


class GenerateImageRequest(BaseModel):
    prompt: str
    workspace: str | None = None
    model: str | None = None
    size: str | None = None
    n: int | None = Field(default=1, ge=1, le=MAX_IMAGES_PER_REQUEST)


class EditImageRequest(BaseModel):
    prompt: str
    image_ids: list[str]
    workspace: str | None = None
    model: str | None = None
    size: str | None = None
    n: int | None = Field(default=1, ge=1, le=MAX_IMAGES_PER_REQUEST)
    background: str | None = None


def _get_user(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME)
    client_host = request.client.host if request.client else "127.0.0.1"
    auth = check_access(client_host=client_host, jwt_token=token)
    if not auth or not auth.user_id:
        raise HTTPException(401, "authentication required")
    return auth.user_id


def _raise_image_error(exc: Exception) -> None:
    if isinstance(exc, PermissionError):
        raise HTTPException(403, str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(400, str(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        detail = f"Image API error: {exc.response.status_code}"
        logger.warning("[images] %s: %s", detail, exc.response.text[:500])
        raise HTTPException(502, detail)
    if isinstance(exc, httpx.ConnectError):
        raise HTTPException(502, "Could not connect to image API")
    raise HTTPException(500, "Image request failed")


@router.post("/generations")
async def create_image_generation(request: Request, body: GenerateImageRequest):
    user_id = _get_user(request)
    try:
        images = await generate_images(
            request,
            body.prompt,
            user_id=user_id,
            model=body.model,
            size=body.size,
            n=body.n,
            workspace=body.workspace,
        )
        return {"images": [image.as_dict() for image in images]}
    except Exception as exc:
        _raise_image_error(exc)


@router.post("/edits")
async def create_image_edit(request: Request, body: EditImageRequest):
    user_id = _get_user(request)
    try:
        images = await edit_images(
            request,
            body.prompt,
            body.image_ids,
            user_id=user_id,
            model=body.model,
            size=body.size,
            n=body.n,
            background=body.background,
            workspace=body.workspace,
        )
        return {"images": [image.as_dict() for image in images]}
    except Exception as exc:
        _raise_image_error(exc)

"""OpenAI-compatible image generation and editing helpers."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
import httpx

from cptr.models import Config, File
from cptr.utils.config import _get_jwt_secret, now_ms
from cptr.utils.crypto import decrypt_key
from cptr.utils.runtime import Runtime
from cptr.utils.storage import get_storage


DEFAULT_IMAGE_BASE_URL = "https://api.openai.com/v1"
DEFAULT_IMAGE_MODEL = "gpt-image-1"
MAX_IMAGES_PER_REQUEST = 4


@dataclass
class ImageResult:
    id: str
    url: str
    name: str
    content_type: str
    size: int
    path: str | None = None

    def as_dict(self) -> dict:
        data = {
            "id": self.id,
            "url": self.url,
            "name": self.name,
            "type": "image",
            "content_type": self.content_type,
            "size": self.size,
        }
        if self.path:
            data["path"] = self.path
        return data


def clamp_image_count(n: int | None) -> int:
    if n is None:
        return 1
    return min(max(int(n), 1), MAX_IMAGES_PER_REQUEST)


def _clean_base_url(value: object) -> str:
    base_url = str(value or DEFAULT_IMAGE_BASE_URL).strip().rstrip("/")
    return base_url or DEFAULT_IMAGE_BASE_URL


def _clean_optional(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


async def _decrypt_config_key(key: str) -> str | None:
    encrypted = await Config.get(key)
    if not encrypted:
        return None
    return decrypt_key(str(encrypted), _get_jwt_secret())


async def image_generation_config() -> dict:
    return {
        "enabled": await Config.get("images.generation_enabled") is True,
        "base_url": _clean_base_url(await Config.get("images.generation_base_url")),
        "api_key": await _decrypt_config_key("images.generation_api_key"),
        "model": _clean_optional(await Config.get("images.generation_model"))
        or DEFAULT_IMAGE_MODEL,
        "size": _clean_optional(await Config.get("images.generation_size")),
    }


async def image_edit_config() -> dict:
    edit_key = await _decrypt_config_key("images.edit_api_key")
    generation_key = await _decrypt_config_key("images.generation_api_key")
    return {
        "enabled": await Config.get("images.edit_enabled") is True,
        "base_url": _clean_base_url(await Config.get("images.edit_base_url")),
        "api_key": edit_key or generation_key,
        "model": _clean_optional(await Config.get("images.edit_model")) or DEFAULT_IMAGE_MODEL,
        "size": _clean_optional(await Config.get("images.edit_size")),
    }


def _extension_for_content_type(content_type: str) -> str:
    content_type = content_type.split(";")[0].strip().lower()
    ext = mimetypes.guess_extension(content_type)
    if ext == ".jpe":
        ext = ".jpg"
    return ext or ".png"


def _file_url(file_id: str, content_type: str) -> str:
    return f"/api/files/{file_id}{_extension_for_content_type(content_type)}"


def _unique_workspace_image_path(workspace: str, source: str, content_type: str) -> Path:
    root = Path(workspace).expanduser().resolve()
    ext = _extension_for_content_type(content_type)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"{source}-{stamp}{ext}"
    path = root / name
    counter = 2
    while path.exists():
        path = root / f"{source}-{stamp}-{counter}{ext}"
        counter += 1
    return path


async def _store_image(
    request: Request,
    data: bytes,
    content_type: str,
    user_id: str | None,
    source: str,
    metadata: dict,
    workspace: str | None = None,
) -> ImageResult:
    content_type = content_type.split(";")[0].strip().lower() or "image/png"
    ext = _extension_for_content_type(content_type)
    workspace_path: Path | None = None

    if workspace:
        workspace_path = _unique_workspace_image_path(workspace, source, content_type)
        await Runtime.write_file(request, str(workspace_path), data)

    filename = workspace_path.name if workspace_path else f"{source}{ext}"
    meta = {
        "content_type": content_type,
        "size": len(data),
        "hash": hashlib.sha256(data).hexdigest(),
        "source": source,
        **metadata,
    }
    if workspace_path:
        meta["path"] = str(workspace_path)

    record = await File.create(
        user_id=user_id,
        filename=filename,
        meta=meta,
        created_at=now_ms(),
    )
    await get_storage().put(record.id, data)
    return ImageResult(
        id=record.id,
        url=_file_url(record.id, content_type),
        name=filename,
        content_type=content_type,
        size=len(data),
        path=str(workspace_path) if workspace_path else None,
    )


async def _decode_image_response_item(
    client: httpx.AsyncClient,
    item: dict,
    headers: dict[str, str],
) -> tuple[bytes, str]:
    if b64_json := item.get("b64_json"):
        return base64.b64decode(b64_json), "image/png"

    if image_url := item.get("url"):
        resp = await client.get(image_url, headers=headers)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "image/png")
        if not content_type.lower().startswith("image/"):
            raise ValueError("Provider URL did not return an image.")
        return resp.content, content_type

    raise ValueError("Image response did not include b64_json or url.")


async def generate_images(
    request: Request,
    prompt: str,
    *,
    user_id: str | None,
    model: str | None = None,
    size: str | None = None,
    n: int | None = None,
    workspace: str | None = None,
) -> list[ImageResult]:
    cfg = await image_generation_config()
    if not cfg["enabled"]:
        raise PermissionError("Image generation is disabled.")
    if not cfg["api_key"]:
        raise ValueError("Image generation is not configured.")

    payload: dict = {
        "model": model or cfg["model"],
        "prompt": prompt,
        "n": clamp_image_count(n),
    }
    if chosen_size := (size or cfg["size"]):
        payload["size"] = chosen_size

    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
        resp = await client.post(
            f"{cfg['base_url']}/images/generations",
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        body = resp.json()

        results = []
        for item in body.get("data", []):
            data, content_type = await _decode_image_response_item(client, item, headers)
            results.append(
                await _store_image(
                    request,
                    data,
                    content_type,
                    user_id,
                    "generated-image",
                    {"prompt": prompt, "model": payload["model"], "kind": "generation"},
                    workspace,
                )
            )
        return results


def clean_file_id(value: str) -> str:
    value = value.strip()
    if value.startswith("/api/files/"):
        value = value.removeprefix("/api/files/")
    value, _ = os.path.splitext(value)
    if "/" in value or "\\" in value or not value:
        raise ValueError("Image edits only accept cptr file IDs.")
    return value


def _resolve_workspace_image_path(value: str, workspace: str | None) -> Path | None:
    raw = value.strip()
    if raw.startswith("/api/workspace/files/serve/"):
        raw = "/" + raw.removeprefix("/api/workspace/files/serve/")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        if not workspace:
            return None
        path = Path(workspace).expanduser().resolve() / raw
    return path.resolve()


async def _load_image_file(
    request: Request, image_ref: str, workspace: str | None = None
) -> tuple[str, bytes, str]:
    path = _resolve_workspace_image_path(image_ref, workspace)
    if path and path.is_file():
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if not content_type.lower().startswith("image/"):
            raise ValueError(f"File is not an image: {image_ref}")
        file_data = await Runtime.read_bytes(request, str(path))
        data = file_data["data"]
        return path.name, data, content_type

    clean_id = clean_file_id(image_ref)
    record = await File.get_by_id(clean_id)
    if not record:
        raise ValueError(f"Image file not found: {image_ref}")
    data = await get_storage().get(record.id)
    if data is None:
        raise ValueError(f"Image blob missing: {image_ref}")
    content_type = (record.meta or {}).get("content_type", "application/octet-stream")
    if not str(content_type).lower().startswith("image/"):
        raise ValueError(f"File is not an image: {image_ref}")
    return record.filename or f"{record.id}.png", data, str(content_type)


async def edit_images(
    request: Request,
    prompt: str,
    image_ids: list[str],
    *,
    user_id: str | None,
    model: str | None = None,
    size: str | None = None,
    n: int | None = None,
    background: str | None = None,
    workspace: str | None = None,
) -> list[ImageResult]:
    cfg = await image_edit_config()
    if not cfg["enabled"]:
        raise PermissionError("Image editing is disabled.")
    if not cfg["api_key"]:
        raise ValueError("Image editing is not configured.")
    if not image_ids:
        raise ValueError("At least one image file ID is required.")

    payload: dict[str, str] = {
        "model": model or cfg["model"],
        "prompt": prompt,
    }
    if n is not None:
        payload["n"] = str(clamp_image_count(n))
    if chosen_size := (size or cfg["size"]):
        payload["size"] = chosen_size
    if background:
        payload["background"] = background

    loaded_files = [await _load_image_file(request, image_id, workspace) for image_id in image_ids]
    files = []
    multi = len(loaded_files) > 1
    for filename, data, content_type in loaded_files:
        field_name = "image[]" if multi else "image"
        files.append((field_name, (filename, data, content_type)))

    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
        resp = await client.post(
            f"{cfg['base_url']}/images/edits",
            headers=headers,
            data=payload,
            files=files,
        )
        resp.raise_for_status()
        body = resp.json()

        results = []
        for item in body.get("data", []):
            data, content_type = await _decode_image_response_item(client, item, headers)
            results.append(
                await _store_image(
                    request,
                    data,
                    content_type,
                    user_id,
                    "edited-image",
                    {"prompt": prompt, "model": payload["model"], "kind": "edit"},
                    workspace,
                )
            )
        return results

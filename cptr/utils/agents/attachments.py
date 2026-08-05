"""Attachment preparation for coding agent turns."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request
from cptr.utils.runtime import Runtime
from cptr.utils.storage import get_storage


@dataclass
class AgentAttachment:
    type: str
    name: str
    mime_type: str
    path: str
    base64: str


@dataclass
class PreparedAgentAttachments:
    images: list[AgentAttachment]
    files: list[AgentAttachment]
    prompt_suffix: str


def _safe_segment(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("._")
    return safe or "attachment"


def _is_image(file_meta: dict[str, Any]) -> bool:
    content_type = str(file_meta.get("content_type") or file_meta.get("mime_type") or "")
    return file_meta.get("type") == "image" or content_type.startswith("image/")


async def prepare_agent_attachments(
    request: Request,
    *,
    workspace: str,
    user_id: str | None,
    chat_id: str,
    message_id: str,
    files: list[dict[str, Any]] | None,
) -> PreparedAgentAttachments:
    if not files:
        return PreparedAgentAttachments(images=[], files=[], prompt_suffix="")

    root = (
        Path(workspace)
        / ".cptr"
        / "attachments"
        / _safe_segment(chat_id)
        / _safe_segment(message_id)
    )

    images: list[AgentAttachment] = []
    staged_files: list[AgentAttachment] = []
    storage = get_storage()
    for file_meta in files:
        if not isinstance(file_meta, dict):
            continue
        file_id = file_meta.get("id")
        if not isinstance(file_id, str) or not file_id:
            continue
        data = await storage.get(file_id)
        if data is None:
            raise RuntimeError(
                f"Uploaded file is no longer available: {file_meta.get('name') or file_id}"
            )

        name = _safe_segment(str(file_meta.get("name") or file_id))
        path = root / f"{_safe_segment(file_id)}-{name}"
        await Runtime.write_file(request, str(path), data)
        mime_type = str(
            file_meta.get("content_type")
            or file_meta.get("mime_type")
            or ("image/png" if _is_image(file_meta) else "application/octet-stream")
        )
        attachment = AgentAttachment(
            type="image" if _is_image(file_meta) else "file",
            name=name,
            mime_type=mime_type,
            path=str(path),
            base64=base64.b64encode(data).decode(),
        )
        if attachment.type == "image":
            images.append(attachment)
        else:
            staged_files.append(attachment)

    suffix = ""
    if staged_files:
        lines = "\n".join(f"- {item.path}" for item in staged_files)
        suffix = f"\n\nAttached files staged in the workspace:\n{lines}"
    return PreparedAgentAttachments(images=images, files=staged_files, prompt_suffix=suffix)

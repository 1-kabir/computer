"""Resolve selected model ids to API connections or agent profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Union

from fastapi import HTTPException

from cptr.models import Config
from cptr.utils.agents.detection import get_agent_status
from cptr.utils.agents.models import parse_agent_model_id


@dataclass(frozen=True)
class ApiModelTarget:
    kind: Literal["api"]
    connection: dict[str, Any]
    runtime_model: str
    full_model_id: str


@dataclass(frozen=True)
class AgentModelTarget:
    kind: Literal["agent"]
    profile_id: str
    agent: Literal["codex", "claude_code", "cursor", "grok", "opencode", "cline", "gemini", "pi"]
    model: str
    full_model_id: str
    config: dict[str, Any]


ModelTarget = Union[ApiModelTarget, AgentModelTarget]


async def resolve_agent_model_target(model_id: str, app_state=None) -> AgentModelTarget:
    parsed = parse_agent_model_id(model_id)
    if parsed is None:
        raise HTTPException(400, f"not an agent model: {model_id}")

    profile_id, model = parsed
    status = await get_agent_status(app_state)
    entry = next((p for p in status["profiles"] if p["id"] == profile_id), None)
    if entry is None:
        raise HTTPException(400, f"agent profile not found: {profile_id}")

    profile = entry["config"]
    if not entry["available"]:
        detection = entry.get("detected") or {}
        raise HTTPException(
            400,
            f"agent profile {profile_id} is not available: "
            f"{detection.get('message') or detection.get('status')}",
        )

    return AgentModelTarget(
        kind="agent",
        profile_id=profile["id"],
        agent=profile["agent"],
        model=model,
        full_model_id=model_id,
        config=profile,
    )


async def resolve_api_model_target(model_id: str, app_state=None) -> ApiModelTarget:
    """Resolve an API-backed model id using the existing connection rules."""
    from cptr.routers.chat import _resolve_connection

    connection, runtime_model = await _resolve_connection(model_id, app_state)
    return ApiModelTarget(
        kind="api",
        connection=connection,
        runtime_model=runtime_model,
        full_model_id=model_id,
    )


async def resolve_model_target(model_id: str, app_state=None) -> ModelTarget:
    if parse_agent_model_id(model_id) is not None:
        return await resolve_agent_model_target(model_id, app_state)
    return await resolve_api_model_target(model_id, app_state)


async def first_api_model_target(app_state=None) -> ApiModelTarget:
    from cptr.routers.chat import _fetch_provider_models

    connections = await Config.get("chat.connections") or []
    for conn in connections:
        if not conn.get("enabled", True):
            continue
        model_ids = conn.get("data", {}).get("models")
        if not model_ids:
            model_ids = await _fetch_provider_models(conn)
        if model_ids:
            prefix = (conn.get("prefix_id") or "").strip()
            runtime_model = model_ids[0]
            full = f"{prefix}/{runtime_model}" if prefix else runtime_model
            return ApiModelTarget(
                kind="api",
                connection=conn,
                runtime_model=runtime_model,
                full_model_id=full,
            )
    raise HTTPException(503, "No API model connections configured.")

"""Admin router for cptr. All endpoints require admin role."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from cptr.routers.chat import invalidate_model_cache
from cptr.models import User, Auth, Config
from cptr.utils.config import AuthResult, _get_jwt_secret, check_access, hash_password, now_ms
from cptr.utils.crypto import decrypt_key, encrypt_key, mask_key
from cptr.utils.agents.detection import get_agent_status, invalidate_agent_detection_cache
from cptr.utils.agents.models import save_agent_profiles

router = APIRouter(prefix="/api/admin", tags=["admin"])

COOKIE_NAME = "cptr_session"


def require_admin(request: Request) -> AuthResult:
    """Extract auth from cookie, raise 403 if not admin."""
    token = request.cookies.get(COOKIE_NAME)
    client_host = request.client.host if request.client else "127.0.0.1"
    auth = check_access(client_host=client_host, jwt_token=token)
    if not auth or auth.role != "admin":
        raise HTTPException(403, "admin required")
    return auth


# ── Users ────────────────────────────────────────────────────


@router.get("/users")
async def list_users(request: Request):
    """List all users with their roles."""
    require_admin(request)
    return {"users": await User.list_all()}


@router.post("/users")
async def create_user(request: Request, body: CreateUserRequest):
    """Create a new user (admin only)."""
    require_admin(request)

    if not body.username or not body.username.strip():
        return JSONResponse({"error": "username required"}, 400)
    if not body.password or len(body.password.strip()) < 6:
        return JSONResponse({"error": "min 6 characters"}, 400)
    if body.role not in ("admin", "user", "pending"):
        return JSONResponse({"error": "role must be admin, user, or pending"}, 400)

    username = body.username.strip()
    if await Auth.username_exists(username):
        return JSONResponse({"error": "username taken"}, 409)

    user_id = await User.create(
        username=username,
        password_hash=hash_password(body.password.strip()),
        role=body.role,
        created_at=now_ms(),
    )
    return {"ok": True, "user_id": user_id}


@router.delete("/users/{user_id}")
async def delete_user(request: Request, user_id: str):
    """Delete a user. Cannot delete yourself."""
    auth = require_admin(request)
    if auth.user_id == user_id:
        return JSONResponse({"error": "cannot delete yourself"}, 400)
    await User.delete_user(user_id)
    return {"ok": True}


@router.put("/users/{user_id}/role")
async def update_role(request: Request, user_id: str, body: RoleRequest):
    """Update a user's role."""
    require_admin(request)
    if body.role not in ("admin", "user", "pending"):
        return JSONResponse({"error": "role must be admin, user, or pending"}, 400)
    if not await User.update_role(user_id, body.role):
        return JSONResponse({"error": "user not found"}, 404)
    return {"ok": True}


@router.put("/users/{user_id}/profile")
async def update_user_profile(request: Request, user_id: str, body: UpdateUserProfileRequest):
    """Update a user's display name (admin only)."""
    require_admin(request)
    await User.update_display_name(user_id, body.display_name)
    return {"ok": True, "display_name": body.display_name}


@router.put("/users/{user_id}/password")
async def reset_user_password(request: Request, user_id: str, body: ResetPasswordRequest):
    """Reset a user's password (admin only)."""
    require_admin(request)
    if not body.password or len(body.password.strip()) < 6:
        return JSONResponse({"error": "min 6 characters"}, 400)
    if not await Auth.update_password(user_id, hash_password(body.password.strip())):
        return JSONResponse({"error": "user not found"}, 404)
    return {"ok": True}


@router.put("/users/{user_id}/username")
async def update_username(request: Request, user_id: str, body: UpdateUsernameRequest):
    """Update a user's username (admin only)."""
    require_admin(request)
    if not body.username or not body.username.strip():
        return JSONResponse({"error": "username required"}, 400)
    if not await Auth.update_username(user_id, body.username.strip()):
        return JSONResponse({"error": "username taken or user not found"}, 400)
    return {"ok": True}


# ── Config ───────────────────────────────────────────────────


@router.get("/config")
async def get_all_config(request: Request):
    """Get all instance config."""
    require_admin(request)
    return {"config": await Config.get_all()}


@router.get("/config/{namespace}")
async def get_config_namespace(request: Request, namespace: str):
    """Get config keys for a namespace (e.g. 'auth' → all 'auth.*' keys)."""
    require_admin(request)
    return {"config": await Config.get_namespace(namespace)}


def _prepare_config_updates(updates: dict) -> dict:
    """Normalize sensitive config values before persisting them."""
    prepared = dict(updates)
    secret = _get_jwt_secret()
    for key in (
        "audio.stt_api_key",
        "audio.tts_api_key",
        "images.generation_api_key",
        "images.edit_api_key",
    ):
        value = prepared.get(key)
        if isinstance(value, str) and value and not value.startswith("encrypted:"):
            prepared[key] = encrypt_key(value, secret)
    return prepared


@router.put("/config")
async def put_config(request: Request, body: ConfigUpdateRequest):
    """Update config keys. Upserts each key."""
    require_admin(request)
    await Config.upsert(_prepare_config_updates(body.config))
    return {"ok": True}


# ── Agents ──────────────────────────────────────────────────


class AgentsUpdateRequest(BaseModel):
    profiles: list[dict]


@router.get("/agents")
async def get_agents(request: Request):
    """Get configured agent profiles plus live detection status."""
    require_admin(request)
    return await get_agent_status(request.app.state)


@router.put("/agents")
async def update_agents(request: Request, body: AgentsUpdateRequest):
    """Replace configured agent profiles."""
    require_admin(request)
    await save_agent_profiles(body.profiles)
    invalidate_agent_detection_cache(request.app.state)
    invalidate_model_cache(request.app.state)
    return await get_agent_status(request.app.state, refresh=True)


@router.post("/agents/refresh")
async def refresh_agents(request: Request):
    """Refresh agent detection status."""
    require_admin(request)
    invalidate_agent_detection_cache(request.app.state)
    invalidate_model_cache(request.app.state)
    return await get_agent_status(request.app.state, refresh=True)


# ── Connections ──────────────────────────────────────────────


async def _get_connections() -> list[dict]:
    """Get all connections from config store."""
    return await Config.get("chat.connections") or []


async def _save_connections(connections: list[dict]):
    """Save connections to config store."""
    await Config.upsert({"chat.connections": connections})


def _mask_connection(conn: dict) -> dict:
    """Return connection with masked API key (for list display)."""
    secret = _get_jwt_secret()
    masked = {**conn}
    if masked.get("api_key"):
        try:
            plain = decrypt_key(masked["api_key"], secret)
            masked["api_key"] = mask_key(plain)
        except Exception:
            masked["api_key"] = "****"
    return masked


@router.get("/connections")
async def list_connections(request: Request):
    """List all connections (API keys masked)."""
    require_admin(request)
    connections = await _get_connections()
    return {"connections": [_mask_connection(c) for c in connections]}


@router.post("/connections")
async def create_connection(request: Request, body: CreateConnectionRequest):
    """Add a new AI connection."""
    require_admin(request)
    import uuid as _uuid

    connections = await _get_connections()
    conn = {
        "id": str(_uuid.uuid4()),
        "name": body.name,
        "provider": body.provider,
        "api_type": body.api_type,  # "chat_completions" | "responses" (openai only)
        "provider_type": "llama.cpp"
        if body.provider == "openai" and body.provider_type == "llama.cpp"
        else "default",
        "prefix_id": body.prefix_id,
        "base_url": body.base_url,
        "api_key": encrypt_key(body.api_key, _get_jwt_secret()) if body.api_key else None,
        "enabled": body.enabled if body.enabled is not None else True,
        "data": {"models": body.models} if body.models else {},
    }
    connections.append(conn)
    await _save_connections(connections)
    invalidate_model_cache(request.app.state)
    return {"ok": True, "id": conn["id"]}


@router.put("/connections/{conn_id}")
async def update_connection(request: Request, conn_id: str, body: UpdateConnectionRequest):
    """Update an existing connection."""
    require_admin(request)
    connections = await _get_connections()
    conn = next((c for c in connections if c["id"] == conn_id), None)
    if not conn:
        raise HTTPException(404, "connection not found")

    if body.name is not None:
        conn["name"] = body.name
    if body.provider is not None:
        conn["provider"] = body.provider
        if body.provider != "openai":
            conn["provider_type"] = "default"
    if body.api_type is not None:
        conn["api_type"] = body.api_type
    if "provider_type" in body.model_fields_set:
        conn["provider_type"] = (
            "llama.cpp"
            if conn.get("provider") == "openai" and body.provider_type == "llama.cpp"
            else "default"
        )
    if body.prefix_id is not None:
        conn["prefix_id"] = body.prefix_id or None
    if body.base_url is not None:
        conn["base_url"] = body.base_url or None
    if body.api_key is not None and body.api_key != "":
        conn["api_key"] = encrypt_key(body.api_key, _get_jwt_secret())
    if body.enabled is not None:
        conn["enabled"] = body.enabled
    if body.models is not None:
        conn.setdefault("data", {})["models"] = body.models
    elif "models" in body.model_fields_set:
        # Explicit null → clear whitelist, enable auto-discovery
        conn.get("data", {}).pop("models", None)

    await _save_connections(connections)
    invalidate_model_cache(request.app.state)
    return {"ok": True}


@router.delete("/connections/{conn_id}")
async def delete_connection(request: Request, conn_id: str):
    """Delete a connection."""
    require_admin(request)
    connections = [c for c in await _get_connections() if c["id"] != conn_id]
    await _save_connections(connections)
    invalidate_model_cache(request.app.state)
    return {"ok": True}


@router.post("/connections/{conn_id}/verify")
async def verify_connection(request: Request, conn_id: str):
    """Test a connection by making a lightweight API call."""
    require_admin(request)
    connections = await _get_connections()
    conn = next((c for c in connections if c["id"] == conn_id), None)
    if not conn:
        raise HTTPException(404, "connection not found")

    secret = _get_jwt_secret()
    api_key = decrypt_key(conn.get("api_key", ""), secret) if conn.get("api_key") else None

    provider = conn.get("provider", "")
    base_url = conn.get("base_url")

    import httpx

    try:
        if provider == "anthropic":
            url = (base_url or "https://api.anthropic.com/v1") + "/messages"
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    url,
                    headers={
                        "x-api-key": api_key or "",
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )
                if r.status_code in (200, 201):
                    return {"ok": True, "message": "Connected"}
                elif r.status_code == 401:
                    return JSONResponse({"ok": False, "message": "Invalid API key"}, 400)
                else:
                    return JSONResponse(
                        {"ok": False, "message": f"API returned {r.status_code}"}, 400
                    )

        elif provider == "openai":
            url = (base_url or "https://api.openai.com/v1") + "/models"
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url, headers={"Authorization": f"Bearer {api_key or ''}"})
                if r.status_code == 200:
                    return {"ok": True, "message": "Connected"}
                elif r.status_code == 401:
                    return JSONResponse({"ok": False, "message": "Invalid API key"}, 400)
                else:
                    return JSONResponse(
                        {"ok": False, "message": f"API returned {r.status_code}"}, 400
                    )

        else:
            return JSONResponse({"ok": False, "message": f"Unknown provider: {provider}"}, 400)

    except httpx.TimeoutException:
        return JSONResponse({"ok": False, "message": "Connection timed out"}, 400)
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, 400)


# ── Request Models ───────────────────────────────────────────


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class RoleRequest(BaseModel):
    role: str


class UpdateUserProfileRequest(BaseModel):
    display_name: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    password: str


class UpdateUsernameRequest(BaseModel):
    username: str


class ConfigUpdateRequest(BaseModel):
    config: dict


class CreateConnectionRequest(BaseModel):
    name: str
    provider: str  # "anthropic" | "openai"
    api_type: str = "chat_completions"  # "chat_completions" | "responses" (openai only)
    provider_type: Optional[str] = None  # None | "default" | "llama.cpp" (openai only)
    prefix_id: Optional[str] = None  # e.g. "openrouter" → "openrouter/model-id"
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    enabled: Optional[bool] = None  # defaults to True in handler
    models: Optional[list[str]] = None


class UpdateConnectionRequest(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    api_type: Optional[str] = None
    provider_type: Optional[str] = None
    prefix_id: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    enabled: Optional[bool] = None
    models: Optional[list[str]] = None


# ── Model config ─────────────────────────────────────────────

CONFIG_KEY_CHAT_MODELS = "chat.models"


@router.get("/models/config")
async def get_model_config(request: Request):
    """Get per-model config and full model list (including inactive) for the admin Models tab."""
    require_admin(request)
    return await _build_model_config(request)


async def _build_model_config(request: Request):
    """Build model configuration response for the admin Models tab."""
    config = await Config.get(CONFIG_KEY_CHAT_MODELS) or {}

    # Build full model list from all enabled connections (same as chat.py
    # get_models but without filtering inactive models).
    from cptr.routers.chat import _get_connections, _get_connection_models

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

    return {"config": config, "models": models}


@router.post("/models/refresh")
async def refresh_model_list(request: Request):
    """Clear cached provider-discovered models and return the refreshed model list."""
    require_admin(request)
    invalidate_model_cache(request.app.state)
    return await _build_model_config(request)


class UpdateModelConfigRequest(BaseModel):
    is_active: Optional[bool] = None
    params: Optional[dict] = None


@router.put("/models/{model_id:path}/config")
async def update_model_config(request: Request, model_id: str, body: UpdateModelConfigRequest):
    """Update config for a specific model (or '*' for global defaults)."""
    require_admin(request)
    all_config = await Config.get(CONFIG_KEY_CHAT_MODELS) or {}

    entry = all_config.get(model_id, {})

    if body.is_active is not None:
        entry["is_active"] = body.is_active

    if body.params is not None:
        entry["params"] = body.params

    # Clean up empty entries
    if not entry or (entry.keys() <= {"is_active"} and entry.get("is_active") is not False):
        if not entry.get("params"):
            all_config.pop(model_id, None)
        else:
            all_config[model_id] = entry
    else:
        all_config[model_id] = entry

    await Config.upsert({CONFIG_KEY_CHAT_MODELS: all_config})
    return {"ok": True}


# ── Tool servers ─────────────────────────────────────────────

CONFIG_KEY_TOOL_SERVERS = "tool_servers"


async def _get_tool_servers() -> list[dict]:
    """Get all tool server configs from config store."""
    return await Config.get(CONFIG_KEY_TOOL_SERVERS) or []


async def _save_tool_servers(servers: list[dict]):
    """Save tool server configs and invalidate cache."""
    await Config.upsert({CONFIG_KEY_TOOL_SERVERS: servers})
    from cptr.utils.tools import invalidate_tool_server_cache

    invalidate_tool_server_cache()


def _mask_tool_server(server: dict) -> dict:
    """Return server with masked API key for display."""
    masked = {**server}
    if masked.get("key"):
        key = masked["key"]
        masked["key"] = key[:4] + "****" + key[-4:] if len(key) > 8 else "****"
    return masked


@router.get("/tools/approval")
async def get_tool_approval(request: Request):
    """Return built-in tool approval defaults for the Chat settings UI."""
    require_admin(request)
    from cptr.utils.tools import (
        ALL_TOOLS,
        BUILTIN_TOOL_GROUPS,
        normalize_tool_approval,
    )

    raw_default = await Config.get("tool_approval.default_builtin_approval")
    raw_overrides = await Config.get("tool_approval.builtin_tools") or {}
    overrides = {}
    if isinstance(raw_overrides, dict):
        overrides = {
            name: policy
            for name, value in raw_overrides.items()
            if (policy := normalize_tool_approval(value))
        }

    groups = []
    for group_id, names in BUILTIN_TOOL_GROUPS.items():
        tools = []
        for name in names:
            tool = ALL_TOOLS.get(name)
            if tool:
                tools.append(
                    {
                        "name": name,
                        "default_approval": normalize_tool_approval(tool.get("approval")),
                    }
                )
        if tools:
            groups.append({"id": group_id, "tools": tools})

    return {
        "default_approval": normalize_tool_approval(raw_default) or "review",
        "overrides": overrides,
        "groups": groups,
    }


@router.get("/tools/servers")
async def list_tool_servers(request: Request):
    """List all configured tool servers (keys masked)."""
    require_admin(request)
    servers = await _get_tool_servers()
    return {"servers": [_mask_tool_server(s) for s in servers]}


class CreateToolServerRequest(BaseModel):
    id: str
    type: str = "openapi"  # "openapi" | "mcp" | "mcp_stdio"
    url: str = ""  # for openapi/mcp
    path: str = "openapi.json"  # OpenAPI spec path (OpenAPI only)
    auth_type: str = "bearer"  # "bearer" | "none"
    key: Optional[str] = None
    name: str = ""
    description: str = ""
    headers: Optional[dict] = None
    enabled: bool = True
    # Stdio MCP fields
    command: str = ""  # for mcp_stdio
    args: list[str] = []  # for mcp_stdio
    env: Optional[dict[str, str]] = None  # for mcp_stdio
    cwd: Optional[str] = None  # for mcp_stdio


@router.post("/tools/servers")
async def create_tool_server(request: Request, body: CreateToolServerRequest):
    """Add a new external tool server."""
    require_admin(request)
    import re as _re

    server_id = body.id.strip()
    if not server_id or not _re.fullmatch(r"[a-z0-9_]+", server_id):
        raise HTTPException(400, "ID must be lowercase alphanumeric with underscores only")

    servers = await _get_tool_servers()
    if any(s["id"] == server_id for s in servers):
        raise HTTPException(409, f"Server ID '{server_id}' already exists")

    server = {
        "id": server_id,
        "type": body.type,
        "url": body.url,
        "path": body.path,
        "auth_type": body.auth_type,
        "key": body.key or "",
        "name": body.name or server_id,
        "description": body.description,
        "headers": body.headers,
        "enabled": body.enabled,
        "command": body.command,
        "args": body.args,
        "env": body.env,
        "cwd": body.cwd,
    }
    servers.append(server)
    await _save_tool_servers(servers)
    return {"ok": True, "id": server["id"]}


class UpdateToolServerRequest(BaseModel):
    type: Optional[str] = None
    url: Optional[str] = None
    path: Optional[str] = None
    auth_type: Optional[str] = None
    key: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    headers: Optional[dict] = None
    enabled: Optional[bool] = None
    # Stdio MCP fields
    command: Optional[str] = None
    args: Optional[list[str]] = None
    env: Optional[dict[str, str]] = None
    cwd: Optional[str] = None


@router.put("/tools/servers/{server_id}")
async def update_tool_server(request: Request, server_id: str, body: UpdateToolServerRequest):
    """Update an existing tool server."""
    require_admin(request)
    servers = await _get_tool_servers()
    server = next((s for s in servers if s["id"] == server_id), None)
    if not server:
        raise HTTPException(404, "tool server not found")

    for field in ("type", "url", "path", "auth_type", "name", "description", "command", "cwd"):
        val = getattr(body, field)
        if val is not None:
            server[field] = val
    if body.key is not None and body.key != "":
        server["key"] = body.key
    if body.headers is not None:
        server["headers"] = body.headers
    if body.enabled is not None:
        server["enabled"] = body.enabled
    if body.args is not None:
        server["args"] = body.args
    if body.env is not None:
        server["env"] = body.env

    await _save_tool_servers(servers)
    return {"ok": True}


@router.delete("/tools/servers/{server_id}")
async def delete_tool_server(request: Request, server_id: str):
    """Delete a tool server."""
    require_admin(request)
    servers = [s for s in await _get_tool_servers() if s["id"] != server_id]
    await _save_tool_servers(servers)
    return {"ok": True}


@router.post("/tools/servers/{server_id}/verify")
async def verify_tool_server(request: Request, server_id: str):
    """Test connectivity to a tool server. Returns discovered tools."""
    require_admin(request)
    servers = await _get_tool_servers()
    server = next((s for s in servers if s["id"] == server_id), None)
    if not server:
        raise HTTPException(404, "tool server not found")

    server_type = server.get("type", "openapi")
    url = server.get("url", "")
    headers = dict(server.get("headers") or {})
    if server.get("auth_type") == "bearer" and server.get("key"):
        headers["Authorization"] = f"Bearer {server['key']}"

    try:
        if server_type == "mcp":
            from cptr.utils.mcp.client import MCPClient

            client = MCPClient()
            await client.connect(url, headers or None)
            try:
                specs = await client.list_tool_specs()
                return {"ok": True, "tools": specs}
            finally:
                await client.disconnect()

        elif server_type == "mcp_stdio":
            from cptr.utils.mcp.client import MCPClient

            client = MCPClient()
            await client.connect_stdio(
                server.get("command", ""),
                server.get("args", []),
                server.get("env"),
                server.get("cwd"),
            )
            try:
                specs = await client.list_tool_specs()
                return {"ok": True, "tools": specs}
            finally:
                await client.disconnect()

        else:  # openapi
            from cptr.utils.openapi import fetch_openapi_spec, convert_openapi_to_tool_specs

            path = server.get("path", "openapi.json")
            if path.startswith("http"):
                spec_url = path
            else:
                spec_url = f"{url.rstrip('/')}/{path.lstrip('/')}"

            spec = await fetch_openapi_spec(spec_url, headers or None)
            tools = convert_openapi_to_tool_specs(spec)
            return {"ok": True, "tools": tools}

    except ModuleNotFoundError as e:
        if e.name == "mcp":
            return JSONResponse(
                {
                    "ok": False,
                    "message": "MCP support is not installed. Run: pip install 'cptr[mcp]'",
                },
                400,
            )
        return JSONResponse({"ok": False, "message": str(e)}, 400)
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, 400)

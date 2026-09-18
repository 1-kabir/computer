"""Configuration model helpers for coding agents."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from fastapi import HTTPException

from cptr.models import Config

CONFIG_KEY_AGENT_PROFILES = "agents.profiles"

DEFAULT_AGENT_PROFILES: list[dict[str, Any]] = [
    {
        "id": "codex",
        "agent": "codex",
        "name": "Codex",
        "mode": "auto",
        "command": "codex",
        "home": None,
        "models": [],
        "default_model": "",
        "approval_mode": "auto",
        "sandbox_mode": "workspace-write",
    },
    {
        "id": "claude-code",
        "agent": "claude_code",
        "name": "Claude Code",
        "mode": "auto",
        "command": "claude",
        "home": None,
        "models": [],
        "default_model": "",
        "permission_mode": "default",
        "launch_args": "",
    },
    {
        "id": "cursor",
        "agent": "cursor",
        "name": "Cursor",
        "mode": "auto",
        "command": "agent",
        "home": None,
        "models": [],
        "default_model": "",
        "api_endpoint": "",
    },
    {
        "id": "grok",
        "agent": "grok",
        "name": "Grok",
        "mode": "auto",
        "command": "grok",
        "home": None,
        "models": [],
        "default_model": "",
    },
    {
        "id": "opencode",
        "agent": "opencode",
        "name": "OpenCode",
        "mode": "auto",
        "command": "opencode",
        "home": None,
        "models": [],
        "default_model": "",
        "server_url": "",
        "server_password": "",
    },
    {
        "id": "cline",
        "agent": "cline",
        "name": "Cline",
        "mode": "auto",
        "command": "cline",
        "home": None,
        "models": [],
        "default_model": "",
    },
    {
        "id": "gemini",
        "agent": "gemini",
        "name": "Gemini",
        "mode": "auto",
        "command": "gemini",
        "home": None,
        "models": [],
        "default_model": "",
    },
    {
        "id": "pi",
        "agent": "pi",
        "name": "Pi",
        "mode": "auto",
        "command": "pi",
        "home": None,
        "models": [],
        "default_model": "",
    },
    {
        "id": "kilo",
        "agent": "kilo",
        "name": "Kilo",
        "mode": "auto",
        "command": "kilo",
        "home": None,
        "models": [],
        "default_model": "",
    },
    {
        "id": "antigravity",
        "agent": "antigravity",
        "name": "Antigravity",
        "mode": "auto",
        "command": "agy",
        "home": None,
        "models": [],
        "default_model": "",
    },
]

_PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_VALID_AGENTS = {
    "codex",
    "claude_code",
    "cursor",
    "grok",
    "opencode",
    "cline",
    "gemini",
    "pi",
    "antigravity",
    "kilo",
}
_VALID_MODES = {"auto", "enabled", "disabled"}
_VALID_CODEX_APPROVAL = {"ask", "auto", "full"}
_VALID_CODEX_SANDBOX = {"read-only", "workspace-write", "danger-full-access"}
_VALID_CLAUDE_PERMISSION = {"default", "accept_edits", "bypass_permissions"}
_AGENT_DEFAULTS: dict[str, dict[str, str]] = {
    "codex": {"name": "Codex", "command": "codex", "model": ""},
    "claude_code": {
        "name": "Claude Code",
        "command": "claude",
        "model": "",
    },
    "cursor": {
        "name": "Cursor",
        "command": "agent",
        "model": "",
    },
    "grok": {"name": "Grok", "command": "grok", "model": ""},
    "opencode": {"name": "OpenCode", "command": "opencode", "model": ""},
    "cline": {"name": "Cline", "command": "cline", "model": ""},
    "gemini": {"name": "Gemini", "command": "gemini", "model": ""},
    "pi": {"name": "Pi", "command": "pi", "model": ""},
    "antigravity": {"name": "Antigravity", "command": "agy", "model": ""},
    "kilo": {"name": "Kilo", "command": "kilo", "model": ""},
}


def default_agent_profiles() -> list[dict[str, Any]]:
    return deepcopy(DEFAULT_AGENT_PROFILES)


def normalize_agent_profile(raw: dict[str, Any]) -> dict[str, Any]:
    profile = dict(raw)
    profile_id = str(profile.get("id") or "").strip()
    if not _PROFILE_ID_RE.fullmatch(profile_id):
        raise HTTPException(400, "agent profile id must be a slug up to 64 characters")

    agent = str(profile.get("agent") or "").strip()
    if agent not in _VALID_AGENTS:
        raise HTTPException(
            400,
            "agent must be codex, claude_code, cursor, grok, opencode, cline, gemini, pi, antigravity, or kilo",
        )
    defaults = _AGENT_DEFAULTS[agent]

    mode = str(profile.get("mode") or "auto").strip()
    if mode not in _VALID_MODES:
        raise HTTPException(400, "agent mode must be auto, enabled, or disabled")

    command = str(profile.get("command") or "").strip()
    if not command:
        command = defaults["command"]

    name = str(profile.get("name") or "").strip() or defaults["name"]
    home = profile.get("home")
    if isinstance(home, str):
        home = home.strip() or None
    elif home is not None:
        raise HTTPException(400, "agent home must be a string or null")

    models = profile.get("models")
    if not isinstance(models, list):
        models = []
    normalized_models = []
    for model in models:
        if isinstance(model, str) and model.strip():
            model_id = model.strip()
            if model_id == "default":
                continue
            normalized_models.append(model_id)
    default_model = str(profile.get("default_model") or "").strip()
    if default_model == "default":
        default_model = ""
    if normalized_models and not default_model:
        default_model = normalized_models[0]
    if normalized_models and default_model not in normalized_models:
        raise HTTPException(400, "agent default_model must be present in models")

    normalized: dict[str, Any] = {
        "id": profile_id,
        "agent": agent,
        "name": name,
        "mode": mode,
        "command": command,
        "home": home,
        "models": normalized_models,
        "default_model": default_model,
    }

    if agent == "codex":
        approval_mode = str(profile.get("approval_mode") or "auto").strip()
        sandbox_mode = str(profile.get("sandbox_mode") or "workspace-write").strip()
        if approval_mode not in _VALID_CODEX_APPROVAL:
            raise HTTPException(400, "codex approval_mode must be ask, auto, or full")
        if sandbox_mode not in _VALID_CODEX_SANDBOX:
            raise HTTPException(
                400, "codex sandbox_mode must be read-only, workspace-write, or danger-full-access"
            )
        normalized.update(
            {
                "approval_mode": approval_mode,
                "sandbox_mode": sandbox_mode,
            }
        )
    elif agent == "claude_code":
        permission_mode = str(profile.get("permission_mode") or "default").strip()
        if permission_mode not in _VALID_CLAUDE_PERMISSION:
            raise HTTPException(
                400, "claude permission_mode must be default, accept_edits, or bypass_permissions"
            )
        normalized.update(
            {
                "permission_mode": permission_mode,
                "launch_args": str(profile.get("launch_args") or "").strip(),
            }
        )
    elif agent == "cursor":
        normalized["api_endpoint"] = str(profile.get("api_endpoint") or "").strip()
    elif agent == "opencode" or agent == "kilo":
        normalized["server_url"] = str(profile.get("server_url") or "").strip()
        normalized["server_password"] = str(profile.get("server_password") or "").strip()

    return normalized


def normalize_agent_profiles(raw_profiles: Any) -> list[dict[str, Any]]:
    if raw_profiles is None:
        raw_profiles = []
    if not isinstance(raw_profiles, list):
        raise HTTPException(400, "agents.profiles must be a list")

    profiles = []
    for profile in raw_profiles:
        if not isinstance(profile, dict):
            raise HTTPException(400, "agent profiles must be objects")
        profiles.append(normalize_agent_profile(profile))

    seen: set[str] = set()
    for profile in profiles:
        profile_id = profile["id"]
        if profile_id in seen:
            raise HTTPException(400, f"duplicate agent profile id: {profile_id}")
        seen.add(profile_id)
    return profiles


async def get_raw_agent_profiles() -> Any:
    return await Config.get(CONFIG_KEY_AGENT_PROFILES)


async def save_agent_profiles(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = normalize_agent_profiles(profiles)
    await Config.upsert({CONFIG_KEY_AGENT_PROFILES: normalized})
    return normalized


def model_id_for_profile(profile: dict[str, Any], model: str) -> str:
    return f"agent:{profile['id']}/{model}"


def parse_agent_model_id(model_id: str) -> tuple[str, str] | None:
    if not model_id.startswith("agent:"):
        return None
    rest = model_id[len("agent:") :]
    if "/" not in rest:
        return None
    profile_id, model = rest.split("/", 1)
    if not profile_id or not model:
        return None
    return profile_id, model

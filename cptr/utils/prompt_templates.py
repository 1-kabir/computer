"""System prompt templates and runtime context for chat tasks."""

from __future__ import annotations

import logging
import os
import platform
import re
import socket
from datetime import date
from importlib.metadata import version as pkg_version
from pathlib import Path

from fastapi import Request
from cptr.models import Config
from cptr.utils.identity import identity_for_user_id
from cptr.utils.runtime import Runtime, FileError
from cptr.utils.skills import build_catalog_xml, discover_skills

logger = logging.getLogger(__name__)

INSTRUCTION_FILENAMES = ["MEMORY.md", "AGENTS.md", "AGENT.md", "CLAUDE.md"]

_TEMPLATE_RE = re.compile(r"\{\{(\w+)\}\}")

DEFAULT_SYSTEM_PROMPT = (
    "You are Computer (cptr), a helpful assistant running inside the user's computer interface. "
    "You have access to tools to read, search, and modify files in the workspace, "
    "run commands, and use configured tools. Use them to help the user directly."
    " Approach hard requests with initiative and persistence: make the best possible "
    "attempt, adapt as needed, and keep going unless a real constraint prevents progress."
    "\n\n{{CPTR_CONTEXT}}"
    "\n\n{{MEMORY}}"
    "\n\n{{INSTRUCTIONS}}"
    "\n\n{{SKILLS}}"
    "\n\nWorkspace: {{WORKSPACE_NAME}}"
    "\nFiles:\n{{FILE_TREE}}"
)

HOME_SYSTEM_PROMPT = (
    "You are Computer (cptr), a helpful assistant in the user's computer interface. "
    "This is a general chat with no workspace open. Use the available tools directly and "
    "ask the user to open a workspace for project files or commands."
    "\n\n{{MEMORY}}"
    "\n\n{{SKILLS}}"
)


def _get_file_tree(workspace: str, max_entries: int = 200) -> str:
    """Generate a compact file tree listing for the workspace."""
    ws = Path(workspace)
    try:
        if not ws.is_dir():
            return ""
        items = sorted(ws.iterdir())
    except OSError:
        return ""
    ignore = {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".next",
        "build",
        "dist",
        ".cptr",
        ".svelte-kit",
        ".DS_Store",
    }
    entries = []
    for item in items:
        if item.name in ignore:
            continue
        try:
            item_is_dir = item.is_dir()
        except OSError:
            item_is_dir = False
        suffix = "/" if item_is_dir else ""
        entries.append(f"  {item.name}{suffix}")
        if item_is_dir:
            try:
                for child in sorted(item.iterdir()):
                    if child.name in ignore:
                        continue
                    try:
                        child_is_dir = child.is_dir()
                    except OSError:
                        child_is_dir = False
                    csuffix = "/" if child_is_dir else ""
                    entries.append(f"    {child.name}{csuffix}")
                    if len(entries) >= max_entries:
                        entries.append("    ...")
                        break
            except OSError:
                pass
        if len(entries) >= max_entries:
            break
    return "\n".join(entries)


def _load_instruction_files(workspace: str, max_bytes: int = 32_000) -> str:
    """Load well-known AI instruction files from workspace root."""
    ws = Path(workspace)
    try:
        if not ws.is_dir():
            return ""
    except OSError:
        return ""
    parts: list[str] = []
    total = 0
    for name in INSTRUCTION_FILENAMES:
        path = ws / name
        try:
            is_file = path.is_file()
        except OSError:
            is_file = False
        if is_file:
            remaining = max_bytes - total
            if remaining <= 0:
                break
            try:
                content = path.read_text(errors="replace")[:remaining].strip()
            except OSError:
                continue
            if content:
                parts.append(f"# {name}\n{content}")
                total += len(content)
                logger.debug("[instructions] Loaded %s (%d bytes)", name, len(content))
    return "\n\n".join(parts)


def _is_containerized() -> bool:
    """Best-effort detection for Docker/Podman/Kubernetes-style containers."""
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(errors="replace").lower()
    except OSError:
        return False
    markers = ("docker", "containerd", "kubepods", "podman", "libpod")
    return any(marker in cgroup for marker in markers)


def _runtime_label() -> str:
    return "container" if _is_containerized() else "host"


def _safe_hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return ""


def _safe_version() -> str:
    try:
        return pkg_version("cptr")
    except Exception:
        return "dev"


def _workspace_name(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return path.name if path.is_dir() else ""
    except OSError:
        return path.name


def _format_cptr_context(
    workspace: str, model: str = "", home: str | None = None, shell: str | None = None
) -> str:
    """Return the default cptr runtime context block for the system prompt."""
    ws_path = Path(workspace)
    runtime = _runtime_label()
    shell = shell or os.environ.get("SHELL") or os.environ.get("COMSPEC") or ""
    home = home or str(Path.home())
    host_control = (
        "Commands run in the cptr backend environment. Because this appears to be a "
        "container, commands affect the container and mounted paths; host-level controls "
        "only work when the host exposes them into the container."
        if runtime == "container"
        else "Commands run on this machine through the cptr backend environment."
    )

    lines = [
        "<cptr_context>",
        "cptr is serving the user's real computer/environment, not a detached chat sandbox.",
        "",
        "Runtime:",
        f"- Environment: {runtime}",
        f"- Hostname: {_safe_hostname() or 'unknown'}",
        f"- OS: {platform.system().replace('Darwin', 'macOS')} {platform.release()}",
        f"- Architecture: {platform.machine() or 'unknown'}",
        f"- Shell: {shell or 'unknown'}",
        f"- Home: {home}",
        f"- cptr version: {_safe_version()}",
    ]
    if model:
        lines.append(f"- Model: {model}")
    lines.extend(
        [
            "",
            "Workspace:",
            f"- Name: {_workspace_name(ws_path)}",
            f"- Path: {ws_path}",
            "",
            "Tool behavior:",
            f"- {host_control}",
            "- Use the available tools before claiming you cannot inspect or change something.",
            "- If the user asks to show a file in chat, use display_file.",
            "- For machine-level requests such as volume, brightness, apps, services, packages, "
            "network state, or files, check the runtime and use appropriate shell commands or "
            "configured tools when available.",
            "- If a task truly cannot reach the requested host capability, explain the runtime "
            "boundary briefly and offer the closest useful check or command.",
            "</cptr_context>",
        ]
    )
    return "\n".join(lines)


def _render_template(template: str, variables: dict[str, str]) -> str:
    """Render {{VARIABLE}} placeholders in a template string.

    Known variables are substituted with their values. Unknown variables are left
    intact so downstream providers or user-specific placeholders are not broken.
    """

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        if key in variables:
            return variables[key]
        return match.group(0)

    result = _TEMPLATE_RE.sub(_replace, template)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _render_system_template(template: str, variables: dict[str, str]) -> str:
    """Render a system prompt and ensure cptr runtime context is present."""
    has_context_slot = "{{CPTR_CONTEXT}}" in template
    rendered = _render_template(template, variables)
    context = variables.get("CPTR_CONTEXT", "").strip()
    if context and not has_context_slot and context not in rendered:
        rendered = f"{rendered}\n\n{context}" if rendered else context
    return re.sub(r"\n{3,}", "\n\n", rendered).strip()


def _build_template_variables(
    workspace: str,
    model: str = "",
    memory: str = "",
    skills_enabled: bool = True,
    home: str | None = None,
    shell: str | None = None,
) -> dict[str, str]:
    """Build the dict of template variable values for the current context."""
    ws_path = Path(workspace) if workspace else None
    os_name = platform.system().replace("Darwin", "macOS")
    shell = shell or os.environ.get("SHELL") or os.environ.get("COMSPEC") or ""
    home = home or str(Path.home())

    instructions = _load_instruction_files(workspace) if workspace else ""
    if instructions:
        instructions_block = (
            f"<instructions>\n{instructions}\n</instructions>"
            "\n\nThe above <instructions> were loaded from instruction files in the workspace root. "
            "These files persist across sessions and are user-authored workspace instructions. "
            "Managed memory is shown separately when available."
        )
    else:
        instructions_block = ""

    skills_block = build_catalog_xml(discover_skills(workspace)) if skills_enabled else ""

    return {
        "WORKSPACE_NAME": _workspace_name(ws_path),
        "WORKSPACE_PATH": str(ws_path) if ws_path else "",
        "FILE_TREE": _get_file_tree(workspace) if workspace else "",
        "INSTRUCTIONS": instructions_block,
        "MEMORY": memory,
        "SKILLS": skills_block,
        "CPTR_CONTEXT": _format_cptr_context(workspace, model, home, shell) if workspace else "",
        "RUNTIME_ENV": _runtime_label(),
        "HOSTNAME": _safe_hostname(),
        "OS": os_name,
        "PLATFORM": platform.platform(),
        "ARCH": platform.machine(),
        "SHELL": shell,
        "HOME": home,
        "CPTR_VERSION": _safe_version(),
        "DATE": date.today().isoformat(),
        "MODEL": model,
    }


async def load_system_prompt(
    request: Request,
    workspace: str,
    model: str = "",
    user_id: str | None = None,
    current_message: str = "",
    recent_messages: list[dict] | None = None,
    mentioned_files: list[str] | None = None,
) -> str:
    """Load and render the system prompt for a workspace/model.

    Resolution order:
      1. .cptr/system.md in the workspace
      2. Per-model system_prompt from chat.models config
      3. Global (*) system_prompt from chat.models config
      4. DEFAULT_SYSTEM_PROMPT
    """
    template = None

    if workspace:
        ws_prompt = Path(workspace) / ".cptr" / "system.md"
        if user_id:
            try:
                file_data = await Runtime.read_file(request, str(ws_prompt))
                if not file_data.get("binary"):
                    template = str(file_data.get("content") or "").strip()
            except FileError:
                pass
        elif ws_prompt.is_file():
            template = ws_prompt.read_text(errors="replace").strip()

    if template is None:
        try:
            chat_models_config = await Config.get("chat.models") or {}
            if model:
                model_prompt = (
                    chat_models_config.get(model, {}).get("params", {}).get("system_prompt")
                )
                if model_prompt:
                    template = model_prompt
            if template is None:
                global_prompt = (
                    chat_models_config.get("*", {}).get("params", {}).get("system_prompt")
                )
                if global_prompt:
                    template = global_prompt
        except Exception:
            logger.debug("[system_prompt] Failed to load from config", exc_info=True)

    if template is None:
        template = DEFAULT_SYSTEM_PROMPT if workspace else HOME_SYSTEM_PROMPT

    memory = ""
    if user_id:
        try:
            from cptr.utils.memory import build_memory_prompt

            memory = await build_memory_prompt(
                request,
                user_id,
                workspace,
                current_message=current_message,
                recent_messages=recent_messages or [],
                mentioned_files=mentioned_files or [],
            )
        except Exception:
            logger.debug("[memory] Failed to load managed memory", exc_info=True)

    if memory and "{{MEMORY}}" not in template:
        template = template.rstrip() + "\n\n{{MEMORY}}"

    try:
        skills_enabled = (await Config.get("skills.enabled")) not in (False, "false", "0")
    except Exception:
        skills_enabled = True

    home = None
    shell = None
    if user_id:
        try:
            identity = await identity_for_user_id(user_id)
            home = identity.home
            shell = identity.shell
        except Exception:
            logger.debug("[system_prompt] Failed to resolve user identity", exc_info=True)

    variables = _build_template_variables(workspace, model, memory, skills_enabled, home, shell)
    return _render_system_template(template, variables)

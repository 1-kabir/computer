"""Agent Skills support following the agentskills.io specification.

Skills are folders containing a SKILL.md file with YAML frontmatter
(name + description) and markdown instructions. Optional subdirectories
include scripts/, references/, and assets/.

Discovery follows progressive disclosure:
  1. Catalog (startup): name + description only (~50-100 tokens/skill)
  2. Instructions (activation): full SKILL.md body (<5000 tokens recommended)
  3. Resources (on-demand): scripts, references, assets loaded individually

See: https://agentskills.io/specification
"""

from __future__ import annotations

import asyncio
import logging
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ── Discovery paths ─────────────────────────────────────────

# Workspace-level: scan client-specific + cross-client convention paths.
# First match on skill name wins within the same scope.
WORKSPACE_SKILL_DIRS = [
    ".cptr/skills",
    ".agents/skills",
    ".claude/skills",
    ".codex/skills",
]

# Global (user-level): scan our own directory plus the cross-agent convention.
GLOBAL_SKILL_DIRS = [
    str(Path.home() / ".cptr" / "skills"),
    str(Path.home() / ".agents" / "skills"),
]

MANAGED_WORKSPACE_SKILL_DIR = ".cptr/skills"
MANAGED_GLOBAL_SKILL_DIR = Path.home() / ".cptr" / "skills"
ALLOWED_BUNDLE_DIRS = {"references", "templates", "scripts", "assets"}
DEFAULT_SKILL_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "tool_enabled": True,
    "background_review_enabled": True,
    "review_interval_turns": 10,
}
SKILL_USAGE_DEFAULT: dict[str, Any] = {
    "created_from": None,
    "view_count": 0,
    "use_count": 0,
    "update_count": 0,
    "last_viewed_at": None,
    "last_used_at": None,
    "last_updated_at": None,
}
USAGE_FILE_NAME = "usage.json"

# Directories to skip during scanning
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}

# Max scan limits (per spec recommendation)
_MAX_SCAN_DEPTH = 4
_MAX_SCAN_DIRS = 2000


# ── Data classes ────────────────────────────────────────────


@dataclass
class SkillMeta:
    """Tier 1: lightweight metadata for the catalog."""

    name: str
    description: str
    location: str  # absolute path to SKILL.md
    source: str  # "workspace" or "global"
    # Optional spec fields
    license: str | None = None
    compatibility: str | None = None
    metadata: dict | None = None
    allowed_tools: str | None = None
    managed: bool = False
    created_by: str | None = None
    created_from: str | None = None
    view_count: int = 0
    use_count: int = 0
    update_count: int = 0
    last_viewed_at: str | None = None
    last_used_at: str | None = None
    last_updated_at: str | None = None


@dataclass
class SkillContent(SkillMeta):
    """Tier 2: full instructions + resource listing."""

    content: str = ""  # full SKILL.md, including frontmatter
    body: str = ""  # markdown body after frontmatter
    resources: list[str] = field(default_factory=list)  # relative paths of bundled files


# ── Frontmatter parsing ────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split SKILL.md into (frontmatter_dict, body_string).

    Handles malformed YAML (unquoted colons) with a fallback retry.
    Returns ({}, body) if frontmatter is missing or unparseable.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text.strip()

    yaml_block = match.group(1)
    body = match.group(2).strip()

    # Try parsing YAML
    try:
        fm = yaml.safe_load(yaml_block)
        if isinstance(fm, dict):
            return fm, body
        return {}, body
    except yaml.YAMLError:
        pass

    # Fallback: try wrapping problematic values in quotes
    # Common issue: unquoted colons in description values
    fixed_lines = []
    for line in yaml_block.splitlines():
        if ":" in line and not line.strip().startswith("#"):
            key_part, _, val_part = line.partition(":")
            val = val_part.strip()
            # If value contains a colon and isn't already quoted
            if ":" in val and not (val.startswith('"') or val.startswith("'")):
                indent = len(line) - len(line.lstrip())
                fixed_lines.append(f'{" " * indent}{key_part.strip()}: "{val}"')
                continue
        fixed_lines.append(line)

    try:
        fm = yaml.safe_load("\n".join(fixed_lines))
        if isinstance(fm, dict):
            logger.debug("[skills] Parsed frontmatter with colon-quoting fallback")
            return fm, body
    except yaml.YAMLError:
        pass

    logger.warning("[skills] Unparseable YAML frontmatter")
    return {}, body


def _usage_path(workspace: str, source: str) -> Path:
    if source == "global":
        return MANAGED_GLOBAL_SKILL_DIR.expanduser().parent / USAGE_FILE_NAME
    if not workspace:
        raise ValueError("workspace is required for workspace usage")
    return Path(workspace).expanduser().resolve() / ".cptr" / USAGE_FILE_NAME


def read_usage(workspace: str, source: str = "workspace") -> dict[str, Any]:
    path = _usage_path(workspace, source)
    if not path.is_file():
        return {"version": 1, "skills": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "skills": {}}
    if not isinstance(data, dict):
        return {"version": 1, "skills": {}}
    skills = data.get("skills")
    if not isinstance(skills, dict):
        skills = {}
    return {"version": 1, "skills": skills}


def write_usage(workspace: str, source: str, data: dict[str, Any]) -> None:
    path = _usage_path(workspace, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def bump_skill_view(
    workspace: str, name: str, source: str = "workspace", *, used: bool = True
) -> None:
    try:
        data = read_usage(workspace, source)
        skills = data.setdefault("skills", {})
        rec = skills.setdefault(name, dict(SKILL_USAGE_DEFAULT))
        now = datetime.now(timezone.utc).isoformat()
        rec["view_count"] = int(rec.get("view_count") or 0) + 1
        rec["last_viewed_at"] = now
        if used:
            rec["use_count"] = int(rec.get("use_count") or 0) + 1
            rec["last_used_at"] = now
        write_usage(workspace, source, data)
    except Exception:
        logger.debug("[skills] Failed to bump view usage for %s", name, exc_info=True)


def bump_skill_update(
    workspace: str,
    name: str,
    source: str = "workspace",
    *,
    created_from: str | None = None,
) -> None:
    try:
        data = read_usage(workspace, source)
        skills = data.setdefault("skills", {})
        rec = skills.setdefault(name, dict(SKILL_USAGE_DEFAULT))
        rec["update_count"] = int(rec.get("update_count") or 0) + 1
        rec["last_updated_at"] = datetime.now(timezone.utc).isoformat()
        if created_from and not rec.get("created_from"):
            rec["created_from"] = created_from
        write_usage(workspace, source, data)
    except Exception:
        logger.debug("[skills] Failed to bump update usage for %s", name, exc_info=True)


def _frontmatter_bounds(content: str) -> tuple[dict, str] | None:
    if not content.startswith("---\n"):
        return None
    match = re.search(r"\n---\s*\n", content[4:])
    if not match:
        return None
    end = match.start() + 4
    fm_text = content[4:end]
    body = content[match.end() + 4 :]
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        fm = {}
    return fm if isinstance(fm, dict) else {}, body


def _with_skill_metadata(
    content: str,
    *,
    created_from: str | None = None,
    preserve_created: dict | None = None,
) -> str:
    parsed = _frontmatter_bounds(content)
    if not parsed:
        return content.strip() + "\n"
    fm, body = parsed
    now = datetime.now(timezone.utc).isoformat()
    if preserve_created:
        for key in ("created_by", "created_from", "created_at"):
            if preserve_created.get(key) and not fm.get(key):
                fm[key] = preserve_created[key]
    if created_from:
        fm.setdefault("created_by", "computer")
        fm.setdefault("created_from", created_from)
        fm.setdefault("created_at", now)
    if fm.get("created_by") == "computer" or created_from or preserve_created:
        fm["updated_at"] = now
    frontmatter = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{frontmatter}\n---\n{body.lstrip()}".strip() + "\n"


# ── Name validation ─────────────────────────────────────────

_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def validate_name(name: str, parent_dir: str) -> list[str]:
    """Validate skill name per spec. Returns list of warnings (lenient).

    Spec constraints:
    - 1-64 characters
    - Lowercase letters, numbers, and hyphens only
    - Must not start or end with a hyphen
    - Must not contain consecutive hyphens
    - Must match the parent directory name
    """
    warnings: list[str] = []

    if not name:
        warnings.append("name is empty")
        return warnings

    if len(name) > 64:
        warnings.append(f"name exceeds 64 characters ({len(name)})")

    if not _NAME_RE.match(name):
        warnings.append(
            f"name '{name}' contains invalid characters (must be lowercase alphanumeric + hyphens)"
        )

    if "--" in name:
        warnings.append(f"name '{name}' contains consecutive hyphens")

    if name != parent_dir:
        warnings.append(f"name '{name}' doesn't match parent directory '{parent_dir}'")

    return warnings


# ── Discovery ───────────────────────────────────────────────


def _is_managed_skill(skill_md: Path, workspace: str = "") -> bool:
    try:
        skill_dir = skill_md.parent.resolve()
        global_root = MANAGED_GLOBAL_SKILL_DIR.expanduser().resolve()
        if skill_dir.is_relative_to(global_root):
            return True
        if workspace:
            workspace_root = (Path(workspace) / MANAGED_WORKSPACE_SKILL_DIR).resolve()
            if skill_dir.is_relative_to(workspace_root):
                return True
    except OSError:
        pass
    return False


def _scan_skills_dir(skills_dir: Path, source: str, workspace: str = "") -> list[SkillMeta]:
    """Scan a single skills directory for skill folders containing SKILL.md."""
    results: list[SkillMeta] = []

    if not skills_dir.is_dir():
        return results

    dirs_scanned = 0
    usage = read_usage(workspace, source).get("skills", {})
    try:
        for item in sorted(skills_dir.iterdir()):
            if dirs_scanned >= _MAX_SCAN_DIRS:
                logger.warning("[skills] Hit max scan limit (%d dirs)", _MAX_SCAN_DIRS)
                break

            if not item.is_dir() or item.name in _SKIP_DIRS or item.name.startswith("."):
                continue

            dirs_scanned += 1
            skill_md = item / "SKILL.md"
            if not skill_md.is_file():
                continue

            try:
                text = skill_md.read_text(errors="replace")
            except OSError:
                logger.debug("[skills] Failed to read %s", skill_md)
                continue

            fm, _body = parse_frontmatter(text)

            name = str(fm.get("name", "")).strip()
            description = str(fm.get("description", "")).strip()

            # Skip if description is missing (per spec: essential for disclosure)
            if not description:
                logger.warning("[skills] Skipping %s: missing description", skill_md)
                continue

            # Use folder name as fallback if name is missing
            if not name:
                name = item.name
                logger.debug("[skills] Using folder name as skill name: %s", name)

            # Lenient validation: warn but load
            warnings = validate_name(name, item.name)
            for w in warnings:
                logger.warning("[skills] %s: %s", skill_md, w)
            rec = usage.get(name) if isinstance(usage, dict) else {}
            if not isinstance(rec, dict):
                rec = {}

            results.append(
                SkillMeta(
                    name=name,
                    description=description[:1024],
                    location=str(skill_md.resolve()),
                    source=source,
                    license=fm.get("license"),
                    compatibility=fm.get("compatibility"),
                    metadata=fm.get("metadata") if isinstance(fm.get("metadata"), dict) else None,
                    allowed_tools=fm.get("allowed-tools"),
                    managed=_is_managed_skill(skill_md, workspace),
                    created_by=fm.get("created_by"),
                    created_from=fm.get("created_from"),
                    view_count=int(rec.get("view_count") or 0),
                    use_count=int(rec.get("use_count") or 0),
                    update_count=int(rec.get("update_count") or 0),
                    last_viewed_at=rec.get("last_viewed_at"),
                    last_used_at=rec.get("last_used_at"),
                    last_updated_at=rec.get("last_updated_at"),
                )
            )

    except PermissionError:
        logger.debug("[skills] Permission denied scanning %s", skills_dir)

    return results


def discover_skills(workspace: str) -> list[SkillMeta]:
    """Discover all available skills from workspace + global directories.

    Scans workspace-level dirs first, then global. Deduplicates by name:
    workspace skills take precedence over global skills.

    Returns list of SkillMeta (frontmatter only, no body content).
    """
    seen_names: set[str] = set()
    all_skills: list[SkillMeta] = []

    ws = Path(workspace)

    # 1. Workspace-level skills (project-specific, git-trackable)
    if workspace and ws.is_dir():
        for rel_dir in WORKSPACE_SKILL_DIRS:
            skills_dir = ws / rel_dir
            for skill in _scan_skills_dir(skills_dir, source="workspace", workspace=workspace):
                if skill.name not in seen_names:
                    seen_names.add(skill.name)
                    all_skills.append(skill)
                else:
                    logger.debug(
                        "[skills] Skipping duplicate '%s' from %s (already found)",
                        skill.name,
                        skill.location,
                    )

    # 2. Global skills (user-level, available across all workspaces)
    for dir_path in GLOBAL_SKILL_DIRS:
        skills_dir = Path(dir_path).expanduser()
        for skill in _scan_skills_dir(skills_dir, source="global", workspace=workspace):
            if skill.name not in seen_names:
                seen_names.add(skill.name)
                all_skills.append(skill)
            else:
                logger.debug(
                    "[skills] Global skill '%s' shadowed by workspace skill",
                    skill.name,
                )

    if all_skills:
        logger.info(
            "[skills] Discovered %d skills (%d workspace, %d global)",
            len(all_skills),
            sum(1 for s in all_skills if s.source == "workspace"),
            sum(1 for s in all_skills if s.source == "global"),
        )

    return all_skills


# ── Loading (tier 2) ────────────────────────────────────────


def _enumerate_resources(skill_dir: Path) -> list[str]:
    """List all files in a skill folder (relative paths), excluding SKILL.md."""
    resources: list[str] = []

    def _walk(directory: Path, prefix: str = "", depth: int = 0):
        if depth >= _MAX_SCAN_DEPTH:
            return
        try:
            for item in sorted(directory.iterdir()):
                if item.name in _SKIP_DIRS:
                    continue
                rel = f"{prefix}{item.name}" if not prefix else f"{prefix}/{item.name}"
                if item.is_file() and item.name != "SKILL.md":
                    resources.append(rel)
                elif item.is_dir():
                    _walk(item, rel, depth + 1)
        except PermissionError:
            pass

    _walk(skill_dir)
    return resources


def load_skill(workspace: str, skill_name: str) -> SkillContent | None:
    """Load full skill content (tier 2): body + resource listing.

    Finds the skill by name using the same discovery order,
    reads the full SKILL.md, and enumerates bundled files.
    """
    # Find the skill's location
    skills = discover_skills(workspace)
    meta = next((s for s in skills if s.name == skill_name), None)
    if not meta:
        return None

    skill_md = Path(meta.location)
    try:
        text = skill_md.read_text(errors="replace")
    except OSError:
        logger.error("[skills] Failed to read %s", skill_md)
        return None

    fm, body = parse_frontmatter(text)
    skill_dir = skill_md.parent
    resources = _enumerate_resources(skill_dir)

    return SkillContent(
        name=meta.name,
        description=meta.description,
        location=meta.location,
        source=meta.source,
        license=meta.license,
        compatibility=meta.compatibility,
        metadata=meta.metadata,
        allowed_tools=meta.allowed_tools,
        managed=meta.managed,
        content=text,
        body=body,
        resources=resources,
    )


def _managed_root(workspace: str, scope: Literal["workspace", "global"]) -> Path:
    if scope == "global":
        return MANAGED_GLOBAL_SKILL_DIR.expanduser()
    if not workspace:
        raise ValueError("workspace is required for workspace skills")
    return Path(workspace).expanduser().resolve() / MANAGED_WORKSPACE_SKILL_DIR


def _validate_skill_name(name: str) -> str:
    normalized = (name or "").strip()
    warnings = validate_name(normalized, normalized)
    if warnings:
        raise ValueError("; ".join(warnings))
    return normalized


def _validate_skill_content(name: str, content: str) -> tuple[dict, str]:
    if not content or not content.strip():
        raise ValueError("content is required")
    fm, body = parse_frontmatter(content)
    skill_name = str(fm.get("name", "")).strip()
    description = str(fm.get("description", "")).strip()
    if skill_name != name:
        raise ValueError("frontmatter name must match skill name")
    if not description:
        raise ValueError("frontmatter description is required")
    if not body:
        raise ValueError("skill body is required")
    return fm, body


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def create_managed_skill(
    workspace: str,
    name: str,
    content: str,
    scope: Literal["workspace", "global"] = "workspace",
    created_from: str | None = None,
) -> dict:
    name = _validate_skill_name(name)
    content = _with_skill_metadata(content, created_from=created_from)
    _validate_skill_content(name, content)
    if any(skill.name == name for skill in discover_skills(workspace)):
        raise ValueError(f"skill '{name}' already exists")
    skill_dir = _managed_root(workspace, scope) / name
    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
        raise ValueError(f"skill '{name}' already exists")
    _write_text_atomic(skill_md, content.strip() + "\n")
    if created_from:
        bump_skill_update(workspace, name, scope, created_from=created_from)
    return {"success": True, "name": name, "location": str(skill_md.resolve())}


def update_managed_skill(workspace: str, name: str, content: str) -> dict:
    name = _validate_skill_name(name)
    skill_dir = _managed_skill_dir(workspace, name)
    skill_md = skill_dir / "SKILL.md"
    old = skill_md.read_text(encoding="utf-8", errors="replace") if skill_md.exists() else ""
    old_fm, _ = parse_frontmatter(old)
    content = _with_skill_metadata(content, preserve_created=old_fm)
    _validate_skill_content(name, content)
    _write_text_atomic(skill_md, content.strip() + "\n")
    source = (
        "global"
        if skill_dir.is_relative_to(MANAGED_GLOBAL_SKILL_DIR.expanduser().resolve())
        else "workspace"
    )
    bump_skill_update(workspace, name, source)
    return {"success": True, "name": name, "location": str(skill_md.resolve())}


def delete_managed_skill(workspace: str, name: str) -> dict:
    skill_dir = _managed_skill_dir(workspace, name)
    shutil.rmtree(skill_dir)
    return {"success": True, "name": name}


def _managed_skill_dir(workspace: str, name: str) -> Path:
    name = _validate_skill_name(name)
    skill = next((s for s in discover_skills(workspace) if s.name == name), None)
    if not skill:
        raise ValueError(f"skill '{name}' not found")
    if not skill.managed:
        raise ValueError(f"skill '{name}' is read-only in Computer")
    return Path(skill.location).parent.resolve()


def write_managed_skill_file(
    workspace: str, name: str, file_path: str, file_content: str | None
) -> dict:
    if file_content is None:
        raise ValueError("file_content is required")
    rel = Path(file_path or "")
    if not rel.parts:
        raise ValueError("file_path is required")
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("file_path must stay inside the skill")
    if rel.parts[0] not in ALLOWED_BUNDLE_DIRS:
        raise ValueError("file_path must start with references/, templates/, scripts/, or assets/")
    if any(part.startswith(".") for part in rel.parts):
        raise ValueError("dot paths are not allowed")
    skill_dir = _managed_skill_dir(workspace, name)
    target = (skill_dir / rel).resolve()
    if not target.is_relative_to(skill_dir):
        raise ValueError("file_path escapes the skill")
    _write_text_atomic(target, file_content)
    source = (
        "global"
        if skill_dir.is_relative_to(MANAGED_GLOBAL_SKILL_DIR.expanduser().resolve())
        else "workspace"
    )
    bump_skill_update(workspace, name, source)
    return {"success": True, "name": name, "location": str(target)}


# ── Catalog & structured output ─────────────────────────────


def build_catalog_xml(skills: list[SkillMeta]) -> str:
    """Build the <available_skills> XML catalog for the system prompt.

    Includes behavioral instructions telling the model how to use skills.
    Returns empty string if no skills.
    """
    if not skills:
        return ""

    lines = [
        "The following skills provide specialized instructions for specific tasks.",
        "When a task matches a skill's description, call the view_skill tool",
        "with the skill's name to load its full instructions.",
        "",
        "<available_skills>",
    ]

    for s in skills:
        lines.append("<skill>")
        lines.append(f"  <name>{s.name}</name>")
        lines.append(f"  <description>{s.description}</description>")
        lines.append(f"  <location>{s.location}</location>")
        lines.append("</skill>")

    lines.append("</available_skills>")
    return "\n".join(lines)


def format_skill_content(skill: SkillContent) -> str:
    """Format loaded skill content with structured wrapping (per spec).

    Wraps in <skill_content> tags with <skill_resources> listing
    so the model can identify skill instructions and available files.
    """
    skill_dir = str(Path(skill.location).parent)

    parts = [
        f'<skill_content name="{skill.name}">',
        skill.body,
        "",
        f"Skill directory: {skill_dir}",
        "Relative paths in this skill are relative to the skill directory.",
        "Load listed resources with view_skill using this skill name and file_path.",
    ]

    if skill.resources:
        parts.append("")
        parts.append("<skill_resources>")
        for r in skill.resources[:50]:  # cap listing for large dirs
            parts.append(f"  <file>{r}</file>")
        if len(skill.resources) > 50:
            parts.append(f"  <!-- ... and {len(skill.resources) - 50} more files -->")
        parts.append("</skill_resources>")

    parts.append("</skill_content>")
    return "\n".join(parts)


async def get_skill_settings() -> dict[str, Any]:
    try:
        from cptr.models import Config

        raw = await Config.get_namespace("skills")
    except Exception:
        raw = {}
    settings = dict(DEFAULT_SKILL_SETTINGS)
    for key, value in raw.items():
        short = key.removeprefix("skills.")
        settings[short] = value
    try:
        settings["review_interval_turns"] = max(1, int(settings["review_interval_turns"]))
    except (TypeError, ValueError):
        settings["review_interval_turns"] = DEFAULT_SKILL_SETTINGS["review_interval_turns"]
    settings["enabled"] = settings.get("enabled") not in (False, "false", "0")
    settings["tool_enabled"] = settings.get("tool_enabled") not in (False, "false", "0")
    settings["background_review_enabled"] = settings.get("background_review_enabled") not in (
        False,
        "false",
        "0",
    )
    return settings


async def review_skills_after_turn(
    *,
    workspace: str,
    conversation_messages: list[dict[str, Any]],
    assistant_reply: str,
    model_connection: dict | None,
    model: str,
    loaded_skill_names: set[str],
    tool_names: set[str],
    skill_create_requested: bool,
    plan_mode: bool,
    subagent: bool,
    skills_enabled: bool,
) -> None:
    if (
        not workspace
        or not assistant_reply.strip()
        or not model_connection
        or plan_mode
        or subagent
        or not skills_enabled
    ):
        return
    settings = await get_skill_settings()
    if (
        not settings["enabled"]
        or not settings["tool_enabled"]
        or not settings["background_review_enabled"]
    ):
        return
    if not tool_names and not skill_create_requested and not loaded_skill_names:
        return
    user_turns = sum(1 for message in conversation_messages if message.get("role") == "user")
    if user_turns <= 0 or user_turns % int(settings["review_interval_turns"]) != 0:
        return
    asyncio.create_task(
        run_skill_review(
            workspace=workspace,
            conversation_messages=list(conversation_messages),
            assistant_reply=assistant_reply,
            model_connection=dict(model_connection),
            model=model,
            loaded_skill_names=set(loaded_skill_names),
            tool_names=set(tool_names),
            skill_create_requested=skill_create_requested,
        )
    )


async def run_skill_review(
    *,
    workspace: str,
    conversation_messages: list[dict[str, Any]],
    assistant_reply: str,
    model_connection: dict,
    model: str,
    loaded_skill_names: set[str],
    tool_names: set[str],
    skill_create_requested: bool,
) -> None:
    try:
        from cptr.utils.ai import generate_json
        from cptr.utils.memory import summarize_recent_conversation
        from cptr.utils.utility_models import configured_utility_model

        skills = discover_skills(workspace)
        transcript = summarize_recent_conversation(conversation_messages, assistant_reply)
        catalog = (
            "\n".join(
                f"- {skill.name} ({skill.source}, {'managed' if skill.managed else 'read-only'}): {skill.description}"
                for skill in skills[:80]
            )
            or "- none"
        )
        loaded_blocks = []
        for name in sorted(loaded_skill_names):
            skill = load_skill(workspace, name)
            if not skill or not skill.managed:
                continue
            content = skill.content
            if len(content) > 5000:
                content = content[:4500] + "\n...(truncated)..."
            loaded_blocks.append(f"## {name}\n{content}")
        loaded_context = "\n\n".join(loaded_blocks) or "None."
        prompt = (
            "Review the completed conversation and decide whether Computer learned a reusable "
            "workflow that should become or improve a skill. Return ONLY JSON with this shape:\n"
            '{"actions":[{"action":"create|update|write_file|none","name":"skill-name",'
            '"reason":"short reason","content":"full SKILL.md for create/update",'
            '"file_path":"references/example.md","file_content":"support file content"}]}\n\n'
            "Rules:\n"
            "- Use action=none when nothing durable was learned.\n"
            "- Prefer updating a loaded managed skill, then another managed skill, then writing a support file, then creating one new workspace skill.\n"
            "- Create or update only for reusable procedures, user corrections, durable commands, verification steps, or workflows likely to recur.\n"
            "- Do not save one-off task summaries, transient local failures, secrets, vague preferences, or project facts better suited for memory.\n"
            "- Never delete. Never update read-only skills. Never write outside references/, templates/, scripts/, or assets/.\n\n"
            f"Workspace: {workspace}\n"
            f"Tools used this turn: {', '.join(sorted(tool_names)) or 'none'}\n"
            f"/skills:create requested: {skill_create_requested}\n"
            f"Loaded skills: {', '.join(sorted(loaded_skill_names)) or 'none'}\n\n"
            f"Available skills:\n{catalog}\n\n"
            f"Loaded managed skill contents:\n{loaded_context}\n\n"
            f"Conversation:\n{transcript}"
        )
        parsed = await generate_json(
            None,
            model_id=await configured_utility_model("skills_background_review"),
            active_connection=model_connection,
            active_model=model,
            messages=[{"role": "user", "content": prompt}],
            system="You are Computer's private skill reviewer. Return only valid JSON.",
            max_tokens=1800,
        )
        if not isinstance(parsed, dict):
            return
        actions = parsed.get("actions")
        if not isinstance(actions, list):
            return
        managed = {skill.name for skill in discover_skills(workspace) if skill.managed}
        for action in actions[:3]:
            if not isinstance(action, dict):
                continue
            kind = action.get("action")
            name = str(action.get("name") or "").strip()
            if kind == "none":
                continue
            if kind == "create":
                content = action.get("content")
                if isinstance(content, str) and content.strip():
                    create_managed_skill(
                        workspace,
                        name,
                        content,
                        scope="workspace",
                        created_from="background_review",
                    )
            elif kind == "update":
                content = action.get("content")
                if name in managed and isinstance(content, str) and content.strip():
                    update_managed_skill(workspace, name, content)
            elif kind == "write_file":
                file_path = action.get("file_path")
                file_content = action.get("file_content")
                if name in managed and isinstance(file_path, str) and isinstance(file_content, str):
                    write_managed_skill_file(workspace, name, file_path, file_content)
    except Exception:
        logger.debug("[skills] Failed to review conversation for skills", exc_info=True)

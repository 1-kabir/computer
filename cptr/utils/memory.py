"""File-backed managed memory for cptr."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request
from cptr.env import DATA_DIR
from cptr.models import Config
from cptr.utils.runtime import FileError, Runtime
from cptr.utils.workspace import ensure_cptr_gitignored

MEMORY_DIR_NAME = "memory"

DEFAULT_MEMORY_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "tool_enabled": True,
    "background_review_enabled": True,
    "background_review.model": None,
    "review_interval_turns": 10,
    "user_char_limit": 2000,
    "workspace_char_limit": 3000,
}

_memory_file_locks: dict[str, asyncio.Lock] = {}
_reviewed_messages: set[str] = set()


@dataclass(frozen=True)
class MemoryFile:
    path: Path
    character_limit: int


@dataclass(frozen=True)
class MemoryRoot:
    scope: str
    root: Path
    baseline_path: Path


@dataclass(frozen=True)
class MemorySnippet:
    scope: str
    path: str
    heading: str
    memory_id: str
    snippet: str
    links: list[str]
    reason: str


@dataclass(frozen=True)
class MemoryContext:
    user: list[MemorySnippet]
    workspace: list[MemorySnippet]


MEMORY_MARKER_RE = re.compile(r"<!--\s*mem:\s*([^>]+?)\s*-->")
WIKI_LINK_RE = re.compile(r"\[\[([^\]\n|]+)(?:\|[^\]\n]+)?\]\]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
QUERY_STOP_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "before",
    "being",
    "could",
    "from",
    "have",
    "into",
    "just",
    "like",
    "memory",
    "should",
    "that",
    "their",
    "there",
    "these",
    "thing",
    "this",
    "with",
    "would",
}
PROMPT_THREAT_RE = re.compile(
    r"(?i)\b("
    r"ignore (all )?(previous|prior|above) instructions|"
    r"system prompt|developer message|"
    r"reveal (secrets|credentials|api keys|tokens)|"
    r"exfiltrate|"
    r"override (the )?(system|developer|user)"
    r")\b"
)


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return cleaned or "user"


def normalize_workspace_path(workspace: str) -> str:
    if not workspace:
        raise ValueError("workspace is required for workspace memory")
    return str(Path(workspace).expanduser().resolve())


def _user_memory_root(user_id: str) -> Path:
    return DATA_DIR / MEMORY_DIR_NAME / "users" / _safe_id(user_id)


def _workspace_memory_root(user_id: str, workspace: str) -> Path:
    root = Path(normalize_workspace_path(workspace))
    return root / ".cptr" / MEMORY_DIR_NAME / "users" / _safe_id(user_id)


def user_memory_path(user_id: str) -> Path:
    return _user_memory_root(user_id) / "USER.md"


def workspace_memory_path(user_id: str, workspace: str) -> Path:
    return _workspace_memory_root(user_id, workspace) / "WORKSPACE.md"


def resolve_memory_roots(user_id: str, workspace: str = "") -> list[MemoryRoot]:
    roots = [
        MemoryRoot(
            scope="user",
            root=_user_memory_root(user_id),
            baseline_path=user_memory_path(user_id),
        )
    ]
    if workspace:
        roots.append(
            MemoryRoot(
                scope="workspace",
                root=_workspace_memory_root(user_id, workspace),
                baseline_path=workspace_memory_path(user_id, workspace),
            )
        )
    return roots


def _memory_file_lock(path: Path) -> asyncio.Lock:
    return _memory_file_locks.setdefault(str(path), asyncio.Lock())


def read_memory_entries(path: Path) -> list[str]:
    if not path.is_file():
        return []
    entries: list[str] = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        entry = line[2:].strip()
        if entry:
            entries.append(entry)
    return entries


def format_memory_entries(entries: list[str]) -> str:
    rendered = "\n".join(f"- {normalize_memory_text(entry)}" for entry in entries if entry.strip())
    return f"{rendered}\n" if rendered else ""


def write_memory_entries(path: Path, entries: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(format_memory_entries(entries), encoding="utf-8")
    os.replace(tmp, path)


def measure_memory_entries(entries: list[str]) -> int:
    if not entries:
        return 0
    return len("\n".join(entries))


def _file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _trash_dir_for(path: Path, root: Path | None = None) -> Path:
    return (root or path.parent) / ".trash"


def _backup_to_trash(path: Path, reason: str = "backup", root: Path | None = None) -> Path:
    trash = _trash_dir_for(path, root)
    trash.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d%H%M%S")
    backup = trash / f"{path.name}.{reason}.{stamp}.{uuid.uuid4().hex[:8]}.bak"
    if path.exists():
        shutil.copy2(path, backup)
    else:
        backup.write_text("", encoding="utf-8")
    return backup


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _safe_relative_path(root: Path, value: str) -> Path:
    if not value:
        raise ValueError("path is required")
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError("path must be relative")
    resolved = (root / candidate).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError("path escapes memory root")
    return resolved


def _relative_to_root(path: Path, root: Path) -> Path:
    return path.resolve().relative_to(root.resolve())


def _is_trash_path(path: Path) -> bool:
    return ".trash" in path.parts


def _is_markdown_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".md" and not path.name.startswith(".")


def _iter_markdown_files(root: Path, include_trash: bool = False) -> list[Path]:
    if not root.is_dir():
        return []
    files: list[Path] = []
    for path in root.rglob("*.md"):
        if not _is_markdown_file(path):
            continue
        if not include_trash and _is_trash_path(_relative_to_root(path, root)):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.stat().st_mtime, reverse=True)


def _extract_wiki_links(text: str) -> list[str]:
    seen: set[str] = set()
    links: list[str] = []
    for match in WIKI_LINK_RE.finditer(text):
        link = match.group(1).strip()
        if link and link not in seen:
            seen.add(link)
            links.append(link)
    return links


def _extract_memory_id(text: str) -> str:
    match = MEMORY_MARKER_RE.search(text)
    return match.group(1).strip() if match else ""


def _split_markdown_sections(text: str, fallback_heading: str = "") -> list[dict[str, Any]]:
    lines = text.splitlines()
    if not lines:
        return []
    headings: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))
    if not headings:
        content = "\n".join(lines).strip()
        return (
            [
                {
                    "heading": fallback_heading,
                    "memory_id": _extract_memory_id(content),
                    "content": content,
                    "start": 0,
                    "end": len(lines),
                }
            ]
            if content
            else []
        )

    sections: list[dict[str, Any]] = []
    for pos, (start, level, heading) in enumerate(headings):
        end = len(lines)
        for next_start, next_level, _ in headings[pos + 1 :]:
            if next_level <= level:
                end = next_start
                break
        content = "\n".join(lines[start:end]).strip()
        sections.append(
            {
                "heading": heading,
                "memory_id": _extract_memory_id(content),
                "content": content,
                "start": start,
                "end": end,
            }
        )
    return sections


def _snippet_for(content: str, query: str | None = None, limit: int = 700) -> str:
    text = "\n".join(line.rstrip() for line in content.strip().splitlines())
    if len(text) <= limit:
        return text
    if query:
        lowered = text.lower()
        terms = [term.lower() for term in re.findall(r"[A-Za-z0-9_.-]{3,}", query)]
        matches = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
        if matches:
            center = min(matches)
            start = max(0, center - limit // 3)
            end = min(len(text), start + limit)
            prefix = "..." if start else ""
            suffix = "..." if end < len(text) else ""
            return prefix + text[start:end].strip() + suffix
    return text[:limit].strip() + "..."


def _query_terms(query: str, limit: int = 16) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9_.-]{3,}", query.lower()):
        term = raw.strip(".-")
        if not term or term in QUERY_STOP_WORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def _section_matches(section: dict[str, Any], query: str) -> bool:
    haystack = f"{section.get('heading', '')}\n{section.get('memory_id', '')}\n{section.get('content', '')}"
    lowered = haystack.lower()
    return any(term in lowered for term in _query_terms(query))


def _path_match_reason(path: Path, root: Path, query: str) -> bool:
    rel = str(_relative_to_root(path, root)).lower()
    return any(term in rel for term in _query_terms(query))


def _shape_snippet(
    *,
    root: MemoryRoot,
    path: Path,
    section: dict[str, Any],
    query: str | None,
    reason: str,
) -> MemorySnippet:
    return MemorySnippet(
        scope=root.scope,
        path=str(_relative_to_root(path, root.root)),
        heading=str(section.get("heading") or path.stem),
        memory_id=str(section.get("memory_id") or ""),
        snippet=_snippet_for(str(section.get("content") or ""), query),
        links=_extract_wiki_links(str(section.get("content") or "")),
        reason=reason,
    )


def _rg_candidate_files(root: Path, query: str, include_trash: bool) -> list[Path] | None:
    terms = _query_terms(query)
    if not terms:
        return None
    try:
        args = [
            "rg",
            "--ignore-case",
            "--files-with-matches",
            "--glob",
            "*.md",
        ]
        if not include_trash:
            args.extend(["--glob", "!.trash/**"])
        for term in terms:
            args.extend(["-e", term])
        args.append(str(root))
        result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode not in {0, 1}:
        return None
    files = [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]
    return [path for path in files if path.is_file()]


def _find_memory_marker(
    roots: list[MemoryRoot],
    memory_id: str,
    include_trash: bool = False,
) -> list[MemorySnippet]:
    results: list[MemorySnippet] = []
    needle = memory_id.strip()
    if not needle:
        return results
    for root in roots:
        for path in _iter_markdown_files(root.root, include_trash=include_trash):
            text = path.read_text(errors="replace")
            for section in _split_markdown_sections(text, path.stem):
                if section.get("memory_id") == needle:
                    results.append(
                        _shape_snippet(
                            root=root,
                            path=path,
                            section=section,
                            query=needle,
                            reason="memory marker matched",
                        )
                    )
    return results


def _resolve_wiki_link(
    roots: list[MemoryRoot],
    link: str,
    include_trash: bool = False,
) -> MemorySnippet | None:
    target = link.strip().lower()
    if not target:
        return None
    target_path = target.replace(" ", "-")
    for root in roots:
        for path in _iter_markdown_files(root.root, include_trash=include_trash):
            rel = str(_relative_to_root(path, root.root)).lower()
            stem = path.stem.lower()
            if target in {stem, rel, rel.removesuffix(".md")} or target_path in {
                stem,
                rel,
                rel.removesuffix(".md"),
            }:
                text = path.read_text(errors="replace")
                sections = _split_markdown_sections(text, path.stem)
                if sections:
                    return _shape_snippet(
                        root=root,
                        path=path,
                        section=sections[0],
                        query=link,
                        reason=f"expanded wiki link [[{link}]]",
                    )
            text = path.read_text(errors="replace")
            for section in _split_markdown_sections(text, path.stem):
                if str(section.get("heading") or "").strip().lower() == target:
                    return _shape_snippet(
                        root=root,
                        path=path,
                        section=section,
                        query=link,
                        reason=f"expanded wiki link [[{link}]]",
                    )
    return None


def search_memory_files_sync(
    roots: list[MemoryRoot],
    *,
    query: str | None = None,
    path: str | None = None,
    memory_id: str | None = None,
    expand_links: bool = False,
    include_trash: bool = False,
    limit: int = 8,
) -> list[MemorySnippet]:
    limit = max(1, min(int(limit or 8), 50))
    results: list[MemorySnippet] = []

    if memory_id:
        results = _find_memory_marker(roots, memory_id, include_trash=include_trash)
    elif path:
        for root in roots:
            try:
                target = _safe_relative_path(root.root, path)
            except ValueError:
                continue
            if target.is_file() and target.suffix.lower() == ".md":
                text = target.read_text(errors="replace")
                for section in _split_markdown_sections(text, target.stem):
                    results.append(
                        _shape_snippet(
                            root=root,
                            path=target,
                            section=section,
                            query=query,
                            reason="path read",
                        )
                    )
                    if len(results) >= limit:
                        break
            if results:
                break
    elif query and query.strip():
        for root in roots:
            candidate_files = _rg_candidate_files(root.root, query, include_trash)
            if candidate_files is None:
                candidate_files = _iter_markdown_files(root.root, include_trash=include_trash)
            for candidate in candidate_files:
                if not candidate.is_file() or candidate.suffix.lower() != ".md":
                    continue
                if not include_trash and _is_trash_path(_relative_to_root(candidate, root.root)):
                    continue
                text = candidate.read_text(errors="replace")
                sections = _split_markdown_sections(text, candidate.stem)
                path_matched = _path_match_reason(candidate, root.root, query)
                for section in sections:
                    if _section_matches(section, query) or path_matched:
                        reason = "query matched path" if path_matched else "query matched text"
                        results.append(
                            _shape_snippet(
                                root=root,
                                path=candidate,
                                section=section,
                                query=query,
                                reason=reason,
                            )
                        )
                    if len(results) >= limit:
                        break
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
    else:
        for root in roots:
            for candidate in _iter_markdown_files(root.root, include_trash=include_trash):
                if candidate == root.baseline_path:
                    continue
                text = candidate.read_text(errors="replace")
                sections = _split_markdown_sections(text, candidate.stem)
                if not sections:
                    continue
                results.append(
                    _shape_snippet(
                        root=root,
                        path=candidate,
                        section=sections[0],
                        query=None,
                        reason="recent markdown file",
                    )
                )
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

    if expand_links and results:
        seen = {(item.scope, item.path, item.heading, item.memory_id) for item in results}
        for item in list(results):
            root_candidates = [root for root in roots if root.scope == item.scope] or roots
            for link in item.links[:5]:
                linked = _resolve_wiki_link(root_candidates, link, include_trash=include_trash)
                if not linked:
                    continue
                key = (linked.scope, linked.path, linked.heading, linked.memory_id)
                if key in seen:
                    continue
                seen.add(key)
                results.append(linked)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
    return results[:limit]


async def search_memory_files(
    roots: list[MemoryRoot],
    *,
    query: str | None = None,
    path: str | None = None,
    memory_id: str | None = None,
    expand_links: bool = False,
    include_trash: bool = False,
    limit: int = 8,
) -> list[MemorySnippet]:
    return await asyncio.to_thread(
        search_memory_files_sync,
        roots,
        query=query,
        path=path,
        memory_id=memory_id,
        expand_links=expand_links,
        include_trash=include_trash,
        limit=limit,
    )


def _snippet_dict(snippet: MemorySnippet) -> dict[str, Any]:
    return {
        "scope": snippet.scope,
        "path": snippet.path,
        "heading": snippet.heading,
        "memory_id": snippet.memory_id,
        "snippet": snippet.snippet,
        "links": snippet.links,
        "reason": snippet.reason,
    }


UNSAFE_MEMORY_CHARACTERS = ("\x00", "\u200b", "\u200c", "\u200d", "\ufeff")


def normalize_memory_text(content: str) -> str:
    return " ".join(str(content).split())


def memory_text_error(content: str) -> str | None:
    """Return a rejection reason for text that cannot be safely stored as memory."""
    if any(ch in content for ch in UNSAFE_MEMORY_CHARACTERS):
        return "memory content contains invisible or null characters"
    if PROMPT_THREAT_RE.search(content):
        return "memory content looks like prompt-injection or credential-exfiltration instructions"
    return None


def _prompt_safe_text(content: str, source: str) -> str:
    if PROMPT_THREAT_RE.search(content):
        return f"[BLOCKED: {source} contained unsafe instruction-like memory and was excluded.]"
    return content


def _trim_prompt_text(content: str, budget: int) -> str:
    text = content.strip()
    if len(text) <= budget:
        return text
    head = max(0, budget - 140)
    return text[:head].rstrip() + "\n...(memory trimmed to fit budget)..."


def _message_content_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, list):
        return " ".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content or "")


def _current_recall_query(
    current_message: str,
    recent_messages: list[dict[str, Any]],
    mentioned_files: list[str],
) -> str:
    parts: list[str] = []
    if current_message:
        parts.append(current_message)
    for message in recent_messages[-6:]:
        if message.get("role") == "user":
            parts.append(_message_content_text(message))
    parts.extend(mentioned_files[:8])
    text = "\n".join(part.strip() for part in parts if part and part.strip())
    return text[-4000:]


def _extract_baseline_links(text: str) -> list[str]:
    links = _extract_wiki_links(text)
    headings = [match.group(2).strip() for match in HEADING_RE.finditer(text)]
    return [*links[:10], *headings[:6]]


def _read_baseline_block(path: Path, budget: int) -> str:
    if not path.is_file() or budget <= 0:
        return ""
    text = path.read_text(errors="replace").strip()
    if not text:
        return ""
    return _trim_prompt_text(_prompt_safe_text(text, path.name), budget)


def _render_context_snippets(snippets: list[MemorySnippet], budget: int) -> str:
    if budget <= 0:
        return ""
    lines: list[str] = []
    remaining = budget
    for snippet in snippets:
        if remaining <= 80:
            break
        title = f"{snippet.path}"
        if snippet.heading:
            title += f" > {snippet.heading}"
        text = _prompt_safe_text(snippet.snippet, title)
        compact = " ".join(text.split())
        line = f"- {title}: {compact}"
        if len(line) > remaining:
            line = line[: max(0, remaining - 20)].rstrip() + "..."
        lines.append(line)
        remaining -= len(line) + 1
    return "\n".join(lines)


def _slugify_heading(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or f"memory-{uuid.uuid4().hex[:8]}"


def _operation_uses_markdown(operation: dict[str, Any]) -> bool:
    action = operation.get("action")
    return bool(
        action in {"link", "move", "split", "merge"}
        or operation.get("path")
        or operation.get("new_path")
        or operation.get("target_path")
        or operation.get("source_path")
        or operation.get("memory_id")
        or operation.get("heading")
    )


def _find_section_bounds(
    text: str, *, memory_id: str = "", heading: str = ""
) -> tuple[int, int] | None:
    sections = _split_markdown_sections(text)
    for section in sections:
        if memory_id and section.get("memory_id") == memory_id:
            return int(section["start"]), int(section["end"])
        if heading and str(section.get("heading", "")).strip().lower() == heading.strip().lower():
            return int(section["start"]), int(section["end"])
    return None


def _replace_lines(text: str, start: int, end: int, replacement: str) -> str:
    lines = text.splitlines()
    next_lines = [*lines[:start], *replacement.strip().splitlines(), *lines[end:]]
    return "\n".join(next_lines).rstrip() + "\n"


def _remove_lines(text: str, start: int, end: int) -> str:
    lines = text.splitlines()
    next_lines = [*lines[:start], *lines[end:]]
    return "\n".join(next_lines).rstrip() + ("\n" if next_lines else "")


def _write_if_unchanged(
    path: Path,
    original_hash: str,
    content: str,
    root: Path | None = None,
) -> tuple[bool, str, str]:
    current_hash = _file_hash(path)
    if current_hash != original_hash:
        backup = _backup_to_trash(path, "conflict", root)
        return (
            False,
            f"refusing to overwrite changed memory file; backup saved to {backup}",
            str(backup),
        )
    _atomic_write_text(path, content)
    return True, "written", ""


def _append_memory_section(existing: str, operation: dict[str, Any], default_title: str) -> str:
    content = str(operation.get("content") or "").strip()
    heading = str(operation.get("heading") or operation.get("title") or default_title).strip()
    memory_id = str(operation.get("memory_id") or "").strip()
    if not heading:
        heading = "Memory"
    pieces = [f"## {heading}"]
    if memory_id:
        pieces.append(f"<!-- mem: {memory_id} -->")
    pieces.append(content)
    section = "\n\n".join(piece for piece in pieces if piece)
    return existing.rstrip() + ("\n\n" if existing.strip() else "") + section.strip() + "\n"


def _add_related_link(section_text: str, link: str) -> str:
    link = link.strip()
    if not link:
        return section_text
    rendered = f"[[{link}]]"
    if rendered in section_text:
        return section_text
    related_re = re.compile(r"(?im)^Related:\s*(.+)$")
    match = related_re.search(section_text)
    if match:
        start, end = match.span(1)
        return section_text[:end] + f" {rendered}" + section_text[end:]
    return section_text.rstrip() + f"\n\nRelated: {rendered}\n"


def apply_markdown_memory_batch(
    root: MemoryRoot, operations: list[dict[str, Any]]
) -> dict[str, Any]:
    if not operations:
        return {"success": False, "error": "operations list is empty"}

    touched: dict[Path, tuple[str, str]] = {}
    messages: list[str] = []

    def read_target(operation: dict[str, Any]) -> Path:
        value = str(operation.get("path") or "")
        return _safe_relative_path(root.root, value) if value else root.baseline_path

    def load(path: Path) -> str:
        if path not in touched:
            touched[path] = (
                _file_hash(path),
                path.read_text(errors="replace") if path.exists() else "",
            )
        return touched[path][1]

    def store(path: Path, content: str) -> None:
        original_hash, _ = touched.get(path, ("", ""))
        touched[path] = (original_hash, content)

    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            return {"success": False, "error": f"Operation {index + 1}: must be an object"}
        action = operation.get("action")
        operation_name = f"Operation {index + 1} ({action or 'unknown'})"

        if action == "add":
            content = str(operation.get("content") or "").strip()
            if not content:
                return {"success": False, "error": f"{operation_name}: content is required"}
            validation_error = memory_text_error(content)
            if validation_error:
                backup = _backup_to_trash(root.baseline_path, "unsafe", root.root)
                return {
                    "success": False,
                    "error": f"{operation_name}: {validation_error}; unsafe content not saved",
                    "backup": str(backup),
                }
            target = read_target(operation)
            existing = load(target)
            title = target.stem.replace("-", " ").replace("_", " ").title()
            next_text = _append_memory_section(existing, operation, title)
            store(target, next_text)
            messages.append(f"added memory section to {_relative_to_root(target, root.root)}")

        elif action == "replace":
            content = str(operation.get("content") or "").strip()
            old_text = str(operation.get("old_text") or "").strip()
            if not content:
                return {"success": False, "error": f"{operation_name}: content is required"}
            validation_error = memory_text_error(content)
            if validation_error:
                return {"success": False, "error": f"{operation_name}: {validation_error}"}
            target = read_target(operation)
            existing = load(target)
            bounds = _find_section_bounds(
                existing,
                memory_id=str(operation.get("memory_id") or ""),
                heading=str(operation.get("heading") or ""),
            )
            if bounds:
                start, end = bounds
                heading = str(operation.get("heading") or operation.get("title") or "Memory")
                marker = str(operation.get("memory_id") or "").strip()
                replacement = _append_memory_section(
                    "", {**operation, "heading": heading, "memory_id": marker}, heading
                )
                store(target, _replace_lines(existing, start, end, replacement))
            elif old_text and old_text in existing:
                store(target, existing.replace(old_text, content, 1))
            else:
                return {"success": False, "error": f"{operation_name}: no matching section or text"}
            messages.append(f"replaced memory in {_relative_to_root(target, root.root)}")

        elif action == "remove":
            target = read_target(operation)
            existing = load(target)
            bounds = _find_section_bounds(
                existing,
                memory_id=str(operation.get("memory_id") or ""),
                heading=str(operation.get("heading") or ""),
            )
            old_text = str(operation.get("old_text") or "").strip()
            if bounds:
                _backup_to_trash(target, "remove", root.root)
                store(target, _remove_lines(existing, *bounds))
            elif old_text and old_text in existing:
                _backup_to_trash(target, "remove", root.root)
                store(target, existing.replace(old_text, "", 1))
            else:
                return {"success": False, "error": f"{operation_name}: no matching section or text"}
            messages.append(f"removed memory from {_relative_to_root(target, root.root)}")

        elif action == "link":
            link = str(
                operation.get("link") or operation.get("target") or operation.get("content") or ""
            ).strip()
            if not link:
                return {"success": False, "error": f"{operation_name}: link is required"}
            target = read_target(operation)
            existing = load(target)
            bounds = _find_section_bounds(
                existing,
                memory_id=str(operation.get("memory_id") or ""),
                heading=str(operation.get("heading") or ""),
            )
            if bounds:
                start, end = bounds
                lines = existing.splitlines()
                section_text = "\n".join(lines[start:end])
                store(
                    target,
                    _replace_lines(existing, start, end, _add_related_link(section_text, link)),
                )
            else:
                store(target, _add_related_link(existing, link))
            messages.append(f"linked memory in {_relative_to_root(target, root.root)}")

        elif action == "move":
            source = read_target(operation)
            destination = _safe_relative_path(root.root, str(operation.get("new_path") or ""))
            if not source.exists():
                return {"success": False, "error": f"{operation_name}: source path does not exist"}
            if destination.exists():
                return {"success": False, "error": f"{operation_name}: destination already exists"}
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            messages.append(
                f"moved {_relative_to_root(source, root.root)} to {_relative_to_root(destination, root.root)}"
            )

        elif action == "split":
            target = read_target(operation)
            existing = load(target)
            sections = [
                section
                for section in _split_markdown_sections(existing, target.stem)
                if section.get("content")
            ]
            if len(sections) < 2:
                return {
                    "success": False,
                    "error": f"{operation_name}: file does not have multiple sections",
                }
            folder = target.with_suffix("")
            folder.mkdir(parents=True, exist_ok=True)
            _backup_to_trash(target, "split", root.root)
            map_lines = [f"# {target.stem.replace('-', ' ').title()}", ""]
            for section in sections:
                heading = str(section.get("heading") or "Memory")
                child = folder / f"{_slugify_heading(heading)}.md"
                child_text = str(section.get("content") or "").strip() + "\n"
                if not child.exists():
                    _atomic_write_text(child, child_text)
                map_lines.append(f"- [[{heading}]]")
            store(target, "\n".join(map_lines).rstrip() + "\n")
            messages.append(
                f"split {_relative_to_root(target, root.root)} into {len(sections)} files"
            )

        elif action == "merge":
            source = _safe_relative_path(
                root.root, str(operation.get("source_path") or operation.get("path") or "")
            )
            target = _safe_relative_path(root.root, str(operation.get("target_path") or ""))
            if not source.exists() or not target.exists():
                return {
                    "success": False,
                    "error": f"{operation_name}: source and target must exist",
                }
            source_text = source.read_text(errors="replace")
            target_text = load(target)
            _backup_to_trash(source, "merge", root.root)
            store(target, target_text.rstrip() + "\n\n" + source_text.strip() + "\n")
            source.unlink()
            messages.append(
                f"merged {_relative_to_root(source, root.root)} into {_relative_to_root(target, root.root)}"
            )

        else:
            return {
                "success": False,
                "error": f"{operation_name}: unknown action; use add, replace, remove, link, move, split, or merge",
            }

    for path, (original_hash, content) in touched.items():
        success, message, backup = _write_if_unchanged(path, original_hash, content, root.root)
        if not success:
            return {"success": False, "error": message, "backup": backup, "path": str(path)}

    return {
        "success": True,
        "message": "; ".join(messages) or "No changes.",
        "path": str(root.root),
    }


async def get_memory_settings() -> dict[str, Any]:
    settings = {**DEFAULT_MEMORY_SETTINGS}
    for key in DEFAULT_MEMORY_SETTINGS:
        value = await Config.get(f"memory.{key}")
        if value is not None:
            settings[key] = value
    settings["review_interval_turns"] = max(1, int(settings.get("review_interval_turns") or 10))
    settings["user_char_limit"] = max(250, int(settings.get("user_char_limit") or 2000))
    settings["workspace_char_limit"] = max(250, int(settings.get("workspace_char_limit") or 3000))
    return settings


async def save_memory_settings(updates: dict[str, Any]) -> dict[str, Any]:
    await Config.upsert(
        {f"memory.{key}": value for key, value in updates.items() if key in DEFAULT_MEMORY_SETTINGS}
    )
    return await get_memory_settings()


async def resolve_memory_file(user_id: str, workspace: str, scope: str) -> MemoryFile:
    settings = await get_memory_settings()
    if scope == "user":
        return MemoryFile(
            path=user_memory_path(user_id),
            character_limit=int(settings["user_char_limit"]),
        )
    if scope == "workspace":
        return MemoryFile(
            path=workspace_memory_path(user_id, workspace),
            character_limit=int(settings["workspace_char_limit"]),
        )
    raise ValueError("scope must be 'user' or 'workspace'")


def apply_memory_batch(
    current_entries: list[str],
    operations: list[dict[str, Any]],
    character_limit: int,
) -> tuple[bool, str, list[str], str]:
    current_usage = f"{measure_memory_entries(current_entries)}/{character_limit}"
    if not operations:
        return False, "operations list is empty", current_entries, current_usage

    next_entries = list(current_entries)
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            return (
                False,
                f"Operation {index + 1}: must be an object",
                current_entries,
                current_usage,
            )
        action = operation.get("action")
        content = normalize_memory_text(str(operation.get("content") or ""))
        old_text = normalize_memory_text(str(operation.get("old_text") or ""))
        operation_name = f"Operation {index + 1} ({action or 'unknown'})"

        if action in {"add", "replace"}:
            validation_error = memory_text_error(content)
            if validation_error:
                return (
                    False,
                    f"{operation_name}: {validation_error}",
                    current_entries,
                    current_usage,
                )

        if action == "add":
            if not content:
                return (
                    False,
                    f"{operation_name}: content is required",
                    current_entries,
                    current_usage,
                )
            if content not in next_entries:
                next_entries.append(content)
        elif action == "replace":
            if not old_text:
                return (
                    False,
                    f"{operation_name}: old_text is required",
                    current_entries,
                    current_usage,
                )
            if not content:
                return (
                    False,
                    f"{operation_name}: content is required",
                    current_entries,
                    current_usage,
                )
            matches = [i for i, entry in enumerate(next_entries) if old_text in entry]
            if not matches:
                return (
                    False,
                    f"{operation_name}: no entry matched '{old_text}'",
                    current_entries,
                    current_usage,
                )
            if len({next_entries[i] for i in matches}) > 1:
                return (
                    False,
                    f"{operation_name}: old_text matched multiple distinct entries",
                    current_entries,
                    current_usage,
                )
            next_entries[matches[0]] = content
        elif action == "remove":
            if not old_text:
                return (
                    False,
                    f"{operation_name}: old_text is required",
                    current_entries,
                    current_usage,
                )
            matches = [i for i, entry in enumerate(next_entries) if old_text in entry]
            if not matches:
                return (
                    False,
                    f"{operation_name}: no entry matched '{old_text}'",
                    current_entries,
                    current_usage,
                )
            if len({next_entries[i] for i in matches}) > 1:
                return (
                    False,
                    f"{operation_name}: old_text matched multiple distinct entries",
                    current_entries,
                    current_usage,
                )
            next_entries.pop(matches[0])
        else:
            return (
                False,
                f"{operation_name}: unknown action; use add, replace, or remove",
                current_entries,
                current_usage,
            )

    next_usage_count = measure_memory_entries(next_entries)
    if next_usage_count > character_limit:
        return (
            False,
            f"final memory would be {next_usage_count}/{character_limit} chars; remove or shorten entries in the same batch",
            current_entries,
            current_usage,
        )
    return (
        True,
        f"Applied {len(operations)} operation(s).",
        next_entries,
        f"{next_usage_count}/{character_limit}",
    )


async def write_memory(
    request: Request,
    user_id: str,
    workspace: str,
    scope: str,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    memory_file = await resolve_memory_file(user_id, workspace, scope)
    uses_markdown = any(
        isinstance(operation, dict) and _operation_uses_markdown(operation)
        for operation in operations
    )
    if scope == "workspace" and uses_markdown:
        await asyncio.to_thread(ensure_cptr_gitignored, workspace)
    lock_path = memory_file.path.parent if uses_markdown else memory_file.path
    async with _memory_file_lock(lock_path):
        if uses_markdown:
            root = (
                MemoryRoot("user", _user_memory_root(user_id), user_memory_path(user_id))
                if scope == "user"
                else MemoryRoot(
                    "workspace",
                    _workspace_memory_root(user_id, workspace),
                    workspace_memory_path(user_id, workspace),
                )
            )
            result = await asyncio.to_thread(apply_markdown_memory_batch, root, operations)
            result.update({"scope": scope, "path": str(root.root)})
            return result
        if scope == "workspace":
            try:
                file_data = await Runtime.read_file(request, str(memory_file.path))
                entries = [
                    line[2:].strip()
                    for line in str(file_data.get("content") or "").splitlines()
                    if line.strip().startswith("- ") and line[2:].strip()
                ]
            except FileError:
                entries = []
        else:
            entries = await asyncio.to_thread(read_memory_entries, memory_file.path)
        success, message, next_entries, usage = apply_memory_batch(
            entries, operations, memory_file.character_limit
        )
        if not success:
            return {
                "success": False,
                "error": message,
                "entries": entries,
                "usage": usage,
                "scope": scope,
                "path": str(memory_file.path),
            }
        if scope == "workspace":
            await Runtime.write_file(request, str(memory_file.path), format_memory_entries(next_entries))
        else:
            await asyncio.to_thread(write_memory_entries, memory_file.path, next_entries)
        return {
            "success": True,
            "message": message,
            "entries": next_entries,
            "usage": usage,
            "scope": scope,
            "path": str(memory_file.path),
        }


async def remember(
    request: Request,
    user_id: str,
    workspace: str,
    scope: str,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    settings = await get_memory_settings()
    if not settings["enabled"]:
        return {"success": False, "error": "memory writes are disabled"}
    return await write_memory(request, user_id, workspace, scope, operations)


async def read_memory_state(request: Request, user_id: str, workspace: str) -> dict[str, Any]:
    settings = await get_memory_settings()
    user_memory = await resolve_memory_file(user_id, workspace, "user")
    user_entries = await asyncio.to_thread(read_memory_entries, user_memory.path)
    workspace_entries: list[str] = []
    workspace_usage = f"0/{settings['workspace_char_limit']}"
    workspace_path_value = ""
    if workspace:
        workspace_memory = await resolve_memory_file(user_id, workspace, "workspace")
        try:
            file_data = await Runtime.read_file(request, str(workspace_memory.path))
            workspace_entries = [
                line[2:].strip()
                for line in str(file_data.get("content") or "").splitlines()
                if line.strip().startswith("- ") and line[2:].strip()
            ]
        except FileError:
            workspace_entries = []
        workspace_usage = (
            f"{measure_memory_entries(workspace_entries)}/{workspace_memory.character_limit}"
        )
        workspace_path_value = str(workspace_memory.path)
    return {
        "settings": settings,
        "user": {
            "entries": user_entries,
            "usage": f"{measure_memory_entries(user_entries)}/{user_memory.character_limit}",
            "path": str(user_memory.path),
            "root": str(_user_memory_root(user_id)),
        },
        "workspace": {
            "entries": workspace_entries,
            "usage": workspace_usage,
            "path": workspace_path_value,
            "root": str(_workspace_memory_root(user_id, workspace)) if workspace else "",
        },
    }


async def recall_memory_context(
    request: Request,
    *,
    user_id: str,
    workspace: str,
    current_message: str = "",
    recent_messages: list[dict[str, Any]] | None = None,
    mentioned_files: list[str] | None = None,
    budget_user: int | None = None,
    budget_workspace: int | None = None,
) -> MemoryContext:
    settings = await get_memory_settings()
    roots = resolve_memory_roots(user_id, workspace)
    query = _current_recall_query(current_message, recent_messages or [], mentioned_files or [])
    user_budget = int(budget_user or settings["user_char_limit"])
    workspace_budget = int(budget_workspace or settings["workspace_char_limit"])
    snippets_by_scope: dict[str, list[MemorySnippet]] = {"user": [], "workspace": []}

    for root in roots:
        if root.scope == "workspace":
            try:
                file_data = await Runtime.read_file(request, str(root.baseline_path))
                baseline_text = str(file_data.get("content") or "")
            except FileError:
                baseline_text = ""
        else:
            baseline_text = (
                root.baseline_path.read_text(errors="replace")
                if root.baseline_path.is_file()
                else ""
            )
        contextual_query = query
        if baseline_text:
            baseline_links = _extract_baseline_links(baseline_text)
            if baseline_links:
                contextual_query = "\n".join([query, *baseline_links[:12]]).strip()
        scope_budget = user_budget if root.scope == "user" else workspace_budget
        if contextual_query and scope_budget > 0:
            snippets = await search_memory_files(
                [root],
                query=contextual_query,
                expand_links=True,
                limit=8,
            )
            snippets_by_scope[root.scope].extend(
                snippet
                for snippet in snippets
                if snippet.path != root.baseline_path.name
                and not PROMPT_THREAT_RE.search(snippet.snippet)
            )
    return MemoryContext(
        user=snippets_by_scope["user"],
        workspace=snippets_by_scope["workspace"],
    )


async def build_memory_prompt(
    request: Request,
    user_id: str | None,
    workspace: str,
    *,
    current_message: str = "",
    recent_messages: list[dict[str, Any]] | None = None,
    mentioned_files: list[str] | None = None,
) -> str:
    if not user_id:
        return ""
    settings = await get_memory_settings()
    if not settings["enabled"]:
        return ""
    blocks: list[str] = []

    context = await recall_memory_context(
        request,
        user_id=user_id,
        workspace=workspace,
        current_message=current_message,
        recent_messages=recent_messages or [],
        mentioned_files=mentioned_files or [],
        budget_user=int(settings["user_char_limit"]),
        budget_workspace=int(settings["workspace_char_limit"]),
    )

    render_roots: list[tuple[MemoryRoot, str, int, list[MemorySnippet]]] = [
        (
            MemoryRoot("user", _user_memory_root(user_id), user_memory_path(user_id)),
            "User Memory",
            int(settings["user_char_limit"]),
            context.user,
        )
    ]
    if workspace:
        render_roots.append(
            (
                MemoryRoot(
                    "workspace",
                    _workspace_memory_root(user_id, workspace),
                    workspace_memory_path(user_id, workspace),
                ),
                "Workspace Memory",
                int(settings["workspace_char_limit"]),
                context.workspace,
            )
        )

    for root, title, budget, snippets in render_roots:
        if root.scope == "workspace":
            try:
                file_data = await Runtime.read_file(request, str(root.baseline_path))
                text = str(file_data.get("content") or "").strip()
                baseline = (
                    _trim_prompt_text(_prompt_safe_text(text, root.baseline_path.name), budget)
                    if text
                    else ""
                )
            except FileError:
                baseline = ""
        else:
            baseline = _read_baseline_block(root.baseline_path, budget)
        remaining = max(0, budget - len(baseline))
        contextual = _render_context_snippets(snippets, remaining)
        content = "\n".join(part for part in (baseline, contextual) if part.strip()).strip()
        if content:
            blocks.append(f"[{title}] [{min(len(content), budget)}/{budget}]\n{content}")
    return "\n\n".join(blocks)


async def search_memory_state(
    user_id: str,
    workspace: str,
    *,
    query: str = "",
    scope: str = "both",
    path: str | None = None,
    memory_id: str | None = None,
    expand_links: bool = False,
    include_trash: bool = False,
    limit: int = 8,
) -> dict[str, Any]:
    roots = resolve_memory_roots(user_id, workspace)
    if scope in {"user", "workspace"}:
        roots = [root for root in roots if root.scope == scope]
    snippets = await search_memory_files(
        roots,
        query=query or None,
        path=path,
        memory_id=memory_id,
        expand_links=expand_links,
        include_trash=include_trash,
        limit=limit,
    )
    return {"results": [_snippet_dict(snippet) for snippet in snippets], "count": len(snippets)}


async def list_memory_files_state(
    user_id: str,
    workspace: str,
    *,
    scope: str = "both",
    query: str = "",
    include_trash: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    roots = resolve_memory_roots(user_id, workspace)
    if scope in {"user", "workspace"}:
        roots = [root for root in roots if root.scope == scope]
    rows: list[dict[str, Any]] = []

    def collect() -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        lowered_query = query.lower().strip()
        for root in roots:
            for path in _iter_markdown_files(root.root, include_trash=include_trash):
                rel = str(_relative_to_root(path, root.root))
                if lowered_query and lowered_query not in rel.lower():
                    text = path.read_text(errors="replace")
                    if lowered_query not in text.lower():
                        continue
                text = path.read_text(errors="replace")
                sections = _split_markdown_sections(text, path.stem)
                stat = path.stat()
                collected.append(
                    {
                        "scope": root.scope,
                        "path": rel,
                        "size": stat.st_size,
                        "modified_at": stat.st_mtime,
                        "headings": [
                            section["heading"] for section in sections if section.get("heading")
                        ][:12],
                        "baseline": path == root.baseline_path,
                        "trash": _is_trash_path(_relative_to_root(path, root.root)),
                    }
                )
                if len(collected) >= limit:
                    return collected
        return collected

    rows = await asyncio.to_thread(collect)
    return {"files": rows, "count": len(rows)}


async def read_memory_file_state(
    request: Request,
    user_id: str,
    workspace: str,
    *,
    scope: str,
    path: str,
) -> dict[str, Any]:
    roots = [root for root in resolve_memory_roots(user_id, workspace) if root.scope == scope]
    if not roots:
        raise ValueError("scope is not available")
    root = roots[0]
    target = _safe_relative_path(root.root, path)
    if not target.is_file() or target.suffix.lower() != ".md":
        raise ValueError("memory file not found")
    if scope == "workspace":
        file_data = await Runtime.read_file(request, str(target))
        content = str(file_data.get("content") or "")
    else:
        content = await asyncio.to_thread(target.read_text, errors="replace")
    sections = _split_markdown_sections(content, target.stem)
    return {
        "scope": scope,
        "path": str(_relative_to_root(target, root.root)),
        "content": content,
        "sections": [
            {
                "heading": section.get("heading", ""),
                "memory_id": section.get("memory_id", ""),
                "links": _extract_wiki_links(str(section.get("content") or "")),
            }
            for section in sections
        ],
    }


async def review_memory_vault(request: Request, user_id: str, workspace: str) -> dict[str, Any]:
    """Deterministic vault review for the UI/API; LLM learning still runs after turns."""
    roots = resolve_memory_roots(user_id, workspace)

    def inspect() -> dict[str, Any]:
        changed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for root in roots:
            for path in _iter_markdown_files(root.root, include_trash=False):
                text = path.read_text(errors="replace")
                rel = str(_relative_to_root(path, root.root))
                if PROMPT_THREAT_RE.search(text):
                    backup = _backup_to_trash(path, "unsafe", root.root)
                    path.unlink(missing_ok=True)
                    changed.append(
                        {
                            "scope": root.scope,
                            "path": rel,
                            "action": "trash",
                            "reason": "unsafe instruction-like content",
                            "backup": str(backup),
                        }
                    )
                    continue
                sections = _split_markdown_sections(text, path.stem)
                if len(sections) > 8:
                    skipped.append(
                        {
                            "scope": root.scope,
                            "path": rel,
                            "action": "split",
                            "reason": "many sections; split when this causes noisy recall",
                        }
                    )
        return {"changed": changed, "skipped": skipped}

    return await asyncio.to_thread(inspect)


def summarize_recent_conversation(messages: list[dict[str, Any]], assistant_reply: str) -> str:
    recent_messages = messages[-16:]
    lines: list[str] = []
    for message in recent_messages:
        role = message.get("role", "unknown")
        content = message.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                str(block.get("text", "")) for block in content if isinstance(block, dict)
            )
        text = str(content).strip()
        if len(text) > 1600:
            text = text[:1000] + "\n...(truncated)...\n" + text[-400:]
        if text:
            lines.append(f"{role}: {text}")
    if assistant_reply:
        text = assistant_reply.strip()
        if len(text) > 1600:
            text = text[:1000] + "\n...(truncated)...\n" + text[-400:]
        lines.append(f"assistant_final: {text}")
    return "\n\n".join(lines)


def build_memory_review_prompt(
    memory_state: dict[str, Any], workspace: str, transcript: str
) -> str:
    return (
        "Review the completed conversation and decide whether cptr should remember "
        "stable facts. Return ONLY JSON with this shape:\n"
        '{"user": [{"action": "add|replace|remove|link", "content": "...", '
        '"old_text": "...", "path": "...", "heading": "...", "memory_id": "...", '
        '"link": "..."}], "workspace": [{"action": "add|replace|remove|link", '
        '"content": "...", "old_text": "...", "path": "...", "heading": "...", '
        '"memory_id": "...", "link": "..."}]}\n\n'
        "Use user memory only for durable user preferences, communication style, repeated "
        "corrections, or cross-workspace habits. Use workspace memory only for facts true "
        "in the current workspace, such as repo conventions, verification commands, "
        "architecture notes, or local tool quirks. Prefer updating or linking an existing "
        "memory over duplicating it. Use plain Markdown sections when path/heading are useful. "
        "Do not invent kind/status/trait/weight schemas. If nothing is worth saving, return "
        '{"user": [], "workspace": []}.\n\n'
        f"Workspace: {workspace}\n\n"
        f"Current user memory ({memory_state['user']['usage']}):\n"
        + "\n".join(f"- {entry}" for entry in memory_state["user"]["entries"])
        + f"\n\nCurrent workspace memory ({memory_state['workspace']['usage']}):\n"
        + "\n".join(f"- {entry}" for entry in memory_state["workspace"]["entries"])
        + f"\n\nConversation:\n{transcript}"
    )


async def review_memory_after_turn(
    request: Request,
    *,
    user_id: str,
    message_id: str,
    workspace: str,
    conversation_messages: list[dict[str, Any]],
    assistant_reply: str,
    model_connection: dict,
    model: str,
) -> None:
    settings = await get_memory_settings()
    if (
        not settings["enabled"]
        or not settings["tool_enabled"]
        or not settings["background_review_enabled"]
        or not assistant_reply.strip()
    ):
        return
    if message_id in _reviewed_messages:
        return
    user_turns = sum(1 for message in conversation_messages if message.get("role") == "user")
    if user_turns <= 0 or user_turns % int(settings["review_interval_turns"]) != 0:
        return
    _reviewed_messages.add(message_id)
    asyncio.create_task(
        run_memory_review(
            request,
            user_id=user_id,
            workspace=workspace,
            conversation_messages=list(conversation_messages),
            assistant_reply=assistant_reply,
            model_connection=dict(model_connection),
            model=model,
        )
    )


async def run_memory_review(
    request: Request,
    *,
    user_id: str,
    workspace: str,
    conversation_messages: list[dict[str, Any]],
    assistant_reply: str,
    model_connection: dict,
    model: str,
) -> None:
    try:
        from cptr.utils.ai import generate_json
        from cptr.utils.utility_models import configured_utility_model

        memory_state = await read_memory_state(request, user_id, workspace)
        transcript = summarize_recent_conversation(conversation_messages, assistant_reply)
        prompt = build_memory_review_prompt(memory_state, workspace, transcript)
        parsed = await generate_json(
            None,
            model_id=await configured_utility_model("memory_background_review"),
            active_connection=model_connection,
            active_model=model,
            messages=[{"role": "user", "content": prompt}],
            system="You are cptr's private memory reviewer. Return only valid JSON.",
            max_tokens=700,
        )
        if not isinstance(parsed, dict):
            return
        for scope in ("user", "workspace"):
            operations = parsed.get(scope) or []
            if not isinstance(operations, list) or not operations:
                continue
            await remember(
                request,
                user_id=user_id,
                workspace=workspace,
                scope=scope,
                operations=operations,
            )
    except Exception:
        return

"""Endpoints for browsing directories and managing files within the workspace.

Security: these endpoints accept arbitrary absolute paths with no sandboxing.
Authenticated users get full filesystem access. This is intentional for the
single-user model. Not safe for shared or public instances. See README.md.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from cptr.utils.gitignore import is_gitignored, load_gitignore

router = APIRouter(prefix="/api/workspace/files", tags=["workspace-files"])


class FileEntry(BaseModel):
    name: str
    type: str  # "directory" | "file" | "symlink"
    size: Optional[int] = None
    modified: Optional[str] = None


class DirectoryListing(BaseModel):
    path: str
    entries: list[FileEntry]


@router.get("", response_model=DirectoryListing)
async def list_directory(path: str = Query(..., description="Absolute path to list")):
    """Return the contents of a directory, sorted dirs-first then alphabetical."""

    target = Path(path).resolve()

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    def _scan() -> list[FileEntry]:
        entries: list[FileEntry] = []
        for item in target.iterdir():
            try:
                st = item.stat()
                modified = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()

                if item.is_symlink():
                    entry_type = "symlink"
                elif item.is_dir():
                    entry_type = "directory"
                else:
                    entry_type = "file"

                entries.append(
                    FileEntry(
                        name=item.name,
                        type=entry_type,
                        size=st.st_size if entry_type == "file" else None,
                        modified=modified,
                    )
                )
            except (PermissionError, OSError):
                entries.append(FileEntry(name=item.name, type="file"))
        # Sort: directories first, then files, alphabetical within each group
        type_order = {"directory": 0, "symlink": 1, "file": 2}
        entries.sort(key=lambda e: (type_order.get(e.type, 2), e.name.lower()))
        return entries

    try:
        entries = await asyncio.to_thread(_scan)
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")

    return DirectoryListing(path=str(target), entries=entries)


# ── File content reading ─────────────────────────────────────────

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

# Extensions we know are text
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".svelte",
    ".css",
    ".html",
    ".htm",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".env",
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
    ".rs",
    ".go",
    ".java",
    ".c",
    ".h",
    ".cc",
    ".cxx",
    ".hh",
    ".hxx",
    ".cpp",
    ".hpp",
    ".rb",
    ".php",
    ".pl",
    ".swift",
    ".kt",
    ".scala",
    ".sql",
    ".r",
    ".lua",
    ".vim",
    ".el",
    ".ex",
    ".exs",
    ".erl",
    ".hs",
    ".ml",
    ".clj",
    ".lisp",
    ".csv",
    ".tsv",
    ".log",
    ".diff",
    ".patch",
    ".makefile",
    ".cmake",
    ".gradle",
    ".sbt",
    ".tf",
    ".hcl",
    ".nix",
    ".lock",
    ".svg",
}


class FileContent(BaseModel):
    path: str
    name: str
    size: int
    binary: bool
    content: Optional[str] = None
    language: Optional[str] = None


def _detect_language(name: str) -> Optional[str]:
    """Map file extension to a language identifier for syntax highlighting."""
    ext = Path(name).suffix.lower()
    lang_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "jsx",
        ".tsx": "tsx",
        ".svelte": "svelte",
        ".css": "css",
        ".html": "html",
        ".json": "json",
        ".xml": "xml",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".md": "markdown",
        ".sh": "bash",
        ".bash": "bash",
        ".zsh": "bash",
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".c": "c",
        ".h": "c",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".hh": "cpp",
        ".hxx": "cpp",
        ".cpp": "cpp",
        ".hpp": "cpp",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".kt": "kotlin",
        ".sql": "sql",
        ".r": "r",
        ".lua": "lua",
        ".dockerfile": "dockerfile",
        ".makefile": "makefile",
        ".tf": "hcl",
        ".hcl": "hcl",
        ".nix": "nix",
    }
    return lang_map.get(ext)


def _is_text_file(filepath: Path) -> bool:
    """Check if a file is likely text based on extension or content sniffing."""
    if filepath.suffix.lower() in TEXT_EXTENSIONS:
        return True
    # Also check common extensionless files
    if filepath.name.lower() in {
        "makefile",
        "dockerfile",
        "gemfile",
        "rakefile",
        "procfile",
        "license",
        "readme",
        "changelog",
    }:
        return True
    # Content sniff: read first 8KB and check for null bytes
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(8192)
            return b"\x00" not in chunk
    except (OSError, PermissionError):
        return False


@router.get("/read", response_model=FileContent)
async def read_file(path: str = Query(..., description="Absolute path to file")):
    """Read the contents of a file."""

    target = Path(path).resolve()

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")

    def _read() -> FileContent:
        size = target.stat().st_size

        if size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large ({size} bytes). Max is {MAX_FILE_SIZE} bytes.",
            )

        is_text = _is_text_file(target)

        if is_text:
            try:
                content = target.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError) as e:
                raise HTTPException(status_code=403, detail=str(e))
        else:
            content = None

        return FileContent(
            path=str(target),
            name=target.name,
            size=size,
            binary=not is_text,
            content=content,
            language=_detect_language(target.name) if is_text else None,
        )

    return await asyncio.to_thread(_read)


# ── File writing ─────────────────────────────────────────────────


class WriteFileRequest(BaseModel):
    path: str
    content: str


@router.post("/write")
async def write_file(req: WriteFileRequest):
    """Write content to a file. Creates the file (and parent dirs) if it doesn't exist."""

    target = Path(req.path).resolve()

    if target.exists() and not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {req.path}")

    def _write() -> dict:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(req.content, encoding="utf-8")
        return {"status": "saved", "path": str(target), "size": target.stat().st_size}

    try:
        return await asyncio.to_thread(_write)
    except (OSError, PermissionError) as e:
        raise HTTPException(status_code=403, detail=str(e))


# ── File search ──────────────────────────────────────────────────


SEARCH_IGNORE_DIRS = {
    ".cptr",
    ".git",
    "node_modules",
    "__pycache__",
    ".svelte-kit",
    ".next",
    "dist",
    "build",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".eggs",
    "*.egg-info",
    ".DS_Store",
}

MATCH_PAGE_SIZE = 100
MAX_CONTENT_MATCHES_PER_FILE = 3
MAX_CONTENT_SEARCH_FILE_SIZE = 1 * 1024 * 1024


class ContentMatch(BaseModel):
    line: int
    column: int
    text: str


class FileMatch(BaseModel):
    path: str
    relative_path: str
    name: str
    type: str  # "file" | "directory"
    name_match: bool
    content_matches: list[ContentMatch]


class FileMatches(BaseModel):
    results: list[FileMatch]
    next_offset: int | None = None


def _is_search_ignored(name: str) -> bool:
    return name in SEARCH_IGNORE_DIRS or name.endswith(".egg-info")


def _walk_match_entries(
    root: Path,
    show_hidden: bool,
    gitignore: tuple[Path, tuple] | None = None,
):
    """Yield visible files and directories without following symlinks."""
    if gitignore is None:
        gitignore = load_gitignore(root)
    ignore_base, ignore_patterns = gitignore

    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name.lower())
    except (OSError, PermissionError):
        return

    for item in entries:
        try:
            item_is_dir = item.is_dir()
            if (
                _is_search_ignored(item.name)
                or (not show_hidden and item.name.startswith("."))
                or is_gitignored(item, ignore_base, ignore_patterns, is_dir=item_is_dir)
            ):
                continue
            if item.is_symlink():
                yield item, "file"
            elif item_is_dir:
                yield item, "directory"
                yield from _walk_match_entries(item, show_hidden, gitignore)
            elif item.is_file():
                yield item, "file"
        except (OSError, PermissionError):
            continue


def _match_column(text: str, query_lower: str) -> int | None:
    index = text.lower().find(query_lower)
    if index < 0:
        return None
    # CodeMirror measures columns in UTF-16 code units.
    return len(text[:index].encode("utf-16-le")) // 2 + 1


def _content_match(text: str, line: int, query_lower: str) -> ContentMatch | None:
    text = text.rstrip("\r\n")
    column = _match_column(text, query_lower)
    return ContentMatch(line=line, column=column, text=text) if column is not None else None


def _content_matches_with_rg(
    root: Path, query: str, query_lower: str, show_hidden: bool, files: set[Path]
) -> tuple[dict[Path, list[ContentMatch]], bool] | None:
    rg = shutil.which("rg")
    if not rg:
        return None

    args = [
        rg,
        "--json",
        "--no-messages",
        "--fixed-strings",
        "--ignore-case",
        "--line-number",
        "--column",
        "--max-count",
        str(MAX_CONTENT_MATCHES_PER_FILE + 1),
    ]
    if show_hidden:
        args.append("--hidden")
    for ignored in SEARCH_IGNORE_DIRS:
        pattern = f"!{ignored}" if ignored == ".DS_Store" else f"!{ignored}/**"
        args.extend(("--glob", pattern))
    args.extend(("--", query, str(root)))

    try:
        completed = subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError:
        return None
    if completed.returncode not in (0, 1):
        return None

    matches: dict[Path, list[ContentMatch]] = {}
    truncated = False
    for raw in completed.stdout.splitlines():
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if message.get("type") != "match":
            continue
        data = message["data"]
        path = Path(data["path"]["text"]).resolve()
        if path not in files:
            continue
        line_matches = matches.setdefault(path, [])
        if len(line_matches) >= MAX_CONTENT_MATCHES_PER_FILE:
            truncated = True
            continue
        match = _content_match(data["lines"]["text"], data["line_number"], query_lower)
        if match:
            line_matches.append(match)
    return matches, truncated


def _content_matches_with_python(
    files: set[Path], query_lower: str
) -> tuple[dict[Path, list[ContentMatch]], bool]:
    matches: dict[Path, list[ContentMatch]] = {}
    truncated = False
    for path in files:
        try:
            if path.stat().st_size > MAX_CONTENT_SEARCH_FILE_SIZE:
                continue
            with path.open("rb") as source:
                if b"\0" in source.read(8192):
                    continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except (OSError, PermissionError):
            continue

        for number, text in enumerate(lines, start=1):
            match = _content_match(text, number, query_lower)
            if not match:
                continue
            file_matches = matches.setdefault(path, [])
            if len(file_matches) >= MAX_CONTENT_MATCHES_PER_FILE:
                truncated = True
                break
            file_matches.append(match)
    return matches, truncated


def find_file_matches(
    root: Path,
    query: str,
    show_hidden: bool = False,
    offset: int = 0,
    limit: int = MATCH_PAGE_SIZE,
) -> FileMatches:
    """Find filename/path and literal text matches below a browser directory."""
    query = query.strip()
    query_lower = query.lower()
    entries = list(_walk_match_entries(root, show_hidden))
    files = {path.resolve() for path, kind in entries if kind == "file" and not path.is_symlink()}
    content_result = _content_matches_with_rg(root, query, query_lower, show_hidden, files)
    content_matches, _ = (
        content_result
        if content_result is not None
        else _content_matches_with_python(files, query_lower)
    )

    matches: list[tuple[int, int, FileMatch]] = []
    for path, kind in entries:
        relative_path = path.relative_to(root).as_posix()
        name_lower = path.name.lower()
        relative_lower = relative_path.lower()
        if name_lower == query_lower:
            score = 0
        elif name_lower.startswith(query_lower):
            score = 1
        elif query_lower in name_lower:
            score = 2
        elif query_lower in relative_lower:
            score = 3
        else:
            score = 4

        path_content_matches = content_matches.get(path.resolve(), []) if kind == "file" else []
        name_match = score < 4
        if not name_match and not path_content_matches:
            continue
        matches.append(
            (
                score,
                len(relative_path),
                FileMatch(
                    path=str(path),
                    relative_path=relative_path,
                    name=path.name,
                    type=kind,
                    name_match=name_match,
                    content_matches=path_content_matches,
                ),
            )
        )

    matches.sort(key=lambda item: (item[0], item[1], item[2].relative_path.lower()))
    next_offset = offset + limit if offset + limit < len(matches) else None
    return FileMatches(
        results=[item[2] for item in matches[offset : offset + limit]],
        next_offset=next_offset,
    )


@router.get("/matches", response_model=FileMatches)
async def file_matches(
    query: str = Query(..., description="Literal text to match"),
    path: str = Query(..., description="Root path to search"),
    show_hidden: bool = Query(False, description="Include dotfiles and dot-directories"),
    offset: int = Query(0, ge=0, description="Result offset"),
    limit: int = Query(MATCH_PAGE_SIZE, ge=1, le=MATCH_PAGE_SIZE, description="Page size"),
):
    """Return filename/path and content matches below a directory."""
    if not query.strip():
        raise HTTPException(status_code=400, detail="Query must not be blank")

    root = Path(path).resolve()
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")

    return await asyncio.to_thread(find_file_matches, root, query, show_hidden, offset, limit)


class SearchResult(BaseModel):
    path: str
    name: str
    type: str  # "file" | "directory"


class SearchResponse(BaseModel):
    results: list[SearchResult]


@router.get("/search", response_model=SearchResponse)
async def search_files(
    query: str = Query(..., description="Search term"),
    path: str = Query(..., description="Root path to search"),
    limit: int = Query(20, description="Max results"),
):
    """Fuzzy file search. Walks tree, matching basename and relative path case-insensitively.

    Results are ranked by where/how the query matches: exact name > name prefix >
    exact path > path prefix > name contains > path contains. Within each tier,
    shorter paths rank higher (more specific match).
    """

    root = Path(path).resolve()
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")

    results = await asyncio.to_thread(walk_and_rank_files, root, query, limit)
    return SearchResponse(results=results)


def walk_and_rank_files(root: Path, query: str, limit: int = 20) -> list[SearchResult]:
    """Fuzzy file search — walks tree, ranks by name match quality.

    Reusable by both the /search endpoint and the unified /api/search endpoint.
    """
    query_lower = query.strip().lower().replace("\\", "/")
    matches: list[tuple[int, int, SearchResult]] = []
    max_collect = limit * 10
    ignore_base, ignore_patterns = load_gitignore(root)

    def walk(directory: Path, depth: int = 0):
        if depth > 8 or len(matches) >= max_collect:
            return
        try:
            for item in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
                item_is_dir = item.is_dir()
                if (
                    item.name in SEARCH_IGNORE_DIRS
                    or item.name.startswith(".")
                    or is_gitignored(item, ignore_base, ignore_patterns, is_dir=item_is_dir)
                ):
                    continue
                if len(matches) >= max_collect:
                    return

                name_lower = item.name.lower()
                if query_lower and query_lower in name_lower:
                    if name_lower == query_lower:
                        score = 0
                    elif name_lower.startswith(query_lower):
                        score = 1
                    else:
                        score = 2
                    matches.append(
                        (
                            score,
                            len(item.name),
                            SearchResult(
                                path=str(item),
                                name=item.name,
                                type="directory" if item_is_dir else "file",
                            ),
                        )
                    )
                elif not query_lower:
                    matches.append(
                        (
                            2,
                            len(item.name),
                            SearchResult(
                                path=str(item),
                                name=item.name,
                                type="directory" if item_is_dir else "file",
                            ),
                        )
                    )

                if item_is_dir:
                    walk(item, depth + 1)
        except (PermissionError, OSError):
            pass

    walk(root)
    matches.sort(key=lambda m: (m[0], m[1]))
    return [m[2] for m in matches[:limit]]


# ── File management ──────────────────────────────────────────────


class CreateRequest(BaseModel):
    path: str
    type: str = "file"  # "file" | "directory"


@router.post("/create")
async def create_item(req: CreateRequest):
    """Create a new file or directory."""
    target = Path(req.path).resolve()

    if target.exists():
        raise HTTPException(status_code=409, detail=f"Already exists: {req.path}")

    def _create():
        if req.type == "directory":
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()

    try:
        await asyncio.to_thread(_create)
    except (OSError, PermissionError) as e:
        raise HTTPException(status_code=403, detail=str(e))

    return {"status": "created", "path": str(target), "type": req.type}


class MoveRequest(BaseModel):
    source: str
    destination: str


@router.post("/move")
async def move_item(req: MoveRequest):
    """Move or rename a file/directory."""
    src = Path(req.source).resolve()
    dst = Path(req.destination).resolve()

    if not src.exists():
        raise HTTPException(status_code=404, detail=f"Source not found: {req.source}")

    # If destination is a directory, move into it
    if dst.is_dir():
        dst = dst / src.name

    if dst.exists():
        raise HTTPException(status_code=409, detail=f"Destination exists: {dst}")

    try:
        await asyncio.to_thread(src.rename, dst)
    except (OSError, PermissionError) as e:
        raise HTTPException(status_code=403, detail=str(e))

    return {"status": "moved", "source": str(src), "destination": str(dst)}


class DeleteRequest(BaseModel):
    path: str


@router.post("/delete")
async def delete_item(req: DeleteRequest):
    """Delete a file or directory."""
    import shutil

    target = Path(req.path).resolve()

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Not found: {req.path}")

    def _delete():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    try:
        await asyncio.to_thread(_delete)
    except (OSError, PermissionError) as e:
        raise HTTPException(status_code=403, detail=str(e))

    return {"status": "deleted", "path": str(target)}


from fastapi import UploadFile, File as FastAPIFile, Form


@router.post("/upload")
async def upload_file(
    file: UploadFile = FastAPIFile(...),
    directory: str = Form(...),
):
    """Upload a file to a directory."""
    target_dir = Path(directory).resolve()

    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {directory}")

    target = _unique_child_path(target_dir, file.filename or "file")
    try:
        content = await file.read()
        await asyncio.to_thread(target.write_bytes, content)
    except (OSError, PermissionError) as e:
        raise HTTPException(status_code=403, detail=str(e))

    return {"status": "uploaded", "path": str(target), "size": len(content)}


def _unique_child_path(directory: Path, filename: str) -> Path:
    """Return a non-existing child path by appending -2, -3, ... on collisions."""
    safe_name = Path(filename).name or "file"
    target = directory / safe_name
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for i in range(2, 10_000):
        candidate = directory / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise HTTPException(status_code=409, detail=f"Could not find available name for: {filename}")


# ── File viewing (inline) ────────────────────────────────────────

import mimetypes

from fastapi.responses import FileResponse


@router.get("/view")
async def view_file(path: str = Query(..., description="Absolute path to file")):
    """Serve a file with correct Content-Type for inline browser rendering.

    Unlike /download (which forces application/octet-stream to trigger a
    save dialog), this endpoint lets the browser render the file inline —
    used by the frontend for image, PDF, video, and audio previews.
    """
    target = Path(path).resolve()

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")

    media_type, _ = mimetypes.guess_type(str(target))

    return FileResponse(
        path=str(target),
        media_type=media_type or "application/octet-stream",
        # No filename= → browser renders inline instead of downloading
    )


# ── File download ────────────────────────────────────────────────


@router.get("/download")
async def download_file(path: str = Query(..., description="Absolute path to file")):
    """Download a file."""
    target = Path(path).resolve()

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")

    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


# ── Archive (zip) ────────────────────────────────────────────────

import io
import zipfile

from fastapi.responses import StreamingResponse


class ArchiveRequest(BaseModel):
    paths: list[str]


@router.post("/archive")
async def archive_files(req: ArchiveRequest):
    """Create a zip archive from a list of file/directory paths."""
    if not req.paths:
        raise HTTPException(status_code=400, detail="No paths provided")

    def _build_archive() -> io.BytesIO:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for raw_path in req.paths:
                target = Path(raw_path).resolve()
                if not target.exists():
                    continue

                if target.is_file():
                    zf.write(target, target.name)
                elif target.is_dir():
                    for child in target.rglob("*"):
                        if child.is_file():
                            arcname = str(child.relative_to(target.parent))
                            zf.write(child, arcname)
        buf.seek(0)
        return buf

    buf = await asyncio.to_thread(_build_archive)

    # Derive a sensible filename
    if len(req.paths) == 1:
        name = Path(req.paths[0]).name + ".zip"
    else:
        name = "archive.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ── Static file serving (path-based) ────────────────────────────


@router.get("/serve/{file_path:path}")
async def serve_static(file_path: str):
    """Serve a file using path-based routing with correct MIME type.

    Unlike /view (query-string based), this uses path segments so
    HTML files in iframes can resolve relative CSS/JS/image references.
    E.g. /api/files/serve/home/user/project/index.html
    """
    # Windows paths arrive as "C:/Users/..." - don't prepend /
    if len(file_path) >= 2 and file_path[1] == ":":
        target = Path(file_path).resolve()
    else:
        target = Path("/" + file_path).resolve()

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {file_path}")

    media_type, _ = mimetypes.guess_type(str(target))

    return FileResponse(
        path=str(target),
        media_type=media_type or "application/octet-stream",
    )

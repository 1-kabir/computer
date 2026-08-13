"""Git operations via subprocess.

Cross-platform (macOS, Linux, Windows). Uses --porcelain flags
for machine-stable output. All functions take a repo root path.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from cptr.utils.identity import ExecutionIdentity, env_for, preexec_for


async def _run(
    *args: str,
    cwd: str,
    check: bool = True,
    identity: ExecutionIdentity | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    env_extra = {"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", **(extra_env or {})}
    env = (
        env_for(identity, cwd, env_extra)
        if identity and identity.is_pam
        else {**os.environ, **env_extra}
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-c",
            "core.quotePath=false",
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            preexec_fn=preexec_for(identity) if identity and identity.is_pam else None,
        )
    except FileNotFoundError as exc:
        if exc.filename != "git":
            raise
        raise GitError("Git is not installed", 127) from exc
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise GitError(stderr.strip() or stdout.strip(), proc.returncode)
    return proc.returncode, stdout, stderr


class GitError(Exception):
    """Raised when a git command fails."""

    def __init__(self, message: str, returncode: int = 1):
        super().__init__(message)
        self.returncode = returncode


async def is_repo(root: str, identity: ExecutionIdentity | None = None) -> bool:
    """Check if directory is inside a git repo."""
    try:
        code, _, _ = await _run(
            "rev-parse",
            "--is-inside-work-tree",
            cwd=root,
            check=False,
            identity=identity,
        )
    except GitError:
        return False
    return code == 0


async def version(root: str, identity: ExecutionIdentity | None = None) -> str | None:
    """Return the installed git version, if git is available."""
    cwd = root if os.path.isdir(root) else "/"
    try:
        code, out, _ = await _run("--version", cwd=cwd, check=False, identity=identity)
    except GitError:
        return None
    return out.strip() if code == 0 else None


async def status(root: str, identity: ExecutionIdentity | None = None) -> dict[str, Any]:
    """Get repo status using porcelain v2 format."""
    # Refresh Git's cached file metadata first so status catches edits whose
    # filesystem stat information was stale.
    await _run("update-index", "-q", "--refresh", cwd=root, check=False, identity=identity)

    _, out, _ = await _run(
        "status",
        "--porcelain=v2",
        "--branch",
        "--untracked-files=all",
        cwd=root,
        identity=identity,
    )

    branch = ""
    upstream = ""
    ahead = 0
    behind = 0
    has_ab = False
    files_by_path: dict[str, dict[str, Any]] = {}

    def add_file(
        path: str,
        status_text: str,
        *,
        staged: bool = False,
        unstaged: bool = False,
    ) -> None:
        entry = files_by_path.setdefault(
            path,
            {
                "path": path,
                "status": status_text,
                "staged": False,
                "unstaged": False,
            },
        )
        if staged:
            entry["staged"] = True
            entry["staged_status"] = status_text
        if unstaged:
            entry["unstaged"] = True
            entry["unstaged_status"] = status_text
        # Prefer the working-tree status for the row label when both sides exist.
        entry["status"] = status_text

    async def add_numstat(*args: str) -> None:
        code, numstat, _ = await _run(
            "diff", "--numstat", *args, cwd=root, check=False, identity=identity
        )
        if code != 0:
            return
        for line in numstat.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            added, deleted, path = parts[0], parts[1], parts[2]
            entry = files_by_path.get(path)
            if not entry:
                continue
            if added == "-" or deleted == "-":
                entry["binary"] = True
                continue
            entry["additions"] = entry.get("additions", 0) + int(added)
            entry["deletions"] = entry.get("deletions", 0) + int(deleted)

    for line in out.splitlines():
        if line.startswith("# branch.head "):
            branch = line.split(" ", 2)[2]
        elif line.startswith("# branch.upstream "):
            upstream = line.split(" ", 2)[2]
        elif line.startswith("# branch.ab "):
            has_ab = True
            parts = line.split()
            ahead = int(parts[2].lstrip("+"))
            behind = abs(int(parts[3].lstrip("-")))
        elif line.startswith("1 ") or line.startswith("2 "):
            # Changed entry
            #  type-1: 1 XY sub mH mI mW hH hI path           (9 fields)
            #  type-2: 1 XY sub mH mI mW hH hI Xscore path\torigPath (10 fields)
            nsplits = 9 if line.startswith("2 ") else 8
            parts = line.split(" ", nsplits)
            xy = parts[1]
            path = parts[-1]
            # "2" entries (rename/copy) have original path after tab
            if line.startswith("2 "):
                path = path.split("\t")[0]
            staged_code = xy[0]
            unstaged_code = xy[1]
            if staged_code != ".":
                add_file(path, _status_char(staged_code), staged=True)
            if unstaged_code != ".":
                add_file(path, _status_char(unstaged_code), unstaged=True)
        elif line.startswith("? "):
            # Untracked
            path = line[2:]
            add_file(path, "untracked", unstaged=True)
            entry = files_by_path[path]
            line_count = _count_text_lines(os.path.join(root, path))
            if line_count is None:
                entry["binary"] = True
            else:
                entry["additions"] = line_count
                entry["deletions"] = 0
        elif line.startswith("u "):
            # Unmerged
            parts = line.split(" ", 10)
            path = parts[-1]
            add_file(path, "conflict", unstaged=True)

    # upstream is set but remote branch doesn't exist yet (no ab line)
    # — treat as unpublished so the frontend shows "Publish"
    if upstream and not has_ab:
        upstream = ""

    await add_numstat()
    await add_numstat("--staged")

    # Get remote URL for "View on GitHub/GitLab" link
    code, remote_out, _ = await _run(
        "remote", "get-url", "origin", cwd=root, check=False, identity=identity
    )
    remote_url = remote_out.strip() if code == 0 else ""

    return {
        "branch": branch,
        "upstream": upstream,
        "remote_url": remote_url,
        "ahead": ahead,
        "behind": behind,
        "files": list(files_by_path.values()),
    }


def _status_char(c: str) -> str:
    """Convert porcelain status char to readable string."""
    return {
        "M": "modified",
        "A": "added",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
        "T": "type-changed",
    }.get(c, c)


def _count_text_lines(path: str) -> int | None:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if b"\0" in data:
        return None
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


async def _config_value(
    root: str,
    scope: str,
    key: str,
    identity: ExecutionIdentity | None = None,
) -> str:
    code, out, _ = await _run(
        "config", scope, "--get", key, cwd=root, check=False, identity=identity
    )
    return out.strip() if code == 0 else ""


async def _config_values(
    root: str,
    key: str,
    identity: ExecutionIdentity | None = None,
) -> list[str]:
    code, out, _ = await _run(
        "config", "--get-all", key, cwd=root, check=False, identity=identity
    )
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


async def effective_config(
    root: str, identity: ExecutionIdentity | None = None
) -> dict[str, Any]:
    """Return the Git config fields the settings UI needs."""
    local_name = await _config_value(root, "--local", "user.name", identity)
    global_name = await _config_value(root, "--global", "user.name", identity)
    local_email = await _config_value(root, "--local", "user.email", identity)
    global_email = await _config_value(root, "--global", "user.email", identity)
    code, remote_out, _ = await _run(
        "remote", "get-url", "origin", cwd=root, check=False, identity=identity
    )
    return {
        "identity": {
            "name": local_name or global_name,
            "name_source": "local" if local_name else "global" if global_name else "",
            "email": local_email or global_email,
            "email_source": "local" if local_email else "global" if global_email else "",
        },
        "credential_helpers": await _config_values(root, "credential.helper", identity),
        "remote_url": remote_out.strip() if code == 0 else "",
    }


async def diff(
    root: str,
    file: str | None = None,
    staged: bool = False,
    untracked: bool = False,
    ignore_whitespace: bool = False,
    identity: ExecutionIdentity | None = None,
) -> dict[str, Any]:
    """Get diff output as structured data."""
    if untracked and file:
        # Untracked files: use --no-index to diff against empty
        null_device = "NUL" if sys.platform == "win32" else "/dev/null"
        args = ["diff", "--no-index", "--unified=3"]
        if ignore_whitespace:
            args.append("--ignore-all-space")
        args.extend(["--", null_device, file])
        _, out, _ = await _run(*args, cwd=root, check=False, identity=identity)
        return _parse_diff(out)

    args = ["diff", "--unified=3"]
    if ignore_whitespace:
        args.append("--ignore-all-space")
    if staged:
        args.append("--staged")
    if file:
        args.extend(["--", file])

    _, out, _ = await _run(*args, cwd=root, identity=identity)
    return _parse_diff(out)


async def compare_diff(
    root: str,
    base: str,
    head: str,
    ignore_whitespace: bool = False,
    identity: ExecutionIdentity | None = None,
) -> dict[str, Any]:
    """Get a structured diff for a base...head comparison."""
    args = ["diff", "--unified=3", "--color=never"]
    if ignore_whitespace:
        args.append("--ignore-all-space")
    args.append(f"{base}...{head}")
    _, out, _ = await _run(*args, cwd=root, identity=identity)
    return _parse_diff(out)


async def staged_diff(
    root: str, max_chars: int = 30000, identity: ExecutionIdentity | None = None
) -> str:
    """Return the staged patch, capped for lightweight AI requests."""
    _, out, _ = await _run("diff", "--staged", "--unified=3", cwd=root, identity=identity)
    return out[:max_chars]


def _parse_diff(raw: str) -> dict[str, Any]:
    """Parse unified diff into structured format."""
    files: list[dict] = []
    current_file: dict | None = None
    current_hunk: dict | None = None

    for line in raw.splitlines():
        if line.startswith("diff --git"):
            if current_file and current_hunk:
                current_file["hunks"].append(current_hunk)
            if current_file:
                files.append(current_file)
            # Extract path from "diff --git a/foo b/foo"
            parts = line.split(" b/", 1)
            path = parts[1] if len(parts) > 1 else ""
            current_file = {"path": path, "hunks": []}
            current_hunk = None
        elif line.startswith("@@ "):
            if current_file and current_hunk:
                current_file["hunks"].append(current_hunk)
            current_hunk = {"header": line, "lines": []}
        elif current_hunk is not None:
            if line.startswith("+"):
                current_hunk["lines"].append({"type": "added", "content": line[1:]})
            elif line.startswith("-"):
                current_hunk["lines"].append({"type": "removed", "content": line[1:]})
            elif line.startswith(" "):
                current_hunk["lines"].append({"type": "context", "content": line[1:]})
            elif line.startswith("\\"):
                # "\ No newline at end of file"
                pass

    if current_file and current_hunk:
        current_file["hunks"].append(current_hunk)
    if current_file:
        files.append(current_file)

    return {"files": files}


async def stage(
    root: str, files: list[str], identity: ExecutionIdentity | None = None
) -> None:
    """Stage files for commit."""
    if not files:
        return
    await _run("add", "--", *files, cwd=root, identity=identity)


async def unstage(
    root: str, files: list[str], identity: ExecutionIdentity | None = None
) -> None:
    """Unstage files."""
    if not files:
        return
    await _run("restore", "--staged", "--", *files, cwd=root, identity=identity)


async def discard(
    root: str, files: list[str], identity: ExecutionIdentity | None = None
) -> None:
    """Fully discard all changes for files — both staged and unstaged.

    Tracked modified/deleted files are unstaged then restored via checkout.
    Newly added (staged) files are unstaged then deleted from disk.
    Untracked files are deleted from disk.
    """
    if not files:
        return

    requested = set(files)

    _, st_out, _ = await _run(
        "status",
        "--porcelain=v2",
        "--untracked-files=all",
        cwd=root,
        check=False,
        identity=identity,
    )

    to_unstage: list[str] = []  # staged changes that need unstaging first
    to_checkout: list[str] = []  # working-tree changes to restore from HEAD
    to_delete: list[str] = []  # untracked / newly-added files to remove

    for line in st_out.splitlines():
        if line.startswith("? "):
            path = line[2:]
            if path in requested:
                to_delete.append(path)
        elif line.startswith("1 ") or line.startswith("2 "):
            nsplits = 9 if line.startswith("2 ") else 8
            parts = line.split(" ", nsplits)
            xy = parts[1]
            path = parts[-1]
            if line.startswith("2 "):
                path = path.split("\t")[0]
            if path not in requested:
                continue
            staged_code = xy[0]
            unstaged_code = xy[1]
            if staged_code == "A":
                # Newly added file: unstage → becomes untracked → delete
                to_unstage.append(path)
                to_delete.append(path)
            elif staged_code != ".":
                # Staged modification/deletion: unstage then checkout
                to_unstage.append(path)
                to_checkout.append(path)
            if unstaged_code != "." and staged_code != "A":
                # Working-tree change: checkout (deduplicated)
                if path not in to_checkout:
                    to_checkout.append(path)

    # 1. Unstage any staged changes (reverts index to HEAD)
    if to_unstage:
        await _run("restore", "--staged", "--", *to_unstage, cwd=root, identity=identity)

    # 2. Restore working-tree files from HEAD
    if to_checkout:
        await _run("checkout", "--", *to_checkout, cwd=root, identity=identity)

    # 3. Remove untracked / newly-added files
    for f in to_delete:
        full = os.path.join(root, f)
        if os.path.isfile(full):
            os.remove(full)


async def commit(
    root: str,
    message: str,
    identity: ExecutionIdentity | None = None,
    author_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Create a commit. Returns hash and message."""
    _, out, _ = await _run("commit", "-m", message, cwd=root, identity=identity, extra_env=author_env)
    # Parse "main abc1234] message"
    hash_short = ""
    for line in out.splitlines():
        if "]" in line:
            bracket = line.split("]")[0]
            parts = bracket.split()
            if parts:
                hash_short = parts[-1]
            break
    return {"hash": hash_short, "message": message}


async def log(
    root: str,
    limit: int = 50,
    offset: int = 0,
    identity: ExecutionIdentity | None = None,
) -> list[dict[str, Any]]:
    """Get commit log."""
    fmt = "%H%x00%h%x00%an%x00%aI%x00%s"
    _, out, _ = await _run(
        "log",
        f"--format={fmt}",
        f"-n{limit}",
        f"--skip={offset}",
        "--no-merges",
        cwd=root,
        check=False,
        identity=identity,
    )

    commits = []
    for line in out.strip().splitlines():
        parts = line.split("\x00")
        if len(parts) >= 5:
            commits.append(
                {
                    "hash": parts[0],
                    "short_hash": parts[1],
                    "author": parts[2],
                    "date": parts[3],
                    "message": parts[4],
                }
            )
    return commits


async def show(
    root: str,
    ref: str,
    ignore_whitespace: bool = False,
    identity: ExecutionIdentity | None = None,
) -> dict[str, Any]:
    """Show a commit's diff."""
    fmt = "%H%x00%h%x00%an%x00%aI%x00%s"
    args = ["show", ref, f"--format={fmt}", "--patch"]
    if ignore_whitespace:
        args.append("--ignore-all-space")
    _, out, _ = await _run(*args, cwd=root, identity=identity)

    # First line is the formatted header, rest is diff
    lines = out.split("\n", 1)
    header_parts = lines[0].split("\x00")
    diff_text = lines[1] if len(lines) > 1 else ""

    info: dict[str, Any] = {}
    if len(header_parts) >= 5:
        info = {
            "hash": header_parts[0],
            "short_hash": header_parts[1],
            "author": header_parts[2],
            "date": header_parts[3],
            "message": header_parts[4],
        }

    info["diff"] = _parse_diff(diff_text)
    return info


async def branches(root: str, identity: ExecutionIdentity | None = None) -> dict[str, Any]:
    """List branches."""
    # Local branches
    _, local_out, _ = await _run(
        "branch",
        "--format=%(refname:short)\t%(HEAD)",
        cwd=root,
        check=False,
        identity=identity,
    )
    # Remote branches
    _, remote_out, _ = await _run(
        "branch",
        "-r",
        "--format=%(refname:short)",
        cwd=root,
        check=False,
        identity=identity,
    )

    current = ""
    local: list[str] = []
    remote: list[str] = []

    for line in local_out.strip().splitlines():
        parts = line.split("\t", 1)
        name = parts[0].strip()
        if not name:
            continue
        is_head = len(parts) > 1 and parts[1].strip() == "*"
        if is_head:
            current = name
        local.append(name)

    for line in remote_out.strip().splitlines():
        name = line.strip()
        if name and "/" in name and name != "origin/HEAD" and "->" not in name:
            remote.append(name)

    # Build a merged branch list (GitHub Desktop style).
    # Remote-only branches are shown without the "origin/" prefix.
    local_set = set(local)
    all_branches: list[dict[str, Any]] = []
    for name in local:
        all_branches.append(
            {
                "name": name,
                "is_current": name == current,
                "is_local": True,
                "is_remote": any(r.endswith(f"/{name}") for r in remote),
            }
        )
    for rname in remote:
        # Strip first remote prefix (e.g. "origin/feature-x" -> "feature-x")
        short = rname.split("/", 1)[1] if "/" in rname else rname
        if short not in local_set:
            all_branches.append(
                {
                    "name": short,
                    "is_current": False,
                    "is_local": False,
                    "is_remote": True,
                }
            )

    return {"current": current, "local": local, "remote": remote, "all": all_branches}


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _parse_worktree_list(raw: str, current_root: str = "") -> list[dict[str, Any]]:
    """Parse `git worktree list --porcelain` output."""
    worktrees: list[dict[str, Any]] = []
    current: dict[str, Any] = {}

    def flush() -> None:
        if not current.get("path"):
            return
        item = {
            "path": current["path"],
            "branch": current.get("branch", ""),
            "head": current.get("head", ""),
            "is_current": bool(current_root and _same_path(current["path"], current_root)),
            "is_detached": bool(current.get("is_detached")),
            "is_bare": bool(current.get("is_bare")),
        }
        if not item["branch"]:
            item["is_detached"] = True
        worktrees.append(item)

    for line in raw.splitlines():
        if not line:
            flush()
            current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line.removeprefix("worktree ")
        elif line.startswith("HEAD "):
            current["head"] = line.removeprefix("HEAD ")
        elif line.startswith("branch "):
            branch = line.removeprefix("branch ")
            current["branch"] = branch.removeprefix("refs/heads/")
        elif line == "detached":
            current["is_detached"] = True
        elif line == "bare":
            current["is_bare"] = True

    flush()
    return worktrees


async def worktrees(root: str, identity: ExecutionIdentity | None = None) -> dict[str, Any]:
    """List worktrees for the current repository."""
    _, repo_root_out, _ = await _run(
        "rev-parse", "--show-toplevel", cwd=root, identity=identity
    )
    repo_root = repo_root_out.strip() or root
    _, out, _ = await _run("worktree", "list", "--porcelain", cwd=root, identity=identity)
    items = _parse_worktree_list(out, repo_root)

    relpath = os.path.relpath(os.path.abspath(root), os.path.abspath(repo_root))
    for item in items:
        target_path = item["path"]
        if relpath != "." and not relpath.startswith(".."):
            candidate = os.path.join(item["path"], relpath)
            if os.path.isdir(candidate):
                target_path = candidate
        item["target_path"] = target_path

    return {"repo_root": repo_root, "current": repo_root, "worktrees": items}


def _worktree_path_for_branch(repo_root: str, branch: str) -> str:
    safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in branch.strip()).strip(".-")
    return os.path.join(os.path.dirname(repo_root), safe or "worktree")


async def create_worktree(
    root: str,
    branch: str,
    path: str | None = None,
    identity: ExecutionIdentity | None = None,
) -> dict[str, str]:
    """Create a new branch-backed worktree beside the current repository."""
    _, repo_root_out, _ = await _run(
        "rev-parse", "--show-toplevel", cwd=root, identity=identity
    )
    repo_root = repo_root_out.strip() or root
    target_path = path or _worktree_path_for_branch(repo_root, branch)
    await _run("worktree", "add", "-b", branch, target_path, cwd=root, identity=identity)
    return {"path": target_path}


async def checkout(root: str, branch: str, identity: ExecutionIdentity | None = None) -> None:
    """Switch branch."""
    await _run("checkout", branch, cwd=root, identity=identity)


async def create_branch(
    root: str,
    name: str,
    from_ref: str | None = None,
    identity: ExecutionIdentity | None = None,
) -> None:
    """Create and switch to a new branch."""
    args = ["checkout", "-b", name]
    if from_ref:
        args.append(from_ref)
    await _run(*args, cwd=root, identity=identity)


async def delete_branch(root: str, name: str, identity: ExecutionIdentity | None = None) -> None:
    """Delete a local branch."""
    await _run("branch", "-d", name, cwd=root, identity=identity)


async def rename_branch(
    root: str, old_name: str, new_name: str, identity: ExecutionIdentity | None = None
) -> None:
    """Rename a local branch."""
    await _run("branch", "-m", old_name, new_name, cwd=root, identity=identity)


async def pull(root: str, identity: ExecutionIdentity | None = None) -> dict[str, Any]:
    """Pull from remote."""
    code, out, err = await _run("pull", cwd=root, check=False, identity=identity)
    return {"ok": code == 0, "message": (out + err).strip()}


async def fetch(root: str, identity: ExecutionIdentity | None = None) -> dict[str, Any]:
    """Fetch remote refs without merging."""
    code, out, err = await _run("fetch", "--prune", cwd=root, check=False, identity=identity)
    return {"ok": code == 0, "message": (out + err).strip()}


async def push(
    root: str,
    force: bool = False,
    set_upstream: bool = False,
    branch: str | None = None,
    remote: str = "origin",
    identity: ExecutionIdentity | None = None,
) -> dict[str, Any]:
    """Push to remote. Use *set_upstream* for first-time branch publish."""
    args = ["push"]
    if set_upstream:
        args.extend(["-u", remote, branch or "HEAD"])
    if force:
        args.append("--force-with-lease")
    code, out, err = await _run(*args, cwd=root, check=False, identity=identity)
    return {"ok": code == 0, "message": (out + err).strip()}


async def uncommit(root: str, identity: ExecutionIdentity | None = None) -> dict[str, str]:
    """Undo the last commit, moving its changes back to the staging area.

    Uses ``git reset --soft HEAD~1``, or ``git update-ref -d HEAD`` for root
    commits (no parent).
    """
    # Grab info about the commit we're about to undo
    _, log_out, _ = await _run(
        "log", "-1", "--format=%H%x00%h%x00%s", cwd=root, check=False, identity=identity
    )
    parts = log_out.strip().split("\x00")
    undone_hash = parts[1] if len(parts) >= 2 else ""
    undone_msg = parts[2] if len(parts) >= 3 else ""

    # Check if HEAD~1 exists (root commits have no parent)
    code, _, _ = await _run(
        "rev-parse", "--verify", "HEAD~1", cwd=root, check=False, identity=identity
    )
    if code != 0:
        # Root commit: remove HEAD ref, keeps index (staged files) intact
        await _run("update-ref", "-d", "HEAD", cwd=root, identity=identity)
    else:
        await _run("reset", "--soft", "HEAD~1", cwd=root, identity=identity)

    return {"hash": undone_hash, "message": undone_msg}


async def stash_list(root: str, identity: ExecutionIdentity | None = None) -> list[dict[str, str]]:
    """List stashes."""
    _, out, _ = await _run(
        "stash", "list", "--format=%gd%x00%s", cwd=root, check=False, identity=identity
    )
    stashes = []
    for line in out.strip().splitlines():
        parts = line.split("\x00", 1)
        if len(parts) >= 2:
            stashes.append({"ref": parts[0], "message": parts[1]})
    return stashes


async def stash_save(
    root: str, message: str | None = None, identity: ExecutionIdentity | None = None
) -> dict[str, Any]:
    """Stash changes."""
    args = ["stash", "push", "--include-untracked"]
    if message:
        args.extend(["-m", message])
    code, out, err = await _run(*args, cwd=root, check=False, identity=identity)
    return {"ok": code == 0, "message": (out + err).strip()}


async def stash_pop(
    root: str, index: int = 0, identity: ExecutionIdentity | None = None
) -> dict[str, Any]:
    """Pop a stash."""
    code, out, err = await _run(
        "stash", "pop", f"stash@{{{index}}}", cwd=root, check=False, identity=identity
    )
    return {"ok": code == 0, "message": (out + err).strip()}

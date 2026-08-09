"""Git API router.

Exposes git operations for the active workspace.
All endpoints require a `root` path (the workspace directory).
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from cptr.models import UserStates
from cptr.utils.ai import generate_text
from cptr.utils.identity import (
    ExecutionIdentity,
    IdentityUnavailable,
    expand_user_path,
    identity_for_request,
)
from cptr.utils import gh
from cptr.utils.json_parser import extract_json
from cptr.utils.git import (
    GitError,
    branches,
    checkout,
    commit,
    create_branch,
    create_worktree,
    delete_branch,
    diff,
    discard,
    effective_config,
    fetch,
    version,
    is_repo,
    log,
    pull,
    push,
    rename_branch,
    show,
    stage,
    stash_list,
    stash_pop,
    stash_save,
    staged_diff,
    status,
    uncommit,
    unstage,
    worktrees,
)

router = APIRouter(prefix="/api/git", tags=["git"])


def _handle_git_error(e: GitError) -> None:
    raise HTTPException(status_code=400, detail=str(e))


def _handle_gh_error(e: gh.GhError) -> None:
    status = 404 if e.returncode == 404 else 400
    raise HTTPException(status_code=status, detail=str(e))


async def _identity(request: Request) -> ExecutionIdentity:
    try:
        return await identity_for_request(request)
    except IdentityUnavailable as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _resolve_root(root: str | None, identity: ExecutionIdentity) -> str:
    return str(expand_user_path(root or identity.home, identity).resolve())


async def _root_identity(request: Request, root: str | None) -> tuple[str, ExecutionIdentity]:
    identity = await _identity(request)
    return _resolve_root(root, identity), identity


async def _require_repo(root: str, identity: ExecutionIdentity) -> None:
    """Raise 400 if root is not a git repository."""
    if not await is_repo(root, identity):
        raise HTTPException(status_code=400, detail="Not a git repository")


def _is_admin(request: Request) -> bool:
    auth = getattr(request.state, "auth", None)
    return bool(auth and getattr(auth, "role", None) == "admin")


def _require_gh_mutation_allowed(request: Request, identity: ExecutionIdentity) -> None:
    if identity.is_pam or _is_admin(request):
        return
    raise HTTPException(status_code=403, detail="admin required")


async def _user_git_identity(identity: ExecutionIdentity) -> dict[str, str]:
    if not identity.app_user_id:
        return {}
    prefs = await UserStates.get_data(identity.app_user_id)
    git_prefs = prefs.get("git") if isinstance(prefs, dict) else None
    ident = git_prefs.get("identity") if isinstance(git_prefs, dict) else None
    if not isinstance(ident, dict):
        return {}
    name = ident.get("name")
    email = ident.get("email")
    return {
        "name": name.strip() if isinstance(name, str) else "",
        "email": email.strip() if isinstance(email, str) else "",
    }


async def _commit_author_env(root: str, identity: ExecutionIdentity) -> dict[str, str] | None:
    config = await effective_config(root, identity)
    git_identity = config.get("identity") or {}
    if git_identity.get("name") and git_identity.get("email"):
        return None
    fallback = await _user_git_identity(identity)
    name = str(git_identity.get("name") or fallback.get("name") or "").strip()
    email = str(git_identity.get("email") or fallback.get("email") or "").strip()
    if not name or not email:
        return None
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }


# -- Query endpoints --


@router.get("/status")
async def git_status(request: Request, root: str):
    """Get branch info and changed files. Returns empty for non-git dirs."""
    root, identity = await _root_identity(request, root)
    if not await is_repo(root, identity):
        return {"is_repo": False, "branch": "", "ahead": 0, "behind": 0, "files": []}
    try:
        result = await status(root, identity)
        result["is_repo"] = True
        return result
    except GitError as e:
        _handle_git_error(e)


@router.get("/config")
async def git_config(request: Request, root: str | None = None):
    """Get Git and gh settings data for the Settings UI."""
    root, identity = await _root_identity(request, root)
    git_version = await version(identity.home, identity)
    git_data: dict = {
        "installed": bool(git_version),
        "version": git_version,
        "is_repo": False,
        "identity": {},
        "credential_helpers": [],
        "remote_url": "",
    }
    if not git_version:
        return {
            "root": root,
            "git": git_data,
            "app_identity": await _user_git_identity(identity),
            "gh": {"installed": False, "version": None, "hosts": {}, "message": "Git is not installed."},
            "permissions": {
                "can_manage_gh": identity.is_pam or _is_admin(request),
                "can_manage_commit_model": _is_admin(request),
            },
        }
    if await is_repo(root, identity):
        git_data = {**git_data, "is_repo": True, **await effective_config(root, identity)}
    else:
        git_data = {**git_data, "is_repo": False, **await effective_config(identity.home, identity)}
    try:
        gh_status = await gh.auth_status(identity)
    except gh.GhError as e:
        gh_status = {"installed": False, "version": None, "hosts": {}, "message": str(e)}
    return {
        "root": root,
        "git": git_data,
        "app_identity": await _user_git_identity(identity),
        "gh": gh_status,
        "permissions": {
            "can_manage_gh": identity.is_pam or _is_admin(request),
            "can_manage_commit_model": _is_admin(request),
        },
    }


@router.get("/diff")
async def git_diff(
    request: Request,
    root: str,
    file: Optional[str] = None,
    staged: bool = False,
    untracked: bool = False,
    ignore_whitespace: bool = False,
):
    """Get diff for working tree or staged changes."""
    root, identity = await _root_identity(request, root)
    await _require_repo(root, identity)
    try:
        return await diff(root, file, staged, untracked, ignore_whitespace, identity)
    except GitError as e:
        _handle_git_error(e)


@router.get("/log")
async def git_log(request: Request, root: str, limit: int = 50, offset: int = 0):
    """Get commit history."""
    root, identity = await _root_identity(request, root)
    await _require_repo(root, identity)
    try:
        return await log(root, limit, offset, identity)
    except GitError as e:
        _handle_git_error(e)


@router.get("/show")
async def git_show(request: Request, root: str, ref: str, ignore_whitespace: bool = False):
    """Show a single commit with its diff."""
    root, identity = await _root_identity(request, root)
    await _require_repo(root, identity)
    try:
        return await show(root, ref, ignore_whitespace, identity)
    except GitError as e:
        _handle_git_error(e)


@router.get("/branches")
async def git_branches(request: Request, root: str):
    """List local and remote branches."""
    root, identity = await _root_identity(request, root)
    await _require_repo(root, identity)
    try:
        return await branches(root, identity)
    except GitError as e:
        _handle_git_error(e)


@router.get("/worktrees")
async def git_worktrees(request: Request, root: str):
    """List git worktrees for the active repository."""
    root, identity = await _root_identity(request, root)
    await _require_repo(root, identity)
    try:
        return await worktrees(root, identity)
    except GitError as e:
        _handle_git_error(e)


@router.get("/stashes")
async def git_stashes(request: Request, root: str):
    """List stashes."""
    root, identity = await _root_identity(request, root)
    await _require_repo(root, identity)
    try:
        return await stash_list(root, identity)
    except GitError as e:
        _handle_git_error(e)


# -- Mutation endpoints --


class StageRequest(BaseModel):
    root: str
    files: List[str]


class CommitRequest(BaseModel):
    root: str
    message: str


COMMIT_MESSAGE_PROMPT = """Write a conventional, concise Git commit message for this staged diff.
Respond with ONLY JSON in this shape: {"summary":"...","description":"..."}.
The summary must be imperative, under 72 characters, and have no trailing period.
The description should be one short paragraph explaining the meaningful change. Do not invent details."""


def _parse_commit_message_response(text: str) -> tuple[str, str]:
    message = extract_json(text)
    summary = str(message.get("summary", "")).strip() if isinstance(message, dict) else ""
    description = str(message.get("description", "")).strip() if isinstance(message, dict) else ""
    if summary:
        return summary.splitlines()[0][:72], description

    lines = [
        line.strip()
        for line in text.strip().splitlines()
        if line.strip() and not line.strip().startswith("```")
    ]
    if not lines:
        return "", ""
    return lines[0][:72], "\n".join(lines[1:]).strip()


class CheckoutRequest(BaseModel):
    root: str
    branch: str


class CreateBranchRequest(BaseModel):
    root: str
    name: str
    from_ref: Optional[str] = None


class CreateWorktreeRequest(BaseModel):
    root: str
    branch: str
    path: Optional[str] = None


class DeleteBranchRequest(BaseModel):
    root: str
    name: str


class RenameBranchRequest(BaseModel):
    root: str
    old_name: str
    new_name: str


class RootRequest(BaseModel):
    root: str


class CommitMessageRequest(RootRequest):
    model_id: Optional[str] = None


class PushRequest(BaseModel):
    root: str
    force: bool = False
    set_upstream: bool = False
    branch: Optional[str] = None


class StashSaveRequest(BaseModel):
    root: str
    message: Optional[str] = None


class StashPopRequest(BaseModel):
    root: str
    index: int = 0


class GhLoginStartRequest(BaseModel):
    hostname: str = "github.com"
    git_protocol: str = "https"


class GhLoginStatusRequest(BaseModel):
    session_id: str


class GhHostRequest(BaseModel):
    hostname: str = "github.com"


class GhUserHostRequest(GhHostRequest):
    user: Optional[str] = None


@router.post("/stage")
async def git_stage(request: Request, body: StageRequest):
    """Stage files for commit."""
    root, identity = await _root_identity(request, body.root)
    try:
        await stage(root, body.files, identity)
        return {"ok": True}
    except GitError as e:
        _handle_git_error(e)


@router.post("/unstage")
async def git_unstage(request: Request, body: StageRequest):
    """Unstage files."""
    root, identity = await _root_identity(request, body.root)
    try:
        await unstage(root, body.files, identity)
        return {"ok": True}
    except GitError as e:
        _handle_git_error(e)


@router.post("/discard")
async def git_discard(request: Request, body: StageRequest):
    """Discard unstaged changes."""
    root, identity = await _root_identity(request, body.root)
    try:
        await discard(root, body.files, identity)
        return {"ok": True}
    except GitError as e:
        _handle_git_error(e)


@router.post("/commit")
async def git_commit(request: Request, body: CommitRequest):
    """Create a commit."""
    root, identity = await _root_identity(request, body.root)
    try:
        return await commit(root, body.message, identity, await _commit_author_env(root, identity))
    except GitError as e:
        _handle_git_error(e)


@router.post("/message")
async def generate_commit_message(request: Request, body: CommitMessageRequest):
    """Generate a commit summary and description from the staged diff."""
    root, identity = await _root_identity(request, body.root)
    await _require_repo(root, identity)
    try:
        patch = await staged_diff(root, identity=identity)
    except GitError as e:
        _handle_git_error(e)
    if not patch:
        raise HTTPException(status_code=400, detail="No staged changes")

    from cptr.models import Config

    message_model = body.model_id.strip() if isinstance(body.model_id, str) else None
    if message_model is None:
        configured_model = await Config.get("git.commit_message_generation.model")
        message_model = configured_model.strip() if isinstance(configured_model, str) else None
    if not message_model:
        default_model = await Config.get("chat.default_model")
        message_model = default_model.strip() if isinstance(default_model, str) else None
    text = await generate_text(
        request,
        model_id=message_model,
        messages=[{"role": "user", "content": patch}],
        system=COMMIT_MESSAGE_PROMPT,
        max_tokens=180,
    )
    if text is None:
        raise HTTPException(status_code=502, detail="Could not generate a commit message")
    summary, description = _parse_commit_message_response(text)
    if not summary:
        raise HTTPException(status_code=502, detail="Could not generate a commit message")
    return {"summary": summary, "description": description}


@router.post("/checkout")
async def git_checkout(request: Request, body: CheckoutRequest):
    """Switch branch."""
    root, identity = await _root_identity(request, body.root)
    try:
        await checkout(root, body.branch, identity)
        return {"ok": True}
    except GitError as e:
        _handle_git_error(e)


@router.post("/branch")
async def git_create_branch(request: Request, body: CreateBranchRequest):
    """Create and switch to a new branch."""
    root, identity = await _root_identity(request, body.root)
    try:
        await create_branch(root, body.name, body.from_ref, identity)
        return {"ok": True}
    except GitError as e:
        _handle_git_error(e)


@router.delete("/branch")
async def git_delete_branch(request: Request, body: DeleteBranchRequest):
    """Delete a local branch."""
    root, identity = await _root_identity(request, body.root)
    try:
        await delete_branch(root, body.name, identity)
        return {"ok": True}
    except GitError as e:
        _handle_git_error(e)


@router.post("/worktrees")
async def git_create_worktree(request: Request, body: CreateWorktreeRequest):
    """Create a new branch-backed worktree."""
    root, identity = await _root_identity(request, body.root)
    try:
        return await create_worktree(root, body.branch, body.path, identity)
    except GitError as e:
        _handle_git_error(e)


@router.patch("/branch")
async def git_rename_branch(request: Request, body: RenameBranchRequest):
    """Rename a local branch."""
    root, identity = await _root_identity(request, body.root)
    try:
        await rename_branch(root, body.old_name, body.new_name, identity)
        return {"ok": True}
    except GitError as e:
        _handle_git_error(e)


@router.post("/pull")
async def git_pull(request: Request, body: RootRequest):
    """Pull from remote."""
    root, identity = await _root_identity(request, body.root)
    try:
        return await pull(root, identity)
    except GitError as e:
        _handle_git_error(e)


@router.post("/fetch")
async def git_fetch(request: Request, body: RootRequest):
    """Fetch from remote without merging."""
    root, identity = await _root_identity(request, body.root)
    try:
        return await fetch(root, identity)
    except GitError as e:
        _handle_git_error(e)


@router.post("/push")
async def git_push(request: Request, body: PushRequest):
    """Push to remote."""
    root, identity = await _root_identity(request, body.root)
    try:
        return await push(root, body.force, body.set_upstream, body.branch, identity=identity)
    except GitError as e:
        _handle_git_error(e)


@router.post("/uncommit")
async def git_uncommit(request: Request, body: RootRequest):
    """Undo the last commit, moving changes back to staging."""
    root, identity = await _root_identity(request, body.root)
    await _require_repo(root, identity)
    try:
        return await uncommit(root, identity)
    except GitError as e:
        _handle_git_error(e)


@router.post("/stash")
async def git_stash(request: Request, body: StashSaveRequest):
    """Stash changes."""
    root, identity = await _root_identity(request, body.root)
    try:
        return await stash_save(root, body.message, identity)
    except GitError as e:
        _handle_git_error(e)


@router.post("/unstash")
async def git_stash_pop(request: Request, body: StashPopRequest):
    """Pop a stash."""
    root, identity = await _root_identity(request, body.root)
    try:
        return await stash_pop(root, body.index, identity)
    except GitError as e:
        _handle_git_error(e)


@router.post("/gh/login/start")
async def gh_login_start(request: Request, body: GhLoginStartRequest):
    identity = await _identity(request)
    _require_gh_mutation_allowed(request, identity)
    try:
        return await gh.start_login(
            identity,
            hostname=body.hostname.strip() or "github.com",
            git_protocol=body.git_protocol if body.git_protocol in {"https", "ssh"} else "https",
        )
    except gh.GhError as e:
        _handle_gh_error(e)


@router.post("/gh/login/status")
async def gh_login_status(request: Request, body: GhLoginStatusRequest):
    identity = await _identity(request)
    try:
        result = gh.login_status(identity, body.session_id)
        if result["status"] == "complete":
            result["auth"] = await gh.auth_status(identity)
        return result
    except gh.GhError as e:
        _handle_gh_error(e)


@router.post("/gh/login/cancel")
async def gh_login_cancel(request: Request, body: GhLoginStatusRequest):
    identity = await _identity(request)
    _require_gh_mutation_allowed(request, identity)
    try:
        gh.cancel_login(identity, body.session_id)
        return {"ok": True}
    except gh.GhError as e:
        _handle_gh_error(e)


@router.post("/gh/logout")
async def gh_logout(request: Request, body: GhUserHostRequest):
    identity = await _identity(request)
    _require_gh_mutation_allowed(request, identity)
    try:
        await gh.logout(identity, body.hostname.strip() or "github.com", body.user)
        return {"ok": True, "auth": await gh.auth_status(identity)}
    except gh.GhError as e:
        _handle_gh_error(e)


@router.post("/gh/switch")
async def gh_switch(request: Request, body: GhUserHostRequest):
    identity = await _identity(request)
    _require_gh_mutation_allowed(request, identity)
    if not body.user:
        raise HTTPException(status_code=400, detail="user is required")
    try:
        await gh.switch(identity, body.hostname.strip() or "github.com", body.user)
        return {"ok": True, "auth": await gh.auth_status(identity)}
    except gh.GhError as e:
        _handle_gh_error(e)


@router.post("/gh/setup-git")
async def gh_setup_git(request: Request, body: GhHostRequest):
    identity = await _identity(request)
    _require_gh_mutation_allowed(request, identity)
    try:
        await gh.setup_git(identity, body.hostname.strip() or "github.com")
        return {"ok": True}
    except gh.GhError as e:
        _handle_gh_error(e)

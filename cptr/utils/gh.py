"""GitHub CLI operations via subprocess."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from cptr.utils.identity import ExecutionIdentity, env_for, preexec_for


class GhError(Exception):
    def __init__(self, message: str, returncode: int = 1):
        super().__init__(message)
        self.returncode = returncode


@dataclass
class GhLoginSession:
    id: str
    owner_key: str
    proc: asyncio.subprocess.Process
    started_at: float
    verification_uri: str = ""
    user_code: str = ""
    output: list[str] = field(default_factory=list)


_login_sessions: dict[str, GhLoginSession] = {}


def _owner_key(identity: ExecutionIdentity) -> str:
    return f"{identity.app_user_id or ''}:{identity.username}:{identity.uid or ''}"


def _gh_path() -> str | None:
    return shutil.which("gh")


async def run_gh(
    args: list[str],
    *,
    identity: ExecutionIdentity,
    cwd: str | None = None,
    check: bool = True,
    timeout: float = 20,
) -> tuple[int, str, str]:
    gh = _gh_path()
    if not gh:
        raise GhError("GitHub CLI is not installed")
    work_dir = cwd or identity.home
    env = env_for(identity, work_dir) if identity.is_pam else os.environ.copy()
    env["GH_PROMPT_DISABLED"] = "1"
    proc = await asyncio.create_subprocess_exec(
        gh,
        *args,
        cwd=work_dir,
        env=env,
        preexec_fn=preexec_for(identity) if identity.is_pam else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise GhError("GitHub CLI command timed out") from exc
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise GhError((stderr or stdout).strip() or "GitHub CLI command failed", proc.returncode)
    return proc.returncode or 0, stdout, stderr


async def version(identity: ExecutionIdentity) -> str | None:
    if not _gh_path():
        return None
    code, out, _ = await run_gh(["--version"], identity=identity, check=False, timeout=5)
    if code != 0:
        return None
    return out.splitlines()[0].strip() if out.strip() else None


async def auth_status(identity: ExecutionIdentity, hostname: str | None = None) -> dict[str, Any]:
    gh_version = await version(identity)
    if gh_version is None:
        return {"installed": False, "version": None, "hosts": {}}
    args = ["auth", "status", "--json", "hosts"]
    if hostname:
        args.extend(["--hostname", hostname])
    code, out, err = await run_gh(args, identity=identity, check=False, timeout=10)
    if not out.strip():
        return {"installed": True, "version": gh_version, "hosts": {}, "message": err.strip()}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {"installed": True, "version": gh_version, "hosts": {}, "message": err.strip()}
    return {"installed": True, "version": gh_version, "hosts": data.get("hosts") or {}}


def parse_device_prompt(text: str) -> tuple[str, str]:
    uri_match = re.search(r"https://\S+/login/device\b", text)
    code_match = re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b", text)
    return (
        uri_match.group(0).rstrip(".,)") if uri_match else "",
        code_match.group(0) if code_match else "",
    )


async def _read_login_stream(session: GhLoginSession, stream: asyncio.StreamReader | None) -> None:
    if stream is None:
        return
    while True:
        chunk = await stream.readline()
        if not chunk:
            return
        text = chunk.decode("utf-8", errors="replace")
        session.output.append(text)
        uri, code = parse_device_prompt("".join(session.output))
        if uri:
            session.verification_uri = uri
        if code:
            session.user_code = code


async def start_login(
    identity: ExecutionIdentity,
    *,
    hostname: str = "github.com",
    git_protocol: str = "https",
) -> dict[str, Any]:
    gh = _gh_path()
    if not gh:
        raise GhError("GitHub CLI is not installed")
    work_dir = identity.home
    env = env_for(identity, work_dir) if identity.is_pam else os.environ.copy()
    proc = await asyncio.create_subprocess_exec(
        gh,
        "auth",
        "login",
        "--web",
        "--hostname",
        hostname,
        "--git-protocol",
        git_protocol,
        cwd=work_dir,
        env=env,
        preexec_fn=preexec_for(identity) if identity.is_pam else None,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    session = GhLoginSession(
        id=uuid.uuid4().hex,
        owner_key=_owner_key(identity),
        proc=proc,
        started_at=time.time(),
    )
    _login_sessions[session.id] = session
    asyncio.create_task(_read_login_stream(session, proc.stdout))
    asyncio.create_task(_read_login_stream(session, proc.stderr))

    deadline = time.time() + 8
    while time.time() < deadline and proc.returncode is None:
        if session.verification_uri and session.user_code:
            break
        await asyncio.sleep(0.1)

    if proc.returncode is not None and proc.returncode != 0:
        _login_sessions.pop(session.id, None)
        raise GhError("".join(session.output).strip() or "GitHub CLI login failed", proc.returncode)

    return login_status(identity, session.id)


def login_status(identity: ExecutionIdentity, session_id: str) -> dict[str, Any]:
    session = _login_sessions.get(session_id)
    if not session or session.owner_key != _owner_key(identity):
        raise GhError("Login session not found", 404)
    state = "pending"
    if session.proc.returncode == 0:
        state = "complete"
        _login_sessions.pop(session_id, None)
    elif session.proc.returncode is not None:
        state = "failed"
        _login_sessions.pop(session_id, None)
    elif time.time() - session.started_at > 900:
        session.proc.kill()
        state = "expired"
        _login_sessions.pop(session_id, None)
    return {
        "session_id": session_id,
        "status": state,
        "verification_uri": session.verification_uri,
        "user_code": session.user_code,
    }


def cancel_login(identity: ExecutionIdentity, session_id: str) -> None:
    session = _login_sessions.get(session_id)
    if not session or session.owner_key != _owner_key(identity):
        raise GhError("Login session not found", 404)
    if session.proc.returncode is None:
        session.proc.kill()
    _login_sessions.pop(session_id, None)


async def logout(identity: ExecutionIdentity, hostname: str, user: str | None = None) -> None:
    args = ["auth", "logout", "--hostname", hostname]
    if user:
        args.extend(["--user", user])
    await run_gh(args, identity=identity, timeout=15)


async def switch(identity: ExecutionIdentity, hostname: str, user: str) -> None:
    await run_gh(["auth", "switch", "--hostname", hostname, "--user", user], identity=identity)


async def setup_git(identity: ExecutionIdentity, hostname: str | None = None) -> None:
    args = ["auth", "setup-git"]
    if hostname:
        args.extend(["--hostname", hostname])
    await run_gh(args, identity=identity)

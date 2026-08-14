"""Execution identity for OS-facing work.

PAM mode means terminals, command execution, and user files should run as the
authenticated Unix user. Password mode remains server-process scoped.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fastapi import Request
from cptr.models import Auth, User
from cptr.utils.config import AuthMode, AuthResult, get_auth_mode

IS_WINDOWS = platform.system() == "Windows"


class IdentityUnavailable(RuntimeError):
    """Raised when an authenticated request cannot be mapped to an OS identity."""

    def __init__(self, message: str, status_code: int = 403) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ExecutionIdentity:
    app_user_id: str | None
    username: str
    uid: int | None
    gid: int | None
    groups: tuple[int, ...]
    home: str
    shell: str
    is_pam: bool


def _current_identity(auth: AuthResult | None = None) -> ExecutionIdentity:
    username = os.environ.get("USER") or os.environ.get("USERNAME") or "user"
    uid = None if IS_WINDOWS else os.getuid()
    gid = None if IS_WINDOWS else os.getgid()
    groups = () if IS_WINDOWS else tuple(os.getgroups())
    return ExecutionIdentity(
        app_user_id=auth.user_id if auth else None,
        username=username,
        uid=uid,
        gid=gid,
        groups=groups,
        home=str(Path.home()),
        shell=os.environ.get("SHELL") or os.environ.get("COMSPEC") or "/bin/sh",
        is_pam=False,
    )


def _pam_identity(auth: AuthResult) -> ExecutionIdentity:
    if IS_WINDOWS:
        raise IdentityUnavailable("PAM impersonation is not available on Windows")
    if not auth.username:
        raise IdentityUnavailable("PAM request is missing a username", status_code=401)

    try:
        import pwd

        pw = pwd.getpwnam(auth.username)
    except KeyError as exc:
        raise IdentityUnavailable(f"Unix user not found: {auth.username}") from exc

    try:
        groups = tuple(os.getgrouplist(auth.username, pw.pw_gid))
    except AttributeError:
        groups = (pw.pw_gid,)

    return ExecutionIdentity(
        app_user_id=auth.user_id,
        username=auth.username,
        uid=pw.pw_uid,
        gid=pw.pw_gid,
        groups=groups,
        home=pw.pw_dir,
        shell=pw.pw_shell or "/bin/sh",
        is_pam=True,
    )


def _identity_from_auth(auth: AuthResult | None) -> ExecutionIdentity:
    if get_auth_mode() != AuthMode.PAM:
        return _current_identity(auth)
    if auth is None:
        raise IdentityUnavailable("not authenticated", status_code=401)
    return _pam_identity(auth)


async def identity_for_request(request) -> ExecutionIdentity:
    if request is None:
        return _identity_from_auth(None)
    return _identity_from_auth(getattr(request.state, "auth", None))


async def identity_for_user_id(user_id: str | None) -> ExecutionIdentity:
    if get_auth_mode() != AuthMode.PAM:
        return _current_identity(AuthResult(user_id=user_id))
    if not user_id:
        raise IdentityUnavailable("missing user id", status_code=401)
    auth = await Auth.get_by_user_id(user_id)
    if auth is None:
        raise IdentityUnavailable("user auth identity not found", status_code=401)
    return _identity_from_auth(AuthResult(user_id=user_id, username=auth.username))


async def identity_for_context(context: dict) -> ExecutionIdentity:
    request = context.get("request")
    if request is not None:
        return await identity_for_request(request)
    return await identity_for_user_id(context.get("user_id"))


async def internal_request_for_user(app, user_id: str | None) -> Request:
    if not user_id:
        raise IdentityUnavailable("missing user id", status_code=401)
    user = await User.get_by_id(user_id)
    if user is None:
        raise IdentityUnavailable("user not found", status_code=401)
    auth = await Auth.get_by_user_id(user_id)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/__internal__",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "server": ("internal", 0),
            "scheme": "http",
            "app": app,
            "state": {},
        }
    )
    request.state.auth = AuthResult(
        user_id=user_id,
        username=auth.username if auth else None,
        role=user.role,
    )
    request.state.internal = True
    return request


def env_for(
    identity: ExecutionIdentity,
    cwd: str | Path,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "HOME": identity.home,
            "USER": identity.username,
            "LOGNAME": identity.username,
            "SHELL": identity.shell,
            "PWD": str(cwd),
            "PATH": env.get("PATH") or "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "TERM": env.get("TERM") or "xterm-256color",
        }
    )
    if extra:
        env.update(extra)
    return env


def preexec_for(identity: ExecutionIdentity) -> Callable[[], None] | None:
    if IS_WINDOWS or not identity.is_pam or identity.uid is None or identity.gid is None:
        return None
    if os.geteuid() == identity.uid:
        return None
    if os.geteuid() != 0:
        raise IdentityUnavailable(
            f"Cannot run OS work as {identity.username}; server must run as root or that user"
        )

    def _drop_privileges() -> None:
        os.setgroups(list(identity.groups))
        os.setgid(identity.gid)
        os.setuid(identity.uid)

    return _drop_privileges


def expand_user_path(path: str | Path, identity: ExecutionIdentity) -> Path:
    raw = str(path)
    if raw == "~":
        return Path(identity.home)
    if raw.startswith("~/"):
        return Path(identity.home) / raw[2:]
    return Path(raw)

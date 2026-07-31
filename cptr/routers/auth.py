"""Authentication router for cptr."""

from __future__ import annotations

from fastapi import APIRouter, Request, UploadFile, File as FastAPIFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from cptr.utils.config import (
    SESSION_MAX_AGE,
    AuthMode,
    check_access,
    check_rate_limit,
    create_token,
    get_auth_mode,
    get_or_create_user,
    has_any_user,
    hash_password,
    load_config,
    now_ms,
    pam_authenticate,
    record_attempt,
    verify_password,
)
from cptr.models import User, Auth, Config

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "cptr_session"


def _ok_with_cookie(jwt_token: str, data: dict | None = None) -> JSONResponse:
    """Return a JSONResponse with the session cookie set."""
    resp = JSONResponse(data or {"ok": True})
    resp.set_cookie(
        key=COOKIE_NAME,
        value=jwt_token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=SESSION_MAX_AGE,
    )
    return resp


@router.get("")
async def get_auth(request: Request):
    """Session check. Returns user info or {authenticated: false}.

    Implements sliding sessions: if the token is past its halfway point,
    a fresh token is issued so active users never get logged out.
    """
    import time

    client_host = request.client.host if request.client else "127.0.0.1"
    token = request.cookies.get(COOKIE_NAME)
    remote_user = None
    if get_auth_mode() == AuthMode.TRUSTED_HEADER:
        header_name = load_config().get("auth", {}).get("header", "Remote-User")
        remote_user = request.headers.get(header_name)
    auth = check_access(client_host=client_host, jwt_token=token, remote_user_header=remote_user)

    if auth is not None and auth.username and not auth.user_id:
        auth.user_id = await get_or_create_user(auth.username)

    if auth is not None and auth.user_id:
        if not auth.exp:
            auth.exp = time.time() + SESSION_MAX_AGE
        user = await User.get_by_id(auth.user_id)
        if user is None:
            from starlette.responses import JSONResponse as StarletteJSONResponse

            response = StarletteJSONResponse({"authenticated": False})
            response.delete_cookie(COOKIE_NAME, path="/")
            return response

        data = {
            "authenticated": True,
            "user_id": auth.user_id,
            "username": auth.username,
            "display_name": user.display_name,
            "role": user.role,
            "profile_image_url": user.profile_image_url,
            "exp": int(auth.exp * 1000),
        }

        # Sliding session: refresh token if past halfway to expiry
        remaining = auth.exp - time.time()
        if remote_user or remaining < SESSION_MAX_AGE / 2:
            new_token = create_token(auth.user_id, auth.username, user.role)
            return _ok_with_cookie(new_token, data)

        return data

    return {"authenticated": False}


@router.post("/setup")
async def setup(body: SetupRequest, request: Request):
    """First-time admin setup. Requires valid startup token."""
    if await has_any_user():
        return JSONResponse({"error": "already set up"}, 400)
    import secrets as _secrets

    expected = getattr(request.app.state, "startup_token", None)
    if not body.token or not expected or not _secrets.compare_digest(body.token, expected):
        return JSONResponse({"error": "invalid startup token"}, 403)
    if not body.username or not body.username.strip():
        return JSONResponse({"error": "username required"}, 400)
    if len(body.password.strip()) < 6:
        return JSONResponse({"error": "min 6 characters"}, 400)

    user_id = await User.create(
        username=body.username.strip(),
        password_hash=hash_password(body.password.strip()),
        role="admin",
        display_name=body.display_name,
        created_at=now_ms(),
    )

    return _ok_with_cookie(create_token(user_id, body.username.strip(), role="admin"))


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    """Login with password or PAM."""
    ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(ip):
        return JSONResponse({"error": "too many attempts"}, 429)
    record_attempt(ip)

    mode = get_auth_mode()

    if mode == AuthMode.PASSWORD:
        if not body.username:
            return JSONResponse({"error": "username required"}, 400)
        result = await Auth.get_with_user(body.username)
        if not result or not verify_password(body.password, result[0].password):
            return JSONResponse({"error": "incorrect credentials"}, 401)
        auth, user = result
        if user.role == "pending":
            return JSONResponse({"error": "account pending approval"}, 403)
        return _ok_with_cookie(create_token(auth.user_id, auth.username, role=user.role))

    if mode == AuthMode.PAM:
        if not body.username:
            return JSONResponse({"error": "username required"}, 400)
        if not pam_authenticate(body.username, body.password):
            return JSONResponse({"error": "incorrect credentials"}, 401)
        user_id = await get_or_create_user(body.username)
        user = await User.get_by_id(user_id)
        return _ok_with_cookie(
            create_token(user_id, body.username, role=user.role if user else "user"),
            {"ok": True, "username": body.username},
        )

    return JSONResponse({"error": "auth not configured"}, 400)


@router.post("/logout")
async def logout():
    """Logout = delete cookie."""
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(key=COOKIE_NAME, path="/")
    return resp


@router.post("/password")
async def update_password(body: UpdatePasswordRequest, request: Request):
    """Update password for the authenticated user."""
    token = request.cookies.get(COOKIE_NAME)
    auth_info = check_access(
        client_host=request.client.host if request.client else "127.0.0.1",
        jwt_token=token,
    )
    if auth_info is None:
        return JSONResponse({"error": "not authenticated"}, 401)

    if len(body.new_password.strip()) < 6:
        return JSONResponse({"error": "min 6 characters"}, 400)

    # Verify current password
    result = await Auth.get_with_user(auth_info.username)
    if not result:
        return JSONResponse({"error": "user not found"}, 404)
    auth, _ = result
    if auth.password and not verify_password(body.current_password, auth.password):
        return JSONResponse({"error": "incorrect current password"}, 401)

    await Auth.update_password(auth_info.user_id, hash_password(body.new_password.strip()))
    return {"ok": True}


@router.post("/signup")
async def signup(body: SignupRequest, request: Request):
    """Self-registration. Only works if auth.signup_enabled is true."""
    signup_enabled = await Config.get("auth.signup_enabled")
    if not signup_enabled:
        return JSONResponse({"error": "sign up disabled"}, 403)

    if not body.username or not body.username.strip():
        return JSONResponse({"error": "username required"}, 400)
    if len(body.password.strip()) < 6:
        return JSONResponse({"error": "min 6 characters"}, 400)

    username = body.username.strip()
    if await Auth.username_exists(username):
        return JSONResponse({"error": "username taken"}, 409)

    await User.create(
        username=username,
        password_hash=hash_password(body.password.strip()),
        role="pending",
        created_at=now_ms(),
    )
    return {"ok": True, "pending": True}


# ── Request Models ───────────────────────────────────────────


class LoginRequest(BaseModel):
    username: Optional[str] = None
    password: str


class SetupRequest(BaseModel):
    username: str
    password: str
    token: str
    display_name: Optional[str] = None


class SignupRequest(BaseModel):
    username: str
    password: str


class UpdatePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None


@router.put("/profile")
async def update_profile(request: Request, body: UpdateProfileRequest):
    """Update display name."""
    token = request.cookies.get(COOKIE_NAME)
    auth_info = check_access(
        client_host=request.client.host if request.client else "127.0.0.1",
        jwt_token=token,
    )
    if auth_info is None:
        return JSONResponse({"error": "not authenticated"}, 401)

    await User.update_display_name(auth_info.user_id, body.display_name)
    return {"ok": True, "display_name": body.display_name}


# ── Avatar ───────────────────────────────────────────────────


@router.put("/avatar")
async def upload_avatar(request: Request, file: UploadFile = FastAPIFile(...)):
    """Upload a profile image. Resizing is done client-side."""
    from cptr.models.files import File as FileModel
    from cptr.utils.storage import get_storage

    token = request.cookies.get(COOKIE_NAME)
    auth_info = check_access(
        client_host=request.client.host if request.client else "127.0.0.1",
        jwt_token=token,
    )
    if auth_info is None:
        return JSONResponse({"error": "not authenticated"}, 401)

    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        return JSONResponse({"error": "file too large (max 5 MB)"}, 413)

    # Delete old avatar if exists
    user = await User.get_by_id(auth_info.user_id)
    if user and user.profile_image_url:
        old_id = user.profile_image_url.rsplit("/", 1)[-1]
        await get_storage().delete(old_id)
        await FileModel.delete_by_id(old_id)

    record = await FileModel.create(
        user_id=auth_info.user_id,
        filename=file.filename or "avatar",
        meta={"content_type": file.content_type or "image/png", "size": len(data)},
        created_at=now_ms(),
    )
    await get_storage().put(record.id, data)

    url = f"/api/files/{record.id}"
    await User.update_profile_image(auth_info.user_id, url)

    return {"ok": True, "profile_image_url": url}


@router.delete("/avatar")
async def delete_avatar(request: Request):
    """Delete profile image."""
    from cptr.models.files import File as FileModel
    from cptr.utils.storage import get_storage

    token = request.cookies.get(COOKIE_NAME)
    auth_info = check_access(
        client_host=request.client.host if request.client else "127.0.0.1",
        jwt_token=token,
    )
    if auth_info is None:
        return JSONResponse({"error": "not authenticated"}, 401)

    user = await User.get_by_id(auth_info.user_id)
    if user and user.profile_image_url:
        old_id = user.profile_image_url.rsplit("/", 1)[-1]
        await get_storage().delete(old_id)
        await FileModel.delete_by_id(old_id)

    await User.update_profile_image(auth_info.user_id, None)
    return {"ok": True}

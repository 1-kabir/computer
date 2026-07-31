"""Server configuration and authentication.

config.toml handles server-level config (host, port, auth mode, JWT secret).
SQLite handles user data (users, auths, user_states)."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from enum import Enum

import bcrypt
import jwt  # PyJWT

from cptr.env import DATA_DIR, CONFIG_FILE

SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days (seconds, for JWT exp)


def now_ms() -> int:
    """Current time as epoch milliseconds. Matches JS Date.now()."""
    return int(time.time() * 1000)


class AuthMode(str, Enum):
    PASSWORD = "password"  # Default: single user with password
    PAM = "pam"  # Linux system users
    TRUSTED_HEADER = "trusted_header"  # Reverse proxy / platform gateway


@dataclass
class AuthResult:
    user_id: str | None = None
    username: str | None = None
    role: str = "user"  # "admin" | "user" | "pending"
    exp: float = 0  # JWT expiry (epoch seconds)


# ── Config (TOML) ───────────────────────────────────────────

_config_cache: dict | None = None


def load_config() -> dict:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if CONFIG_FILE.exists():
        try:
            # Python 3.11+
            import tomllib

            with open(CONFIG_FILE, "rb") as f:
                _config_cache = tomllib.load(f)
        except ImportError:
            try:
                # Python 3.9-3.10 backport
                import tomli

                with open(CONFIG_FILE, "rb") as f:
                    _config_cache = tomli.load(f)
            except ImportError:
                # Minimal fallback for our simple config format
                _config_cache = _parse_simple_toml(CONFIG_FILE.read_text())
        except Exception:
            _config_cache = {}
    else:
        _config_cache = {}
    return _config_cache


def _parse_simple_toml(text: str) -> dict:
    """Minimal TOML parser for our config format: [section] + key = \"value\"."""
    result: dict = {}
    current_section = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1]
            result[current_section] = {}
        elif "=" in line and current_section:
            key, _, value = line.partition("=")
            key = key.strip()
            # Handle quoted keys (e.g. "auth.signup_enabled")
            if key.startswith('"') and key.endswith('"'):
                key = key[1:-1]
            elif key.startswith("'") and key.endswith("'"):
                key = key[1:-1]
            value = value.strip()
            # Strip quotes
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
                # Unescape
                value = value.replace('\\"', '"').replace("\\\\", "\\")
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            elif value == "true":
                value = True
            elif value == "false":
                value = False
            elif value.startswith("[") and value.endswith("]"):
                # Simple list: ["a", "b"]
                import json

                try:
                    value = json.loads(value)
                except Exception:
                    pass
            result[current_section][key] = value
    return result


def save_config(config: dict):
    global _config_cache
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    for section, values in config.items():
        if isinstance(values, dict):
            lines.append(f"[{section}]")
            for k, v in values.items():
                # Quote keys that contain dots (e.g. "auth.signup_enabled")
                key_str = f'"{k}"' if "." in k else k
                if isinstance(v, str):
                    # Escape backslashes and quotes in string values
                    escaped = v.replace("\\", "\\\\").replace('"', '\\"')
                    lines.append(f'{key_str} = "{escaped}"')
                elif isinstance(v, bool):
                    lines.append(f"{key_str} = {'true' if v else 'false'}")
                elif isinstance(v, (int, float)):
                    lines.append(f"{key_str} = {v}")
                elif isinstance(v, list):
                    items = ", ".join(f'"{i}"' if isinstance(i, str) else str(i) for i in v)
                    lines.append(f"{key_str} = [{items}]")
                else:
                    lines.append(f"{key_str} = {v}")
            lines.append("")
    CONFIG_FILE.write_text("\n".join(lines))
    _config_cache = config


def invalidate_config_cache():
    global _config_cache
    _config_cache = None


# ── App Config ↔ TOML sync ──────────────────────────────────


def sync_config_to_toml(app_config: dict) -> None:
    """Sync app config (DB key-value pairs) into the [app_config] section of config.toml.

    Preserves existing server-level sections ([server], [auth], etc.).
    Complex values (lists, dicts) are JSON-encoded as strings.
    Called after every Config.upsert() so the file mirrors the DB.
    """
    import json as _json

    config = load_config()
    invalidate_config_cache()  # Force re-read next time

    # Build the app_config section
    toml_section: dict = {}
    for key, value in app_config.items():
        if value is None:
            continue
        if isinstance(value, (list, dict)):
            # Store complex types as JSON strings
            toml_section[key] = _json.dumps(value, ensure_ascii=False)
        else:
            toml_section[key] = value

    config["app_config"] = toml_section
    save_config(config)


def load_app_config_from_toml() -> dict:
    """Read the [app_config] section from config.toml.

    Returns {key: value} dict with JSON strings decoded back to Python objects.
    Called on startup to seed the DB config table from the file.
    """
    import json as _json

    invalidate_config_cache()  # Force fresh read from disk
    config = load_config()
    raw = config.get("app_config", {})
    if not isinstance(raw, dict):
        return {}

    result: dict = {}
    for key, value in raw.items():
        if isinstance(value, str):
            # Try to decode JSON strings back to complex types
            try:
                decoded = _json.loads(value)
                if isinstance(decoded, (list, dict)):
                    result[key] = decoded
                else:
                    result[key] = value
            except (ValueError, _json.JSONDecodeError):
                result[key] = value
        else:
            result[key] = value
    return result


# ── Password ─────────────────────────────────────────────────


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(12)).decode()


def verify_password(pw: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), h.encode())
    except Exception:
        return False


async def has_any_user() -> bool:
    """Check if any user exists in the DB (i.e., setup has been completed)."""
    from cptr.utils.db import get_db
    from cptr.models import Auth
    from sqlalchemy import select

    async with await get_db() as db:
        result = await db.execute(select(Auth).limit(1))
        return result.scalar_one_or_none() is not None


# ── PAM ──────────────────────────────────────────────────────


def pam_authenticate(username: str, password: str) -> bool:
    """Authenticate against Linux PAM. Returns True if valid."""
    try:
        import pam

        p = pam.pam()
        return p.authenticate(username, password, service="login")
    except ImportError:
        return False
    except Exception:
        return False


def get_user_uid_gid(username: str) -> tuple[int, int] | None:
    """Get UID and GID for a Linux user. Returns (uid, gid) or None."""
    try:
        import pwd

        pw = pwd.getpwnam(username)
        return (pw.pw_uid, pw.pw_gid)
    except (KeyError, ImportError):
        return None


# ── Users ────────────────────────────────────────────────────


async def get_or_create_user(username: str) -> str:
    """Get or create a user + auth row, return user.id.
    Looks up by auths.username. Auto-creates on first login.
    """
    from cptr.utils.db import get_db
    from cptr.models import User, Auth
    from sqlalchemy import select, update

    async with await get_db() as db:
        result = await db.execute(select(Auth).where(Auth.username == username))
        auth = result.scalar_one_or_none()
        if not auth:
            user = User(created_at=now_ms())
            db.add(user)
            await db.flush()  # generate user.id
            db.add(Auth(user_id=user.id, username=username))
            await db.commit()
            return user.id
        else:
            # Update last_seen directly to avoid lazy load on auth.user
            await db.execute(
                update(User).where(User.id == auth.user_id).values(last_seen_at=now_ms())
            )
            await db.commit()
            return auth.user_id


# ── JWT Tokens (stateless auth) ──────────────────────────────


def _get_jwt_secret() -> str:
    """Get or auto-generate JWT secret. Stored in config.toml."""
    config = load_config()
    secret = config.get("server", {}).get("secret")
    if not secret:
        secret = secrets.token_hex(32)
        config.setdefault("server", {})["secret"] = secret
        save_config(config)
    return secret


def create_token(user_id: str, username: str, role: str = "user") -> str:
    """Create a signed JWT per RFC 7519. Includes standard claims:
    - sub: subject (user_id)
    - exp: expiration time
    - jti: unique token identifier
    """
    import uuid

    return jwt.encode(
        {
            "sub": user_id,
            "username": username,
            "role": role,
            "exp": time.time() + SESSION_MAX_AGE,
            "jti": str(uuid.uuid4()),
        },
        _get_jwt_secret(),
        algorithm="HS256",
    )


def verify_token(token: str) -> AuthResult | None:
    """Verify a JWT. No DB read."""
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=["HS256"])
        return AuthResult(
            user_id=payload.get("sub"),
            username=payload.get("username"),
            role=payload.get("role", "user"),
            exp=payload.get("exp", 0),
        )
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ── Rate Limiting (in-memory, fine) ──────────────────────────

_attempts: dict[str, list[float]] = {}


def check_rate_limit(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _attempts.get(ip, []) if now - t < 3600]
    _attempts[ip] = attempts
    return sum(1 for t in attempts if now - t < 60) < 5


def record_attempt(ip: str):
    _attempts.setdefault(ip, []).append(time.time())


# ── Access Check ─────────────────────────────────────────────


def get_auth_mode() -> AuthMode:
    """Returns the current auth mode. Default is PASSWORD."""
    config = load_config()
    mode = config.get("auth", {}).get("mode", "")
    if mode == "pam":
        return AuthMode.PAM
    if mode == "trusted_header":
        return AuthMode.TRUSTED_HEADER
    return AuthMode.PASSWORD  # Default


def check_access(
    client_host: str,
    jwt_token: str | None,
    remote_user_header: str | None = None,
) -> AuthResult | None:
    """Every request must be authenticated. No localhost bypass.
    Returns AuthResult if authenticated, None if not."""
    mode = get_auth_mode()

    # Password and PAM: verify JWT cookie
    if mode in (AuthMode.PASSWORD, AuthMode.PAM):
        if jwt_token:
            return verify_token(jwt_token)
        return None

    # Trusted Header Authentication
    if mode == AuthMode.TRUSTED_HEADER:
        config = load_config()
        auth_cfg = config.get("auth", {})
        trusted_sources = auth_cfg.get("trusted_sources", [])
        if trusted_sources and client_host not in trusted_sources:
            return None
        if remote_user_header:
            return AuthResult(username=remote_user_header)
        if jwt_token:
            return verify_token(jwt_token)
        return None

    return None

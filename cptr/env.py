"""Centralized environment configuration for cptr.

All environment variable reads go here. Import from this module
instead of reading os.environ directly.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env_bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).lower() in ("true", "1", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


# ── Data directory ──────────────────────────────────────────
# Where cptr stores its database, config, and user data.
# Default: ~/.cptr
DATA_DIR = Path(os.environ.get("CPTR_DATA_DIR", str(Path.home() / ".cptr")))
CONFIG_FILE = DATA_DIR / "config.toml"
DB_FILE = DATA_DIR / "app.db"

# ── Logging ─────────────────────────────────────────────────
LOG_LEVEL = os.environ.get("CPTR_LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.environ.get("CPTR_LOG_FORMAT", "text").lower()

AUDIT_LOG_LEVEL = os.environ.get("CPTR_AUDIT_LOG_LEVEL", "NONE").upper()
AUDIT_LOG_PATH = Path(os.environ.get("CPTR_AUDIT_LOG_PATH", str(DATA_DIR / "logs" / "audit.jsonl")))
AUDIT_LOG_ROTATION = os.environ.get("CPTR_AUDIT_LOG_ROTATION", "10 MB")
AUDIT_MAX_BODY_SIZE = _env_int("CPTR_AUDIT_MAX_BODY_SIZE", 2048)
AUDIT_EXCLUDED_PATHS = [
    path.strip()
    for path in os.environ.get("CPTR_AUDIT_EXCLUDED_PATHS", "/api/chats,/v1/chat").split(",")
    if path.strip()
]

LOG_UPSTREAM_REQUESTS = _env_bool("CPTR_LOG_UPSTREAM_REQUESTS", "false")
UPSTREAM_REQUEST_LOG_PATH = Path(
    os.environ.get(
        "CPTR_UPSTREAM_REQUEST_LOG_PATH",
        str(DATA_DIR / "logs" / "upstream-requests.jsonl"),
    )
)
UPSTREAM_REQUEST_LOG_ROTATION = os.environ.get("CPTR_UPSTREAM_REQUEST_LOG_ROTATION", "50 MB")

# ── Startup token ───────────────────────────────────────────
# One-time token for first-time setup. Set by CLI, consumed by app.
STARTUP_TOKEN: str | None = os.environ.pop("CPTR_STARTUP_TOKEN", None)

# ── Chat settings ───────────────────────────────────────────
CHAT_MAX_ITERATIONS = int(os.environ.get("CHAT_MAX_ITERATIONS", "2048"))
ENABLE_CHAT_RECONCILE_ON_STARTUP: bool = os.environ.get(
    "ENABLE_CHAT_RECONCILE_ON_STARTUP", "true"
).lower() in ("true", "1", "yes")
CHAT_TOOL_MAX_CHARS = int(os.environ.get("CHAT_TOOL_MAX_CHARS", "50000"))
CHAT_TOOL_COMMAND_MAX_CHARS = int(os.environ.get("CHAT_TOOL_COMMAND_MAX_CHARS", "8000"))
CHAT_COMPACT_TOKEN_THRESHOLD = int(os.environ.get("CHAT_COMPACT_TOKEN_THRESHOLD", "80000"))
AGENT_SEED_TRANSCRIPT_MAX_CHARS = max(
    0, _env_int("CHAT_AGENT_SEED_TRANSCRIPT_MAX_CHARS", 12000)
)
# Claude SDK stdout JSON buffer; chat/tool output caps apply later after parsing.
CLAUDE_CODE_MAX_BUFFER_SIZE = _env_int("CPTR_CLAUDE_CODE_MAX_BUFFER_SIZE", 128 * 1024 * 1024)

# ── Workspace storage ───────────────────────────────────────
WORKSPACE_AUTO_GITIGNORE_DOT_CPTR_ENV = os.environ.get("CPTR_AUTO_GITIGNORE_DOT_CPTR")
WORKSPACE_AUTO_GITIGNORE_DOT_CPTR = _env_bool("CPTR_AUTO_GITIGNORE_DOT_CPTR", "true")

# ── Execute timeout ─────────────────────────────────────────
# Default wait (seconds) for run_command / check_task when the caller
# doesn't pass an explicit wait value.  None = return immediately.
EXECUTE_TIMEOUT: float | None = None
_execute_timeout = os.environ.get("CPTR_EXECUTE_TIMEOUT")
if _execute_timeout is not None:
    EXECUTE_TIMEOUT = float(_execute_timeout)

# ── AI stream settings ──────────────────────────────────────
STREAM_CONNECT_TIMEOUT_SECONDS = float(os.environ.get("CPTR_STREAM_CONNECT_TIMEOUT", "30"))
STREAM_READ_TIMEOUT_SECONDS = float(os.environ.get("CPTR_STREAM_READ_TIMEOUT", "300"))
STREAM_WRITE_TIMEOUT_SECONDS = float(os.environ.get("CPTR_STREAM_WRITE_TIMEOUT", "600"))

# ── Automation scheduler ────────────────────────────────────
AUTOMATION_POLL_INTERVAL = int(os.environ.get("AUTOMATION_POLL_INTERVAL", "10"))
TIMER_POLL_INTERVAL = int(os.environ.get("TIMER_POLL_INTERVAL", "1"))

# ── CORS ────────────────────────────────────────────────────
# Socket.IO CORS allowed origins.
# Default → "*" (allow all origins)
# Comma-separated list → allow specific origins only
#   e.g. "https://example.com,https://app.example.com"
_cors_raw = os.environ.get("CPTR_CORS_ALLOWED_ORIGINS", "*")
if _cors_raw.strip() == "*":
    CORS_ALLOWED_ORIGINS = "*"
else:
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors_raw.split(",") if o.strip()] or "*"

"""Cross-platform PTY terminal session manager.

Uses stdlib pty/os.fork on Unix (zero dependencies).
Uses pywinpty on Windows (optional dependency, installed only on Windows).
"""

from __future__ import annotations

import asyncio
import os
import platform
import shlex
import struct
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from cptr.utils.identity import ExecutionIdentity, env_for, expand_user_path, preexec_for

SCROLLBACK_SIZE = 64 * 1024  # 64 KB
OUTPUT_CAP = 256 * 1024  # in-memory output buffer cap for reattach tailing
IS_WINDOWS = platform.system() == "Windows"


class TerminalUnavailable(RuntimeError):
    """Raised when the platform terminal backend cannot be loaded."""


@dataclass
class TerminalSession:
    """Platform-agnostic terminal session with a session-owned output drain.

    Output is always drained into an in-memory buffer by a per-session task,
    independent of any connected WebSocket client. Clients (terminal_ws)
    subscribe by tailing the buffer with a byte offset, so shell sessions and
    the TUIs running inside them keep running (and producing output) while no
    browser is attached. Buffer keeps the last OUTPUT_CAP bytes.
    """

    session_id: str
    cwd: str
    user_id: str | None = None
    identity: ExecutionIdentity | None = field(default=None, repr=False)
    rows: int = 24
    cols: int = 80
    _scrollback: bytearray = field(default_factory=bytearray, repr=False)

    # Persistent output buffer + byte counter for detached draining.
    _output: bytearray = field(default_factory=bytearray, repr=False)
    _total_bytes: int = 0
    _output_condition: asyncio.Condition = field(default_factory=asyncio.Condition, repr=False)
    _drain_task: Optional[asyncio.Task] = field(default=None, repr=False)
    _done: bool = False

    # Platform handles
    _fd: int = -1  # Unix: master pty fd
    _pid: int = -1  # Unix: child pid
    _process: object = None  # Windows: winpty PtyProcess

    def write(self, data: bytes) -> None:
        if IS_WINDOWS:
            data = data.decode("utf-8", errors="replace")
            self._process.write(data)  # type: ignore
        else:
            os.write(self._fd, data)

    def read(self, size: int = 4096) -> bytes:
        try:
            if IS_WINDOWS:
                data = self._process.read(size)  # type: ignore
                if isinstance(data, str):
                    data = data.encode("utf-8", errors="replace")
            else:
                data = os.read(self._fd, size)
        except (BlockingIOError, OSError):
            return b""
        if data:
            self._scrollback.extend(data)
            # Trim at 2x cap to amortize the cost. This halves the number of
            # 64 KB memcpy operations during high-throughput output.
            if len(self._scrollback) > SCROLLBACK_SIZE * 2:
                self._scrollback = self._scrollback[-SCROLLBACK_SIZE:]
        return data

    def get_scrollback(self) -> bytes:
        return bytes(self._scrollback)

    # ── Detached output draining ─────────────────────────────

    def _append_output(self, data: bytes) -> None:
        if not data:
            return
        self._output.extend(data)
        self._total_bytes += len(data)
        if len(self._output) > OUTPUT_CAP * 2:
            self._output = self._output[-OUTPUT_CAP:]

    def bytes_since(self, offset: int) -> tuple[bytes, int]:
        """Return (output bytes after offset, new offset). Empty when caught up."""
        total = self._total_bytes
        if offset >= total:
            return b"", total
        start_in_buf = total - len(self._output)
        start = max(0, offset - start_in_buf)
        return bytes(self._output[start:]), total

    async def _notify_output(self) -> None:
        async with self._output_condition:
            self._output_condition.notify_all()

    def start_drain(self) -> None:
        """Start the session-owned PTY drain task (idempotent).

        Must be called from a running event loop (router endpoints are async).
        The task keeps reading the PTY regardless of WebSocket attachments so
        child processes never block on a full pty buffer while detached.
        """
        if self._drain_task is not None and not self._drain_task.done():
            return
        if IS_WINDOWS:
            self._drain_task = asyncio.create_task(self._drain_windows())
        else:
            self._drain_task = asyncio.create_task(self._drain_unix())

    async def _drain_unix(self) -> None:
        loop = asyncio.get_running_loop()
        readable = asyncio.Event()
        loop.add_reader(self._fd, readable.set)
        try:
            while True:
                await readable.wait()
                readable.clear()
                data = self.read(65536)
                if data:
                    self._append_output(data)
                    await self._notify_output()
                elif not self.is_alive():
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            import logging

            logging.getLogger("cptr.terminal").error(f"drain task error for {self.session_id}: {e}")
        finally:
            try:
                loop.remove_reader(self._fd)
            except Exception:
                pass
            self._done = True
            await self._notify_output()

    async def _drain_windows(self) -> None:
        # pywinpty read() blocks; run it outside the event loop.
        while True:
            if not self.is_alive():
                break
            try:
                data = await asyncio.to_thread(self._process.read, 16384)  # type: ignore
                if isinstance(data, str):
                    data = data.encode("utf-8", errors="replace")
            except (EOFError, OSError, IOError):
                break
            if data:
                self._append_output(data)
                await self._notify_output()
        self._done = True
        await self._notify_output()

    def resize(self, rows: int, cols: int) -> None:
        self.rows = rows
        self.cols = cols
        if IS_WINDOWS:
            try:
                self._process.set_size(cols, rows)  # type: ignore
            except Exception:
                pass
        else:
            import fcntl
            import termios

            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)

    def is_alive(self) -> bool:
        if IS_WINDOWS:
            return self._process.isalive()  # type: ignore
        try:
            pid, _ = os.waitpid(self._pid, os.WNOHANG)
            return pid == 0
        except ChildProcessError:
            return False

    def close(self) -> None:
        if self._drain_task is not None and not self._drain_task.done():
            self._drain_task.cancel()
        if IS_WINDOWS:
            try:
                self._process.close()  # type: ignore
            except Exception:
                pass
        else:
            import signal

            try:
                os.kill(self._pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass


def _make_readable(path: str) -> None:
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass


def _create_unix(
    session_id: str,
    identity: ExecutionIdentity,
    shell: str,
    work_dir: str,
    env: dict,
    rows: int,
    cols: int,
) -> TerminalSession:
    """Create a terminal session on Unix using stdlib pty."""
    import fcntl
    import pty
    import tempfile
    import termios

    master_fd, slave_fd = pty.openpty()

    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

    shell_name = os.path.basename(shell)
    shell_args = [shell]
    home = identity.home
    preexec = preexec_for(identity) if identity.is_pam else None

    # Create a temp init script that sources the user's real config then cd's.
    # This guarantees cwd is set AFTER all init files run. No timing hacks.
    if shell_name in ("zsh",):
        # For zsh: use ZDOTDIR to inject our .zshrc
        zdotdir = tempfile.mkdtemp(prefix="cptr_zsh_")
        try:
            os.chmod(zdotdir, 0o755)
        except OSError:
            pass
        init_content = (
            f'ZDOTDIR="{home}"\n'
            f'[[ -f "{home}/.zshenv" ]] && source "{home}/.zshenv"\n'
            f'[[ -f "{home}/.zprofile" ]] && source "{home}/.zprofile"\n'
            f'[[ -f "{home}/.zshrc" ]] && source "{home}/.zshrc"\n'
            f"cd {shlex.quote(work_dir)}\n"
        )
        with open(os.path.join(zdotdir, ".zshrc"), "w") as f:
            f.write(init_content)
        _make_readable(os.path.join(zdotdir, ".zshrc"))
        # Also create .zshenv to prevent system zshenv from interfering
        with open(os.path.join(zdotdir, ".zshenv"), "w") as f:
            f.write("")
        _make_readable(os.path.join(zdotdir, ".zshenv"))
        env["ZDOTDIR"] = zdotdir
        shell_args = [shell, "-i"]
    elif shell_name in ("bash",):
        # For bash: use --rcfile
        tmpf = tempfile.NamedTemporaryFile(
            mode="w", prefix="cptr_bash_", suffix=".sh", delete=False
        )
        tmpf.write(
            f'[[ -f "{home}/.bashrc" ]] && source "{home}/.bashrc"\ncd {shlex.quote(work_dir)}\n'
        )
        tmpf.close()
        _make_readable(tmpf.name)
        shell_args = [shell, "--rcfile", tmpf.name, "-i"]
    else:
        # Generic POSIX: use ENV variable
        tmpf = tempfile.NamedTemporaryFile(mode="w", prefix="cptr_sh_", suffix=".sh", delete=False)
        tmpf.write(f'[ -f "$HOME/.profile" ] && . "$HOME/.profile"\ncd {shlex.quote(work_dir)}\n')
        tmpf.close()
        _make_readable(tmpf.name)
        env["ENV"] = tmpf.name
        shell_args = [shell, "-i"]

    child_pid = os.fork()
    if child_pid == 0:
        # Child process
        os.close(master_fd)
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)

        if preexec:
            preexec()
        os.chdir(work_dir)
        os.execvpe(shell, shell_args, env)
    else:
        # Parent process
        os.close(slave_fd)
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        return TerminalSession(
            session_id=session_id,
            cwd=work_dir,
            user_id=identity.app_user_id,
            identity=identity,
            rows=rows,
            cols=cols,
            _fd=master_fd,
            _pid=child_pid,
        )


def _create_windows(
    session_id: str,
    identity: ExecutionIdentity,
    shell: str,
    work_dir: str,
    env: dict,
    rows: int,
    cols: int,
) -> TerminalSession:
    """Create a terminal session on Windows using pywinpty."""
    try:
        from winpty import PtyProcess  # type: ignore
    except (ImportError, OSError) as exc:
        raise TerminalUnavailable(
            "Windows terminal support requires pywinpty and the Microsoft Visual C++ "
            "Redistributable. Install the latest supported Visual C++ Redistributable "
            "from Microsoft, then restart cptr."
        ) from exc

    proc = PtyProcess.spawn(
        [shell],
        cwd=work_dir,
        env=env,
        dimensions=(rows, cols),
    )

    return TerminalSession(
        session_id=session_id,
        cwd=work_dir,
        user_id=identity.app_user_id,
        identity=identity,
        rows=rows,
        cols=cols,
        _process=proc,
    )


class SessionManager:
    """Registry of active terminal sessions."""

    def __init__(self) -> None:
        self._sessions: Dict[str, TerminalSession] = {}

    def create(
        self,
        identity: ExecutionIdentity,
        rows: int = 24,
        cols: int = 80,
        cwd: Optional[str] = None,
    ) -> TerminalSession:
        session_id = uuid.uuid4().hex[:12]
        work_dir = (
            str(expand_user_path(cwd, identity).resolve())
            if identity.is_pam and cwd
            else cwd or identity.home
        )

        if IS_WINDOWS:
            shell = os.environ.get("COMSPEC", "cmd.exe")
        else:
            shell = identity.shell if identity.is_pam else os.environ.get("SHELL", "/bin/sh")

        if identity.is_pam:
            env = env_for(
                identity,
                work_dir,
                {
                    "TERM": "xterm-256color",
                    "COLORTERM": "truecolor",
                    "COLUMNS": str(cols),
                    "LINES": str(rows),
                },
            )
        else:
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLORTERM"] = "truecolor"
            env["COLUMNS"] = str(cols)
            env["LINES"] = str(rows)
            env["PWD"] = work_dir

        if IS_WINDOWS:
            session = _create_windows(session_id, identity, shell, work_dir, env, rows, cols)
        else:
            session = _create_unix(session_id, identity, shell, work_dir, env, rows, cols)

        # Drain output session-side so detached clients (closed tab, sleeping
        # laptop) never stall the child process on a full pty buffer.
        session.start_drain()
        self._sessions[session_id] = session
        return session

    def get(self, request, session_id: str, auth=None) -> Optional[TerminalSession]:
        session = self._sessions.get(session_id)
        if request is not None:
            auth = getattr(getattr(request, "state", None), "auth", None)
        user_id = getattr(auth, "user_id", None)
        if session and user_id is not None and session.user_id != user_id:
            return None
        if session and not session.is_alive():
            session.close()
            del self._sessions[session_id]
            return None
        return session

    def list_sessions(self, request, auth=None) -> List[dict]:
        result = []
        dead = []
        if request is not None:
            auth = getattr(getattr(request, "state", None), "auth", None)
        user_id = getattr(auth, "user_id", None)
        for sid, session in self._sessions.items():
            if user_id is not None and session.user_id != user_id:
                continue
            if session.is_alive():
                result.append({"session_id": sid, "cwd": session.cwd})
            else:
                dead.append(sid)
        for sid in dead:
            self._sessions[sid].close()
            del self._sessions[sid]
        return result

    def close(self, request, session_id: str, auth=None) -> bool:
        session = self._sessions.get(session_id)
        if request is not None:
            auth = getattr(getattr(request, "state", None), "auth", None)
        user_id = getattr(auth, "user_id", None)
        if user_id is not None and session and session.user_id != user_id:
            return False
        session = self._sessions.pop(session_id, None)
        if session:
            session.close()
            return True
        return False

    def close_all(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()


manager = SessionManager()

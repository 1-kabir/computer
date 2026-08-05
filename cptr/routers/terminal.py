"""Endpoints for spawning, managing, and interacting with terminal PTY sessions."""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from cptr.utils.config import check_access
from cptr.utils.identity import IdentityUnavailable, identity_for_request
from cptr.utils.terminal import TerminalUnavailable, manager, IS_WINDOWS
from cptr.utils.tools import (
    command_session_bytes_since,
    drain_command_session_input,
    get_command_session,
    list_command_sessions,
    resize_command_session,
    send_command_session_input,
    stop_command_session,
)

logger = logging.getLogger("cptr.terminal")

router = APIRouter(prefix="/api/terminal", tags=["terminal"])


class CreateSessionRequest(BaseModel):
    rows: int = 24
    cols: int = 80
    cwd: Optional[str] = None


class SessionInfo(BaseModel):
    session_id: str
    cwd: str


class CommandSessionInfo(BaseModel):
    command_session_id: str
    task_id: str
    workspace: str
    chat_id: str | None = None
    message_id: str | None = None
    call_id: str | None = None
    command: str
    created_at: float
    status: str
    done: bool
    exit_code: int | None = None
    total_bytes: int
    output: str = ""


@router.post("", response_model=SessionInfo)
async def create_session(request: Request, req: CreateSessionRequest):
    """Create a new terminal session."""
    logger.info(f"Creating terminal session: cwd={req.cwd}, rows={req.rows}, cols={req.cols}")
    try:
        identity = await identity_for_request(request)
        session = manager.create(identity=identity, rows=req.rows, cols=req.cols, cwd=req.cwd)
    except TerminalUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except IdentityUnavailable as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    logger.info(
        f"Created session {session.session_id} at {session.cwd}, fd={session._fd}, alive={session.is_alive()}"
    )
    return SessionInfo(session_id=session.session_id, cwd=session.cwd)


@router.get("", response_model=List[SessionInfo])
async def list_sessions(request: Request):
    """List active terminal sessions."""
    sessions = manager.list_sessions(request)
    logger.debug(f"Listing sessions: {len(sessions)} active")
    return [SessionInfo(**s) for s in sessions]


@router.get("/sessions", response_model=List[CommandSessionInfo])
async def list_command_session_endpoint(
    request: Request, workspace: str | None = None, chat_id: str | None = None
):
    """List live command sessions created by run_command."""
    return [CommandSessionInfo(**s) for s in list_command_sessions(request, workspace, chat_id)]


@router.delete("/{session_id}")
async def delete_session(request: Request, session_id: str):
    """Kill a terminal session."""
    logger.info(f"Deleting session {session_id}")
    if not manager.close(request, session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "closed"}


@router.websocket("/sessions/{command_session_id}/ws")
async def command_session_ws(websocket: WebSocket, command_session_id: str):
    """WebSocket endpoint for command-session I/O.

    Uses the same compact binary client protocol as normal terminals:
        0x00 + raw input bytes
        0x02 + uint16 cols + uint16 rows
        0x03 stop
    Server → Client: raw command output bytes.
    """
    client_host = websocket.client.host if websocket.client else "127.0.0.1"
    token = websocket.cookies.get("cptr_session") or websocket.query_params.get("token")
    auth = check_access(client_host=client_host, jwt_token=token)
    if auth is None:
        await websocket.close(code=4001, reason="unauthorized")
        return
    websocket.state.auth = auth

    session = get_command_session(websocket, command_session_id)
    if not session:
        await websocket.close(code=4004, reason="Command session not found")
        return

    await websocket.accept()

    MSG_INPUT = 0
    MSG_RESIZE = 2
    MSG_STOP = 3
    MSG_FORCE_STOP = 4

    try:
        initial_offset = max(0, int(websocket.query_params.get("offset", "0")))
    except ValueError:
        initial_offset = 0

    async def stream_output():
        offset = initial_offset
        try:
            while True:
                session = get_command_session(websocket, command_session_id)
                if not session:
                    break
                raw, offset = command_session_bytes_since(session, offset)
                if raw:
                    await websocket.send_bytes(raw)
                if session.get("done"):
                    break
                condition = session["condition"]
                async with condition:
                    await condition.wait_for(
                        lambda: (
                            session.get("done") or int(session.get("total_bytes") or 0) != offset
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("command session stream error for %s: %s", command_session_id, e)

    stream_task = asyncio.create_task(stream_output())

    try:
        while True:
            raw = await websocket.receive_bytes()
            if len(raw) < 1:
                continue

            msg_type = raw[0]
            payload = raw[1:]

            if msg_type == MSG_INPUT:
                error = send_command_session_input(websocket, command_session_id, payload)
                if error:
                    logger.debug(
                        "command session input ignored for %s: %s", command_session_id, error
                    )
                await drain_command_session_input(websocket, command_session_id)
            elif msg_type == MSG_RESIZE:
                try:
                    if len(payload) == 4:
                        cols = int.from_bytes(payload[0:2], "big")
                        rows = int.from_bytes(payload[2:4], "big")
                    else:
                        import json as _json

                        resize_data = _json.loads(payload)
                        cols = resize_data.get("cols", 80)
                        rows = resize_data.get("rows", 24)
                    resize_command_session(websocket, command_session_id, rows, cols)
                except (ValueError, KeyError) as e:
                    logger.warning(
                        "Invalid command-session resize payload for %s: %s", command_session_id, e
                    )
            elif msg_type == MSG_STOP:
                stop_command_session(websocket, command_session_id)
            elif msg_type == MSG_FORCE_STOP:
                stop_command_session(websocket, command_session_id, force=True)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for command session %s", command_session_id)
    except Exception as e:
        logger.error("WebSocket error for command session %s: %s", command_session_id, e)
    finally:
        stream_task.cancel()
        try:
            await stream_task
        except asyncio.CancelledError:
            pass


@router.websocket("/{session_id}/ws")
async def terminal_ws(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for terminal I/O.

    Binary protocol (compact, ttyd-inspired):
    Client → Server:  byte[0] = type, byte[1:] = payload
        0x00 + raw input bytes (microtask-batched by frontend)
        0x02 + uint16 cols + uint16 rows (big-endian, 4 bytes)
              OR JSON {"cols": N, "rows": N} (legacy fallback)
    Server → Client:  raw PTY output bytes (no prefix)
    """
    # Auth check for WebSocket (middleware doesn't cover WebSocket upgrades)
    client_host = websocket.client.host if websocket.client else "127.0.0.1"
    token = websocket.cookies.get("cptr_session") or websocket.query_params.get("token")
    auth = check_access(client_host=client_host, jwt_token=token)
    if auth is None:
        await websocket.close(code=4001, reason="unauthorized")
        return
    websocket.state.auth = auth

    session = manager.get(websocket, session_id)
    if not session:
        logger.warning(f"WebSocket: session {session_id} not found")
        await websocket.close(code=4004, reason="Session not found")
        return

    await websocket.accept()
    logger.info(
        f"WebSocket connected for session {session_id}, fd={session._fd}, alive={session.is_alive()}"
    )

    # Replay scrollback buffer so reconnecting clients see history
    scrollback = session.get_scrollback()
    if scrollback:
        logger.info(f"Replaying {len(scrollback)} bytes of scrollback")
        await websocket.send_bytes(scrollback)

    # Message type constants (must match frontend)
    MSG_INPUT = 0
    MSG_RESIZE = 2

    async def read_pty():
        """Read from PTY and send to WebSocket.

        Event-driven via add_reader. Wakes instantly when the PTY has
        output (0 ms latency vs 5 ms poll-sleep).  Batch-reads all
        available data into a single WebSocket frame to minimise framing
        overhead.
        """
        try:
            if IS_WINDOWS:
                # Windows ProactorEventLoop doesn't support add_reader;
                # pywinpty read() blocks, so run it outside the event loop.
                while True:
                    if not session.is_alive():
                        logger.info(f"Session {session_id} died, stopping read")
                        break
                    try:
                        data = await asyncio.to_thread(session.read, 16384)
                        if data:
                            await websocket.send_bytes(data)
                    except EOFError:
                        logger.info(f"Session {session_id} reached EOF")
                        break
                    except (OSError, IOError) as e:
                        logger.error(f"PTY read error for {session_id}: {e}")
                        break
            else:
                # Unix: event-driven, zero-latency
                loop = asyncio.get_running_loop()
                readable = asyncio.Event()
                loop.add_reader(session._fd, readable.set)
                try:
                    while True:
                        await readable.wait()
                        readable.clear()

                        # Batch-read all available data (up to 64 KB)
                        chunks: list[bytes] = []
                        total = 0
                        while total < 65536:
                            data = session.read(16384)
                            if not data:
                                break
                            chunks.append(data)
                            total += len(data)

                        if chunks:
                            await websocket.send_bytes(b"".join(chunks))
                        elif not session.is_alive():
                            logger.info(f"Session {session_id} died, stopping read")
                            break
                finally:
                    try:
                        loop.remove_reader(session._fd)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"read_pty unexpected error for {session_id}: {e}")

    read_task = asyncio.create_task(read_pty())

    try:
        while True:
            raw = await websocket.receive_bytes()
            if len(raw) < 1:
                continue

            msg_type = raw[0]
            payload = raw[1:]

            if msg_type == MSG_INPUT:
                session.write(payload)
            elif msg_type == MSG_RESIZE:
                try:
                    if len(payload) == 4:
                        # Compact binary: uint16 cols + uint16 rows (big-endian)
                        cols = int.from_bytes(payload[0:2], "big")
                        rows = int.from_bytes(payload[2:4], "big")
                    else:
                        # Legacy JSON fallback
                        import json as _json

                        resize_data = _json.loads(payload)
                        cols = resize_data.get("cols", 80)
                        rows = resize_data.get("rows", 24)
                    logger.debug(f"Resize {session_id}: {cols}x{rows}")
                    session.resize(rows, cols)
                except (ValueError, KeyError) as e:
                    logger.warning(f"Invalid resize payload for {session_id}: {e}")
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for {session_id}")
    except Exception as e:
        logger.error(f"WebSocket error for {session_id}: {e}")
    finally:
        read_task.cancel()
        try:
            await read_task
        except asyncio.CancelledError:
            pass

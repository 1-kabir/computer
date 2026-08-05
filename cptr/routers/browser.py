"""Same-origin HTTP/WebSocket proxy backing Browser editor tabs."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from cptr.routers.auth import COOKIE_NAME
from cptr.routers.admin import require_admin
from cptr.models import Config
from cptr.utils.browser.proxy import (
    manager,
    rewrite_css,
    rewrite_html,
    rewrite_javascript,
    target_url,
)
from cptr.utils.browser.viewer import (
    CDPConnection,
    local_origin,
    manager as chrome_viewer_manager,
    resolve_cdp_endpoint,
)
from cptr.utils.config import AuthResult, check_access

router = APIRouter(prefix="/api/browser", tags=["browser"])
logger = logging.getLogger(__name__)

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
_SKIP_REQUEST_HEADERS = _HOP_BY_HOP | {
    "accept-encoding",
    "host",
    "cookie",
    "authorization",
    "content-length",
}
_SKIP_RESPONSE_HEADERS = _HOP_BY_HOP | {
    "content-encoding",
    "content-length",
    "content-security-policy",
    "set-cookie",
    "x-frame-options",
}


def _owner(auth: AuthResult) -> str:
    return auth.user_id or auth.username or "default"


def _auth(request: Request) -> AuthResult:
    auth = getattr(request.state, "auth", None)
    if auth is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return auth


def _headers(request: Request, upstream_url: str) -> dict[str, str]:
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _SKIP_REQUEST_HEADERS and not key.lower().startswith("x-cptr-")
    }
    parsed = urlsplit(upstream_url)
    headers["host"] = parsed.netloc
    # The proxy rewrites HTML and CSS after httpx has decoded it. Do not negotiate
    # encodings a downstream browser supports but this process may not decode.
    headers["accept-encoding"] = "gzip, deflate"
    referer = request.headers.get("referer")
    if referer and "/api/browser/frame/" in referer:
        headers["referer"] = upstream_url
    if request.headers.get("origin"):
        headers["origin"] = f"{parsed.scheme}://{parsed.netloc}"
    return headers


def _response_headers(upstream: httpx.Response) -> dict[str, str]:
    headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _SKIP_RESPONSE_HEADERS
    }
    headers["x-cptr-browser-url"] = str(upstream.url)
    return headers


def _session_payload(session) -> dict[str, object]:
    return {
        "session_id": session.session_id,
        "url": session.url,
        "title": session.title,
        "mode": session.mode,
        "status": session.status,
    }


async def _stream_response(upstream: httpx.Response) -> AsyncIterator[bytes]:
    try:
        async for chunk in upstream.aiter_bytes():
            yield chunk
    finally:
        await upstream.aclose()


async def proxy_browser_request(
    request: Request, session_id: str, url: str, owner: str
) -> Response:
    client = manager.client(session_id, owner)
    if client is None:
        raise HTTPException(status_code=404, detail="Browser tab expired")
    try:
        upstream_url = target_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    body = await request.body() if request.method not in {"GET", "HEAD"} else None
    upstream = None
    candidates = [upstream_url]
    parsed = urlsplit(upstream_url)
    if parsed.hostname == "127.0.0.1":
        port = f":{parsed.port}" if parsed.port else ""
        candidates.append(
            urlunsplit((parsed.scheme, f"[::1]{port}", parsed.path, parsed.query, ""))
        )
    for candidate in candidates:
        try:
            upstream = await client.send(
                client.build_request(
                    request.method, candidate, headers=_headers(request, candidate), content=body
                ),
                stream=True,
            )
            break
        except httpx.ConnectError:
            continue
    if upstream is None:
        raise HTTPException(status_code=502, detail=f"Cannot connect to {parsed.netloc}")
    content_type = upstream.headers.get("content-type", "")
    headers = _response_headers(upstream)

    if content_type.startswith("text/event-stream"):
        return StreamingResponse(
            _stream_response(upstream),
            status_code=upstream.status_code,
            headers=headers,
            media_type=content_type,
        )

    content = await upstream.aread()
    await upstream.aclose()
    if "text/html" in content_type or "application/xhtml" in content_type:
        final_url = str(upstream.url)
        if parsed.hostname == "127.0.0.1" and urlsplit(final_url).hostname == "::1":
            redirected = urlsplit(final_url)
            final_url = urlunsplit(
                (parsed.scheme, parsed.netloc, redirected.path, redirected.query, "")
            )
        if f"/api/browser/frame/{session_id}/" not in urlsplit(final_url).path:
            await manager.update(session_id, owner, url=final_url)
        text = content.decode(upstream.encoding or "utf-8", errors="replace")
        content = rewrite_html(text, final_url, session_id, final_url).encode("utf-8")
        headers.pop("content-type", None)
        content_type = "text/html; charset=utf-8"
    elif "text/css" in content_type:
        text = content.decode(upstream.encoding or "utf-8", errors="replace")
        content = rewrite_css(text, upstream_url, session_id).encode("utf-8")
        headers.pop("content-type", None)
        content_type = "text/css; charset=utf-8"
    elif parsed.hostname in {"localhost", "127.0.0.1", "::1"} and (
        "javascript" in content_type or "ecmascript" in content_type
    ):
        text = content.decode(upstream.encoding or "utf-8", errors="replace")
        content = rewrite_javascript(text, upstream_url, session_id).encode("utf-8")
        headers.pop("content-type", None)
        content_type = "text/javascript; charset=utf-8"
    return Response(
        content=content, status_code=upstream.status_code, headers=headers, media_type=content_type
    )


async def _proxy(request: Request, session_id: str, url: str) -> Response:
    return await proxy_browser_request(request, session_id, url, _owner(_auth(request)))


@router.get("/availability")
async def browser_availability(request: Request):
    _auth(request)
    return chrome_viewer_manager.availability(await _chrome_cdp_url())


async def _chrome_cdp_url() -> str:
    if await Config.get("browser.tab_chrome_source") != "personal":
        return ""
    return str(await Config.get("browser.cdp_url") or "").strip()


async def _personal_keep_alive() -> bool:
    return await Config.get("browser.personal_keep_alive") is not False


def _initial_url(value: object) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid Browser URL")
    return url


@router.post("/sessions")
async def create_session(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    explicit_mode = isinstance(payload, dict) and "mode" in payload
    mode = (
        payload.get("mode")
        if explicit_mode
        else "chrome"
        if await Config.get("browser.tab_default_mode") == "chrome"
        else "proxy"
    )
    if mode not in {"proxy", "chrome"}:
        raise HTTPException(status_code=400, detail="Invalid Browser mode")
    initial_url = _initial_url(payload.get("url") if isinstance(payload, dict) else None)
    auth = _auth(request)
    session = await manager.create(_owner(auth))
    if initial_url:
        await manager.update(session.session_id, session.owner, url=initial_url)
    if mode == "chrome":
        try:
            cdp_url = await _chrome_cdp_url()
            if cdp_url:
                if auth.role != "admin":
                    raise RuntimeError("Personal Chrome is available to administrators only")
                await chrome_viewer_manager.attach_personal(
                    session, local_origin(str(request.base_url)), cdp_url
                )
            else:
                await chrome_viewer_manager.start(session, local_origin(str(request.base_url)))
            session.mode = "chrome"
        except Exception as exc:
            logger.warning("Managed Chrome startup failed: %s", exc)
            await manager.close(session.session_id, session.owner)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _session_payload(session)


@router.get("/runtime.js", response_class=FileResponse)
async def browser_runtime():
    path = Path(__file__).parents[1] / "frontend" / "build" / "browser-runtime.js"
    return FileResponse(path, media_type="text/javascript", headers={"cache-control": "no-store"})


@router.get("/sessions")
async def list_sessions(request: Request):
    return {"session_ids": manager.ids(_owner(_auth(request)))}


@router.delete("/sessions/{session_id}")
async def close_session(request: Request, session_id: str):
    owner = _owner(_auth(request))
    if not await chrome_viewer_manager.detach_personal(
        session_id, owner, await _personal_keep_alive()
    ):
        await chrome_viewer_manager.stop(session_id, owner)
    if not await manager.close(session_id, owner):
        raise HTTPException(status_code=404, detail="Browser tab not found")
    return {"status": "closed"}


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str):
    session = manager.session(session_id, _owner(_auth(request)))
    if session is None:
        raise HTTPException(status_code=404, detail="Browser tab expired")
    return _session_payload(session)


@router.patch("/sessions/{session_id}")
async def update_session(request: Request, session_id: str):
    payload = await request.json()
    owner = _owner(_auth(request))
    existing = manager.session(session_id, owner)
    if existing is None:
        raise HTTPException(status_code=404, detail="Browser tab expired")
    url = str(payload["url"]) if "url" in payload else None
    title = str(payload["title"])[:512] if "title" in payload else None
    if url:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=400, detail="Invalid Browser URL")
    session = await manager.update(session_id, owner, url=url, title=title)
    if session is None:
        raise HTTPException(status_code=404, detail="Browser tab expired")
    requested_mode = payload.get("mode")
    if requested_mode is not None and requested_mode not in {"proxy", "chrome"}:
        raise HTTPException(status_code=400, detail="Invalid Browser mode")
    if requested_mode == "chrome" and session.mode != "chrome":
        session.status = "connecting"
        try:
            cdp_url = await _chrome_cdp_url()
            if cdp_url:
                if _auth(request).role != "admin":
                    raise RuntimeError("Personal Chrome is available to administrators only")
                await chrome_viewer_manager.attach_personal(
                    session, local_origin(str(request.base_url)), cdp_url
                )
            else:
                await chrome_viewer_manager.start(session, local_origin(str(request.base_url)))
        except Exception as exc:
            logger.warning("Managed Chrome startup failed: %s", exc)
            session.status = "ready"
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.mode = "chrome"
        session.status = "playing"
    elif requested_mode == "proxy" and session.mode == "chrome":
        if not await chrome_viewer_manager.detach_personal(
            session_id, owner, await _personal_keep_alive()
        ):
            await chrome_viewer_manager.stop(session_id, owner)
        session.mode = "proxy"
        session.status = "ready"
    return _session_payload(session)


@router.delete("/profile")
async def clear_managed_chrome_profile(request: Request):
    owner = _owner(require_admin(request))
    session_ids = await chrome_viewer_manager.clear_managed_profile(owner)
    for session_id in session_ids:
        await manager.close(session_id, owner)
    return {"status": "cleared", "closed_session_ids": session_ids}


@router.post("/cdp")
async def test_chrome_cdp(request: Request):
    require_admin(request)
    url = str((await request.json()).get("url", "")).strip().rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid CDP URL")
    connection = None
    try:
        connection = await CDPConnection.connect(await resolve_cdp_endpoint(url))
        version = await connection.send("Browser.getVersion")
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Could not connect to Chrome") from exc
    finally:
        if connection:
            await connection.close()
    return {"browser": str(version.get("product", "Chrome"))}


@router.get("/personal")
async def personal_chrome_status(request: Request):
    return chrome_viewer_manager.personal_status(_owner(require_admin(request)))


@router.post("/personal")
async def connect_personal_chrome(request: Request):
    owner = _owner(require_admin(request))
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    cdp_url = str(payload.get("url") or await Config.get("browser.cdp_url") or "").strip()
    try:
        await chrome_viewer_manager.connect_personal(
            owner, local_origin(str(request.base_url)), cdp_url
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return chrome_viewer_manager.personal_status(owner)


@router.delete("/personal")
async def disconnect_personal_chrome(request: Request):
    owner = _owner(require_admin(request))
    await chrome_viewer_manager.disconnect_personal(owner)
    return chrome_viewer_manager.personal_status(owner)


@router.get("/sessions/{session_id}/blank")
async def blank(request: Request, session_id: str):
    if manager.session(session_id, _owner(_auth(request))) is None:
        raise HTTPException(status_code=404, detail="Browser tab expired")
    return HTMLResponse("<!doctype html><title>Browser</title>")


@router.api_route(
    "/resources/{session_id}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy_resource(request: Request, session_id: str, path: str):
    owner = _owner(_auth(request))
    session = manager.session(session_id, owner)
    if session is None or not session.origin:
        raise HTTPException(status_code=404, detail="Browser tab expired")
    url = urljoin(session.origin + "/", path.lstrip("/"))
    if request.url.query:
        url += f"?{request.url.query}"
    return await proxy_browser_request(request, session_id, url, owner)


@router.api_route(
    "/frame/{session_id}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
)
async def proxy_frame(request: Request, session_id: str, url: str):
    return await _proxy(request, session_id, url)


@router.api_route(
    "/frame/{session_id}/{scheme}/{host}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def legacy_proxy_frame(request: Request, session_id: str, scheme: str, host: str, path: str):
    return await _proxy(
        request,
        session_id,
        urlunsplit((scheme, host, "/" + path.lstrip("/"), request.url.query, "")),
    )


@router.api_route(
    "/frame/{session_id}/{scheme}/{host}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def legacy_proxy_frame_root(request: Request, session_id: str, scheme: str, host: str):
    return await _proxy(request, session_id, urlunsplit((scheme, host, "/", request.url.query, "")))


@router.websocket("/sessions/{session_id}/ws")
async def proxy_websocket(websocket: WebSocket, session_id: str):
    client_host = websocket.client.host if websocket.client else "127.0.0.1"
    auth = check_access(client_host, websocket.cookies.get(COOKIE_NAME))
    owner = _owner(auth) if auth else ""
    if not auth or manager.session(session_id, owner) is None:
        await websocket.close(code=4001, reason="unauthorized")
        return
    await websocket.accept()
    try:
        open_message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        target = str(open_message.get("url", ""))
        parsed = urlsplit(target)
        if parsed.scheme not in {"ws", "wss"}:
            await websocket.close(code=4000, reason="invalid websocket URL")
            return
        client = manager.client(session_id, owner)
        assert client is not None
        prepared = client.build_request(
            "GET", target.replace("ws:", "http:", 1).replace("wss:", "https:", 1)
        )
        headers = {"cookie": prepared.headers["cookie"]} if "cookie" in prepared.headers else None
        upstream = await websockets.connect(target, additional_headers=headers)
        await websocket.send_json({"type": "open", "protocol": upstream.subprotocol or ""})

        async def to_upstream() -> None:
            while True:
                message = await websocket.receive()
                if message.get("text") is not None:
                    await upstream.send(message["text"])
                elif message.get("bytes") is not None:
                    await upstream.send(message["bytes"])
                else:
                    break

        async def to_client() -> None:
            async for message in upstream:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)

        done, pending = await asyncio.wait(
            (asyncio.create_task(to_upstream()), asyncio.create_task(to_client())),
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
        await upstream.close()
    except (WebSocketDisconnect, asyncio.TimeoutError, json.JSONDecodeError):
        pass
    except Exception:
        await websocket.close(code=1011, reason="Browser WebSocket failed")


@router.websocket("/sessions/{session_id}/stream")
async def chrome_viewer_stream(websocket: WebSocket, session_id: str):
    client_host = websocket.client.host if websocket.client else "127.0.0.1"
    token = websocket.cookies.get(COOKIE_NAME) or websocket.query_params.get("token")
    auth = check_access(client_host, token)
    owner = _owner(auth) if auth else ""
    session = manager.session(session_id, owner) if auth else None
    viewer = chrome_viewer_manager.viewer_for(session_id)
    if not session or session.mode != "chrome" or not viewer:
        await websocket.close(code=4004, reason="Chrome Browser session not found")
        return
    await chrome_viewer_manager.viewer_socket(websocket, viewer, session_id)


@router.websocket("/sessions/{session_id}/encoder")
async def chrome_encoder_stream(websocket: WebSocket, session_id: str):
    await chrome_viewer_manager.encoder_socket(
        websocket, session_id, websocket.query_params.get("token", "")
    )

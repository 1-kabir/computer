"""Optional Chrome-backed Browser tabs with H.264-over-WebSocket streaming."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlencode, urlsplit

import httpx
import websockets
from fastapi import WebSocket, WebSocketDisconnect

from cptr.models import Config
from cptr.utils.browser.launcher import find_browser
from cptr.env import DATA_DIR

FRAME_HEADER_SIZE = 14
MAX_FRAME_SIZE = 8 * 1024 * 1024
CAPTURE_TITLE = "Open WebUI Computer Browser"
PERSONAL_VIEWER_ID = "personal"
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

QUALITY_PRESETS = ("low", "balanced", "crisp")
ENCODER_HARDWARE_ACCELERATION = ("no-preference", "prefer-hardware", "prefer-software")
DEFAULT_QUALITY_PROFILES = {
    "low": {"bitrate": 3_000_000, "frame_rate": 15},
    "balanced": {"bitrate": 6_000_000, "frame_rate": 24},
    "crisp": {"bitrate": 12_000_000, "frame_rate": 30},
}


def _integer(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _number(value: object, default: float, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(maximum, float(value)))
    except (TypeError, ValueError):
        return default


async def _quality_settings() -> tuple[str, dict[str, dict[str, int]], int, int]:
    configured = await Config.get("browser.quality.profiles")
    profiles: dict[str, dict[str, int]] = {}
    for name, fallback in DEFAULT_QUALITY_PROFILES.items():
        value = configured.get(name) if isinstance(configured, dict) else None
        value = value if isinstance(value, dict) else {}
        profiles[name] = {
            "bitrate": _integer(value.get("bitrate"), fallback["bitrate"], 1_000_000, 12_000_000),
            "frame_rate": _integer(
                value.get("frame_rate", value.get("fps")), fallback["frame_rate"], 1, 60
            ),
        }
    default = await Config.get("browser.quality.default")
    default = default if default in QUALITY_PRESETS else "balanced"
    max_capture_height = _integer(
        await Config.get("browser.quality.max_resolution"), 1080, 240, 2160
    )
    max_bitrate = _integer(
        await Config.get("browser.quality.max_bitrate"), 12_000_000, 1_000_000, 12_000_000
    )
    return default, profiles, max_capture_height, max_bitrate


def _resolve_quality(
    preset: object,
    profiles: dict[str, dict[str, int]],
    max_capture_height: int,
    max_bitrate: int,
    default: str,
) -> tuple[str, dict[str, int]]:
    name = preset if preset in QUALITY_PRESETS else default
    value = profiles[name]
    return name, {
        "bitrate": min(value["bitrate"], max_bitrate),
        "frame_rate": value["frame_rate"],
        "max_height": max_capture_height,
    }


def _device_profile(data: object) -> dict[str, Any]:
    data = data if isinstance(data, dict) else {}
    user_agent = str(data.get("userAgent", ""))[:1024]
    metadata = data.get("userAgentMetadata")
    return {
        "user_agent": user_agent,
        "language": str(data.get("language", ""))[:64],
        "max_touch_points": _integer(data.get("maxTouchPoints"), 0, 0, 10),
        "screen_width": _integer(data.get("screenWidth"), 1280, 320, 4096),
        "screen_height": _integer(data.get("screenHeight"), 720, 240, 4096),
        "device_scale_factor": _integer(data.get("devicePixelRatio"), 1, 1, 3),
        "orientation": "landscapePrimary"
        if str(data.get("orientation")) == "landscapePrimary"
        else "portraitPrimary",
        "mobile": bool(data.get("mobile")),
        "user_agent_metadata": metadata if isinstance(metadata, dict) else None,
    }


def _mobile_user_agent(desktop_user_agent: str) -> str:
    version = re.search(r"Chrome/([\d.]+)", desktop_user_agent)
    chrome = version.group(1) if version else "120.0.0.0"
    return (
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{chrome} Mobile Safari/537.36"
    )


def _effective_device_profile(
    profile: dict[str, Any], mode: str, mobile_viewport: tuple[int, int], desktop_user_agent: str
) -> tuple[dict[str, Any], tuple[int, int]]:
    if mode == "desktop":
        return {
            **profile,
            "user_agent": desktop_user_agent or profile["user_agent"],
            "max_touch_points": 0,
            "mobile": False,
            "user_agent_metadata": None,
        }, mobile_viewport
    if mode == "mobile":
        width, height = mobile_viewport
        return {
            **profile,
            "user_agent": _mobile_user_agent(desktop_user_agent),
            "max_touch_points": 5,
            "screen_width": width,
            "screen_height": height,
            "orientation": "landscapePrimary" if width > height else "portraitPrimary",
            "mobile": True,
            "user_agent_metadata": {"mobile": True, "platform": "Android"},
        }, mobile_viewport
    return profile, mobile_viewport


def _user_agent_metadata(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    metadata: dict[str, Any] = {
        key: str(value[key])[:128]
        for key in (
            "platform",
            "platformVersion",
            "architecture",
            "model",
            "bitness",
            "fullVersion",
        )
        if isinstance(value.get(key), str)
    }
    metadata["mobile"] = bool(value.get("mobile"))
    for key in ("brands", "fullVersionList"):
        entries = value.get(key)
        if isinstance(entries, list):
            metadata[key] = [
                {
                    "brand": str(item.get("brand", ""))[:128],
                    "version": str(item.get("version", ""))[:64],
                }
                for item in entries[:10]
                if isinstance(item, dict) and item.get("brand") and item.get("version")
            ]
    return metadata if metadata.get("brands") or metadata.get("fullVersionList") else None


def _target_modifiers(modifiers: int, primary: bool) -> int:
    if not primary:
        return modifiers
    return (modifiers & ~6) | (4 if sys.platform == "darwin" else 2)


def _target_key(
    key: str, code: str, virtual_key: int, location: int, primary: bool
) -> tuple[str, str, int]:
    if not primary:
        return key, code, virtual_key
    target = "Meta" if sys.platform == "darwin" else "Control"
    side = "Right" if location == 2 else "Left"
    virtual_key = (92 if side == "Right" else 91) if target == "Meta" else 17
    return target, f"{target}{side}", virtual_key


def _editing_commands(key: str, modifiers: int, primary: bool) -> list[str]:
    if not primary or modifiers & 1:
        return []
    key = key.lower()
    if key == "z":
        return ["Redo" if modifiers & 8 else "Undo"]
    if key == "y":
        return ["Redo"]
    command = {
        "a": "SelectAll",
        "b": "ToggleBold",
        "c": "Copy",
        "i": "ToggleItalic",
        "u": "ToggleUnderline",
        "x": "Cut",
    }.get(key)
    return [command] if command else []


def local_origin(fallback: str) -> str:
    """Prefer the CLI's loopback port so encoder traffic never traverses a tunnel."""
    port = os.environ.get("CPTR_PORT")
    return f"http://127.0.0.1:{port}" if port else fallback.rstrip("/")


def managed_profile_path(owner: str) -> Path:
    safe_owner = hashlib.sha256(owner.encode()).hexdigest()[:24]
    return DATA_DIR / "browser-profiles" / safe_owner


async def resolve_cdp_endpoint(cdp_url: str, data_dir: Path | None = None) -> str:
    base = cdp_url.rstrip("/")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{base}/json/version", timeout=5)
            response.raise_for_status()
            return str(response.json()["webSocketDebuggerUrl"])
    except Exception as exc:
        parsed = urlsplit(base)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or not parsed.port:
            raise RuntimeError("Could not connect to Chrome") from exc
        if data_dir is not None:
            data_dirs = [data_dir]
        else:
            home = Path.home()
            if sys.platform == "darwin":
                support = home / "Library/Application Support"
                data_dirs = [
                    support / "Google/Chrome",
                    support / "Google/Chrome Canary",
                    support / "Chromium",
                    support / "BraveSoftware/Brave-Browser",
                    support / "Microsoft Edge",
                ]
            elif sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
                local = Path(os.environ["LOCALAPPDATA"])
                data_dirs = [
                    local / "Google/Chrome/User Data",
                    local / "Google/Chrome SxS/User Data",
                    local / "Chromium/User Data",
                    local / "BraveSoftware/Brave-Browser/User Data",
                    local / "Microsoft/Edge/User Data",
                ]
            else:
                config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
                data_dirs = [
                    config / "google-chrome",
                    config / "google-chrome-unstable",
                    config / "chromium",
                    config / "BraveSoftware/Brave-Browser",
                    config / "microsoft-edge",
                ]
        for directory in data_dirs:
            try:
                port, path = (directory / "DevToolsActivePort").read_text().splitlines()[:2]
            except (OSError, ValueError):
                continue
            if (
                port.isdigit()
                and int(port) == parsed.port
                and path.startswith("/devtools/browser/")
            ):
                return f"ws://127.0.0.1:{port}{path}"
        raise RuntimeError("Could not connect to Chrome") from exc


class CDPConnection:
    """Small event-safe CDP connection used only by the Chrome viewer."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.message_id = 0
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.handlers: dict[tuple[str, str | None], list[EventHandler]] = {}
        self.send_lock = asyncio.Lock()
        self.reader = asyncio.create_task(self._read())

    @classmethod
    async def connect(cls, ws_url: str) -> "CDPConnection":
        return cls(await websockets.connect(ws_url, max_size=16 * 1024 * 1024, ping_interval=None))

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                message = json.loads(raw)
                if message_id := message.get("id"):
                    future = self.pending.pop(message_id, None)
                    if future and not future.done():
                        future.set_result(message)
                    continue
                key = (message.get("method", ""), message.get("sessionId"))
                for handler in self.handlers.get(key, ()):
                    asyncio.create_task(handler(message.get("params", {})))
        except Exception as exc:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(exc)
            self.pending.clear()

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        async with self.send_lock:
            self.message_id += 1
            message_id = self.message_id
            future = asyncio.get_running_loop().create_future()
            self.pending[message_id] = future
            payload: dict[str, Any] = {"id": message_id, "method": method}
            if params:
                payload["params"] = params
            if session_id:
                payload["sessionId"] = session_id
            await self.ws.send(json.dumps(payload))
        try:
            response = await asyncio.wait_for(future, 15)
        except BaseException:
            self.pending.pop(message_id, None)
            raise
        if error := response.get("error"):
            raise RuntimeError(error.get("message", str(error)))
        return response.get("result", {})

    def on(self, method: str, handler: EventHandler, *, session_id: str | None = None) -> None:
        self.handlers.setdefault((method, session_id), []).append(handler)

    def remove_session_handlers(self, session_id: str) -> None:
        for key in [key for key in self.handlers if key[1] == session_id]:
            self.handlers.pop(key, None)

    async def close(self) -> None:
        await self.ws.close()
        self.reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.reader


@dataclass
class CDPTargetSession:
    connection: CDPConnection
    session_id: str

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.connection.send(method, params, session_id=self.session_id)

    def on(self, method: str, handler: EventHandler) -> None:
        self.connection.on(method, handler, session_id=self.session_id)

    async def close(self) -> None:
        self.connection.remove_session_handlers(self.session_id)
        with contextlib.suppress(Exception):
            await self.connection.send("Target.detachFromTarget", {"sessionId": self.session_id})


@dataclass
class ChromeHost:
    owner: str
    browser_path: str
    process: asyncio.subprocess.Process | None
    profile: Path | None
    browser_cdp: CDPConnection
    source: str = "managed"
    user_agent: str = ""
    start_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def create_target(
        self, url: str, *, new_window: bool = False
    ) -> tuple[str, CDPTargetSession]:
        params: dict[str, Any] = {"url": url}
        if new_window:
            params["newWindow"] = True
        target = await self.browser_cdp.send("Target.createTarget", params)
        target_id = str(target["targetId"])
        attached = await self.browser_cdp.send(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )
        return target_id, CDPTargetSession(self.browser_cdp, str(attached["sessionId"]))

    async def close_target(self, target_id: str) -> None:
        with contextlib.suppress(Exception):
            await self.browser_cdp.send("Target.closeTarget", {"targetId": target_id})

    async def close(self) -> None:
        await self.browser_cdp.close()
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()


@dataclass(eq=False)
class ViewerPeer:
    websocket: WebSocket
    queue: asyncio.Queue[dict[str, Any] | bytes] = field(
        default_factory=lambda: asyncio.Queue(maxsize=32)
    )
    waiting_keyframe: bool = True
    visible: bool = False
    session_id: str = ""


@dataclass
class ChromeViewer:
    session: Any
    host: ChromeHost
    target_id: str
    target_cdp: CDPTargetSession
    controller_id: str
    controller_cdp: CDPTargetSession
    encoder_token: str
    controller_origin: str
    encoder: WebSocket | None = None
    encoder_connected: asyncio.Event = field(default_factory=asyncio.Event)
    encoder_configured: asyncio.Event = field(default_factory=asyncio.Event)
    first_keyframe: asyncio.Event = field(default_factory=asyncio.Event)
    encoder_error: str = ""
    config: dict[str, Any] | None = None
    audio_config: dict[str, Any] | None = None
    editable_regions: list[dict[str, float]] = field(default_factory=list)
    keyframe: bytes | None = None
    peers: set[ViewerPeer] = field(default_factory=set)
    controller: ViewerPeer | None = None
    viewport: tuple[int, int] = (0, 0)
    target_viewport: tuple[float, float] = (0, 0)
    viewport_initialized: bool = False
    restart_attempted: bool = False
    initial_navigation_pending: bool = True
    quality_default: str = "balanced"
    quality_profiles: dict[str, dict[str, int]] = field(default_factory=dict)
    max_capture_height: int = 1080
    max_bitrate: int = 12_000_000
    personal: bool = False


@dataclass
class PersonalTab:
    session: Any
    target_id: str
    target_cdp: CDPTargetSession
    peers: set[ViewerPeer] = field(default_factory=set)
    controller: ViewerPeer | None = None
    viewport: tuple[int, int] = (1280, 720)
    target_viewport: tuple[float, float] = (1280, 720)
    config: dict[str, Any] | None = None
    keyframe: bytes | None = None
    personal: bool = True


@dataclass
class PersonalChrome:
    owner: str
    cdp_url: str
    host: ChromeHost
    controller_id: str
    controller_cdp: CDPTargetSession
    window_id: int
    encoder_token: str
    controller_origin: str
    width_inset: int
    height_inset: int
    encoder: WebSocket | None = None
    first_keyframe: asyncio.Event = field(default_factory=asyncio.Event)
    encoder_error: str = ""
    config: dict[str, Any] | None = None
    tabs: dict[str, PersonalTab] = field(default_factory=dict)
    active_session_id: str = ""
    status: str = "connecting"


class ChromeViewerManager:
    def __init__(self) -> None:
        self.hosts: dict[tuple[str, str], ChromeHost] = {}
        self.viewers: dict[str, ChromeViewer] = {}
        self.lock = asyncio.Lock()
        self.personal: PersonalChrome | None = None

    def availability(self, cdp_url: str = "") -> dict[str, Any]:
        path = find_browser()
        return {
            "proxy": {"available": True},
            "chrome": {
                "available": bool(path or cdp_url),
                "reason": None if path or cdp_url else "No compatible Chrome-family browser found",
            },
        }

    async def _host(self, owner: str, cdp_url: str = "") -> ChromeHost:
        source = cdp_url.rstrip("/")
        key = (owner, source)
        if existing := self.hosts.get(key):
            if existing.process is None or existing.process.returncode is None:
                return existing
        if source:
            browser_ws_url = await resolve_cdp_endpoint(source)
            browser_cdp = await CDPConnection.connect(browser_ws_url)
            version = await browser_cdp.send("Browser.getVersion")
            host = ChromeHost(
                owner,
                "",
                None,
                None,
                browser_cdp,
                "external",
                str(version.get("userAgent", "")),
            )
            self.hosts[key] = host
            return host
        browser_path = find_browser()
        if not browser_path:
            raise RuntimeError("No compatible Chrome-family browser found")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        profile = managed_profile_path(owner)
        profile.mkdir(parents=True, exist_ok=True)
        args = [
            browser_path,
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            f"--auto-select-tab-capture-source-by-title={CAPTURE_TITLE}",
            "about:blank",
        ]
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        version: dict[str, Any] | None = None
        async with httpx.AsyncClient() as client:
            for _ in range(30):
                if process.returncode is not None:
                    break
                try:
                    response = await client.get(f"http://127.0.0.1:{port}/json/version", timeout=1)
                    version = response.json()
                    break
                except Exception:
                    await asyncio.sleep(0.2)
        if not version:
            process.terminate()
            raise RuntimeError("Chrome started without an available graphical capture session")
        browser_cdp = await CDPConnection.connect(version["webSocketDebuggerUrl"])
        info = await browser_cdp.send("SystemInfo.getInfo")
        capabilities = info.get("gpu", {}).get("videoEncoding", [])
        # Some macOS Chrome builds expose an empty capability list even when the
        # real WebCodecs encoder succeeds, so only reject an explicit negative.
        if capabilities and not any(
            "h264" in str(item.get("profile", "")).lower() for item in capabilities
        ):
            await browser_cdp.close()
            process.terminate()
            raise RuntimeError("Chrome does not report hardware H.264 encoding support")
        host = ChromeHost(
            owner,
            browser_path,
            process,
            profile,
            browser_cdp,
            user_agent=str(version.get("User-Agent", version.get("userAgent", ""))),
        )
        self.hosts[key] = host
        return host

    async def start(self, session: Any, origin: str, *, cdp_url: str = "") -> ChromeViewer:
        if session.session_id in self.viewers:
            return self.viewers[session.session_id]
        host = await self._host(session.owner, cdp_url)
        async with host.start_lock:
            default, profiles, max_capture_height, max_bitrate = await _quality_settings()
            preset, quality = _resolve_quality(
                session.quality_preset, profiles, max_capture_height, max_bitrate, default
            )
            session.quality_preset = preset
            session.resolved_quality = quality
            marker = f"data:text/html,<title>{quote(CAPTURE_TITLE)}</title>"
            target_id, target_cdp = await host.create_target(marker)
            await target_cdp.send("Page.enable")
            await target_cdp.send("Runtime.enable")
            await target_cdp.send(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": """
                    addEventListener('click', event => {
                      const link = event.target?.closest?.('a[href]');
                      if (link && link.target && link.target !== '_self') {
                        event.preventDefault(); location.href = link.href;
                      }
                    }, true);
                    window.open = url => { if (url) location.href = url; return null; };
                    """,
                },
            )
            token = secrets.token_urlsafe(32)
            base = local_origin(origin)
            ws_scheme = "wss" if base.startswith("https:") else "ws"
            ws_url = f"{ws_scheme}://{urlsplit(base).netloc}/api/browser/sessions/{session.session_id}/encoder"
            hardware_acceleration = await Config.get("browser.encoder.hardware_acceleration")
            if hardware_acceleration not in ENCODER_HARDWARE_ACCELERATION:
                hardware_acceleration = "no-preference"
            fragment = urlencode(
                {
                    "session": session.session_id,
                    "token": token,
                    "ws": ws_url,
                    "audio": "1",
                    "hardwareAcceleration": hardware_acceleration,
                }
            )
            controller_url = f"{base}/browser-encoder.html#{fragment}"
            controller_id, controller_cdp = await host.create_target(controller_url)
            await controller_cdp.send("Page.enable")
            await controller_cdp.send("Runtime.enable")
            viewer = ChromeViewer(
                session, host, target_id, target_cdp, controller_id, controller_cdp, token, base
            )
            viewer.quality_default = default
            viewer.quality_profiles = profiles
            viewer.max_capture_height = max_capture_height
            viewer.max_bitrate = max_bitrate
            self.viewers[session.session_id] = viewer
            self._wire_events(viewer)
            try:
                for _ in range(30):
                    ready = await controller_cdp.send(
                        "Runtime.evaluate",
                        {"expression": "typeof window.startCapture === 'function'"},
                    )
                    if ready.get("result", {}).get("value") is True:
                        break
                    await asyncio.sleep(0.1)
                await host.browser_cdp.send("Target.activateTarget", {"targetId": controller_id})
                await controller_cdp.send(
                    "Runtime.evaluate",
                    {
                        "expression": "window.startCapture()",
                        "userGesture": True,
                    },
                )
                await asyncio.wait_for(
                    viewer.encoder_configured.wait(), 90 if host.source == "external" else 5
                )
                if viewer.encoder_error:
                    raise RuntimeError(viewer.encoder_error)
                await host.browser_cdp.send("Target.activateTarget", {"targetId": target_id})
                session.status = "playing"
                await self._refresh_state(viewer)
                await self._send_encoder(viewer, {"type": "pause"})
                return viewer
            except Exception:
                self.viewers.pop(session.session_id, None)
                await target_cdp.close()
                await controller_cdp.close()
                await host.close_target(target_id)
                await host.close_target(controller_id)
                raise

    async def connect_personal(self, owner: str, origin: str, cdp_url: str) -> PersonalChrome:
        if self.personal:
            if self.personal.owner != owner:
                raise RuntimeError("Personal Chrome is connected by another administrator")
            if self.personal.status == "playing":
                return self.personal
            await self.disconnect_personal(owner)
        if not cdp_url:
            raise RuntimeError("Personal Chrome CDP URL is not configured")
        async with self.lock:
            if self.personal:
                return self.personal
            host = await self._host(owner, cdp_url)
            token = secrets.token_urlsafe(32)
            base = local_origin(origin)
            ws_scheme = "wss" if base.startswith("https:") else "ws"
            ws_url = f"{ws_scheme}://{urlsplit(base).netloc}/api/browser/sessions/personal/encoder"
            hardware_acceleration = await Config.get("browser.encoder.hardware_acceleration")
            if hardware_acceleration not in ENCODER_HARDWARE_ACCELERATION:
                hardware_acceleration = "no-preference"
            fragment = urlencode(
                {
                    "token": token,
                    "ws": ws_url,
                    "window": "1",
                    "hardwareAcceleration": hardware_acceleration,
                }
            )
            controller_id, controller_cdp = await host.create_target(
                f"{base}/browser-encoder.html#{fragment}", new_window=True
            )
            await controller_cdp.send("Runtime.enable")
            window = await host.browser_cdp.send(
                "Browser.getWindowForTarget", {"targetId": controller_id}
            )
            metrics = await controller_cdp.send(
                "Runtime.evaluate",
                {
                    "expression": "({w:outerWidth-innerWidth,h:outerHeight-innerHeight})",
                    "returnByValue": True,
                },
            )
            inset = metrics.get("result", {}).get("value", {})
            personal = PersonalChrome(
                owner,
                cdp_url.rstrip("/"),
                host,
                controller_id,
                controller_cdp,
                int(window["windowId"]),
                token,
                base,
                max(0, int(inset.get("w", 0))),
                max(0, int(inset.get("h", 0))),
            )
            self.personal = personal
            try:
                await host.browser_cdp.send("Target.activateTarget", {"targetId": controller_id})
                for _ in range(30):
                    ready = await controller_cdp.send(
                        "Runtime.evaluate",
                        {"expression": "typeof window.startCapture === 'function'"},
                    )
                    if ready.get("result", {}).get("value") is True:
                        break
                    await asyncio.sleep(0.1)
                await controller_cdp.send(
                    "Runtime.evaluate",
                    {"expression": "window.startCapture()", "userGesture": True},
                )
                await asyncio.wait_for(personal.first_keyframe.wait(), 90)
                if personal.encoder_error:
                    raise RuntimeError(personal.encoder_error)
                personal.status = "playing"
                await self._send_personal({"type": "pause"})
                return personal
            except Exception:
                await self.disconnect_personal(owner)
                raise

    async def attach_personal(self, session: Any, origin: str, cdp_url: str) -> PersonalTab:
        personal = await self.connect_personal(session.owner, origin, cdp_url)
        await personal.host.browser_cdp.send(
            "Target.activateTarget", {"targetId": personal.controller_id}
        )
        target_id, target_cdp = await personal.host.create_target(session.url or "about:blank")
        window = await personal.host.browser_cdp.send(
            "Browser.getWindowForTarget", {"targetId": target_id}
        )
        if int(window["windowId"]) != personal.window_id:
            await target_cdp.close()
            await personal.host.close_target(target_id)
            raise RuntimeError("Chrome created the Browser tab outside the captured window")
        await target_cdp.send("Page.enable")
        await target_cdp.send("Runtime.enable")
        tab = PersonalTab(session, target_id, target_cdp)
        personal.tabs[session.session_id] = tab
        session.mode = "chrome"
        session.status = "playing"
        self._wire_events(tab)
        await self._refresh_state(tab)
        await self._focus_personal(tab)
        return tab

    def viewer_for(self, session_id: str) -> ChromeViewer | PersonalTab | None:
        return self.viewers.get(session_id) or (
            self.personal.tabs.get(session_id) if self.personal else None
        )

    async def detach_personal(self, session_id: str, owner: str, keep_alive: bool) -> bool:
        if not self.personal or self.personal.owner != owner:
            return False
        tab = self.personal.tabs.pop(session_id, None)
        if not tab:
            return False
        for peer in tuple(tab.peers):
            with contextlib.suppress(Exception):
                await peer.websocket.close(code=1001)
        await tab.target_cdp.close()
        await self.personal.host.close_target(tab.target_id)
        if self.personal.active_session_id == session_id:
            self.personal.active_session_id = ""
        if not self.personal.tabs and not keep_alive:
            await self.disconnect_personal(owner)
        return True

    async def disconnect_personal(self, owner: str) -> bool:
        personal = self.personal
        if not personal or personal.owner != owner:
            return False
        self.personal = None
        for tab in personal.tabs.values():
            tab.session.status = "lost"
            for peer in tuple(tab.peers):
                with contextlib.suppress(Exception):
                    await peer.websocket.close(code=1001)
            await tab.target_cdp.close()
            await personal.host.close_target(tab.target_id)
        if personal.encoder:
            with contextlib.suppress(Exception):
                await personal.encoder.close(code=1001)
        await personal.controller_cdp.close()
        await personal.host.close_target(personal.controller_id)
        host = self.hosts.pop((owner, personal.cdp_url), None)
        if host:
            await host.close()
        return True

    def personal_status(self, owner: str) -> dict[str, Any]:
        if not self.personal:
            return {"status": "disconnected", "browser": "", "session_count": 0}
        if self.personal.owner != owner:
            return {"status": "unavailable", "browser": "", "session_count": 0}
        return {
            "status": self.personal.status,
            "browser": "Chrome",
            "session_count": len(self.personal.tabs),
        }

    def _wire_events(self, viewer: ChromeViewer | PersonalTab) -> None:
        async def refresh(_: dict[str, Any]) -> None:
            await self._refresh_state(viewer)

        async def dialog(_: dict[str, Any]) -> None:
            # Browser chrome is not part of tab capture, so never leave an invisible modal blocking it.
            with contextlib.suppress(Exception):
                await viewer.target_cdp.send("Page.handleJavaScriptDialog", {"accept": True})

        for method in (
            "Page.frameNavigated",
            "Page.navigatedWithinDocument",
            "Page.loadEventFired",
        ):
            viewer.target_cdp.on(method, refresh)
        viewer.target_cdp.on("Page.javascriptDialogOpening", dialog)

    async def _refresh_state(self, viewer: ChromeViewer | PersonalTab) -> None:
        try:
            value = await viewer.target_cdp.send(
                "Runtime.evaluate",
                {
                    "expression": "({url: location.href, title: document.title})",
                    "returnByValue": True,
                },
            )
            page = value.get("result", {}).get("value", {})
            history = await viewer.target_cdp.send("Page.getNavigationHistory")
            entries = history.get("entries", [])
            index = int(history.get("currentIndex", 0))
            url = str(page.get("url", ""))
            if url.startswith(("http://", "https://")):
                viewer.session.url = url
                parsed = urlsplit(url)
                viewer.session.origin = f"{parsed.scheme}://{parsed.netloc}"
            viewer.session.title = str(page.get("title", ""))[:512]
            await self._broadcast_json(
                viewer,
                {
                    "type": "state",
                    "url": viewer.session.url,
                    "title": viewer.session.title,
                    "can_go_back": index > 0,
                    "can_go_forward": index < len(entries) - 1,
                },
            )
            if not viewer.personal:
                await self._refresh_editable_regions(viewer)
        except Exception:
            pass

    async def _refresh_editable_regions(self, viewer: ChromeViewer) -> None:
        try:
            value = await viewer.target_cdp.send(
                "Runtime.evaluate",
                {
                    "expression": """JSON.stringify([...document.querySelectorAll(
                      'input:not([type=hidden]):not([disabled]):not([readonly]),textarea:not([disabled]):not([readonly]),[contenteditable]:not([contenteditable=false])'
                    )].map(element => { const rect = element.getBoundingClientRect(); return {x: rect.left, y: rect.top, width: rect.width, height: rect.height}; }).filter(rect => rect.width > 0 && rect.height > 0))""",
                    "returnByValue": True,
                },
            )
            raw = value.get("result", {}).get("value", "[]")
            regions = json.loads(raw) if isinstance(raw, str) else []
            width, height = viewer.target_viewport
            normalized = [
                {
                    "x": max(0.0, float(region["x"]) / width),
                    "y": max(0.0, float(region["y"]) / height),
                    "width": min(1.0, float(region["width"]) / width),
                    "height": min(1.0, float(region["height"]) / height),
                }
                for region in regions[:100]
                if isinstance(region, dict)
                and width > 0
                and height > 0
                and float(region.get("width", 0)) > 0
                and float(region.get("height", 0)) > 0
            ]
            if normalized != viewer.editable_regions:
                viewer.editable_regions = normalized
                await self._broadcast_json(
                    viewer, {"type": "editable_regions", "regions": normalized}
                )
        except Exception:
            pass

    async def encoder_socket(self, websocket: WebSocket, session_id: str, token: str) -> None:
        if session_id == PERSONAL_VIEWER_ID:
            await self._personal_encoder_socket(websocket, token)
            return
        viewer = self.viewers.get(session_id)
        if not viewer or not secrets.compare_digest(viewer.encoder_token, token):
            await websocket.close(code=4001, reason="unauthorized")
            return
        if viewer.encoder is not None:
            await websocket.close(code=4009, reason="encoder already connected")
            return
        await websocket.accept()
        viewer.encoder = websocket
        viewer.encoder_connected.set()
        await self._send_encoder(viewer, {"type": "quality", **viewer.session.resolved_quality})
        try:
            while True:
                message = await websocket.receive()
                if message.get("text") is not None:
                    data = json.loads(message["text"])
                    if data.get("type") == "config":
                        viewer.config = data
                        viewer.encoder_configured.set()
                        viewer.keyframe = None
                        if viewer.viewport_initialized:
                            for peer in tuple(viewer.peers):
                                while not peer.queue.empty():
                                    with contextlib.suppress(asyncio.QueueEmpty):
                                        peer.queue.get_nowait()
                                peer.waiting_keyframe = True
                                with contextlib.suppress(asyncio.QueueFull):
                                    peer.queue.put_nowait(data)
                    elif data.get("type") == "audio_config":
                        viewer.audio_config = data
                        await self._broadcast_json(viewer, data)
                    elif data.get("type") == "error":
                        viewer.encoder_error = str(data.get("message", "Chrome encoder failed"))
                        viewer.encoder_configured.set()
                        viewer.first_keyframe.set()
                elif message.get("bytes") is not None:
                    frame = message["bytes"]
                    if not FRAME_HEADER_SIZE <= len(frame) <= MAX_FRAME_SIZE or frame[0] not in {
                        1,
                        2,
                    }:
                        continue
                    if frame[0] == 1 and frame[1] & 1:
                        viewer.first_keyframe.set()
                    if frame[0] == 1:
                        await self._broadcast_frame(viewer, frame)
                    else:
                        await self._broadcast_audio(viewer, frame)
                else:
                    break
        except (WebSocketDisconnect, json.JSONDecodeError):
            pass
        finally:
            if viewer.encoder is websocket:
                viewer.encoder = None
                viewer.session.status = "lost"
                await self._broadcast_json(
                    viewer,
                    {"type": "status", "status": "lost", "message": "Chrome encoder disconnected"},
                )
                if self.viewers.get(session_id) is viewer:
                    asyncio.create_task(self._restart_encoder(viewer))

    async def _personal_encoder_socket(self, websocket: WebSocket, token: str) -> None:
        personal = self.personal
        if not personal or not secrets.compare_digest(personal.encoder_token, token):
            await websocket.close(code=4001, reason="unauthorized")
            return
        if personal.encoder is not None:
            await websocket.close(code=4009, reason="encoder already connected")
            return
        await websocket.accept()
        personal.encoder = websocket
        try:
            while True:
                message = await websocket.receive()
                if message.get("text") is not None:
                    data = json.loads(message["text"])
                    if data.get("type") == "config":
                        personal.config = data
                        if tab := personal.tabs.get(personal.active_session_id):
                            await self._broadcast_json(tab, data)
                    elif data.get("type") == "error":
                        personal.encoder_error = str(data.get("message", "Chrome encoder failed"))
                        personal.first_keyframe.set()
                elif message.get("bytes") is not None:
                    frame = message["bytes"]
                    if not FRAME_HEADER_SIZE <= len(frame) <= MAX_FRAME_SIZE or frame[0] != 1:
                        continue
                    if frame[1] & 1:
                        personal.first_keyframe.set()
                    if tab := personal.tabs.get(personal.active_session_id):
                        if frame[1] & 1:
                            tab.config = personal.config
                        await self._broadcast_frame(tab, frame)
                else:
                    break
        except (WebSocketDisconnect, json.JSONDecodeError):
            pass
        finally:
            if self.personal is personal and personal.encoder is websocket:
                personal.encoder = None
                personal.status = "lost"
                for tab in personal.tabs.values():
                    tab.session.status = "lost"
                    await self._broadcast_json(
                        tab,
                        {
                            "type": "status",
                            "status": "lost",
                            "message": "Chrome encoder disconnected",
                        },
                    )

    async def _restart_encoder(self, viewer: ChromeViewer) -> None:
        if viewer.restart_attempted:
            await self._fallback_to_proxy(viewer, "Chrome encoder could not reconnect")
            return
        viewer.restart_attempted = True
        try:
            async with viewer.host.start_lock:
                await viewer.controller_cdp.close()
                await viewer.host.close_target(viewer.controller_id)
                await viewer.target_cdp.send(
                    "Runtime.evaluate",
                    {"expression": f"document.title={CAPTURE_TITLE!r}"},
                )
                viewer.encoder_token = secrets.token_urlsafe(32)
                base = viewer.controller_origin
                ws_scheme = "wss" if base.startswith("https:") else "ws"
                ws_url = (
                    f"{ws_scheme}://{urlsplit(base).netloc}/api/browser/sessions/"
                    f"{viewer.session.session_id}/encoder"
                )
                hardware_acceleration = await Config.get("browser.encoder.hardware_acceleration")
                if hardware_acceleration not in ENCODER_HARDWARE_ACCELERATION:
                    hardware_acceleration = "no-preference"
                fragment = urlencode(
                    {
                        "session": viewer.session.session_id,
                        "token": viewer.encoder_token,
                        "ws": ws_url,
                        "audio": "1",
                        "hardwareAcceleration": hardware_acceleration,
                    }
                )
                viewer.controller_id, viewer.controller_cdp = await viewer.host.create_target(
                    f"{base}/browser-encoder.html#{fragment}"
                )
                await viewer.controller_cdp.send("Runtime.enable")
                viewer.first_keyframe.clear()
                viewer.encoder_configured.clear()
                viewer.encoder_error = ""
                for _ in range(30):
                    ready = await viewer.controller_cdp.send(
                        "Runtime.evaluate",
                        {"expression": "typeof window.startCapture === 'function'"},
                    )
                    if ready.get("result", {}).get("value") is True:
                        break
                    await asyncio.sleep(0.1)
                await viewer.controller_cdp.send(
                    "Runtime.evaluate",
                    {"expression": "window.startCapture()", "userGesture": True},
                )
                await asyncio.wait_for(viewer.encoder_configured.wait(), 5)
                if viewer.encoder_error:
                    raise RuntimeError(viewer.encoder_error)
                viewer.session.status = "playing"
                await self._broadcast_json(
                    viewer, {"type": "status", "status": "playing", "message": ""}
                )
        except Exception:
            await self._fallback_to_proxy(viewer, "Chrome encoder could not reconnect")

    async def _fallback_to_proxy(self, viewer: ChromeViewer, message: str) -> None:
        if self.viewers.get(viewer.session.session_id) is not viewer:
            return
        viewer.session.mode = "proxy"
        viewer.session.status = "ready"
        await self._broadcast_json(
            viewer,
            {"type": "status", "status": "lost", "mode": "proxy", "message": message},
        )
        await asyncio.sleep(0.1)
        await self.stop(viewer.session.session_id, viewer.session.owner)

    async def viewer_socket(
        self, websocket: WebSocket, viewer: ChromeViewer | PersonalTab, session_id: str = ""
    ) -> None:
        await websocket.accept()
        peer = ViewerPeer(websocket, session_id=session_id or viewer.session.session_id)
        viewer.peers.add(peer)
        if viewer.controller is None:
            viewer.controller = peer
        await peer.queue.put(
            {
                "type": "ready",
                "mode": "chrome",
                "controller": viewer.controller is peer,
                "managed": not viewer.personal,
                "quality": {
                    "preset": viewer.session.quality_preset,
                    **viewer.session.resolved_quality,
                }
                if not viewer.personal
                else None,
                "device_mode": viewer.session.device_mode if not viewer.personal else None,
                "mobile_viewport": {
                    "width": viewer.session.mobile_viewport[0],
                    "height": viewer.session.mobile_viewport[1],
                }
                if not viewer.personal and viewer.session.mobile_viewport
                else None,
            }
        )
        config = (
            viewer.config or self.personal.config
            if viewer.personal and self.personal
            else viewer.config
        )
        if viewer.personal or viewer.viewport_initialized:
            if config:
                await peer.queue.put(config)
            if not viewer.personal and viewer.audio_config:
                await peer.queue.put(viewer.audio_config)
            if viewer.keyframe:
                await peer.queue.put(viewer.keyframe)
        await self._refresh_state(viewer)
        if viewer.personal or viewer.viewport_initialized:
            await self._request_keyframe(viewer)

        async def write() -> None:
            while True:
                message = await peer.queue.get()
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_json(message)

        writer = asyncio.create_task(write())
        try:
            while True:
                data = await websocket.receive_json()
                if viewer.personal:
                    with contextlib.suppress(Exception):
                        await self._viewer_message(viewer, peer, data)
                else:
                    await self._viewer_message(viewer, peer, data)
        except (WebSocketDisconnect, json.JSONDecodeError):
            pass
        finally:
            writer.cancel()
            viewer.peers.discard(peer)
            if viewer.controller is peer:
                viewer.controller = next(iter(viewer.peers), None)
                if viewer.controller:
                    with contextlib.suppress(asyncio.QueueFull):
                        viewer.controller.queue.put_nowait(
                            {"type": "ready", "mode": "chrome", "controller": True}
                        )
            await self._set_visibility(viewer, peer, False)

    async def _viewer_message(
        self, viewer: ChromeViewer | PersonalTab, peer: ViewerPeer, data: dict[str, Any]
    ) -> None:
        kind = data.get("type")
        if kind == "visibility":
            await self._set_visibility(viewer, peer, bool(data.get("visible")))
            return
        if kind == "focus":
            if not peer.visible:
                await self._set_visibility(viewer, peer, True)
            if viewer.personal:
                await self._focus_personal(viewer, peer)
            return
        if viewer.controller is not peer:
            return
        if kind == "viewport":
            try:
                width, height = int(data.get("width")), int(data.get("height"))
            except (TypeError, ValueError):
                return
            if width <= 0 or height <= 0:
                return
            if viewer.personal:
                viewer.viewport = (width, height)
                if self.personal and self.personal.active_session_id == viewer.session.session_id:
                    await self._resize_personal(viewer)
                return
            await self._apply_managed_viewport(viewer, data, width, height)
        elif kind == "navigate":
            url = str(data.get("url", ""))
            parsed = urlsplit(url)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                if not viewer.personal and viewer.initial_navigation_pending:
                    viewer.session.url = url
                    return
                await viewer.target_cdp.send("Page.navigate", {"url": url})
        elif kind in {"back", "forward"}:
            history = await viewer.target_cdp.send("Page.getNavigationHistory")
            entries = history.get("entries", [])
            index = int(history.get("currentIndex", 0)) + (-1 if kind == "back" else 1)
            if 0 <= index < len(entries):
                await viewer.target_cdp.send(
                    "Page.navigateToHistoryEntry", {"entryId": entries[index]["id"]}
                )
        elif kind == "reload":
            await viewer.target_cdp.send("Page.reload")
        elif kind == "pointer":
            await self._pointer(viewer, data)
        elif kind == "touch":
            if viewer.personal:
                event = {"start": "down", "move": "move", "end": "up"}.get(str(data.get("event")))
                if event:
                    await self._pointer(viewer, {**data, "event": event})
            else:
                await self._touch(viewer, data)
        elif kind == "wheel":
            await self._wheel(viewer, data)
        elif kind == "key":
            event = str(data.get("event", ""))
            if event not in {"keyDown", "keyUp", "char"}:
                return
            text = str(data.get("text", ""))
            key = str(data.get("key", ""))
            code = str(data.get("code", ""))
            location = max(0, min(3, int(data.get("location", 0))))
            virtual_key = max(0, min(65535, int(data.get("windows_virtual_key_code", 0))))
            key, code, virtual_key = _target_key(
                key,
                code,
                virtual_key,
                location,
                bool(data.get("primary_modifier_key", False)),
            )
            modifiers = _target_modifiers(
                int(data.get("modifiers", 0)), bool(data.get("primary_modifier", False))
            )
            params: dict[str, Any] = {
                "type": "rawKeyDown" if event == "keyDown" and not text else event,
                "key": key,
                "code": code,
                "text": text,
                "unmodifiedText": str(data.get("unmodified_text", "")),
                "modifiers": modifiers,
                "autoRepeat": bool(data.get("auto_repeat", False)),
                "location": location,
                "isKeypad": bool(data.get("is_keypad", False)),
                "windowsVirtualKeyCode": virtual_key,
            }
            commands = _editing_commands(
                key, int(data.get("modifiers", 0)), bool(data.get("primary_modifier", False))
            )
            if event == "keyDown" and commands:
                params["commands"] = commands
            await viewer.target_cdp.send("Input.dispatchKeyEvent", params)
        elif kind == "paste":
            await viewer.target_cdp.send("Input.insertText", {"text": str(data.get("text", ""))})
        elif kind == "text" and not viewer.personal:
            await viewer.target_cdp.send("Input.insertText", {"text": str(data.get("text", ""))})
        elif kind == "request_keyframe":
            await self._request_keyframe(viewer)

    async def _apply_managed_viewport(
        self, viewer: ChromeViewer, data: dict[str, Any], width: int, height: int
    ) -> None:
        requested_profile = _device_profile(data.get("device"))
        mode = str(data.get("device_mode", viewer.session.device_mode))
        mode = mode if mode in {"auto", "desktop", "mobile"} else "auto"
        requested_mobile_viewport = data.get("mobile_viewport")
        current_mobile_viewport = viewer.session.mobile_viewport
        if "mobile_viewport" not in data:
            mobile_viewport = current_mobile_viewport
        elif isinstance(requested_mobile_viewport, dict):
            try:
                mobile_viewport = (
                    int(requested_mobile_viewport.get("width")),
                    int(requested_mobile_viewport.get("height")),
                )
            except (TypeError, ValueError):
                mobile_viewport = None
            if not mobile_viewport or mobile_viewport[0] <= 0 or mobile_viewport[1] <= 0:
                mobile_viewport = None
        else:
            mobile_viewport = None
        emulated_viewport = (
            mobile_viewport if mode == "mobile" and mobile_viewport else (width, height)
        )
        profile, _ = _effective_device_profile(
            requested_profile, mode, emulated_viewport, viewer.host.user_agent
        )
        layout_viewport = emulated_viewport
        preset, quality = _resolve_quality(
            data.get("quality"),
            viewer.quality_profiles,
            viewer.max_capture_height,
            viewer.max_bitrate,
            viewer.quality_default,
        )
        profile_changed = profile != viewer.session.device_profile
        quality_changed = quality != viewer.session.resolved_quality
        device_changed = (
            mode != viewer.session.device_mode or mobile_viewport != viewer.session.mobile_viewport
        )
        viewport_changed = viewer.viewport != layout_viewport or data.get("force") is True
        viewer.viewport = layout_viewport
        viewer.session.device_profile = profile
        viewer.session.device_mode = mode
        viewer.session.mobile_viewport = mobile_viewport
        viewer.session.quality_preset = preset
        viewer.session.resolved_quality = quality

        if profile_changed and profile["user_agent"]:
            params: dict[str, Any] = {
                "userAgent": profile["user_agent"],
                "acceptLanguage": profile["language"],
            }
            if metadata := _user_agent_metadata(profile["user_agent_metadata"]):
                params["userAgentMetadata"] = metadata
            await viewer.target_cdp.send("Emulation.setUserAgentOverride", params)
        if profile_changed:
            await viewer.target_cdp.send(
                "Emulation.setTouchEmulationEnabled",
                {
                    "enabled": bool(profile["mobile"] and profile["max_touch_points"]),
                    "maxTouchPoints": profile["max_touch_points"],
                },
            )
        if profile_changed or device_changed or viewport_changed:
            await viewer.target_cdp.send(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": layout_viewport[0],
                    "height": layout_viewport[1],
                    "deviceScaleFactor": profile["device_scale_factor"],
                    "mobile": profile["mobile"],
                    "screenWidth": profile["screen_width"],
                    "screenHeight": profile["screen_height"],
                    "screenOrientation": {
                        "type": profile["orientation"],
                        "angle": 90 if profile["orientation"].startswith("landscape") else 0,
                    },
                },
            )
            metrics = await viewer.target_cdp.send("Page.getLayoutMetrics")
            visual = metrics.get("cssVisualViewport", {})
            viewer.target_viewport = (
                float(visual.get("clientWidth", layout_viewport[0])),
                float(visual.get("clientHeight", layout_viewport[1])),
            )
        if quality_changed:
            await self._send_encoder(viewer, {"type": "quality", **quality})
            await self._broadcast_json(viewer, {"type": "quality", "preset": preset, **quality})
        if viewer.initial_navigation_pending:
            viewer.initial_navigation_pending = False
            viewer.viewport_initialized = True
            for peer in tuple(viewer.peers):
                while not peer.queue.empty():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        peer.queue.get_nowait()
                peer.waiting_keyframe = True
                if viewer.config:
                    with contextlib.suppress(asyncio.QueueFull):
                        peer.queue.put_nowait(viewer.config)
            await viewer.target_cdp.send(
                "Page.navigate", {"url": viewer.session.url or "about:blank"}
            )
            await self._send_encoder(viewer, {"type": "resize"})
            await self._send_encoder(
                viewer,
                {"type": "resume" if any(item.visible for item in viewer.peers) else "pause"},
            )
            if any(item.visible for item in viewer.peers):
                await self._request_keyframe(viewer)
        elif device_changed and viewer.session.url:
            await viewer.target_cdp.send("Page.reload")

    async def _focus_personal(self, tab: PersonalTab, peer: ViewerPeer | None = None) -> None:
        async with self.lock:
            personal = self.personal
            if not personal or personal.tabs.get(tab.session.session_id) is not tab:
                return
            already_active = personal.active_session_id == tab.session.session_id
            if already_active and (peer is None or tab.controller is peer):
                await asyncio.wait_for(tab.target_cdp.send("Page.bringToFront"), 5)
                return
            previous = personal.tabs.get(personal.active_session_id)
            if previous and previous is not tab and previous.controller:
                with contextlib.suppress(asyncio.QueueFull):
                    previous.controller.queue.put_nowait(
                        {"type": "ready", "mode": "chrome", "controller": False}
                    )
            for item in tab.peers:
                while not item.queue.empty():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        item.queue.get_nowait()
                item.waiting_keyframe = True
                if tab.config and tab.keyframe:
                    with contextlib.suppress(asyncio.QueueFull):
                        item.queue.put_nowait(tab.config)
                        item.queue.put_nowait(tab.keyframe)
                elif personal.config:
                    with contextlib.suppress(asyncio.QueueFull):
                        item.queue.put_nowait(personal.config)
            if peer:
                previous = tab.controller
                tab.controller = peer
                if previous is not peer:
                    if previous and previous in tab.peers:
                        with contextlib.suppress(asyncio.QueueFull):
                            previous.queue.put_nowait(
                                {"type": "ready", "mode": "chrome", "controller": False}
                            )
                    with contextlib.suppress(asyncio.QueueFull):
                        peer.queue.put_nowait(
                            {"type": "ready", "mode": "chrome", "controller": True}
                        )
            await asyncio.wait_for(
                tab.target_cdp.send("Page.bringToFront"),
                5,
            )
            personal.active_session_id = tab.session.session_id
            await self._resize_personal(tab)

    async def _resize_personal(self, tab: PersonalTab) -> None:
        personal = self.personal
        if not personal:
            return
        width, height = tab.viewport
        tab.target_viewport = (width, height)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                personal.host.browser_cdp.send(
                    "Browser.setWindowBounds",
                    {
                        "windowId": personal.window_id,
                        "bounds": {
                            "windowState": "normal",
                            "width": width + personal.width_inset,
                            "height": height + personal.height_inset,
                        },
                    },
                ),
                1,
            )
        with contextlib.suppress(Exception):
            metrics = await asyncio.wait_for(tab.target_cdp.send("Page.getLayoutMetrics"), 1)
            visual = metrics.get("cssVisualViewport", {})
            tab.target_viewport = (
                float(visual.get("clientWidth", width)),
                float(visual.get("clientHeight", height)),
            )
        await self._send_personal({"type": "resize"})
        await self._send_personal({"type": "keyframe"})

    async def _pointer(self, viewer: ChromeViewer | PersonalTab, data: dict[str, Any]) -> None:
        coordinates = self._coordinates(viewer, data)
        if coordinates is None:
            return
        x, y = coordinates
        event = {"move": "mouseMoved", "down": "mousePressed", "up": "mouseReleased"}.get(
            str(data.get("event"))
        )
        if not event:
            return
        params = {
            "x": x,
            "y": y,
            "button": str(data.get("button", "none")),
            "buttons": max(0, min(7, int(data.get("buttons", 0)))),
            "clickCount": max(0, min(3, int(data.get("click_count", 0)))),
            "modifiers": int(data.get("modifiers", 0)),
            "pointerType": "mouse",
        }
        if event == "mousePressed":
            await viewer.target_cdp.send(
                "Input.dispatchMouseEvent", {**params, "type": "mouseMoved", "button": "none"}
            )
        await viewer.target_cdp.send(
            "Input.dispatchMouseEvent",
            {**params, "type": event},
        )

    async def _touch(self, viewer: ChromeViewer, data: dict[str, Any]) -> None:
        event = {
            "start": "touchStart",
            "move": "touchMove",
            "end": "touchEnd",
            "cancel": "touchCancel",
        }.get(str(data.get("event")))
        points = data.get("points")
        if not event or not isinstance(points, list) or len(points) > 10:
            return
        touch_points = []
        for point in points:
            if not isinstance(point, dict):
                return
            coordinates = self._coordinates(viewer, point)
            if coordinates is None:
                return
            x, y = coordinates
            touch_points.append(
                {
                    "id": _integer(point.get("id"), 0, 0, 2**31 - 1),
                    "x": x,
                    "y": y,
                    "radiusX": _integer(point.get("radiusX"), 1, 1, 100),
                    "radiusY": _integer(point.get("radiusY"), 1, 1, 100),
                    "force": _number(point.get("force"), 1, 0, 1),
                }
            )
        await viewer.target_cdp.send(
            "Input.dispatchTouchEvent",
            {
                "type": event,
                "touchPoints": touch_points,
                "modifiers": int(data.get("modifiers", 0)),
            },
        )
        if event in {"touchEnd", "touchCancel"}:
            asyncio.create_task(self._refresh_editable_regions(viewer))

    async def _wheel(self, viewer: ChromeViewer | PersonalTab, data: dict[str, Any]) -> None:
        coordinates = self._coordinates(viewer, data)
        if coordinates is None:
            return
        x, y = coordinates
        await viewer.target_cdp.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseWheel",
                "x": x,
                "y": y,
                "deltaX": float(data.get("delta_x", 0)),
                "deltaY": float(data.get("delta_y", 0)),
                "modifiers": int(data.get("modifiers", 0)),
            },
        )

    @staticmethod
    def _coordinates(
        viewer: ChromeViewer | PersonalTab, data: dict[str, Any]
    ) -> tuple[float, float] | None:
        x, y = float(data.get("x", -1)), float(data.get("y", -1))
        if data.get("normalized"):
            if not (0 <= x <= 1 and 0 <= y <= 1):
                return None
            width, height = viewer.target_viewport
            return x * width, y * height
        width, height = viewer.viewport
        return (x, y) if 0 <= x <= width and 0 <= y <= height else None

    async def _set_visibility(
        self, viewer: ChromeViewer | PersonalTab, peer: ViewerPeer, visible: bool
    ) -> None:
        peer.visible = visible
        if viewer.personal:
            visible_personal = bool(
                self.personal
                and any(peer.visible for tab in self.personal.tabs.values() for peer in tab.peers)
            )
            await self._send_personal({"type": "resume" if visible_personal else "pause"})
            if (
                visible
                and self.personal
                and self.personal.active_session_id == viewer.session.session_id
            ):
                await self._send_personal({"type": "keyframe"})
            return
        await self._send_encoder(
            viewer,
            {
                "type": "resume"
                if viewer.viewport_initialized and any(item.visible for item in viewer.peers)
                else "pause"
            },
        )
        if visible and viewer.viewport_initialized:
            await self._request_keyframe(viewer)

    async def _request_keyframe(self, viewer: ChromeViewer | PersonalTab) -> None:
        if viewer.personal:
            await self._send_personal({"type": "keyframe"})
            return
        await self._send_encoder(viewer, {"type": "keyframe"})

    async def _send_encoder(self, viewer: ChromeViewer, message: dict[str, Any]) -> None:
        if viewer.encoder:
            with contextlib.suppress(Exception):
                await viewer.encoder.send_json(message)

    async def _send_personal(self, message: dict[str, Any]) -> None:
        if self.personal and self.personal.encoder:
            with contextlib.suppress(Exception):
                await self.personal.encoder.send_json(message)

    async def _broadcast_json(
        self, viewer: ChromeViewer | PersonalTab, message: dict[str, Any]
    ) -> None:
        for peer in tuple(viewer.peers):
            if peer.queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    peer.queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                peer.queue.put_nowait(message)

    async def _broadcast_frame(self, viewer: ChromeViewer | PersonalTab, frame: bytes) -> None:
        keyframe = bool(frame[1] & 1)
        if keyframe:
            viewer.keyframe = frame
        for peer in tuple(viewer.peers):
            if not peer.visible or (peer.waiting_keyframe and not keyframe):
                continue
            if peer.queue.full():
                while not peer.queue.empty():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        peer.queue.get_nowait()
                peer.waiting_keyframe = True
                await self._request_keyframe(viewer)
                if not keyframe:
                    continue
            if keyframe:
                peer.waiting_keyframe = False
            peer.queue.put_nowait(frame)

    async def _broadcast_audio(self, viewer: ChromeViewer, frame: bytes) -> None:
        for peer in tuple(viewer.peers):
            if not peer.visible:
                continue
            if peer.queue.full():
                continue
            peer.queue.put_nowait(frame)

    async def stop(self, session_id: str, owner: str) -> bool:
        viewer = self.viewers.get(session_id)
        if not viewer or viewer.session.owner != owner:
            return False
        self.viewers.pop(session_id, None)
        for peer in tuple(viewer.peers):
            with contextlib.suppress(Exception):
                await peer.websocket.close(code=1001)
        if viewer.encoder:
            with contextlib.suppress(Exception):
                await viewer.encoder.close(code=1001)
        await viewer.target_cdp.close()
        await viewer.controller_cdp.close()
        await viewer.host.close_target(viewer.target_id)
        await viewer.host.close_target(viewer.controller_id)
        return True

    async def clear_managed_profile(self, owner: str) -> list[str]:
        session_ids = [
            viewer.session.session_id
            for viewer in self.viewers.values()
            if viewer.session.owner == owner and viewer.host.source == "managed"
        ]
        for session_id in session_ids:
            await self.stop(session_id, owner)
        host = self.hosts.pop((owner, ""), None)
        if host:
            await host.close()
        shutil.rmtree(managed_profile_path(owner), ignore_errors=True)
        return session_ids

    async def close_all(self) -> None:
        if self.personal:
            await self.disconnect_personal(self.personal.owner)
        for viewer in tuple(self.viewers.values()):
            await self.stop(viewer.session.session_id, viewer.session.owner)
        for host in tuple(self.hosts.values()):
            await host.close()
        self.hosts.clear()


manager = ChromeViewerManager()

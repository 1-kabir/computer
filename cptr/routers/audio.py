"""Audio router: STT transcription via Whisper-compatible API.

Supports optional pydub/ffmpeg for compressing and splitting long recordings.
If pydub is not installed, raw audio is sent directly (works for < 25MB files).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

from cptr.models import Config
from cptr.utils.config import _get_jwt_secret, check_access
from cptr.utils.crypto import decrypt_key
from cptr.utils.runtime import Runtime, FileError

logger = logging.getLogger(__name__)

# Optional pydub for long audio handling
try:
    from pydub import AudioSegment  # type: ignore[import-untyped]

    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False

router = APIRouter(prefix="/api/audio", tags=["audio"])

COOKIE_NAME = "cptr_session"
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB Whisper API limit


class SpeechRequest(BaseModel):
    text: str
    voice: str | None = None
    workspace: str | None = None


class AudioStateResponse(BaseModel):
    voice_memos_enabled: bool
    transcribe_enabled: bool
    stt_configured: bool
    recording_quality: str
    tts_enabled: bool
    tts_configured: bool
    tts_voice: str
    tts_format: str
    tts_playback_speed: float
    tts_auto_stream_enabled: bool
    voice_mode_stt_mode: str


def _get_user(request: Request) -> str:
    """Extract user_id from cookie, raise 401 if not authenticated."""
    token = request.cookies.get(COOKIE_NAME)
    client_host = request.client.host if request.client else "127.0.0.1"
    auth = check_access(client_host=client_host, jwt_token=token)
    if not auth or not auth.user_id:
        raise HTTPException(401, "authentication required")
    return auth.user_id


async def _workspace_audio_cache_dir(
    request: Request, workspace: str | None, kind: str
) -> Path | None:
    # Voice samples are project context, so they live under the workspace .cptr
    # folder and move with the project. That keeps STT and TTS artifacts available
    # for reuse, debugging, and the local data flywheel. By default,
    # ensure_cptr_gitignored keeps them out of git.
    if not workspace:
        return None
    try:
        listing = await Runtime.list_directory(request, workspace)
    except FileError:
        return None
    ws = Path(listing["path"])
    cache_dir = ws / ".cptr" / "cache" / "audio" / kind
    return cache_dir


def _cache_key(payload: dict, data: bytes | None = None) -> str:
    # The key includes the provider settings plus the exact audio bytes or text.
    # Without those inputs, a model, voice, or format change could accidentally reuse
    # the wrong artifact. The JSON file beside the audio explains what the hash means.
    h = hashlib.sha256()
    h.update(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    if data is not None:
        h.update(b"\0")
        h.update(data)
    return h.hexdigest()


def _cache_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _audio_extension(filename: str, content_type: str, fallback: str = ".webm") -> str:
    ext = Path(filename).suffix.lower()
    if ext and len(ext) <= 10 and ext[1:].replace("-", "").isalnum():
        return ext
    return {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/aac": ".aac",
        "audio/flac": ".flac",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mp4": ".m4a",
    }.get(content_type.split(";")[0].lower(), fallback)


# ── Audio processing (optional, requires pydub + ffmpeg) ─────────


def compress_audio(file_path: str) -> str:
    """Compress audio to 16kHz mono 32kbps MP3 if larger than MAX_FILE_SIZE."""
    if not HAS_PYDUB or os.path.getsize(file_path) <= MAX_FILE_SIZE:
        return file_path

    audio = AudioSegment.from_file(file_path)
    audio = audio.set_frame_rate(16000).set_channels(1)

    base, _ = os.path.splitext(file_path)
    compressed_path = f"{base}_compressed.mp3"
    audio.export(compressed_path, format="mp3", bitrate="32k")
    return compressed_path


def split_audio(file_path: str, max_bytes: int) -> list[str]:
    """Split audio into chunks not exceeding max_bytes.

    Returns a list of chunk file paths. If audio fits, returns [file_path].
    """
    file_size = os.path.getsize(file_path)
    if file_size <= max_bytes:
        return [file_path]

    if not HAS_PYDUB:
        return [file_path]  # Can't split without pydub

    audio = AudioSegment.from_file(file_path)
    duration_ms = len(audio)

    approx_chunk_ms = max(int(duration_ms * (max_bytes / file_size)) - 1000, 1000)
    chunks: list[str] = []
    start = 0
    i = 0
    base, _ = os.path.splitext(file_path)

    while start < duration_ms:
        end = min(start + approx_chunk_ms, duration_ms)
        chunk = audio[start:end]
        chunk_path = f"{base}_chunk_{i}.mp3"
        chunk.export(chunk_path, format="mp3", bitrate="32k")

        # Halve chunk duration if still too large
        while os.path.getsize(chunk_path) > max_bytes and (end - start) > 5000:
            end = start + ((end - start) // 2)
            chunk = audio[start:end]
            chunk.export(chunk_path, format="mp3", bitrate="32k")

        if os.path.getsize(chunk_path) > max_bytes:
            os.remove(chunk_path)
            raise RuntimeError("Audio chunk cannot be reduced below max file size.")

        chunks.append(chunk_path)
        start = end
        i += 1

    return chunks


# ── Transcription ────────────────────────────────────────────────


@router.get("/state", response_model=AudioStateResponse)
async def audio_state(request: Request):
    """Return non-sensitive audio feature state for the authenticated UI."""
    _get_user(request)

    tts_key = await Config.get("audio.tts_api_key")
    stt_key = await Config.get("audio.stt_api_key")
    quality = await Config.get("audio.recording_quality")
    if quality not in ("high", "medium", "low"):
        quality = "high"
    playback_speed = await Config.get("audio.tts_playback_speed")
    try:
        playback_speed = float(playback_speed)
    except (TypeError, ValueError):
        playback_speed = 1.0
    playback_speed = min(max(playback_speed, 0.5), 2.0)

    return AudioStateResponse(
        voice_memos_enabled=await Config.get("audio.voice_memos_enabled") is True,
        transcribe_enabled=await Config.get("audio.transcribe_enabled") is not False,
        stt_configured=bool(stt_key),
        recording_quality=str(quality),
        tts_enabled=await Config.get("audio.tts_enabled") is True,
        tts_configured=bool(tts_key or stt_key),
        tts_voice=str((await Config.get("audio.tts_voice")) or "alloy"),
        tts_format=str((await Config.get("audio.tts_format")) or "mp3"),
        tts_playback_speed=playback_speed,
        tts_auto_stream_enabled=await Config.get("audio.tts_auto_stream_enabled") is True,
        voice_mode_stt_mode=str((await Config.get("audio.voice_mode_stt_mode")) or "browser"),
    )


async def _transcribe_chunk(
    data: bytes,
    filename: str,
    content_type: str,
    base_url: str,
    api_key: str,
    model: str,
) -> str:
    """Send a single audio chunk to the STT API."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
        resp = await client.post(
            f"{base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (filename, data, content_type)},
            data={"model": model},
        )
        resp.raise_for_status()
    return resp.json().get("text", "")


@router.post("/transcribe")
async def transcribe(
    request: Request,
    file: UploadFile,
    workspace: str | None = Form(None),
    text: str | None = Form(None),
    source: str | None = Form(None),
    language: str | None = Form(None),
):
    """Send audio to a Whisper-compatible STT API, return transcript text.

    If pydub + ffmpeg are available and the file is large, it will be
    compressed and split into chunks that are transcribed concurrently.
    """
    _get_user(request)

    raw_data = await file.read()
    filename = file.filename or "audio.webm"
    content_type = file.content_type or "audio/webm"
    cache_dir = await _workspace_audio_cache_dir(request, workspace, "stt")
    cache_json_path: Path | None = None
    cache_audio_path: Path | None = None
    provided_text = (text or "").strip()

    if provided_text:
        # Browser speech recognition already produced text, so there is no provider
        # call to cache. We still store the captured audio and transcript in the
        # normal STT folder so browser mode and provider mode produce the same kind
        # of data flywheel sample without paying for a second transcription.
        if not raw_data:
            raise HTTPException(400, "Speech-to-text capture requires non-empty audio.")
        if cache_dir:
            audio_ext = _audio_extension(filename, content_type)
            key = _cache_key(
                {
                    "type": "stt",
                    "source": source or "browser_speech_recognition",
                    "text": provided_text,
                    "filename": filename,
                    "content_type": content_type,
                    "language": language or "",
                },
                raw_data,
            )
            cache_json_path = cache_dir / f"{key}.json"
            cache_audio_path = cache_dir / f"{key}{audio_ext}"
            await Runtime.write_file(request, str(cache_audio_path), raw_data)
            await Runtime.write_file(
                request,
                str(cache_json_path),
                _cache_json(
                    {
                        "type": "stt",
                        "source": source or "browser_speech_recognition",
                        "transcription_source": "browser_speech_recognition",
                        "text": provided_text,
                        "audio_file": cache_audio_path.name,
                        "filename": filename,
                        "content_type": content_type,
                        "language": language,
                    }
                ),
            )
        return {"text": provided_text, "cached": True}

    api_key_encrypted = await Config.get("audio.stt_api_key")
    if not api_key_encrypted:
        raise HTTPException(
            400,
            "Speech-to-text not configured. Set up in Settings → Audio.",
        )

    api_key = decrypt_key(api_key_encrypted, _get_jwt_secret())
    base_url = (await Config.get("audio.stt_base_url")) or "https://api.openai.com/v1"
    model = (await Config.get("audio.stt_model")) or "whisper-1"

    if cache_dir:
        # Provider STT is cacheable because identical audio with the same provider
        # settings should produce the same transcript. Keeping the original audio
        # beside the JSON makes the sample reusable for audits, replay, and future
        # data flywheel work instead of being a one-off API response.
        key = _cache_key(
            {
                "type": "stt",
                "base_url": str(base_url).rstrip("/"),
                "model": model,
                "filename": filename,
                "content_type": content_type,
            },
            raw_data,
        )
        audio_ext = _audio_extension(filename, content_type)
        cache_json_path = cache_dir / f"{key}.json"
        cache_audio_path = cache_dir / f"{key}{audio_ext}"
        try:
            cached_file = await Runtime.read_file(request, str(cache_json_path))
            cached = json.loads(str(cached_file.get("content") or ""))
            return {"text": str(cached.get("text") or ""), "cached": True}
        except (FileError, json.JSONDecodeError):
            pass

    # Small file: send directly, no temp files needed
    if len(raw_data) <= MAX_FILE_SIZE or not HAS_PYDUB:
        try:
            text = await _transcribe_chunk(
                raw_data, filename, content_type, base_url, api_key, model
            )
            if cache_json_path and cache_audio_path:
                await Runtime.write_file(request, str(cache_audio_path), raw_data)
                await Runtime.write_file(
                    request,
                    str(cache_json_path),
                    _cache_json(
                        {
                            "type": "stt",
                            "text": text,
                            "audio_file": cache_audio_path.name,
                            "filename": filename,
                            "content_type": content_type,
                            "base_url": str(base_url).rstrip("/"),
                            "model": model,
                        }
                    ),
                )
            return {"text": text}
        except httpx.HTTPStatusError as exc:
            detail = f"STT API error: {exc.response.status_code}"
            if len(raw_data) > MAX_FILE_SIZE and not HAS_PYDUB:
                detail += (
                    ". Recording is too large. Install ffmpeg and pydub for automatic splitting."
                )
            logger.warning("[transcribe] %s: %s", detail, exc.response.text[:500])
            raise HTTPException(502, detail)
        except httpx.ConnectError:
            raise HTTPException(502, "Could not connect to STT API")

    # Large file: compress → split → transcribe chunks concurrently
    tmp_dir = tempfile.mkdtemp(prefix="cptr-stt-")
    file_path = os.path.join(tmp_dir, filename)
    Path(file_path).write_bytes(raw_data)

    chunk_paths: list[str] = []
    try:
        # Compress
        compressed = await asyncio.to_thread(compress_audio, file_path)

        # Split
        chunk_paths = await asyncio.to_thread(split_audio, compressed, MAX_FILE_SIZE)

        # Transcribe all chunks concurrently
        async def _do_chunk(path: str) -> str:
            chunk_data = await asyncio.to_thread(Path(path).read_bytes)
            chunk_name = os.path.basename(path)
            return await _transcribe_chunk(
                chunk_data, chunk_name, "audio/mpeg", base_url, api_key, model
            )

        tasks = [_do_chunk(p) for p in chunk_paths]
        # Use gather to preserve order (as_completed doesn't guarantee it)
        results = await asyncio.gather(*tasks)

        text = " ".join(r for r in results if r)
        if cache_json_path and cache_audio_path:
            await Runtime.write_file(request, str(cache_audio_path), raw_data)
            await Runtime.write_file(
                request,
                str(cache_json_path),
                _cache_json(
                    {
                        "type": "stt",
                        "text": text,
                        "audio_file": cache_audio_path.name,
                        "filename": filename,
                        "content_type": content_type,
                        "base_url": str(base_url).rstrip("/"),
                        "model": model,
                    }
                ),
            )
        return {"text": text}

    except httpx.HTTPStatusError as exc:
        logger.warning(
            "[transcribe] STT API error %s: %s", exc.response.status_code, exc.response.text[:500]
        )
        raise HTTPException(502, f"STT API error: {exc.response.status_code}")
    except httpx.ConnectError:
        raise HTTPException(502, "Could not connect to STT API")
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    finally:
        # Clean up temp files
        for p in chunk_paths:
            if p != file_path and os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        # Clean up compressed file if different from original
        if "compressed" in locals() and compressed != file_path and os.path.isfile(compressed):
            try:
                os.remove(compressed)
            except OSError:
                pass
        try:
            os.remove(file_path)
            os.rmdir(tmp_dir)
        except OSError:
            pass


def _audio_media_type(fmt: str) -> str:
    return {
        "mp3": "audio/mpeg",
        "opus": "audio/ogg",
        "aac": "audio/aac",
        "flac": "audio/flac",
        "wav": "audio/wav",
        "pcm": "audio/L16",
    }.get(fmt, "audio/mpeg")


@router.post("/speech")
async def speech(request: Request, body: SpeechRequest):
    """Generate speech audio using an OpenAI-compatible TTS API."""
    _get_user(request)

    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Text-to-speech requires non-empty text.")

    if await Config.get("audio.tts_enabled") is not True:
        raise HTTPException(400, "Text-to-speech is disabled in Settings → Audio.")

    api_key_encrypted = await Config.get("audio.tts_api_key")
    if not api_key_encrypted:
        api_key_encrypted = await Config.get("audio.stt_api_key")
    if not api_key_encrypted:
        raise HTTPException(
            400,
            "Text-to-speech not configured. Set up a TTS or STT API key in Settings → Audio.",
        )

    api_key = decrypt_key(api_key_encrypted, _get_jwt_secret())
    base_url = ((await Config.get("audio.tts_base_url")) or "https://api.openai.com/v1").rstrip("/")
    model = (await Config.get("audio.tts_model")) or "tts-1"
    voice = body.voice or (await Config.get("audio.tts_voice")) or "alloy"
    fmt = str((await Config.get("audio.tts_format")) or "mp3").lower()

    payload = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": fmt,
    }
    cache_dir = await _workspace_audio_cache_dir(request, body.workspace, "tts")
    cache_audio_path: Path | None = None
    cache_json_path: Path | None = None
    if cache_dir:
        # TTS is especially worth caching because short phrases repeat often across
        # messages and replays. The key uses provider, model, voice, format, and exact
        # text so a phrase like "Hi." can be reused safely without new spend, while
        # the neighboring JSON records why that audio file exists.
        key = _cache_key(
            {
                "type": "tts",
                "base_url": base_url,
                "model": model,
                "voice": voice,
                "format": fmt,
                "text": text,
            }
        )
        cache_audio_path = cache_dir / f"{key}.{fmt}"
        cache_json_path = cache_dir / f"{key}.json"
        try:
            cached_audio = await Runtime.read_bytes(request, str(cache_audio_path))
            if cached_audio["data"]:
                return Response(
                    content=cached_audio["data"],
                    media_type=_audio_media_type(str(fmt)),
                    headers={"X-CPTR-Audio-Cache": "hit"},
                )
            await Runtime.delete_item(request, str(cache_audio_path))
            await Runtime.delete_item(request, str(cache_json_path))
        except FileError:
            pass

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
            resp = await client.post(
                f"{base_url}/audio/speech",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "[speech] TTS API error %s: %s",
            exc.response.status_code,
            exc.response.text[:500],
        )
        raise HTTPException(502, f"TTS API error: {exc.response.status_code}")
    except httpx.ConnectError:
        raise HTTPException(502, "Could not connect to TTS API")

    if not resp.content:
        raise HTTPException(502, "TTS API returned empty audio.")

    cache_state = "disabled"
    if cache_audio_path and cache_json_path:
        await Runtime.write_file(request, str(cache_audio_path), resp.content)
        await Runtime.write_file(
            request,
            str(cache_json_path),
            _cache_json(
                {
                    "type": "tts",
                    "text": text,
                    "audio_file": cache_audio_path.name,
                    "base_url": base_url,
                    "model": model,
                    "voice": voice,
                    "format": fmt,
                    "content_type": _audio_media_type(str(fmt)),
                }
            ),
        )
        cache_state = "write"

    return Response(
        content=resp.content,
        media_type=_audio_media_type(str(fmt)),
        headers={"X-CPTR-Audio-Cache": cache_state},
    )

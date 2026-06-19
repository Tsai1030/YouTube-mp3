from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest, urlopen

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, sanitize_filename


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DOWNLOAD_ROOT = Path(os.environ.get("YTDLP_DOWNLOAD_DIR", tempfile.gettempdir())) / "yt-dlp-web"
MAX_DURATION_SECONDS = int(os.environ.get("YTDLP_MAX_DURATION_SECONDS", "1800"))
MAX_FILE_MB = int(os.environ.get("YTDLP_MAX_FILE_MB", "150"))
MAX_CONCURRENT_DOWNLOADS = int(os.environ.get("YTDLP_MAX_CONCURRENT_DOWNLOADS", "1"))
APP_ACCESS_TOKEN = os.environ.get("APP_ACCESS_TOKEN")
# Admin-only token for /admin cookie refresh. Falls back to APP_ACCESS_TOKEN so the
# public frontend can be left open (no APP_ACCESS_TOKEN) while /admin stays protected.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN") or APP_ACCESS_TOKEN
YTDLP_COOKIES_BASE64 = os.environ.get("YTDLP_COOKIES_BASE64")
YTDLP_COOKIES_TEXT = os.environ.get("YTDLP_COOKIES_TEXT")
YTDLP_COOKIES_PATH = os.environ.get("YTDLP_COOKIES_PATH")
YTDLP_PROXY = os.environ.get("YTDLP_PROXY")
# YouTube "player client(s)" to use. tv_embedded bypasses the datacenter-IP bot check
# without cookies. Comma-separated; empty string falls back to yt-dlp defaults.
YTDLP_PLAYER_CLIENT = os.environ.get("YTDLP_PLAYER_CLIENT", "tv_embedded")
APP_CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("APP_CORS_ORIGINS", "").split(",")
    if origin.strip()
]

# Upstash Redis (REST) — optional persistence so cookies survive Render cold starts.
UPSTASH_REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
COOKIE_KV_KEY = os.environ.get("COOKIE_KV_KEY", "ytdlp:cookies")
ADMIN_COOKIE_PROBE_URL = os.environ.get(
    "ADMIN_COOKIE_PROBE_URL", "https://www.youtube.com/watch?v=jNQXAC9IVRw"
)

# Mutable cookie state: an admin refresh (or KV restore) points this at the live file,
# so the next parse/download picks it up without a redeploy. cookies_file() prefers it.
_COOKIE_LOCK = threading.Lock()
_RUNTIME_COOKIE_PATH: Path | None = None
COOKIES_FILE = DOWNLOAD_ROOT / "cookies.txt"
_LAST_VALIDATION: dict[str, Any] = {}  # never holds cookie content

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}

app = FastAPI(title="yt-dlp Audio Web", docs_url=None, redoc_url=None)
if APP_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=APP_CORS_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
download_slots = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)


class UrlPayload(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class DownloadPayload(UrlPayload):
    audio_format: Literal["best", "mp3", "m4a"] = "mp3"


class CookieRefreshPayload(BaseModel):
    cookies_text: str | None = Field(default=None, max_length=262144)
    cookies_base64: str | None = Field(default=None, max_length=349536)
    run_validation: bool = Field(default=True, alias="validate")
    persist: bool = True


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _decode_cookie_input(text: str | None, b64: str | None) -> bytes:
    if b64:
        try:
            return base64.b64decode(b64, validate=True)
        except binascii.Error as exc:
            raise ValueError("Invalid base64 cookie value.") from exc
    if text:
        return text.encode("utf-8")
    raise ValueError("No cookie content provided.")


def _validate_netscape_header(raw: bytes) -> None:
    head = raw[:200].lstrip()
    if not (
        head.startswith(b"# Netscape HTTP Cookie File")
        or head.startswith(b"# HTTP Cookie File")
        or b"\t" in raw[:4096]
    ):
        raise ValueError("Content does not look like a Netscape cookies.txt file.")


def _write_cookies(raw: bytes) -> Path:
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = COOKIES_FILE.with_suffix(".txt.tmp")
    tmp.write_bytes(raw)
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(COOKIES_FILE)  # atomic swap; readers never see a half-written file
    try:
        COOKIES_FILE.chmod(0o600)
    except OSError:
        pass
    return COOKIES_FILE


def set_runtime_cookies(text: str | None = None, b64: str | None = None) -> Path:
    raw = _decode_cookie_input(text, b64)
    _validate_netscape_header(raw)  # reject garbage before clobbering the live file
    with _COOKIE_LOCK:
        global _RUNTIME_COOKIE_PATH
        _RUNTIME_COOKIE_PATH = _write_cookies(raw)
    return _RUNTIME_COOKIE_PATH


def cookies_file() -> Path | None:
    # Priority: admin/KV runtime state -> explicit path -> boot-time env (materialized once).
    if _RUNTIME_COOKIE_PATH and _RUNTIME_COOKIE_PATH.exists():
        return _RUNTIME_COOKIE_PATH

    if YTDLP_COOKIES_PATH:
        path = Path(YTDLP_COOKIES_PATH)
        return path if path.exists() else None

    if not YTDLP_COOKIES_BASE64 and not YTDLP_COOKIES_TEXT:
        return None

    if COOKIES_FILE.exists():
        return COOKIES_FILE

    try:
        cookie_bytes = _decode_cookie_input(YTDLP_COOKIES_TEXT, YTDLP_COOKIES_BASE64)
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("Invalid YTDLP_COOKIES_BASE64 or YTDLP_COOKIES_TEXT value.") from exc

    return _write_cookies(cookie_bytes)


def cookies_available() -> bool:
    try:
        return cookies_file() is not None
    except RuntimeError:
        return False


def _cookie_source() -> str | None:
    if _RUNTIME_COOKIE_PATH and _RUNTIME_COOKIE_PATH.exists():
        return "runtime"
    if YTDLP_COOKIES_PATH and Path(YTDLP_COOKIES_PATH).exists():
        return "path"
    if YTDLP_COOKIES_BASE64 or YTDLP_COOKIES_TEXT:
        return "env"
    return None


def validate_youtube_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in YOUTUBE_HOSTS:
        raise HTTPException(status_code=400, detail="Only YouTube URLs are supported.")
    return parsed.geturl()


def require_access_token(token: str | None) -> None:
    if APP_ACCESS_TOKEN and token != APP_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Access token required.")


def require_admin(token: str | None) -> None:
    # Stricter than require_access_token: admin write endpoints must never be open.
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Admin disabled: set ADMIN_TOKEN.")
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Access token required.")


def upstash_configured() -> bool:
    return bool(UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN)


def _upstash(*command: str) -> Any:
    if not upstash_configured():
        raise RuntimeError("Upstash is not configured.")
    request = UrlRequest(
        UPSTASH_REDIS_REST_URL.rstrip("/"),
        data=json.dumps(list(command)).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return payload.get("result") if isinstance(payload, dict) else None


def persist_cookies(b64: str) -> None:
    _upstash("SET", COOKIE_KV_KEY, b64)


def restore_cookies_from_kv() -> bool:
    if not upstash_configured():
        return False
    stored = _upstash("GET", COOKIE_KV_KEY)
    if not stored:
        return False
    set_runtime_cookies(b64=stored)
    return True


def duration_text(seconds: Any) -> str:
    if not isinstance(seconds, int):
        return "Unknown"
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def base_ydl_options() -> dict[str, Any]:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 3,
        "max_filesize": MAX_FILE_MB * 1024 * 1024,
        "cachedir": False,
    }
    cookie_path = cookies_file()
    if cookie_path:
        options["cookiefile"] = str(cookie_path)
    if YTDLP_PROXY:
        options["proxy"] = YTDLP_PROXY
    if YTDLP_PLAYER_CLIENT:
        clients = [c.strip() for c in YTDLP_PLAYER_CLIENT.split(",") if c.strip()]
        options["extractor_args"] = {"youtube": {"player_client": clients}}
    return options


def extract_info(url: str) -> dict[str, Any]:
    with YoutubeDL({**base_ydl_options(), "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)

    duration = info.get("duration")
    if isinstance(duration, int) and duration > MAX_DURATION_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=f"Video is longer than the {duration_text(MAX_DURATION_SECONDS)} limit.",
        )

    return {
        "id": info.get("id"),
        "title": info.get("title") or "Untitled audio",
        "duration": duration,
        "durationText": duration_text(duration),
        "thumbnail": info.get("thumbnail"),
        "webpageUrl": info.get("webpage_url") or url,
        "uploader": info.get("uploader"),
        "ffmpegAvailable": ffmpeg_available(),
        "cookiesAvailable": cookies_available(),
        "limits": {
            "maxDurationSeconds": MAX_DURATION_SECONDS,
            "maxFileMb": MAX_FILE_MB,
        },
    }


def download_audio(url: str, audio_format: str) -> Path:
    if audio_format == "mp3" and not ffmpeg_available():
        raise HTTPException(status_code=400, detail="MP3 conversion requires ffmpeg and ffprobe.")

    workdir = Path(tempfile.mkdtemp(prefix="job-", dir=DOWNLOAD_ROOT))
    outtmpl = str(workdir / "%(title).200B.%(ext)s")
    options = {
        **base_ydl_options(),
        "outtmpl": {"default": outtmpl},
        "paths": {"home": str(workdir)},
    }

    if audio_format == "mp3":
        options.update(
            {
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "0",
                    }
                ],
            }
        )
    elif audio_format == "m4a":
        options["format"] = "bestaudio[ext=m4a]/bestaudio/best"
    else:
        options["format"] = "bestaudio/best"

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise

    duration = info.get("duration") if isinstance(info, dict) else None
    if isinstance(duration, int) and duration > MAX_DURATION_SECONDS:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(
            status_code=400,
            detail=f"Video is longer than the {duration_text(MAX_DURATION_SECONDS)} limit.",
        )

    candidates = [
        path
        for path in workdir.iterdir()
        if path.is_file() and path.suffix not in {".part", ".ytdl", ".temp"}
    ]
    if not candidates:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(status_code=500, detail="Download finished but no output file was created.")

    return max(candidates, key=lambda path: path.stat().st_mtime)


def _looks_like_bot_block(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in ("sign in to confirm", "not a bot", "cookies", "login required")
    )


def validate_cookies() -> dict[str, Any]:
    # Lightweight probe: does a metadata-only extract on a known video succeed?
    if not cookies_available():
        result = {"ok": False, "reason": "no_cookies", "message": "尚未設定 cookies。"}
    else:
        options = {**base_ydl_options(), "skip_download": True}
        try:
            with YoutubeDL(options) as ydl:
                ydl.extract_info(ADMIN_COOKIE_PROBE_URL, download=False)
            result = {"ok": True, "reason": "ok", "message": "Cookies 有效。"}
        except DownloadError as exc:
            detail = str(exc)[:300]
            if _looks_like_bot_block(detail):
                result = {"ok": False, "reason": "expired", "message": "Cookies 已失效或被擋，請重新匯出上傳。"}
            else:
                result = {"ok": False, "reason": "error", "message": detail}
        except Exception as exc:  # noqa: BLE001 - report any probe failure to admin
            result = {"ok": False, "reason": "error", "message": str(exc)[:300]}
    result["checkedAt"] = int(time.time())
    _LAST_VALIDATION.clear()
    _LAST_VALIDATION.update(result)
    return result


@app.on_event("startup")
def startup() -> None:
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    if upstash_configured():
        try:
            if restore_cookies_from_kv():
                print("[startup] Restored cookies from Upstash.")
        except Exception as exc:  # noqa: BLE001 - never let restore block boot
            print(f"[startup] Cookie restore from Upstash failed: {exc}")


@app.get("/api/config")
def api_config() -> dict[str, Any]:
    return {
        "requiresToken": bool(APP_ACCESS_TOKEN),
        "ffmpegAvailable": ffmpeg_available(),
        "cookiesAvailable": cookies_available(),
        "maxDurationSeconds": MAX_DURATION_SECONDS,
        "maxFileMb": MAX_FILE_MB,
    }


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    return {
        "ok": True,
        "ffmpegAvailable": ffmpeg_available(),
        "cookiesAvailable": cookies_available(),
        "time": int(time.time()),
    }


@app.get("/api/diag")
def api_diag() -> dict[str, Any]:
    # Non-sensitive diagnostics only (no tokens, cookies, or URLs).
    from urllib.error import HTTPError

    pot_url = "http://127.0.0.1:4416"
    pot_reachable = False
    pot_detail: str | None = None
    try:
        with urlopen(pot_url + "/ping", timeout=5) as resp:
            pot_reachable = True
            pot_detail = resp.read(200).decode("utf-8", "replace")
    except HTTPError:
        pot_reachable = True  # server responded, just not on /ping
    except Exception as exc:  # noqa: BLE001
        pot_detail = str(exc)[:200]

    try:
        import importlib.metadata as md

        plugin_version = md.version("bgutil-ytdlp-pot-provider")
    except Exception:  # noqa: BLE001
        plugin_version = None

    try:
        import yt_dlp.version as ydl_version

        ytdlp_version = ydl_version.__version__
    except Exception:  # noqa: BLE001
        ytdlp_version = None

    import subprocess

    try:
        nv = subprocess.run(
            ["/usr/local/bin/node", "--version"],
            capture_output=True, timeout=5, text=True,
        )
        node_check = {
            "returncode": nv.returncode,
            "stdout": nv.stdout.strip(),
            "stderr": nv.stderr.strip()[:300],
        }
    except Exception as exc:  # noqa: BLE001
        node_check = {"error": str(exc)[:300]}

    try:
        pot_log = Path("/tmp/pot-provider.log").read_text("utf-8", "replace")[-1200:]
    except Exception:  # noqa: BLE001
        pot_log = None

    return {
        "playerClient": YTDLP_PLAYER_CLIENT,
        "ffmpegAvailable": ffmpeg_available(),
        "cookiesAvailable": cookies_available(),
        "upstashConfigured": upstash_configured(),
        "potProvider": {"reachable": pot_reachable, "detail": pot_detail},
        "potPluginVersion": plugin_version,
        "ytdlpVersion": ytdlp_version,
        "nodeCheck": node_check,
        "potLog": pot_log,
    }


@app.get("/api/diag/extract")
def api_diag_extract() -> dict[str, Any]:
    # Verbose extraction on a fixed probe video to debug PO token usage.
    class _Collector:
        def __init__(self) -> None:
            self.lines: list[str] = []

        def debug(self, msg: str) -> None:
            self.lines.append(str(msg))

        warning = debug
        error = debug

    logger = _Collector()
    options = {
        **base_ydl_options(),
        "skip_download": True,
        "verbose": True,
        "logger": logger,
    }
    ok = False
    error = None
    try:
        with YoutubeDL(options) as ydl:
            ydl.extract_info(ADMIN_COOKIE_PROBE_URL, download=False)
        ok = True
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:300]

    interesting = [
        ln for ln in logger.lines
        if any(k in ln.lower() for k in ("pot", "gvs", "player", "client", "sign in", "bot", "token"))
    ]
    return {"ok": ok, "error": error, "log": interesting[-60:]}


@app.get("/api/diag/clients")
def api_diag_clients() -> dict[str, Any]:
    # Try each candidate player client (with POT) and report which works cookie-free.
    candidates = [
        "web", "mweb", "web_embedded", "web_safari", "web_creator",
        "tv", "tv_embedded", "android", "android_vr", "ios",
    ]
    results: dict[str, str] = {}
    for client in candidates:
        options = {
            **base_ydl_options(),
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "extractor_args": {"youtube": {"player_client": [client]}},
        }
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(ADMIN_COOKIE_PROBE_URL, download=False)
            has_audio = any(
                f.get("acodec") not in (None, "none")
                for f in (info.get("formats") or [])
            )
            results[client] = f"OK title={info.get('title')!r} audio={has_audio}"
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "not a bot" in msg or "LOGIN_REQUIRED" in msg:
                results[client] = "BLOCKED: login/bot"
            else:
                results[client] = "ERR: " + msg[:120]
    return {"results": results}


@app.post("/api/parse")
async def api_parse(payload: UrlPayload, x_app_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_access_token(x_app_token)
    url = validate_youtube_url(payload.url)
    try:
        return await asyncio.to_thread(extract_info, url)
    except DownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/download")
async def api_download(
    payload: DownloadPayload,
    request: Request,
    x_app_token: str | None = Header(default=None),
) -> FileResponse:
    require_access_token(x_app_token)
    url = validate_youtube_url(payload.url)
    if await request.is_disconnected():
        raise HTTPException(status_code=499, detail="Client disconnected.")

    async with download_slots:
        output_path = await asyncio.to_thread(download_audio, url, payload.audio_format)

    filename = sanitize_filename(output_path.name, restricted=False)
    cleanup = BackgroundTask(shutil.rmtree, output_path.parent, ignore_errors=True)
    return FileResponse(
        output_path,
        media_type="application/octet-stream",
        filename=filename,
        background=cleanup,
    )


@app.get("/api/admin/cookies/status")
def api_admin_cookie_status(x_app_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_app_token)
    source = _cookie_source()
    path = None
    try:
        path = cookies_file()
    except RuntimeError:
        pass
    byte_length = path.stat().st_size if path and path.exists() else 0
    updated_at = int(path.stat().st_mtime) if path and path.exists() else None
    return {
        "loaded": bool(path),
        "source": source,
        "byteLength": byte_length,
        "updatedAt": updated_at,
        "persistBackend": "upstash" if upstash_configured() else None,
        "lastValidation": _LAST_VALIDATION or None,
    }


@app.post("/api/admin/cookies")
async def api_admin_set_cookies(
    payload: CookieRefreshPayload, x_app_token: str | None = Header(default=None)
) -> dict[str, Any]:
    require_admin(x_app_token)
    try:
        await asyncio.to_thread(
            set_runtime_cookies, payload.cookies_text, payload.cookies_base64
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    response: dict[str, Any] = {"saved": True}

    if payload.persist and upstash_configured():
        try:
            raw = COOKIES_FILE.read_bytes()
            b64 = base64.b64encode(raw).decode("ascii")
            await asyncio.to_thread(persist_cookies, b64)
            response["persist"] = {"persisted": True, "backend": "upstash"}
        except Exception as exc:  # noqa: BLE001 - surface persist failure without leaking cookies
            response["persist"] = {"persisted": False, "error": str(exc)[:200]}
    elif payload.persist:
        response["persist"] = {"persisted": False, "error": "Upstash not configured."}

    if payload.run_validation:
        response["validation"] = await asyncio.to_thread(validate_cookies)

    return response


@app.post("/api/admin/cookies/validate")
async def api_admin_validate_cookies(x_app_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_app_token)
    return await asyncio.to_thread(validate_cookies)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

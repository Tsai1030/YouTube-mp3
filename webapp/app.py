from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

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
APP_CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("APP_CORS_ORIGINS", "").split(",")
    if origin.strip()
]

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


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def validate_youtube_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in YOUTUBE_HOSTS:
        raise HTTPException(status_code=400, detail="Only YouTube URLs are supported.")
    return parsed.geturl()


def require_access_token(token: str | None) -> None:
    if APP_ACCESS_TOKEN and token != APP_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Access token required.")


def duration_text(seconds: Any) -> str:
    if not isinstance(seconds, int):
        return "Unknown"
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def base_ydl_options() -> dict[str, Any]:
    return {
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


@app.on_event("startup")
def startup() -> None:
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)


@app.get("/api/config")
def api_config() -> dict[str, Any]:
    return {
        "requiresToken": bool(APP_ACCESS_TOKEN),
        "ffmpegAvailable": ffmpeg_available(),
        "maxDurationSeconds": MAX_DURATION_SECONDS,
        "maxFileMb": MAX_FILE_MB,
    }


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    return {
        "ok": True,
        "ffmpegAvailable": ffmpeg_available(),
        "time": int(time.time()),
    }


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


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

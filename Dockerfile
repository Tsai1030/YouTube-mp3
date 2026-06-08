# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YTDLP_DOWNLOAD_DIR=/tmp

WORKDIR /app

ARG FFMPEG_URL=https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz
ENV FFMPEG_URL=${FFMPEG_URL}

RUN python - <<'PY'
import os
import shutil
import stat
import tarfile
import urllib.request
from pathlib import Path

archive_path = Path("/tmp/ffmpeg.tar.xz")
bin_dir = Path("/usr/local/bin")
wanted = {"ffmpeg", "ffprobe"}

with urllib.request.urlopen(os.environ.get("FFMPEG_URL", "")) as response:
    with archive_path.open("wb") as archive:
        shutil.copyfileobj(response, archive)

with tarfile.open(archive_path) as tar:
    for member in tar.getmembers():
        name = Path(member.name).name
        if name not in wanted:
            continue
        source = tar.extractfile(member)
        if source is None:
            continue
        target = bin_dir / name
        with source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

missing = [name for name in wanted if not (bin_dir / name).exists()]
if missing:
    raise SystemExit(f"Missing FFmpeg binaries: {', '.join(missing)}")
archive_path.unlink(missing_ok=True)
PY

COPY . .

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r webapp/requirements.txt

EXPOSE 10000

CMD ["sh", "-c", "uvicorn webapp.app:app --host 0.0.0.0 --port ${PORT:-10000}"]

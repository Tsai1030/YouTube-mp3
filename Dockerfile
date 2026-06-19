# syntax=docker/dockerfile:1

# Prebuilt bgutil PO token provider (Node server, listens on :4416).
# Same Debian bookworm base as our image, so the node binary is ABI-compatible.
FROM brainicism/bgutil-ytdlp-pot-provider:1.3.1 AS potprovider

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YTDLP_DOWNLOAD_DIR=/tmp

WORKDIR /app

# Node runtime + built POT provider server copied from the prebuilt image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=potprovider /usr/local/bin/node /usr/local/bin/node
COPY --from=potprovider /app /opt/bgutil-provider

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
    && python -m pip install --no-cache-dir -r webapp/requirements.txt \
    && python -m pip install --no-cache-dir "bgutil-ytdlp-pot-provider==1.3.1"

EXPOSE 10000

# Start the POT provider in the background, then the web app. If the provider
# dies the app still serves (it just loses cookie-free auth and falls back).
CMD ["sh", "-c", "(cd /opt/bgutil-provider && node build/main.js &) ; exec uvicorn webapp.app:app --host 0.0.0.0 --port ${PORT:-10000}"]

# yt-dlp Audio Web

Small FastAPI web UI for downloading YouTube audio with yt-dlp.

## Local development

```powershell
cd C:\Users\226376\Desktop\YT
uv pip install -r webapp/requirements.txt
uv run uvicorn webapp.app:app --host 127.0.0.1 --port 8001
```

Open `http://127.0.0.1:8001`.

MP3 conversion requires `ffmpeg` and `ffprobe`. The local Windows machine must have them in `PATH`; the Docker image includes them.

## Render

Render is the recommended target for this app. The included `render.yaml` uses Docker, and the `Dockerfile` installs static `ffmpeg` and `ffprobe` binaries before starting Uvicorn.

1. Push this repo to GitHub.
2. In Render, create a Blueprint from the repo or create a Web Service with Docker runtime.
3. Set `APP_ACCESS_TOKEN` as a secret value.
4. Deploy.

The app binds to `0.0.0.0` and uses Render's `PORT` environment variable.

## Vercel

Vercel is not recommended for the download backend because direct audio downloads usually exceed serverless response limits and long conversions can hit function limits. A practical Vercel setup would host only the frontend and call this backend on Render.

## Environment variables

| Name | Default | Purpose |
| --- | --- | --- |
| `APP_ACCESS_TOKEN` | empty | Optional token required by API calls when set |
| `YTDLP_COOKIES_BASE64` | empty | Base64-encoded Netscape cookies.txt for YouTube bot checks |
| `YTDLP_COOKIES_TEXT` | empty | Raw cookies.txt content; base64 is safer for multi-line Render env |
| `YTDLP_COOKIES_PATH` | empty | Existing cookies file path inside the container |
| `YTDLP_DOWNLOAD_DIR` | system temp | Temporary job output directory |
| `YTDLP_MAX_DURATION_SECONDS` | `1800` | Reject videos longer than this |
| `YTDLP_MAX_FILE_MB` | `150` | yt-dlp max file size limit |
| `YTDLP_MAX_CONCURRENT_DOWNLOADS` | `1` | Download concurrency limit |
| `APP_CORS_ORIGINS` | empty | Comma-separated origins allowed to call the API |

Only download content that you have the right to save.

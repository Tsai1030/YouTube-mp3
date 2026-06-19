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

## Refreshing YouTube cookies (no redeploy)

When YouTube returns "Sign in to confirm you're not a bot", refresh cookies from the
token-protected admin page instead of editing Render env vars:

1. `APP_ACCESS_TOKEN` **must** be set, otherwise the admin endpoints return 403.
2. Export `cookies.txt` locally (see the README) and open `https://<your-app>/admin/`.
3. Enter the admin token, paste the `cookies.txt`, click **儲存並啟用**. It is validated
   immediately and takes effect on the next parse/download — no redeploy.

### Persisting cookies across cold starts (Upstash, free)

Render free instances wipe `/tmp` on cold start, so set up Upstash so cookies auto-restore:

1. Create a free database at [upstash.com](https://upstash.com) (Redis).
2. Copy the **REST URL** and **REST token** from the database's REST API section.
3. In Render, set `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` (secrets).
4. Refresh cookies once via `/admin/`; they are stored in Upstash and restored on every
   boot automatically (no re-paste, no redeploy).

Never commit `cookies.txt` or the Upstash credentials.

## Vercel

Vercel is not recommended for the download backend because direct audio downloads usually exceed serverless response limits and long conversions can hit function limits. A practical Vercel setup would host only the frontend and call this backend on Render.

## Environment variables

| Name | Default | Purpose |
| --- | --- | --- |
| `APP_ACCESS_TOKEN` | empty | Optional token for API calls; **required** to enable the `/admin/` cookie refresh |
| `YTDLP_COOKIES_BASE64` | empty | Base64-encoded Netscape cookies.txt for YouTube bot checks |
| `YTDLP_COOKIES_TEXT` | empty | Raw cookies.txt content; base64 is safer for multi-line Render env |
| `YTDLP_COOKIES_PATH` | empty | Existing cookies file path inside the container |
| `UPSTASH_REDIS_REST_URL` | empty | Upstash Redis REST URL; persists cookies across cold starts |
| `UPSTASH_REDIS_REST_TOKEN` | empty | Upstash Redis REST token |
| `COOKIE_KV_KEY` | `ytdlp:cookies` | Redis key used to store cookies |
| `ADMIN_COOKIE_PROBE_URL` | a known video | URL the admin "validate" check resolves |
| `YTDLP_PROXY` | empty | Optional proxy URL for yt-dlp (e.g. residential proxy) |
| `YTDLP_PLAYER_CLIENT` | `tv_embedded` | YouTube player client(s), comma-separated. `tv_embedded` bypasses the bot check without cookies; set empty for yt-dlp defaults |
| `YTDLP_DOWNLOAD_DIR` | system temp | Temporary job output directory |
| `YTDLP_MAX_DURATION_SECONDS` | `1800` | Reject videos longer than this |
| `YTDLP_MAX_FILE_MB` | `150` | yt-dlp max file size limit |
| `YTDLP_MAX_CONCURRENT_DOWNLOADS` | `1` | Download concurrency limit |
| `APP_CORS_ORIGINS` | empty | Comma-separated origins allowed to call the API |

Only download content that you have the right to save.

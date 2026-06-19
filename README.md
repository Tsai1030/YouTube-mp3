# YouTube MP3 Downloader

一個用 `yt-dlp`、FastAPI 和 Docker 做成的簡單 YouTube 音訊下載網頁。

使用者貼上 YouTube 網址後，可以先解析影片資訊，再選擇下載：

- `MP3`
- `M4A`
- 原始最佳音訊格式

Docker 映像會自動加入 `ffmpeg` 和 `ffprobe`，所以部署到 Render 後可以直接轉 MP3。

> 請只下載你有權保存或平台允許離線使用的內容。

## 功能

- YouTube 網址解析
- 下載音訊檔
- MP3 轉檔
- Render Docker 部署
- 可選 access token 保護 API
- 可設定影片長度、檔案大小、同時下載數

## 本機開發

需求：

- Python 3.10+
- uv
- ffmpeg / ffprobe，如果要在本機轉 MP3

安裝依賴：

```powershell
uv pip install -r webapp/requirements.txt
```

啟動：

```powershell
uv run uvicorn webapp.app:app --host 127.0.0.1 --port 8001
```

開啟：

```text
http://127.0.0.1:8001
```

## Docker

建置：

```bash
docker build -t youtube-mp3 .
```

啟動：

```bash
docker run --rm -p 10000:10000 -e APP_ACCESS_TOKEN=your-token youtube-mp3
```

開啟：

```text
http://127.0.0.1:10000
```

## Render 部署

這個專案最適合部署到 Render Web Service，因為 Render 支援 Docker，可以把 `ffmpeg` 放進同一個容器。

1. 將此 repo 推到 GitHub。
2. 登入 Render。
3. 點選 `New +`。
4. 選 `Blueprint`，或選 `Web Service` 後連接這個 GitHub repo。
5. 如果使用 Blueprint，Render 會讀取根目錄的 `render.yaml`。
6. 如果手動建立 Web Service：
   - Runtime 選 `Docker`
   - Dockerfile path 使用 `./Dockerfile`
   - Health check path 填 `/api/health`
7. 設定環境變數 `APP_ACCESS_TOKEN`。
8. Deploy。

部署完成後，Render 會給你一個網址，例如：

```text
https://your-service.onrender.com
```

打開後就可以貼 YouTube 網址解析與下載。

## YouTube bot 驗證

如果 Render 上出現這種錯誤：

```text
Sign in to confirm you’re not a bot
```

代表 YouTube 對 Render 的雲端 IP 觸發了 bot 驗證。

**第一招（免 cookies，預設已啟用）**：本專案預設用 `YTDLP_PLAYER_CLIENT=tv_embedded`
這個 player client，多數情況下不需要 cookies 就能繞過 bot 驗證 —— 你和使用者都不用碰憑證。
大部分影片這樣就能用了。

**第二招（少數影片仍被擋時）**：才需要提供 cookies。以下兩種方式擇一。

在本機 PowerShell 匯出 cookies：

```powershell
uvx yt-dlp --cookies-from-browser chrome --cookies cookies.txt --skip-download "https://www.youtube.com"
```

如果你用 Edge：

```powershell
uvx yt-dlp --cookies-from-browser edge --cookies cookies.txt --skip-download "https://www.youtube.com"
```

把 cookies 轉成 base64 並複製到剪貼簿：

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes(".\cookies.txt")) | Set-Clipboard
```

到 Render Dashboard 的服務頁面：

1. 進入 `Environment`
2. 新增 `YTDLP_COOKIES_BASE64`
3. 貼上剛剛複製的內容
4. 儲存後重新 Deploy
5. 不要把 `cookies.txt` commit 到 GitHub

## 用 /admin 直覺刷新 cookies（免重新部署）

cookies 會過期。與其每次都進 Render Dashboard 改 env 再重新部署，可以用內建的管理員頁面：

1. 先設好 `APP_ACCESS_TOKEN`（**必填**，否則管理端點會回 403 停用）。
2. 本機匯出 `cookies.txt`（用上面的指令）。
3. 打開 `https://<你的網址>/admin/`，輸入管理員 token，把 `cookies.txt` 全文貼進去，按
   **儲存並啟用**。系統會立刻驗證並在「下一次解析/下載」生效，不需要重新部署。

一般使用者完全不會碰到 cookies，照常貼網址下載即可。

### 用 Upstash 讓 cookies 撐過 Render 休眠（免費）

Render 免費版冷啟動會清空 `/tmp`，cookies 會遺失。設定 Upstash 後，開機會自動還原：

1. 到 [upstash.com](https://upstash.com) 免費開一個 Redis 資料庫。
2. 複製資料庫的 **REST URL** 與 **REST token**。
3. 在 Render 設定 `UPSTASH_REDIS_REST_URL`、`UPSTASH_REDIS_REST_TOKEN`（secret）。
4. 在 `/admin/` 刷新一次 cookies，之後每次開機都會自動還原，免重貼、免重新部署。

別把 `cookies.txt` 或 Upstash 憑證 commit 到 GitHub。

## 環境變數

| 變數 | 預設值 | 說明 |
| --- | --- | --- |
| `APP_ACCESS_TOKEN` | 空 | 設定後，前端會要求輸入 token 才能解析與下載；也是啟用 `/admin/` cookies 刷新的必要條件 |
| `APP_CORS_ORIGINS` | 空 | 允許跨網域呼叫 API 的來源，多個用逗號分隔 |
| `YTDLP_COOKIES_BASE64` | 空 | base64 後的 Netscape cookies.txt，用來處理 YouTube bot 驗證 |
| `YTDLP_COOKIES_TEXT` | 空 | 原始 cookies.txt 內容，不建議優先使用，因為多行 env 容易格式錯 |
| `YTDLP_COOKIES_PATH` | 空 | 容器內 cookies 檔案路徑 |
| `UPSTASH_REDIS_REST_URL` | 空 | Upstash Redis REST URL，用來持久化 cookies、撐過冷啟動 |
| `UPSTASH_REDIS_REST_TOKEN` | 空 | Upstash Redis REST token |
| `COOKIE_KV_KEY` | `ytdlp:cookies` | 存 cookies 用的 Redis key |
| `ADMIN_COOKIE_PROBE_URL` | 一支已知影片 | 管理頁「驗證」時用來測試的網址 |
| `YTDLP_PROXY` | 空 | 可選的 yt-dlp proxy（例如住宅代理），從根本避開資料中心 IP 的 bot 驗證 |
| `YTDLP_PLAYER_CLIENT` | `tv_embedded` | YouTube player client，逗號分隔。預設 `tv_embedded` 可不靠 cookies 繞過 bot 驗證；留空則用 yt-dlp 預設 |
| `YTDLP_DOWNLOAD_DIR` | 系統暫存資料夾 | 暫存下載檔案的位置 |
| `YTDLP_MAX_DURATION_SECONDS` | `1800` | 影片最長秒數限制 |
| `YTDLP_MAX_FILE_MB` | `150` | 最大檔案大小限制 |
| `YTDLP_MAX_CONCURRENT_DOWNLOADS` | `1` | 同時下載數限制 |

## Vercel 說明

不建議把完整後端部署到 Vercel。音訊下載常會超過 Vercel Functions 的 response size 限制，MP3 轉檔也可能碰到執行時間限制。

比較實際的做法是：

- 後端部署在 Render
- 前端如果要放 Vercel，再用 `APP_CORS_ORIGINS` 允許 Vercel 網址呼叫 Render API

但目前這個專案已經把前端和後端放在同一個 Render 服務裡，部署最簡單。

## 技術

- yt-dlp
- FastAPI
- Uvicorn
- ffmpeg / ffprobe
- Docker
- Render

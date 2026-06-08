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

## 環境變數

| 變數 | 預設值 | 說明 |
| --- | --- | --- |
| `APP_ACCESS_TOKEN` | 空 | 設定後，前端會要求輸入 token 才能解析與下載 |
| `APP_CORS_ORIGINS` | 空 | 允許跨網域呼叫 API 的來源，多個用逗號分隔 |
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

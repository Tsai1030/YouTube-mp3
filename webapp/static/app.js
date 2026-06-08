const form = document.querySelector("#parse-form");
const urlInput = document.querySelector("#url");
const tokenWrap = document.querySelector("#token-wrap");
const tokenInput = document.querySelector("#token");
const parseButton = document.querySelector("#parse-button");
const result = document.querySelector("#result");
const thumbnail = document.querySelector("#thumbnail");
const title = document.querySelector("#title");
const uploader = document.querySelector("#uploader");
const duration = document.querySelector("#duration");
const format = document.querySelector("#format");
const downloadButton = document.querySelector("#download-button");
const statusLine = document.querySelector("#status");

let currentUrl = "";

function tokenHeaders() {
  const token = tokenInput.value.trim();
  return token ? { "X-App-Token": token } : {};
}

function setStatus(message, isError = false) {
  statusLine.textContent = message;
  statusLine.classList.toggle("error", isError);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || "Request failed.");
  }
  return payload;
}

function filenameFromDisposition(header) {
  if (!header) {
    return "audio";
  }
  const utf8 = header.match(/filename\*=utf-8''([^;]+)/i);
  if (utf8) {
    return decodeURIComponent(utf8[1]);
  }
  const ascii = header.match(/filename="?([^";]+)"?/i);
  return ascii ? ascii[1] : "audio";
}

async function loadConfig() {
  const config = await requestJson("/api/config");
  tokenWrap.hidden = !config.requiresToken;
  if (!config.ffmpegAvailable) {
    format.querySelector('option[value="mp3"]').disabled = true;
    format.value = "m4a";
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  result.hidden = true;
  parseButton.disabled = true;
  setStatus("解析中...");

  try {
    const url = urlInput.value.trim();
    const info = await requestJson("/api/parse", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...tokenHeaders(),
      },
      body: JSON.stringify({ url }),
    });

    currentUrl = url;
    title.textContent = info.title;
    uploader.textContent = info.uploader || "YouTube";
    duration.textContent = info.durationText;
    thumbnail.src = info.thumbnail || "";
    thumbnail.hidden = !info.thumbnail;
    result.hidden = false;
    setStatus("準備下載");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    parseButton.disabled = false;
  }
});

downloadButton.addEventListener("click", async () => {
  if (!currentUrl) {
    return;
  }

  downloadButton.disabled = true;
  setStatus("下載處理中...");

  try {
    const response = await fetch("/api/download", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...tokenHeaders(),
      },
      body: JSON.stringify({
        url: currentUrl,
        audio_format: format.value,
      }),
    });

    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || "Download failed.");
    }

    const blob = await response.blob();
    const downloadUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = filenameFromDisposition(response.headers.get("Content-Disposition"));
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(downloadUrl);
    setStatus("下載完成");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    downloadButton.disabled = false;
  }
});

loadConfig().catch((error) => setStatus(error.message, true));
